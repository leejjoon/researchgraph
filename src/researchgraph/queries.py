"""Pure graph predicates and JSON-compatible derived read projections.

View accepts authoritative typed maps, including candidate-write before states.
QueryView adds the disposable index's provenance and historical intervals. Neither
class imports Project or performs source refreshes: the public Project boundary
always refreshes before constructing one.
"""

import json
import subprocess
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta

from researchgraph.model import RuleViolation, Status, format_timestamp, parse_timestamp
from researchgraph.store.history import intervals_as_dicts
from researchgraph.store.io import _canonical_equal

CONTAINMENT = frozenset({"part-of", "tests", "addresses", "derived-from"})
EVIDENCE = frozenset({"cites", "derived-from"})


def created_order(record):
    return record.created, record.id


def ready_order(node):
    return node.priority is None, -(node.priority or 0), node.created, node.id


def closure(start, links):
    """Set closure including start, safe even for cyclic non-DAG relations."""
    seen, pending = set(), [start]
    while pending:
        current = pending.pop()
        if current not in seen:
            seen.add(current)
            pending.extend(links.get(current, ()))
    return seen


class View:
    """Reusable graph predicates over typed node/edge maps at an evaluation time."""

    def __init__(self, profile, nodes, edges, now):
        self.profile, self.nodes, self.edges, self.now = profile, nodes, edges, now
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
        return (
            node.status == Status.RUNNING
            and node.lease is not None
            and self.now >= node.lease.expires_at
        )

    def blocking(self, node):
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
        descendants = closure(identifier, self.accounting)
        return {
            key: sum((self.nodes[d].usage or {}).get(key, 0) for d in descendants)
            for key in ("tokens", "compute_seconds")
        }

    def over_budget(self, node):
        if not node.budget:
            return False
        usage = self.usage(node.id)
        return any(usage[key] >= limit for key, limit in node.budget.items())

    def ready(self, node):
        return (
            self.profile.node_type(node.type).claimable
            and (node.status == Status.ACTIVE or self.lease_expired(node))
            and not self.blocking(node)
            and not any(
                self.over_budget(self.nodes[a]) for a in closure(node.id, self.parents)
            )
        )

    def responsible(self, identifier):
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
        return any(self.replacements[i] for i in closure(identifier, self.inputs))

    def stale(self, node):
        return (
            node.type == "Claim"
            and node.status != Status.RETIRED
            and bool(self.responsible(node.id))
        )

    def non_discriminating(self, identifier):
        tests = [e for e in self.outgoing[identifier] if e.type == "tests"]
        return len(tests) >= 2 and all(
            _canonical_equal(e.fields.get("expected"), tests[0].fields.get("expected"))
            for e in tests[1:]
        )

    def group(self, identifier):
        return {
            e.from_
            for e in self.incoming[identifier]
            if e.type == "answers" and self.nodes[e.from_].type == "Hypothesis"
        }


def evaluation_time(project, now, as_of):
    """Validate read clocks without applying the write-only HEAD clock floor."""
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
            result = project._git.run(
                "show", "-s", "--format=%ct", "--end-of-options", as_of + "^{commit}"
            )
            return datetime.fromtimestamp(int(result.stdout.strip()), UTC)
    except (ValueError, TypeError, AttributeError, subprocess.SubprocessError) as exc:
        raise RuleViolation("schema", f"Invalid read time: {exc}") from exc


class QueryView(View):
    """One refreshed query snapshot, optionally reconstructed at an inclusive time."""

    def __init__(self, project, now, as_of):
        self.index = project.index
        self.as_of = as_of is not None
        self.intervals = intervals_as_dicts(self.index.connection)
        nodes = {n.id: n for n in self.index.get_nodes()}
        edges = {e.id: e for e in self.index.get_edges()}
        if self.as_of:
            boundary = format_timestamp(now)
            available = {}
            for interval in self.intervals:
                if interval["from_time"] <= boundary and (
                    interval["to_time"] is None or boundary < interval["to_time"]
                ):
                    previous = available.get(interval["node_id"])
                    if (
                        previous is None
                        or interval["from_time"] >= previous["from_time"]
                    ):
                        available[interval["node_id"]] = interval
            nodes = {
                i: n for i, n in nodes.items() if n.created <= now and i in available
            }
            for identifier, node in nodes.items():
                node.status = Status(available[identifier]["status"])
                node.approvals = [a for a in node.approvals if a.at <= now]
                node.artifacts = [a for a in node.artifacts if a.added_at <= now]
            edges = {
                i: e
                for i, e in edges.items()
                if e.created <= now and e.from_ in nodes and e.to in nodes
            }
            for edge in edges.values():
                if edge.retired_at is not None and edge.retired_at > now:
                    edge.retired, edge.retired_at = False, None
        super().__init__(project.profile, nodes, edges, now)

    def require(self, identifier):
        if not isinstance(identifier, str):
            raise RuleViolation("schema", "Node arguments must be identifiers")
        if identifier not in self.nodes:
            raise RuleViolation("not_found", "Node not found", [identifier])
        return self.nodes[identifier]

    def ordered(self, identifiers):
        return sorted((self.nodes[i] for i in identifiers), key=created_order)

    def vocabulary(self, *names):
        return set(names) <= {n.name for n in self.profile.node_types}

    def ready_items(self):
        return [
            n.to_dict()
            for n in sorted(self.nodes.values(), key=ready_order)
            if self.ready(n)
        ]

    def blocked_items(self):
        return [
            dict(n.to_dict(), blocking=self.blocking(n))
            for n in sorted(self.nodes.values(), key=ready_order)
            if self.blocking(n)
        ]

    def queue_items(self):
        result = []
        for node in sorted(self.nodes.values(), key=created_order):
            gates = self.profile.gates_for(node.type)
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
        return [
            dict(n.to_dict(), responsible=self.responsible(n.id))
            for n in sorted(self.nodes.values(), key=created_order)
            if self.stale(n)
        ]

    def weakest(self, identifier):
        node = self.require(identifier)
        if not self.vocabulary("Claim", "Result", "Reference") or node.type != "Claim":
            return []
        targets = {e.to for e in self.outgoing[identifier] if e.type == "cites"}
        return [
            n.to_dict()
            for n in sorted(
                (self.nodes[i] for i in targets),
                key=lambda n: (
                    not self.superseded_input(n.id),
                    n.fields.get("runs") or 0,
                    n.fields.get("held_out") or False,
                    n.id,
                ),
            )
        ]

    def predictions(self, identifier):
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
                expected=e.fields.get("expected"),
                discriminating=e.fields.get("discriminating", True),
                status=str(self.nodes[e.from_].status),
            )
            for e in sorted(self.incoming[identifier], key=created_order)
            if e.type == "tests"
        ]

    def matrix(self, identifier):
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
                    matches[0].fields.get("expected") if len(matches) == 1 else None
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
        if field is None:
            fields = {}
        elif isinstance(field, dict):
            fields = field
        else:
            entries = [field] if isinstance(field, str) else field
            try:
                fields = dict(entry.split("=", 1) for entry in entries)
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

    def digest(self):
        cutoff = self.now - timedelta(hours=24)
        failed = {
            i["node_id"]
            for i in self.intervals
            if i["status"] == "failed"
            and cutoff <= parse_timestamp(i["from_time"]) <= self.now
            and i["node_id"] in self.nodes
        }
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
