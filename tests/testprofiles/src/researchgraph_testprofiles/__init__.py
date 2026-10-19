"""Installable ad-hoc profiles for real subprocess acceptance tests."""

from researchgraph.profiles.base import NodeType, Profile

DEFAULTS_PROFILE = Profile(
    name="test-defaults",
    version="1.0",
    node_types=[
        NodeType(
            name="Task",
            prefix="ta",
            schema={
                "type": "object",
                "properties": {
                    "mode": {
                        "type": "string",
                        "enum": ["fast", "safe"],
                        "default": "fast",
                    },
                },
            },
            claimable=True,
        ),
    ],
    edge_types=[],
    gates=[],
    roles=[],
    validators=[],
)
