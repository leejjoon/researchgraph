"""Bundled ungated task graph profile."""

from copy import deepcopy

from researchgraph.profiles.base import NodeType, Profile
from researchgraph.profiles.research import FAILURE_SCHEMA, TASK_SCHEMA

PROFILE = Profile(
    name="tasks",
    version="1.0",
    node_types=[
        NodeType("Task", "ta", deepcopy(TASK_SCHEMA), claimable=True),
        NodeType("Result", "re", {}),
        NodeType("Instruction", "in", {}),
        NodeType("Failure", "fl", deepcopy(FAILURE_SCHEMA)),
        NodeType("Note", "no", {}),
    ],
    edge_types=[],
    gates=[],
    roles=[],
    validators=[],
)
