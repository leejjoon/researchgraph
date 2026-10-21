"""Pure graph predicates and JSON-compatible derived read projections.

View accepts authoritative typed maps, including candidate-write before states.
QueryView adds the disposable index's provenance and historical intervals. Neither
class imports Project or performs source refreshes: the public Project boundary
always refreshes before constructing one.
"""

import json
import subprocess
from bisect import bisect_right
from collections import Counter, defaultdict
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from researchgraph import _native_clone
from researchgraph.model import RuleViolation, Status, format_timestamp, parse_timestamp
from researchgraph.store.history import intervals_as_dicts
from researchgraph.store.io import _canonical_equal

CONTAINMENT = frozenset({"part-of", "tests", "addresses", "derived-from"})
EVIDENCE = frozenset({"cites", "derived-from"})


def created_order(record):
    """Return the ascending (created timestamp, identifier) key for a record."""
    return record.created, record.id


def ready_order(node):
    """Sort explicit priorities first, highest first, then creation time and ID.

    A missing priority sorts after every explicit priority, including zero.
    """
    return node.priority is None, -(node.priority or 0), node.created, node.id


def closure(start, links):
    """Return IDs reachable from start through an adjacency mapping, including start.

    Missing adjacency entries are leaves; repeated visits and cycles terminate.
    """
    seen, pending = set(), [start]
    while pending:
        current = pending.pop()
        if current not in seen:
            seen.add(current)
            pending.extend(links.get(current, ()))
    return seen


class View:
    """Evaluate graph predicates over supplied typed maps at the supplied now.

    Active edges drive traversal except usage accounting, which retains retired
    containment edges. Maps may describe candidate writes rather than disk state;
    this class neither refreshes sources nor reconstructs historical records.
    """

    def __init__(self, profile, nodes, edges, now):
        self.profile, self.nodes, self.edges, self.now = profile, nodes, edges, now
        self.claimable = {n.name for n in profile.node_types if n.claimable}
        self.outgoing, self.incoming = defaultdict(list), defaultdict(list)
        self.parents, self.children = defaultdict(list), defaultdict(list)
        self.accounting = defaultdict(list)
        self.inputs, self.replacements = defaultdict(list), defaultdict(list)
        for edge in edges.values():
            if edge.type in CONTAINMENT:
                self.accounting[edge.to].append(edge.from_)
                if not edge.retired:
                    self.parents[edge.from_].append(edge.to)
                    self.children[edge.to].append(edge.from_)
            if edge.retired:
                continue
            self.outgoing[edge.from_].append(edge)
            self.incoming[edge.to].append(edge)
            if edge.type in EVIDENCE:
                self.inputs[edge.from_].append(edge.to)
            if edge.type == "supersedes":
                self.replacements[edge.to].append(edge.from_)

    def lease_expired(self, node):
        """Return whether a running node's non-null lease expires at or before now."""
        return (
            node.status == Status.RUNNING
            and node.lease is not None
            and self.now >= node.lease.expires_at
        )

    def blocking(self, node):
        """Return sorted unique non-done blocker IDs for an active or running node.

        Other statuses return an empty list; only non-retired blocks edges count.
        """
        if node.status not in (Status.ACTIVE, Status.RUNNING):
            return []
        return sorted(
            {
                e.from_
                for e in self.incoming[node.id]
                if e.type == "blocks" and self.nodes[e.from_].status != Status.DONE
            }
        )

    def usage(self, identifier):
        """Sum tokens and compute_seconds over the containment accounting closure.

        Return both keys, defaulting absent usage to zero. Include the root and
        retired containment links; shared descendants and cycles count only once.
        """
        descendants = closure(identifier, self.accounting)
        return {
            key: sum((self.nodes[d].usage or {}).get(key, 0) for d in descendants)
            for key in ("tokens", "compute_seconds")
        }

    def over_budget(self, node):
        """Return whether aggregate usage meets any configured node budget limit.

        Equality exhausts a limit; a missing or empty budget returns False.
        """
        if not node.budget:
            return False
        usage = self.usage(node.id)
        return any(usage[key] >= limit for key, limit in node.budget.items())

    def ready(self, node):
        """Test claimability, active or expired-running status, blocks, and budgets.

        The node and every active containment ancestor must be under budget;
        usage totals themselves include retired containment links.
        """
        return (
            node.type in self.claimable
            and (node.status == Status.ACTIVE or self.lease_expired(node))
            and (not self.incoming.get(node.id) or not self.blocking(node))
            and not self.over_budget(node)
            and (
                not self.parents.get(node.id)
                or not any(
                    self.over_budget(self.nodes[a])
                    for a in closure(node.id, self.parents) - {node.id}
                )
            )
        )

    def responsible(self, identifier):
        """Return sorted replacement-head IDs for transitively superseded inputs.

        Traverse active cites/derived-from inputs excluding the root, then active
        supersedes links toward terminal replacements; return IDs, not records.
        """
        heads = set()
        for source in closure(identifier, self.inputs) - {identifier}:
            if self.replacements[source]:
                heads.update(
                    head
                    for head in closure(source, self.replacements)
                    if not self.replacements[head]
                )
        return sorted(heads)

    def superseded_input(self, identifier):
        """Test whether the root or any transitive evidence input has a replacement."""
        return any(self.replacements[i] for i in closure(identifier, self.inputs))

    def stale(self, node):
        """Return whether a non-retired Claim has responsible replacement heads."""
        return (
            node.type == "Claim"
            and node.status != Status.RETIRED
            and bool(self.responsible(node.id))
        )

    def non_discriminating(self, identifier):
        """Test whether at least two outgoing tests have canonically equal expected.

        Missing expected values compare as None; only active edges participate.
        """
        tests = [e for e in self.outgoing[identifier] if e.type == "tests"]
        return len(tests) >= 2 and all(
            _canonical_equal(e.fields.get("expected"), tests[0].fields.get("expected"))
            for e in tests[1:]
        )

    def group(self, identifier):
        """Return the set of Hypothesis IDs actively answering the given node ID."""
        return {
            e.from_
            for e in self.incoming[identifier]
            if e.type == "answers" and self.nodes[e.from_].type == "Hypothesis"
        }


def evaluation_time(project, now, as_of):
    """Resolve a read clock without applying the write-only HEAD clock floor.

    Validate now even when as_of is supplied. None selects now; a datetime or
    timestamp selects that instant; a Git revision selects its commit time.
    HEAD uses the scanned head, and unresolved revisions go through Git. Invalid
    clocks or revisions raise RuleViolation with code schema. This does not
    refresh source bytes or HEAD; the Project read boundary must do so first.
    """
    try:
        format_timestamp(now)
        if as_of is None:
            return now
        if isinstance(as_of, datetime):
            format_timestamp(as_of)
            return as_of
        if not isinstance(as_of, str):
            raise TypeError("as_of must be a timestamp or commit")
        try:
            return parse_timestamp(as_of)
        except ValueError:
            source = query_source(project)
            commit = project.index._scanned_head if as_of == "HEAD" else as_of
            if commit in source.commit_times:
                return source.commit_times[commit]
            result = project._git.run(
                "show", "-s", "--format=%ct", "--end-of-options", as_of + "^{commit}"
            )
            return datetime.fromtimestamp(int(result.stdout.strip()), UTC)
    except (ValueError, TypeError, AttributeError, subprocess.SubprocessError) as exc:
        raise RuleViolation("schema", f"Invalid read time: {exc}") from exc


def copy_json(value):
    """Recursively copy JSON dictionaries and lists, sharing immutable scalar leaves.

    Inputs must already be JSON-compatible; other object types pass through.
    """
    if isinstance(value, dict):
        return {key: copy_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [copy_json(item) for item in value]
    return value


class _NodeColumns:
    pass


class _ActorColumns:
    pass


def _shared_columns(values, holder_type):
    # Temporary holders give ordinary dictionaries shared immutable keys. Copies
    # still own independent values; profile-field mappings never use this path.
    holder = holder_type()
    for key, value in values.items():
        setattr(holder, key, value)
    return vars(holder)


class QuerySource:
    """Read-only, source-epoch-derived timelines and reusable typed overlays.

    Owned by Index and discarded whenever its authoritative models are reloaded.
    Typed overlays are internal and may share models; serialized outputs detach
    mutable containers eagerly. Epoch reuse and the native static envelope are
    private computation, never substitutes for the Project boundary's mandatory
    fresh source-byte and HEAD scan.
    """

    def __init__(self, index):
        self.profile = index.project.profile
        self.nodes, self.edges = index._nodes, index._edges
        # Private templates and overlays are read-only. Reuse epoch-owned empty
        # containers by field; public projections always allocate fresh ones.
        self._empty_fields = index._empty_fields
        self._empty_approvals = index._empty_approvals
        self._empty_artifacts = index._empty_artifacts
        self._empty_tags = index._empty_tags
        self._actor_templates = {}
        self.ready_order = [n.id for n in sorted(self.nodes.values(), key=ready_order)]
        # These nodes cannot be affected by graph readiness constraints at any
        # historical boundary in this epoch. Include retired/future edges when
        # identifying constraints, so their temporal visibility needs no cache.
        constrained = {n.id for n in self.nodes.values() if n.budget}
        for edge in self.edges.values():
            if edge.type == "blocks":
                constrained.add(edge.to)
            if edge.type in CONTAINMENT:
                constrained.add(edge.from_)
        claimable = {n.name for n in self.profile.node_types if n.claimable}
        self.unconstrained = {
            n.id
            for n in self.nodes.values()
            if n.type in claimable and n.id not in constrained
        }
        self.intervals = intervals_as_dicts(index.connection)
        self.timelines = defaultdict(list)
        # Archive commit strings are opaque provenance, not locally resolvable
        # revisions. Cache only actual commits reachable from the scanned HEAD;
        # other refs and abbreviations still go through git show.
        self.commit_times = {}
        if index._scanned_head is not None:
            for line in index.project._git.run(
                "log", "--format=%H %ct", index._scanned_head
            ).stdout.splitlines():
                commit, seconds = line.split()
                self.commit_times[commit] = datetime.fromtimestamp(int(seconds), UTC)
        self.variants, self.serialized = {}, {}
        for interval in self.intervals:
            start = parse_timestamp(interval["from_time"])
            end = parse_timestamp(interval["to_time"]) if interval["to_time"] else None
            # Empty half-open intervals never take effect, including tied commits.
            if end == start:
                continue
            self.timelines[interval["node_id"]].append(
                (start, end, Status(interval["status"]))
            )
        for timeline in self.timelines.values():
            timeline.sort(key=lambda row: row[0])
        self.static_map, self.static_bounds, self.dynamic_nodes = {}, {}, {}
        for identifier, node in self.nodes.items():
            timeline = self.timelines.get(identifier, ())
            if (
                len(timeline) == 1
                and timeline[0][2] == node.status
                and not node.approvals
                and not node.artifacts
            ):
                start, end, _ = timeline[0]
                self.static_map[identifier] = node
                self.static_bounds[identifier] = max(start, node.created), end
                # The range fully represents this node's single interval. Do
                # not also retain per-node lists and status-enum tuples.
                self.timelines.pop(identifier, None)
            else:
                self.dynamic_nodes[identifier] = node
        self.starts = {
            identifier: [row[0] for row in timeline]
            for identifier, timeline in self.timelines.items()
        }
        self.static_start = max(
            (start for start, _ in self.static_bounds.values()), default=None
        )
        self.static_end = min(
            (end for _, end in self.static_bounds.values() if end is not None),
            default=None,
        )
        # A single epoch-owned batch, never a cache of per-boundary ready sets.
        # Complex rows retain the normal serializer and dynamic predicates.
        ready_templates, self.ready_ranks, batched = [], [], set()
        self.ready_rank = {
            identifier: rank for rank, identifier in enumerate(self.ready_order)
        }
        for identifier in self.ready_order:
            node = self.static_map.get(identifier)
            if (
                node is None
                or node.status != Status.ACTIVE
                or identifier not in self.unconstrained
                or node.budget is not None
                or node.usage is not None
                or node.lease is not None
            ):
                continue
            template = self._template(node)
            if any(
                isinstance(value, (dict, list)) for value in template["fields"].values()
            ):
                continue
            # Scalar skeletons preserve canonical key order without retaining
            # a GC-visible mutable DTO tree per static row. All mutable columns
            # are inserted into fresh caller-owned containers when cloned.
            columns = {"author", "fields", "approvals", "artifacts", "tags"}
            skeleton = _shared_columns(
                {
                    key: None if key in columns else value
                    for key, value in template.items()
                },
                _NodeColumns,
            )
            ready_templates.append(
                (
                    skeleton,
                    template["author"],
                    template["fields"],
                    tuple(template["tags"]),
                )
            )
            self.ready_ranks.append(self.ready_rank[identifier])
            batched.add(identifier)
        self._ready_columns = _native_clone.compile_templates(ready_templates)
        self.ready_remaining = [
            identifier for identifier in self.ready_order if identifier not in batched
        ]

    def _template(self, node):
        """Compact canonical private DTO columns; every return path detaches."""
        template = node.to_dict()
        for name in ("fields", "approvals", "artifacts", "tags"):
            if not template[name]:
                template[name] = getattr(self, "_empty_" + name)

        def actor(value):
            key = tuple(
                value.get(name)
                for name in ("kind", "name", "email", "model", "version")
            )
            if key not in self._actor_templates:
                self._actor_templates[key] = _shared_columns(value, _ActorColumns)
            return self._actor_templates[key]

        template["author"] = actor(template["author"])
        if template["lease"] is not None:
            template["lease"]["actor"] = actor(template["lease"]["actor"])
        for approval in template["approvals"]:
            approval["actor"] = actor(approval["actor"])
        for artifact in template["artifacts"]:
            artifact["added_by"] = actor(artifact["added_by"])
        return template

    def in_static_envelope(self, now):
        """Test the common half-open visibility range of all static node intervals.

        This private-computation optimization says nothing about source freshness
        and does not validate current bytes or HEAD.
        """
        return (self.static_start is None or self.static_start <= now) and (
            self.static_end is None or now < self.static_end
        )

    def clone_ready_batch(self):
        """Eagerly clone the epoch's proven, privately owned flat columns.

        All top-level dictionaries and mutable column containers are fresh.
        Event lists are empty, actor/field values and tag elements are scalars;
        budget, usage and lease are null by construction.
        """
        return _native_clone.clone_rows(self._ready_columns)

    def at(self, now):
        """Return internal (node-map, edge-map) overlays visible at inclusive now.

        Select half-open status intervals, omit future nodes/edges, filter approval
        and artifact events, and undo edge retirements later than now. Other node
        values, including budget, usage, tags, priority and lease, remain current:
        this is not a historical checkout. Models may be shared and must not be
        mutated; these maps are not detached public JSON results.
        """
        nodes = (
            self.static_map.copy()
            if self.in_static_envelope(now)
            else {
                identifier: self.static_map[identifier]
                for identifier, (start, end) in self.static_bounds.items()
                if start <= now and (end is None or now < end)
            }
        )
        for identifier, node in self.dynamic_nodes.items():
            if node.created > now:
                continue
            timeline = self.timelines.get(identifier, ())
            position = bisect_right(self.starts.get(identifier, ()), now) - 1
            while position >= 0:
                _, end, status = timeline[position]
                if end is None or now < end:
                    break
                position -= 1
            if position < 0:
                continue
            if status == node.status and not node.approvals and not node.artifacts:
                nodes[identifier] = node
                continue
            approvals = tuple(i for i, a in enumerate(node.approvals) if a.at <= now)
            artifacts = tuple(
                i for i, a in enumerate(node.artifacts) if a.added_at <= now
            )
            if (
                status == node.status
                and len(approvals) == len(node.approvals)
                and len(artifacts) == len(node.artifacts)
            ):
                nodes[identifier] = node
                continue
            key = status, approvals, artifacts
            cached = self.variants.get(identifier)
            if cached is None or cached[0] != key:
                variant = replace(
                    node,
                    status=status,
                    approvals=(
                        [node.approvals[i] for i in approvals]
                        if approvals
                        else self._empty_approvals
                    ),
                    artifacts=(
                        [node.artifacts[i] for i in artifacts]
                        if artifacts
                        else self._empty_artifacts
                    ),
                )
                # Retain only the latest projection of each node, not every
                # boundary's growing event prefix during a timeline scrub.
                self.variants[identifier] = key, variant
            nodes[identifier] = self.variants[identifier][1]
        edges = {}
        for identifier, edge in self.edges.items():
            if edge.created > now or edge.from_ not in nodes or edge.to not in nodes:
                continue
            if edge.retired_at is not None and edge.retired_at > now:
                edge = replace(edge, retired=False, retired_at=None)
            edges[identifier] = edge
        return nodes, edges

    def serialize(self, node):
        """Return a fully materialized node dictionary with fresh mutable containers.

        Reuse only private templates keyed by model identity; nested fields,
        event payloads, actors, tags, budgets, usage and leases are detached.
        """
        key = node.id
        cached = self.serialized.get(key)
        if cached is None or cached[2] is not node:
            template = self._template(node)
            # Only fields admit arbitrary nested JSON. Flat scalar fields need
            # a shallow mapping copy, while all fixed empty containers can be
            # allocated directly without a recursive visitor.
            nested_fields = any(
                isinstance(v, (dict, list)) for v in template["fields"].values()
            )
            # Keep a strong model identity with this node's latest DTO. Older
            # retained views can be revisited without stale id()-key reuse.
            self.serialized[key] = template, nested_fields, node
        template, nested_fields, _ = self.serialized[key]
        result = dict(template)
        result["author"] = dict(template["author"])
        result["fields"] = (
            copy_json(template["fields"]) if nested_fields else dict(template["fields"])
        )
        result["approvals"] = (
            copy_json(template["approvals"]) if template["approvals"] else []
        )
        result["artifacts"] = (
            copy_json(template["artifacts"]) if template["artifacts"] else []
        )
        result["tags"] = list(template["tags"])
        for name in ("budget", "usage"):
            if template[name] is not None:
                result[name] = dict(template[name])
        if template["lease"] is not None:
            result["lease"] = dict(template["lease"])
            result["lease"]["actor"] = dict(template["lease"]["actor"])
        return result


def query_source(project):
    """Return the index-owned source, rebuilding when absent or profile identity changes.

    The index invalidates this private derived state when reloading its models.
    Calling this factory alone does not scan authoritative source bytes or HEAD.
    """
    index = project.index
    if (
        index._query_source is None
        or index._query_source.profile is not project.profile
    ):
        index._query_source = QuerySource(index)
    return index._query_source


class QueryView(View):
    """Project read projections over an already-refreshed source epoch.

    A non-None as_of selects reconstruction at now: node/edge visibility, status,
    approval/artifact events and edge retirement reflect that boundary. Budget,
    usage, tags, priority, lease and other node fields remain current, not a Git
    checkout. With as_of None, current models are evaluated at now. Construction
    itself does not refresh bytes or HEAD; Project must do that before each read.
    Public projections are eager dictionaries/lists, not lazy cache views.
    """

    def __init__(self, project, now, as_of):
        self.index = project.index
        self.as_of = as_of is not None
        self.source = query_source(project)
        self.intervals = self.source.intervals
        nodes, edges = self.source.nodes, self.source.edges
        if self.as_of:
            nodes, edges = self.source.at(now)
        super().__init__(project.profile, nodes, edges, now)

    def require(self, identifier):
        """Return the internal visible node or raise schema/not_found RuleViolation.

        Non-string identifiers are schema errors; absent IDs are not_found errors.
        The returned typed model is shared internal state, not a public DTO.
        """
        if not isinstance(identifier, str):
            raise RuleViolation("schema", "Node arguments must be identifiers")
        if identifier not in self.nodes:
            raise RuleViolation("not_found", "Node not found", [identifier])
        return self.nodes[identifier]

    def ordered(self, identifiers):
        """Return internal nodes for IDs ordered by creation time then identifier.

        All IDs must exist in this view; a missing ID raises KeyError.
        """
        return sorted((self.nodes[i] for i in identifiers), key=created_order)

    def vocabulary(self, *names):
        """Return whether every requested node-type name exists in the profile."""
        return set(names) <= {n.name for n in self.profile.node_types}

    def graph(self):
        """Return all visible nodes/edges, including retired, in created/ID order.

        Use the same historical projection as show, without show-only incident
        or derived fields. Each returned record owns its mutable containers.
        """
        return {
            "nodes": [
                node.to_dict()
                for node in sorted(self.nodes.values(), key=created_order)
            ],
            "edges": [
                edge.to_dict()
                for edge in sorted(self.edges.values(), key=created_order)
            ],
        }

    def ready_items(self, prepared=None):
        """Return eager ready-node dictionaries in priority/creation/ID order.

        Readiness uses this view's status and time but current budget, usage,
        priority and lease values. Optional prepared is an internal (source, rows)
        pair: matching source identity permits inserting remaining rows into that
        caller-owned batch. Otherwise clone a fresh batch; no lazy rows escape.
        """
        if not self.as_of or self.source.in_static_envelope(self.now):
            rows = (
                prepared[1]
                if prepared is not None and prepared[0] is self.source
                else self.source.clone_ready_batch()
            )
            # Only non-batched nodes need temporal readiness evaluation. Insert
            # their fresh rows by source-epoch rank, preserving exact ready order.
            added = 0
            for identifier in self.source.ready_remaining:
                node = self.nodes.get(identifier)
                if node is None or not self.ready(node):
                    continue
                rank = self.source.ready_rank[identifier]
                position = bisect_right(self.source.ready_ranks, rank) + added
                rows.insert(position, self.source.serialize(node))
                added += 1
            return rows
        return [
            self.source.serialize(n)
            for identifier in self.source.ready_order
            if (n := self.nodes.get(identifier)) is not None
            if (n.status == Status.ACTIVE or self.lease_expired(n))
            and (identifier in self.source.unconstrained or self.ready(n))
        ]

    def blocked_items(self):
        """Return blocked node dictionaries in ready order, each with blocking IDs.

        The blocking array is sorted and unique; only active/running nodes qualify.
        """
        return [
            dict(n.to_dict(), blocking=self.blocking(n))
            for n in sorted(self.nodes.values(), key=ready_order)
            if self.blocking(n)
        ]

    def queue_items(self):
        """Return proposed nodes with activation gates, ordered by creation then ID.

        Each node gains gate={roles: sorted unique roles, agents_allowed: bool};
        agents_allowed is true only when every applicable gate permits agents.
        """
        result = []
        for node in sorted(self.nodes.values(), key=created_order):
            gates = self.profile.gates_for(node.type, node.fields)
            if node.status == Status.PROPOSED and gates:
                result.append(
                    dict(
                        node.to_dict(),
                        gate={
                            "roles": sorted(
                                {role for gate in gates for role in gate.roles}
                            ),
                            "agents_allowed": all(
                                gate.agents_allowed for gate in gates
                            ),
                        },
                    )
                )
        return result

    def evidence(self, identifier):
        """Return {items: node dictionaries} for a Claim's transitive evidence inputs.

        Exclude the Claim and sort by creation/ID. A non-Claim or missing Claim
        vocabulary yields empty items; invalid or absent IDs raise via require.
        """
        node = self.require(identifier)
        if not self.vocabulary("Claim") or node.type != "Claim":
            return {"items": []}
        return {
            "items": [
                n.to_dict()
                for n in self.ordered(closure(identifier, self.inputs) - {identifier})
            ]
        }

    def stale_items(self):
        """Return stale Claim dictionaries in creation/ID order with responsible arrays.

        Each responsible array contains sorted terminal replacement IDs, not nodes.
        """
        return [
            dict(n.to_dict(), responsible=self.responsible(n.id))
            for n in sorted(self.nodes.values(), key=created_order)
            if self.stale(n)
        ]

    def weakest(self, identifier):
        """Return directly cited node dictionaries ranked weakest first for a Claim.

        Superseded-input nodes lead, then lower runs (missing means zero), false
        held_out, and ID. Return [] for non-Claims or missing Claim
        vocabulary; invalid or absent identifiers raise via require.
        """
        node = self.require(identifier)
        if not self.vocabulary("Claim") or node.type != "Claim":
            return []
        targets = {e.to for e in self.outgoing[identifier] if e.type == "cites"}
        return [
            n.to_dict()
            for n in sorted(
                (self.nodes[i] for i in targets),
                key=lambda n: (
                    not self.superseded_input(n.id),
                    n.fields.get("runs", 0),
                    n.fields.get("held_out", False),
                    n.id,
                ),
            )
        ]

    def predictions(self, identifier):
        """Return incoming tests-edge dictionaries for a Hypothesis in creation order.

        Each gains source (node dictionary), expected (None if absent),
        discriminating (True if absent), and the source status string. Return []
        for wrong type or missing Hypothesis/Question/Prediction vocabulary;
        invalid or absent identifiers raise via require.
        """
        node = self.require(identifier)
        if (
            not self.vocabulary("Hypothesis", "Question", "Prediction")
            or node.type != "Hypothesis"
        ):
            return []
        return [
            dict(
                e.to_dict(),
                source=self.nodes[e.from_].to_dict(),
                expected=copy_json(e.fields.get("expected")),
                discriminating=copy_json(e.fields.get("discriminating", True)),
                status=str(self.nodes[e.from_].status),
            )
            for e in sorted(self.incoming[identifier], key=created_order)
            if e.type == "tests"
        ]

    def matrix(self, identifier):
        """Return {rows, columns} comparing tests of a Question's answering hypotheses.

        Columns are hypothesis dictionaries; rows contain source dictionaries,
        cells mapping every column ID to its sole test's expected value (otherwise
        None), and non_discriminating booleans. Both axes use creation/ID order.
        Wrong type or missing Hypothesis/Question/Prediction vocabulary returns
        empty axes; invalid or absent identifiers raise via require.
        """
        node = self.require(identifier)
        if (
            not self.vocabulary("Hypothesis", "Question", "Prediction")
            or node.type != "Question"
        ):
            return {"rows": [], "columns": []}
        columns = self.ordered(self.group(identifier))
        group = {n.id for n in columns}
        sources = {
            e.from_ for h in group for e in self.incoming[h] if e.type == "tests"
        }
        rows = []
        for source in self.ordered(sources):
            cells = {}
            for target in columns:
                matches = [
                    e
                    for e in self.outgoing[source.id]
                    if e.type == "tests" and e.to == target.id
                ]
                cells[target.id] = (
                    copy_json(matches[0].fields.get("expected"))
                    if len(matches) == 1
                    else None
                )
            rows.append(
                {
                    "source": source.to_dict(),
                    "cells": cells,
                    "non_discriminating": self.non_discriminating(source.id),
                }
            )
        return {"rows": rows, "columns": [n.to_dict() for n in columns]}

    def alternatives(self, identifier):
        """Return other hypotheses answering any question answered by this hypothesis.

        Creation/ID-ordered node dictionaries gain supports and contradicts arrays
        of source-node dictionaries, also creation/ID ordered. Wrong type or missing
        Hypothesis/Question/Prediction vocabulary returns []; invalid or absent
        identifiers raise via require.
        """
        node = self.require(identifier)
        if (
            not self.vocabulary("Hypothesis", "Question", "Prediction")
            or node.type != "Hypothesis"
        ):
            return []
        questions = {e.to for e in self.outgoing[identifier] if e.type == "answers"}
        members = set().union(*(self.group(q) for q in questions)) - {identifier}
        return [
            dict(
                n.to_dict(),
                **{
                    kind: [
                        c.to_dict()
                        for c in self.ordered(
                            {e.from_ for e in self.incoming[n.id] if e.type == kind}
                        )
                    ]
                    for kind in ("supports", "contradicts")
                },
            )
            for n in self.ordered(members)
        ]

    def notes(self, identifier):
        """Return Note dictionaries linked about the node, newest first with ID ties.

        Missing Note vocabulary yields []; invalid or absent IDs raise via require.
        """
        self.require(identifier)
        if not self.vocabulary("Note"):
            return []
        notes = {
            e.from_
            for e in self.incoming[identifier]
            if e.type == "about" and self.nodes[e.from_].type == "Note"
        }
        # Reverse the time only: equal timestamps retain ascending identifier ties.
        return [
            n.to_dict()
            for n in sorted(
                (self.nodes[i] for i in notes),
                key=lambda n: (-n.created.timestamp(), n.id),
            )
        ]

    def subtree(self, identifier):
        """Return nodes, status_counts and usage for a containment subtree.

        Nodes include the root and active-link descendants in creation/ID order;
        status_counts maps sorted status strings to counts. Usage instead includes
        retired containment links and always has tokens and compute_seconds keys.
        Invalid or absent identifiers raise via require.
        """
        self.require(identifier)
        nodes = self.ordered(closure(identifier, self.children))
        return {
            "nodes": [n.to_dict() for n in nodes],
            "status_counts": dict(
                sorted(Counter(str(n.status) for n in nodes).items())
            ),
            "usage": self.usage(identifier),
        }

    def since(self, time):
        """Return node/edge dictionaries with recorded change times strictly after time.

        Accept a timestamp string or datetime; invalid clocks raise schema
        RuleViolation. Consider node creation/update/events and edge creation/
        retirement, bounded inclusively by now only in as-of views. Sort results
        by creation/ID, not change time; this is not a diff of historical fields.
        """
        try:
            time = parse_timestamp(time) if isinstance(time, str) else time
            format_timestamp(time)
        except (TypeError, ValueError, AttributeError) as exc:
            raise RuleViolation("schema", f"Invalid since time: {exc}") from exc

        def changed(times):
            return any(
                t is not None and t > time and (not self.as_of or t <= self.now)
                for t in times
            )

        nodes = [
            n
            for n in self.nodes.values()
            if changed(
                [
                    n.created,
                    n.updated,
                    *(a.at for a in n.approvals),
                    *(a.added_at for a in n.artifacts),
                ]
            )
        ]
        edges = [e for e in self.edges.values() if changed([e.created, e.retired_at])]
        return [r.to_dict() for r in sorted([*nodes, *edges], key=created_order)]

    def where(
        self,
        *,
        type=None,
        status=None,
        author=None,
        field=None,
        tag=None,
        text=None,
        over_budget=False,
    ):
        """Return creation/ID-ordered node dictionaries matching all supplied filters.

        None disables type, status, author-name, tag and text filters. Text is a
        case-insensitive title/body substring. Field accepts a mapping, one k=v
        string, or iterable of k=v strings, split at the first equals sign. Values
        compare as strings; identical repeats are allowed, while conflicting
        repeats and malformed entries raise schema RuleViolation.
        over_budget=False adds no constraint;
        True requires exhausted current budget/usage. Tags and other non-status
        fields remain current even in an as-of view.
        """
        if field is None:
            fields = {}
        elif isinstance(field, dict):
            fields = field
        else:
            entries = [field] if isinstance(field, str) else field
            fields = {}
            try:
                for entry in entries:
                    key, value = entry.split("=", 1)
                    if key in fields and fields[key] != value:
                        raise RuleViolation(
                            "schema", f"Conflicting values for field {key}"
                        )
                    fields[key] = value
            except (ValueError, TypeError, AttributeError) as exc:
                raise RuleViolation("schema", "Fields must be k=v filters") from exc
        return [
            n.to_dict()
            for n in sorted(self.nodes.values(), key=created_order)
            if (type is None or n.type == type)
            and (status is None or n.status == status)
            and (author is None or n.author.name == author)
            and all(
                key in n.fields and str(n.fields[key]) == str(value)
                for key, value in fields.items()
            )
            and (tag is None or tag in n.tags)
            and (
                text is None or text.casefold() in (n.title + "\n" + n.body).casefold()
            )
            and (not over_budget or self.over_budget(n))
        ]

    def show(self, identifier):
        """Return an edge dictionary or an enriched node dictionary for a visible ID.

        Nodes gain active incident edges, instructing nodes, outgoing pins, flags
        (blocked, ready, stale, lease_expired, over_budget), and warnings containing
        non_discriminating when applicable. Arrays use creation/ID order. Non-string
        IDs raise schema and absent IDs raise not_found RuleViolation.
        """
        if not isinstance(identifier, str):
            raise RuleViolation("schema", "Graph arguments must be identifiers")
        if identifier in self.edges:
            return self.edges[identifier].to_dict()
        node = self.require(identifier)
        edges = {
            e.id: e for e in [*self.incoming[identifier], *self.outgoing[identifier]]
        }
        instructions = {
            e.from_ for e in self.incoming[identifier] if e.type == "instructs"
        }
        return dict(
            node.to_dict(),
            edges=[e.to_dict() for e in sorted(edges.values(), key=created_order)],
            instructions=[n.to_dict() for n in self.ordered(instructions)],
            pins=[
                e.to_dict()
                for e in sorted(self.outgoing[identifier], key=created_order)
                if e.type == "pins"
            ],
            flags={
                "blocked": bool(self.blocking(node)),
                "ready": self.ready(node),
                "stale": self.stale(node),
                "lease_expired": self.lease_expired(node),
                "over_budget": self.over_budget(node),
            },
            warnings=["non_discriminating"]
            if self.non_discriminating(identifier)
            else [],
        )

    def history(self, identifier):
        """Return provenance events across the node's undirected supersession chain.

        Interval entries retain interval fields plus kind, node, commit and at;
        approval/artifact entries retain payloads plus original ordinal and those
        event keys. Supersedes entries retain edge fields plus kind, node (source),
        commit and at. Include retired supersedes edges visible in this view.
        Sort by at, node, kind, ordinal (default -1), then ID (default empty).
        As-of views omit later events and truncate later interval ends to null;
        this does not recover historical budget, usage, tags, priority or lease.
        Invalid or absent identifiers raise via require.
        """
        self.require(identifier)
        links = defaultdict(list)
        for edge in self.edges.values():
            if edge.type == "supersedes":
                links[edge.from_].append(edge.to)
                links[edge.to].append(edge.from_)
        chain = closure(identifier, links)
        boundary = format_timestamp(self.now)
        events = []
        for interval in self.intervals:
            if interval["node_id"] not in chain or (
                self.as_of and interval["from_time"] > boundary
            ):
                continue
            interval = dict(interval)
            if (
                self.as_of
                and interval["to_time"] is not None
                and interval["to_time"] > boundary
            ):
                interval["to_time"], interval["to_commit"] = None, None
            events.append(
                dict(
                    interval,
                    kind="interval",
                    node=interval["node_id"],
                    commit=interval["from_commit"],
                    at=interval["from_time"],
                )
            )
        # Read original ordinals and payloads, not positions in as-of-filtered lists.
        for table, kind, time_key in (
            ("approvals", "approval", "at"),
            ("artifacts", "artifact", "added_at"),
        ):
            for row in self.index.connection.execute(
                f"SELECT node_id,ordinal,payload,from_commit FROM {table}"
            ):
                node_id, ordinal, payload, commit = row
                payload = json.loads(payload)
                at = payload[time_key]
                if node_id in chain and (not self.as_of or at <= boundary):
                    events.append(
                        dict(
                            payload,
                            kind=kind,
                            node=node_id,
                            ordinal=ordinal,
                            commit=commit,
                            at=at,
                        )
                    )
        for edge in self.edges.values():
            if edge.type == "supersedes" and edge.from_ in chain and edge.to in chain:
                commit = self.index.connection.execute(
                    "SELECT from_commit FROM edges WHERE id=?", (edge.id,)
                ).fetchone()[0]
                events.append(
                    dict(
                        edge.to_dict(),
                        kind="supersedes",
                        node=edge.from_,
                        commit=commit,
                        at=format_timestamp(edge.created),
                    )
                )
        return sorted(
            events,
            key=lambda e: (
                e["at"],
                e["node"],
                e["kind"],
                e.get("ordinal", -1),
                e.get("id", ""),
            ),
        )

    def _recent_failures(self):
        cutoff = self.now - timedelta(hours=24)
        return {
            interval["node_id"]
            for interval in self.intervals
            if interval["status"] == "failed"
            and cutoff <= parse_timestamp(interval["from_time"]) <= self.now
            and interval["node_id"] in self.nodes
        }

    def time_state(self):
        """Return clock-dependent lease/failure memberships for notifications.

        These change when evaluation time crosses a lease deadline or a digest
        failure-window boundary, even if all source bytes remain unchanged.
        """
        return (
            frozenset(
                node.id for node in self.nodes.values() if self.lease_expired(node)
            ),
            frozenset(self._recent_failures()),
        )

    def digest(self):
        """Return {items: [{kind, count, nodes}, ...]} for six summary groups.

        Groups are running, ready, blocked, awaiting, failed_24h and claims, in that
        order. Failed includes visible nodes entering failed during the inclusive
        last 24 hours, even if no longer failed; claims excludes retired Claims.
        Ready/blocked use ready order and awaiting uses queue gate enrichment;
        other groups use creation/ID order. As-of status/events are reconstructed
        while readiness still uses current budget, usage, priority and lease.
        """
        failed = self._recent_failures()
        groups = {
            "running": [
                n.to_dict()
                for n in sorted(self.nodes.values(), key=created_order)
                if n.status == Status.RUNNING
            ],
            "ready": self.ready_items(),
            "blocked": self.blocked_items(),
            "awaiting": self.queue_items(),
            "failed_24h": [n.to_dict() for n in self.ordered(failed)],
            "claims": [
                n.to_dict()
                for n in sorted(self.nodes.values(), key=created_order)
                if n.type == "Claim" and n.status != Status.RETIRED
            ],
        }
        return {
            "items": [
                {"kind": kind, "count": len(nodes), "nodes": nodes}
                for kind, nodes in groups.items()
            ]
        }
