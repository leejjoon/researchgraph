"""Core graph invariants and reusable candidate-write policy.

Inputs are file-authoritative before-state mappings and a candidate Write. These
functions never write files, invoke profile callbacks, or consult an index.
"""

from collections import deque
from datetime import timedelta

from jsonschema import ValidationError
from jsonschema.validators import validator_for

from researchgraph.model import (
    EDGE_IMMUTABLE_KEYS,
    NODE_IMMUTABLE_KEYS,
    RuleViolation,
    Status,
)
from researchgraph.queries import View
from researchgraph.store.io import _canonical_equal

ACYCLIC_RELATIONS = frozenset(
    {"blocks", "derived-from", "supersedes", "cites", "part-of"}
)
_HUMAN_ONLY = frozenset(
    {"init", "reject", "instruct", "expand", "budget", "retire", "import"}
)
_EVENTS = {
    "approve",
    "reject",
    "retire",
    "claim",
    "renew",
    "release",
    "complete",
    "fail",
    "supersede",
    "budget",
}
_TRANSITIONS = {
    "approve": {(Status.PROPOSED, Status.ACTIVE), (Status.PROPOSED, Status.PROPOSED)},
    "reject": {(Status.PROPOSED, Status.RETIRED)},
    "claim": {(Status.ACTIVE, Status.RUNNING), (Status.RUNNING, Status.RUNNING)},
    "renew": {(Status.RUNNING, Status.RUNNING)},
    "release": {(Status.RUNNING, Status.ACTIVE)},
    "complete": {(Status.RUNNING, Status.DONE)},
    "fail": {(Status.RUNNING, Status.FAILED)},
    "supersede": {
        (s, Status.RETIRED)
        for s in (Status.PROPOSED, Status.ACTIVE, Status.DONE, Status.FAILED)
    },
    "retire": {
        (s, Status.RETIRED) for s in (Status.PROPOSED, Status.ACTIVE, Status.FAILED)
    },
}


def _refuse(rule, message, *ids):
    raise RuleViolation(rule, message, list(ids))


def validate_references(write, nodes):
    """Check explicit id arguments before profile-version or permission checks."""
    references = []
    if "node" in write.args:
        references.append(write.args["node"])
    if write.op == "expand":
        targets = write.args.get("nodes", [])
        if not isinstance(targets, list):
            _refuse("schema", "Expansion targets must be a list")
        references.extend(targets)
    if write.op == "link":
        references.extend(
            write.args[key] for key in ("from", "to") if key in write.args
        )
    if write.op == "propose" and isinstance(write.args.get("edges"), list):
        # Only well-formed incident specifications name existing ids. Shape
        # errors belong to the operation, after the shared preflight rules.
        for spec in write.args["edges"]:
            if isinstance(spec, dict) and ("from" in spec) != ("to" in spec):
                references.append(spec["from"] if "from" in spec else spec["to"])
    for identifier in references:
        if not isinstance(identifier, str):
            _refuse("schema", "Node arguments must be identifiers")
        if identifier not in nodes:
            _refuse("not_found", "Node not found", identifier)
    available = set(nodes) | {node.id for node in write.nodes}
    for edge in write.edges:
        for identifier in (edge.from_, edge.to):
            if not isinstance(identifier, str):
                _refuse("schema", "Edge endpoints must be identifiers")
            if identifier not in available:
                _refuse("not_found", "Edge endpoint not found", identifier)


def gate_accepts(gate, actor, role):
    """Whether one declared identity/role can satisfy an activate gate."""
    if actor.kind == "agent" and not gate.agents_allowed:
        return False
    return role in gate.roles or actor.kind in gate.roles


def gates_satisfied(profile, node):
    """Every matching gate needs a qualifying approval event."""
    return all(
        any(
            a.what == "approve" and gate_accepts(gate, a.actor, a.role)
            for a in node.approvals
        )
        for gate in profile.gates_for(node.type)
    )


def validate_permissions(profile, write, nodes):
    """Shared actor/role permissions, then operation-specific state preconditions."""
    target = nodes.get(write.args.get("node"))
    gates = (
        profile.gates_for(target.type)
        if target is not None and write.op == "approve"
        else []
    )
    human_only = write.op in _HUMAN_ONLY or (
        write.op == "approve"
        and (not gates or any(not g.agents_allowed for g in gates))
    )
    # Approve explicitly checks not_proposed before rejecting an agent's gate.
    if (
        write.op == "approve"
        and target is not None
        and target.status != Status.PROPOSED
        and write.actor.kind == "agent"
        and human_only
    ):
        _refuse("not_proposed", "Only proposed nodes can be approved", target.id)
    if write.actor.kind == "agent" and human_only:
        _refuse("human_only_approval", "This operation requires a human")
    # human/agent are gate-wide identity tokens, not profile role declarations.
    if (
        write.role is not None
        and write.role not in profile.roles
        and not (
            write.op == "approve"
            and write.role in {"human", "agent"}
            and any(write.role in g.roles for g in gates)
        )
    ):
        _refuse("wrong_role", "Unknown declared role")
    if gates and not any(gate_accepts(gate, write.actor, write.role) for gate in gates):
        _refuse("wrong_role", "Declared role satisfies no activate gate")
    required = {"instruct": "Instruction", "fail": "Failure", "expand": "Task"}.get(
        write.op
    )
    if write.op == "supersede" and write.args.get("instruction") is not None:
        required = "Instruction"
    if required is not None and required not in {n.name for n in profile.node_types}:
        _refuse("unsupported_by_profile", f"Profile does not declare {required}")
    if write.op == "expand" and write.args["nodes"]:
        try:
            profile.normalize_fields(
                "Task", {"kind": "expansion", "targets": write.args["nodes"]}
            )
        except RuleViolation as exc:
            raise RuleViolation(
                "unsupported_by_profile",
                "Task schema cannot represent an expansion task",
            ) from exc
    if target is None:
        return
    op = write.op
    if op == "approve":
        if target.status != Status.PROPOSED:
            _refuse("not_proposed", "Only proposed nodes can be approved", target.id)
        if any(
            a.what == "approve" and a.actor == write.actor for a in target.approvals
        ):
            _refuse("already_approved", "Actor already approved", target.id)
        if write.args.get("what", "activate") != "activate":
            _refuse("unknown_what", "Only activate approvals are supported", target.id)
    if op == "reject" and target.status != Status.PROPOSED:
        _refuse("not_proposed", "Only proposed nodes can be rejected", target.id)
    if op in {"supersede", "retire"}:
        if (
            op == "supersede"
            and write.actor.kind == "agent"
            and target.type not in {"Task", "Result"}
        ):
            _refuse(
                "agent_cannot_supersede_type",
                "Agent may supersede only Task or Result",
                target.id,
            )
        if target.status == Status.RETIRED or (
            op == "retire" and target.status == Status.DONE
        ):
            _refuse("terminal_status", "Node is terminal", target.id)
        if target.status == Status.RUNNING:
            _refuse(
                "running", "Running nodes cannot be retired or superseded", target.id
            )
    if op == "instruct" and target.status not in {Status.PROPOSED, Status.ACTIVE}:
        _refuse("already_ran", "Node has already run", target.id)
    if op == "attach" and target.status == Status.RETIRED:
        _refuse("terminal_status", "Retired nodes cannot receive artifacts", target.id)
    if op in {"renew", "release", "complete", "fail"}:
        if target.status != Status.RUNNING:
            _refuse("not_running", "Node is not running", target.id)
        if target.lease is None or (
            target.lease.actor != write.actor
            and not (op == "release" and write.actor.kind == "human")
        ):
            _refuse("not_lease_holder", "Actor is not the lease holder", target.id)
    if (
        op == "claim"
        and target.status == Status.RUNNING
        and target.lease is not None
        and write.now < target.lease.expires_at
    ):
        _refuse("lease_held", "The current lease has not expired", target.id)


def validate_edge_endpoints(profile, edge, nodes):
    """Check endpoint existence and the profile's type pair without fields."""
    if edge.from_ not in nodes or edge.to not in nodes:
        _refuse("not_found", "Edge endpoint not found", edge.from_, edge.to)
    definition = profile.edge_type(edge.type)
    source, target = nodes[edge.from_], nodes[edge.to]
    if not any(
        a in {"*", source.type} and b in {"*", target.type}
        for a, b in definition.allowed
    ) or (edge.type == "supersedes" and source.type != target.type):
        _refuse("edge_endpoints", "Invalid endpoint types", edge.id)


def validate_graph(profile, nodes, edges):
    """I1, I3–I5 over the complete post-write graph, including restored imports."""
    for node in nodes.values():
        if (node.status == Status.RUNNING) != (node.lease is not None):
            _refuse("schema", "Lease must exist exactly when running", node.id)
        if node.lease is not None and node.lease.expires_at <= node.lease.acquired_at:
            _refuse("schema", "Lease must have positive duration", node.id)
    live, superseded = set(), set()
    duplicates = []
    adjacency = {identifier: [] for identifier in nodes}
    indegree = dict.fromkeys(nodes, 0)
    for edge in edges.values():
        validate_edge_endpoints(profile, edge, nodes)
        definition = profile.edge_type(edge.type)
        try:
            validator_for(definition.schema)(definition.schema).validate(edge.fields)
        except ValidationError as exc:
            raise RuleViolation("schema", exc.message, [edge.id]) from exc
        if edge.retired != (edge.retired_at is not None):
            _refuse("schema", "Retirement timestamp must match retired flag", edge.id)
        if edge.retired:
            continue
        key = (edge.type, edge.from_, edge.to)
        if key in live:
            duplicates.append(edge.id)
        live.add(key)
        if edge.type == "supersedes":
            if edge.to in superseded:
                _refuse("schema", "Node has multiple live supersessions", edge.to)
            superseded.add(edge.to)
        if edge.type in ACYCLIC_RELATIONS:
            adjacency[edge.from_].append(edge.to)
            indegree[edge.to] += 1
    pending = deque(
        identifier for identifier, degree in indegree.items() if degree == 0
    )
    count = 0
    while pending:
        identifier = pending.popleft()
        count += 1
        for target in adjacency[identifier]:
            indegree[target] -= 1
            if indegree[target] == 0:
                pending.append(target)
    if count != len(nodes):
        _refuse(
            "cycle",
            "Live acyclic relations contain a directed cycle",
            *sorted(k for k, v in indegree.items() if v),
        )
    if duplicates:
        _refuse("duplicate_edge", "Duplicate live edge", *duplicates)


def _prefix(before, after):
    return len(after) >= len(before) and _canonical_equal(after[: len(before)], before)


def _ready(profile, target, nodes, edges, now):
    return View(profile, nodes, edges, now).ready(target)


def validate_changes(profile, write, before_nodes, before_edges):
    """I2, I6 and candidate lifecycle effects; no privileged fixture path."""
    for edge in write.edges:
        old = before_edges.get(edge.id)
        if old is not None:
            a, b = old.to_dict(), edge.to_dict()
            if not _canonical_equal(
                {key: a[key] for key in EDGE_IMMUTABLE_KEYS},
                {key: b[key] for key in EDGE_IMMUTABLE_KEYS},
            ) or (
                old.retired and (not edge.retired or old.retired_at != edge.retired_at)
            ):
                _refuse("schema", "Immutable edge or retired event changed", edge.id)
        else:
            if write.op != "import" and (
                edge.author.to_dict() != write.actor.to_dict()
                or edge.created != write.now
            ):
                _refuse("schema", "Invalid generated-edge author or timestamp", edge.id)
            if write.op in {"propose", "link"} and edge.type in {
                "supersedes",
                "instructs",
                "explains",
            }:
                _refuse(
                    "reserved_edge_type",
                    "Relation is reserved for its operation",
                    edge.id,
                )
    for node in write.nodes:
        old = before_nodes.get(node.id)
        if old is None:
            if write.op != "import":
                expected = (
                    Status.PROPOSED if profile.gates_for(node.type) else Status.ACTIVE
                )
                if (
                    node.status != expected
                    or node.author.to_dict() != write.actor.to_dict()
                    or node.created != write.now
                    or node.updated != write.now
                ):
                    _refuse("schema", "Invalid generated-node initial state", node.id)
                if (
                    node.approvals
                    or node.artifacts
                    or node.usage is not None
                    or node.lease is not None
                    or node.budget is not None
                    or node.tags
                    or node.priority is not None
                ):
                    _refuse(
                        "schema",
                        "Generated nodes cannot contain historical events",
                        node.id,
                    )
            continue
        a, b = old.to_dict(), node.to_dict()
        if not _canonical_equal(
            {key: a[key] for key in NODE_IMMUTABLE_KEYS},
            {key: b[key] for key in NODE_IMMUTABLE_KEYS},
        ):
            _refuse("schema", "Immutable node key changed", node.id)
        if not _prefix(a["approvals"], b["approvals"]) or not _prefix(
            a["artifacts"], b["artifacts"]
        ):
            _refuse("schema", "Append-only record prefix changed", node.id)
        for key, operations in (
            ("budget", {"budget"}),
            ("usage", {"complete", "fail", "release"}),
            ("artifacts", {"complete", "attach"}),
            ("tags", {"tag"}),
            ("priority", {"tag"}),
        ):
            if not _canonical_equal(a[key], b[key]) and write.op not in operations:
                _refuse("schema", f"{key} changed outside its operation", node.id)
        targeted = write.args.get("node") == node.id
        if (node.status != old.status or (targeted and write.op in _TRANSITIONS)) and (
            not targeted
            or (old.status, node.status) not in _TRANSITIONS.get(write.op, set())
        ):
            _refuse("schema", "Invalid status transition", node.id)
        added = node.approvals[len(old.approvals) :]
        expected_event = "superseded" if write.op == "supersede" else write.op
        if targeted and write.op in _EVENTS:
            if (
                len(added) != 1
                or added[0].what != expected_event
                or added[0].actor.to_dict() != write.actor.to_dict()
                or added[0].role != write.role
                or added[0].at != write.now
            ):
                _refuse("schema", "Operation must append its approval event", node.id)
        elif added:
            _refuse("schema", "Unexpected approval event", node.id)
        if (
            write.op == "approve"
            and targeted
            and (node.status == Status.ACTIVE) != gates_satisfied(profile, node)
        ):
            _refuse("schema", "Activation does not match gate approvals", node.id)
        if write.op == "claim" and targeted:
            if not _ready(profile, old, before_nodes, before_edges, write.now):
                _refuse("not_ready", "Node is not ready", node.id)
            seconds = write.args.get("lease_seconds", 7200)
            if type(seconds) is not int or seconds < 1:
                _refuse("negative", "Lease duration must be positive", node.id)
            if (
                node.lease is None
                or node.lease.actor != write.actor
                or node.lease.acquired_at != write.now
                or node.lease.expires_at != write.now + timedelta(seconds=seconds)
            ):
                _refuse("schema", "Invalid claim lease", node.id)
        if write.op == "renew" and targeted:
            seconds = write.args.get("lease_seconds", 7200)
            if type(seconds) is not int or seconds < 1:
                _refuse("negative", "Lease duration must be positive", node.id)
            if (
                node.lease is None
                or old.lease is None
                or node.lease.actor.to_dict() != old.lease.actor.to_dict()
                or node.lease.acquired_at != old.lease.acquired_at
                or node.lease.expires_at
                != max(old.lease.expires_at, write.now + timedelta(seconds=seconds))
            ):
                _refuse("schema", "Invalid renewal lease", node.id)
        if old.lease != node.lease and not (
            targeted and write.op in {"claim", "renew", "release", "complete", "fail"}
        ):
            _refuse("schema", "Lease changed outside a lease operation", node.id)
        for key in ("tokens", "compute_seconds"):
            if (node.usage or {}).get(key, 0) < (old.usage or {}).get(key, 0):
                _refuse("schema", "Usage totals cannot decrease", node.id)
        if not _canonical_equal(a, b) and node.updated != write.now:
            _refuse(
                "schema",
                "Changed node updated timestamp must equal write time",
                node.id,
            )


def validate_write(profile, write, before_nodes, before_edges):
    """Validate a normalized candidate after shared permissions/schema checks."""
    validate_changes(profile, write, before_nodes, before_edges)
    nodes = {**before_nodes, **{n.id: n for n in write.nodes}}
    edges = {**before_edges, **{e.id: e for e in write.edges}}
    validate_graph(profile, nodes, edges)
