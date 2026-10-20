"""Candidate construction for proposal, revision and execution operations.

Builders run only inside Project's writer lock, after shared preflight. They
modify private candidates, never caller objects or the file-authoritative state.
"""

from copy import deepcopy
from datetime import timedelta

from researchgraph import model
from researchgraph.model import Approval, Edge, Lease, Node, RuleViolation, Status
from researchgraph.queries import View
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


def _execution_node(write, nodes, status, note=None):
    node = deepcopy(nodes[write.args["node"]])
    node.status = status
    node.updated = write.now
    node.approvals.append(Approval(write.actor, write.now, write.op, write.role, note))
    write.nodes = [node]
    return node


def _lease_expiry(write):
    seconds = write.args["lease_seconds"]
    if type(seconds) is not int or seconds < 1:
        raise RuleViolation("negative", "Lease duration must be a positive integer")
    try:
        return write.now + timedelta(seconds=seconds)
    except OverflowError as exc:
        raise RuleViolation("schema", "Lease duration exceeds timestamp range") from exc


def _add_usage(node, usage):
    if usage is None:
        return
    if not isinstance(usage, dict) or any(
        key not in {"tokens", "compute_seconds"} or type(value) is not int
        for key, value in usage.items()
    ):
        raise RuleViolation(
            "schema", "Usage must contain integer accounting dimensions"
        )
    if any(value < 0 for value in usage.values()):
        raise RuleViolation("negative", "Usage must be non-negative")
    totals = dict(node.usage or {})
    for key, value in usage.items():
        totals[key] = totals.get(key, 0) + value
    node.usage = totals or None


def _claim(project, write, nodes, edges):
    old = nodes[write.args["node"]]
    if not View(project.profile, nodes, edges, write.now).ready(old):
        raise RuleViolation("not_ready", "Node is not ready", [old.id])
    expires = _lease_expiry(write)
    node = _execution_node(write, nodes, Status.RUNNING)
    node.lease = Lease(write.actor, write.now, expires)
    return node.id, False


def _renew(project, write, nodes, edges):
    expires = _lease_expiry(write)
    node = _execution_node(write, nodes, Status.RUNNING)
    node.lease.expires_at = max(node.lease.expires_at, expires)
    return node.id, False


def _release(project, write, nodes, edges):
    node = _execution_node(write, nodes, Status.ACTIVE, write.args["note"])
    _add_usage(node, write.args["usage"])
    node.lease = None
    return node.id, False


def _complete(project, write, nodes, edges):
    node = _execution_node(write, nodes, Status.DONE, write.args["summary"])
    node.artifacts.extend(
        project._artifact_records(
            write.args["artifacts"], node.id, write.actor, write.now
        )
    )
    _add_usage(node, write.args["usage"])
    node.lease = None
    return node.id, False


def _fail(project, write, nodes, edges):
    node = _execution_node(write, nodes, Status.FAILED, write.args["summary"])
    _add_usage(node, write.args["usage"])
    node.lease = None
    pending = set()
    failure = _node(
        project,
        write,
        pending,
        "Failure",
        ("Failure: " + node.title)[:200],
        write.args["summary"],
        {
            key: write.args[key]
            for key in ("step", "check", "measured", "error")
            if write.args[key] is not None
        },
    )
    write.nodes.append(failure)
    write.edges.append(
        _edge(project, write, pending, "explains", failure.id, node.id, {})
    )
    return node.id, False


def _expand(project, write, nodes, edges):
    targets = write.args["nodes"]
    if not targets:
        raise RuleViolation("no_targets", "Expansion requires at least one target")
    hint = write.args["hint"]
    if hint is not None and not isinstance(hint, str):
        raise RuleViolation("schema", "Expansion hint must be a string")
    node = _node(
        project,
        write,
        set(),
        "Task",
        ("Expand: " + ", ".join(nodes[target].title for target in targets))[:200],
        "" if hint is None else hint,
        {"kind": "expansion", "targets": targets},
    )
    write.nodes = [node]
    return node.id, False


def _budget(project, write, nodes, edges):
    dimensions = {
        key: write.args[key]
        for key in ("tokens", "compute_seconds")
        if write.args[key] is not None
    }
    if any(type(value) is not int for value in dimensions.values()):
        raise RuleViolation("schema", "Budget dimensions must be integers")
    if any(value < 0 for value in dimensions.values()):
        raise RuleViolation("negative", "Budget must be non-negative")
    node = deepcopy(nodes[write.args["node"]])
    node.budget = dimensions or None
    node.updated = write.now
    note = " ".join(f"{key}={value}" for key, value in dimensions.items()) or "none"
    node.approvals.append(Approval(write.actor, write.now, "budget", write.role, note))
    write.nodes = [node]
    return node.id, False


def _tag(project, write, nodes, edges):
    tags = {}
    for key in ("add", "remove"):
        value = write.args[key]
        if value is None:
            value = []
        if not isinstance(value, list) or any(
            not isinstance(tag, str) for tag in value
        ):
            raise RuleViolation("schema", "Tags must be lists of strings")
        tags[key] = value
    node = deepcopy(nodes[write.args["node"]])
    node.tags = sorted((set(node.tags) | set(tags["add"])) - set(tags["remove"]))
    if "priority" in write.args:
        priority = write.args["priority"]
        if priority is not None and type(priority) is not int:
            raise RuleViolation("schema", "Priority must be an integer or null")
        node.priority = priority
    node.updated = write.now
    write.nodes = [node]
    return node.id, False


def _attach(project, write, nodes, edges):
    node = deepcopy(nodes[write.args["node"]])
    node.artifacts.extend(
        project._artifact_records(
            write.args["artifacts"], node.id, write.actor, write.now
        )
    )
    node.updated = write.now
    write.nodes = [node]
    return node.id, False
