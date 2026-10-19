"""Bundled research declarations and evidence validators.

Validator project protocol (read-only): ``get_node(node_id: str) -> Node`` and
``get_edges() -> Iterable[Edge]``. get_edges returns all stored edges, including
retired ones. Write.nodes and Write.edges override stored records by id, so the
validators inspect the proposed post-write graph without requiring a store.
"""

from researchgraph.model import RuleViolation, Write
from researchgraph.profiles.base import EdgeType, Gate, NodeType, Profile

TASK_SCHEMA = {
    "properties": {
        "kind": {"type": "string"},
        "targets": {"type": "array", "items": {"type": "string"}},
        "command": {"type": "string"},
    },
}
FAILURE_SCHEMA = {
    "properties": {
        name: {"type": "string"} for name in ("step", "check", "measured", "error")
    },
}


def claim_needs_evidence(project, write: Write) -> None:
    """Only approving a Claim requires a non-retired outgoing cites edge."""
    if write.op != "approve":
        return
    claims = [node for node in write.nodes if node.type == "Claim"]
    if not claims:
        return
    edges = {edge.id: edge for edge in project.get_edges()}
    edges.update((edge.id, edge) for edge in write.edges)
    for claim in claims:
        if not any(
            edge.type == "cites" and edge.from_ == claim.id and not edge.retired
            for edge in edges.values()
        ):
            raise RuleViolation(
                "claim_needs_evidence",
                "A Claim needs evidence before approval",
                [claim.id],
            )


def pin_artifact_exists(project, write: Write) -> None:
    """Each new/changed live pin names an artifact recorded on its target."""
    nodes = {node.id: node for node in write.nodes}
    for edge in write.edges:
        if edge.type != "pins" or edge.retired:
            continue
        target = nodes[edge.to] if edge.to in nodes else project.get_node(edge.to)
        if not any(
            artifact.path == edge.fields.get("artifact")
            for artifact in target.artifacts
        ):
            raise RuleViolation(
                "schema",
                "Pinned artifact is not recorded on the target Result",
                [edge.id, edge.to],
            )


PROFILE = Profile(
    name="research",
    version="1.0",
    node_types=[
        NodeType(
            "Question",
            "qu",
            {"properties": {"closed": {"type": "boolean", "default": False}}},
        ),
        NodeType(
            "Reference",
            "rf",
            {
                "properties": {
                    name: {"type": "string"} for name in ("doi", "url", "title")
                }
            },
        ),
        NodeType("Hypothesis", "hy", {}),
        NodeType("Prediction", "pr", {}),
        NodeType("Task", "ta", TASK_SCHEMA, claimable=True),
        NodeType(
            "Result",
            "re",
            {
                "properties": {
                    "held_out": {"type": "boolean"},
                    "runs": {"type": "integer", "minimum": 0},
                }
            },
        ),
        NodeType("Claim", "cl", {}),
        NodeType("Instruction", "in", {}),
        NodeType("Failure", "fl", FAILURE_SCHEMA),
        NodeType("Note", "no", {}),
    ],
    edge_types=[
        EdgeType(
            "tests",
            [("Prediction", "Hypothesis"), ("Task", "Hypothesis")],
            {
                "properties": {
                    "expected": {"type": "string"},
                    "discriminating": {"type": "boolean", "default": True},
                },
                "required": ["expected"],
            },
            inherit="both",
        ),
        EdgeType(
            "supports",
            [("Claim", "Hypothesis")],
            {"properties": {"strength": {"enum": ["weak", "moderate", "strong"]}}},
        ),
        EdgeType(
            "contradicts",
            [("Claim", "Hypothesis")],
            {"properties": {"strength": {"enum": ["weak", "moderate", "strong"]}}},
        ),
        EdgeType(
            "cites",
            [("Claim", "Result"), ("Claim", "Reference")],
            {
                "properties": {
                    name: {"type": "string"}
                    for name in ("artifact", "locator", "sha256")
                }
            },
        ),
        EdgeType(
            "pins",
            [("Hypothesis", "Result")],
            {"properties": {"artifact": {"type": "string"}}, "required": ["artifact"]},
        ),
    ],
    gates=[Gate(node_type=name) for name in ("Hypothesis", "Prediction", "Claim")],
    roles=["owner", "reviewer"],
    validators=[claim_needs_evidence, pin_artifact_exists],
)
