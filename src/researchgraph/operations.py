"""Candidate construction for the four proposal lifecycle operations.

Builders run only inside Project's writer lock, after shared preflight. They
modify private candidates, never caller objects or the file-authoritative state.
"""

from copy import deepcopy

from researchgraph import model
from researchgraph.model import Approval, Edge, Node, RuleViolation, Status
from researchgraph.validation import gates_satisfied

_RESERVED = frozenset({"supersedes", "explains", "instructs"})


def _identifier(project, prefix, pending):
    identifier = model.new_id(
        prefix,
        lambda value: (
            value in pending
            or any(
                (project.path / f"graph/{kind}/{value}.yaml").exists()
                for kind in ("nodes", "edges")
            )
        ),
    )
    pending.add(identifier)
    return identifier


def _reserved(type_):
    if not isinstance(type_, str):
        raise RuleViolation("schema", "Edge type must be a string")
    if type_ in _RESERVED:
        raise RuleViolation(
            "reserved_edge_type", "Relation is reserved for its operation"
        )


def _edge(project, write, pending, type_, source, target, fields):
    return Edge(
        _identifier(project, "ed", pending),
        type_,
        source,
        target,
        write.actor,
        write.now,
        deepcopy(fields),
    )


def _propose(project, write, nodes, edges):
    args = write.args
    definition = project.profile.node_type(args["type"])
    specs = args["edges"]
    if specs is None:
        specs = []
    if not isinstance(specs, list):
        raise RuleViolation("schema", "Edges must be a list of edge specifications")
    # Reserved relations precede all proposal schema errors, including a
    # different spec's malformed direction or fields.
    for spec in specs:
        if isinstance(spec, dict) and isinstance(spec.get("type"), str):
            _reserved(spec["type"])
    for spec in specs:
        if (
            not isinstance(spec, dict)
            or set(spec) - {"type", "from", "to", "fields"}
            or "type" not in spec
            or not isinstance(spec["type"], str)
            or ("from" in spec) == ("to" in spec)
        ):
            raise RuleViolation(
                "schema", "Each edge needs a type and exactly one from or to"
            )
    pending = set()
    node = Node(
        _identifier(project, definition.prefix, pending),
        args["type"],
        args["title"],
        args["body"],
        write.actor,
        write.now,
        deepcopy({} if args["fields"] is None else args["fields"]),
        status=Status.PROPOSED
        if project.profile.gates_for(args["type"])
        else Status.ACTIVE,
    )
    write.nodes = [node]
    write.edges = [
        _edge(
            project,
            write,
            pending,
            spec["type"],
            spec.get("from", node.id),
            spec.get("to", node.id),
            {} if spec.get("fields") is None else spec["fields"],
        )
        for spec in specs
    ]
    return node.id, False


def _link(project, write, nodes, edges):
    args = write.args
    _reserved(args["type"])
    edge = _edge(
        project,
        write,
        set(),
        args["type"],
        args["from"],
        args["to"],
        {} if args["fields"] is None else args["fields"],
    )
    write.edges = [edge]
    return edge.id, True


def _approve(project, write, nodes, edges):
    node = deepcopy(nodes[write.args["node"]])
    node.approvals.append(
        Approval(write.actor, write.now, "approve", write.role, write.args["note"])
    )
    node.updated = write.now
    if gates_satisfied(project.profile, node):
        node.status = Status.ACTIVE
    write.nodes = [node]
    return node.id, False


def _reject(project, write, nodes, edges):
    if not isinstance(write.args["reason"], str):
        raise RuleViolation("schema", "Rejection reason must be a string")
    node = deepcopy(nodes[write.args["node"]])
    node.status = Status.RETIRED
    node.updated = write.now
    node.approvals.append(
        Approval(write.actor, write.now, "reject", write.role, write.args["reason"])
    )
    write.nodes = [node]
    for edge in edges.values():
        if not edge.retired and node.id in (edge.from_, edge.to):
            retired = deepcopy(edge)
            retired.retired, retired.retired_at = True, write.now
            write.edges.append(retired)
    return node.id, False
