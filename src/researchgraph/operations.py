"""Candidate construction for proposal and revision lifecycle operations.

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


def _node(project, write, pending, type_, title, body, fields):
    definition = project.profile.node_type(type_)
    return Node(
        _identifier(project, definition.prefix, pending),
        type_,
        title,
        body,
        write.actor,
        write.now,
        deepcopy(fields),
        status=Status.PROPOSED if project.profile.gates_for(type_) else Status.ACTIVE,
    )


def _retired_edge(edge, now):
    retired = deepcopy(edge)
    retired.retired, retired.retired_at = True, now
    return retired


def _retired_node(write, original, what, note):
    node = deepcopy(original)
    node.status = Status.RETIRED
    node.updated = write.now
    node.approvals.append(Approval(write.actor, write.now, what, write.role, note))
    return node


def _instruction(project, write, pending, target, text):
    if not isinstance(text, str):
        raise RuleViolation("schema", "Instruction text must be a string")
    lines = text.splitlines()
    node = _node(
        project,
        write,
        pending,
        "Instruction",
        lines[0][:200] if lines else "",
        text,
        {},
    )
    write.nodes.append(node)
    write.edges.append(_edge(project, write, pending, "instructs", node.id, target, {}))
    return node


def _propose(project, write, nodes, edges):
    args = write.args
    project.profile.node_type(args["type"])
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
    node = _node(
        project,
        write,
        pending,
        args["type"],
        args["title"],
        args["body"],
        {} if args["fields"] is None else args["fields"],
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
    node = _retired_node(
        write, nodes[write.args["node"]], "reject", write.args["reason"]
    )
    write.nodes = [node]
    for edge in edges.values():
        if not edge.retired and node.id in (edge.from_, edge.to):
            write.edges.append(_retired_edge(edge, write.now))
    return node.id, False


def _supersede(project, write, nodes, edges):
    args = write.args
    if type(args["inherit_edges"]) is not bool:
        raise RuleViolation("schema", "inherit_edges must be a boolean")
    old = nodes[args["node"]]
    pending = set()
    replacement = _node(
        project,
        write,
        pending,
        old.type,
        old.title if args["title"] is None else args["title"],
        old.body if args["body"] is None else args["body"],
        old.fields if args["fields"] is None else args["fields"],
    )
    write.nodes = [
        _retired_node(write, old, "superseded", replacement.id),
        replacement,
    ]
    write.edges = [
        _edge(project, write, pending, "supersedes", replacement.id, old.id, {})
    ]
    for edge in edges.values():
        if edge.retired or old.id not in (edge.from_, edge.to):
            continue
        inherit = project.profile.edge_type(edge.type).inherit
        if inherit != "both" and not (inherit == "outgoing" and edge.from_ == old.id):
            continue
        write.edges.append(_retired_edge(edge, write.now))
        if args["inherit_edges"]:
            write.edges.append(
                _edge(
                    project,
                    write,
                    pending,
                    edge.type,
                    replacement.id if edge.from_ == old.id else edge.from_,
                    replacement.id if edge.to == old.id else edge.to,
                    edge.fields,
                )
            )
    if args["instruction"] is not None:
        _instruction(project, write, pending, replacement.id, args["instruction"])
    return replacement.id, False


def _instruct(project, write, nodes, edges):
    node = _instruction(project, write, set(), write.args["node"], write.args["text"])
    return node.id, False


def _retire(project, write, nodes, edges):
    if not isinstance(write.args["reason"], str):
        raise RuleViolation("schema", "Retirement reason must be a string")
    node = _retired_node(
        write, nodes[write.args["node"]], "retire", write.args["reason"]
    )
    write.nodes = [node]
    write.edges = [
        _retired_edge(edge, write.now)
        for edge in edges.values()
        if not edge.retired and node.id in (edge.from_, edge.to)
    ]
    return node.id, False
