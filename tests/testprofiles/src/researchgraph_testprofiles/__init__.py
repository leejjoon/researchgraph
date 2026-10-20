"""Installable ad-hoc profiles for real subprocess acceptance tests."""

import json
import os
from copy import deepcopy
from pathlib import Path

from researchgraph.model import RuleViolation
from researchgraph.profiles.base import EdgeType, NodeType, Profile
from researchgraph.profiles.research import PROFILE as RESEARCH_PROFILE

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


def reject_forbidden(before, write):
    for node in write.nodes:
        if node.fields.get("mode") == "forbidden":
            raise RuleViolation(
                "fixture_validation", "Rejected by installed profile", [node.id]
            )


def record_event(project, write, commit):
    destination = os.environ.get("RG_TEST_EVENT_LOG")
    if destination:
        with Path(destination).open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"op": write.op, "commit": commit}) + "\n")


CLI_PROFILE = deepcopy(DEFAULTS_PROFILE)
CLI_PROFILE.name = "test-cli-contract"
CLI_PROFILE.node_types[0].schema["properties"]["mode"]["enum"].append("forbidden")
CLI_PROFILE.node_types[0].schema["properties"]["settings"] = {
    "type": "object",
    "properties": {"enabled": {"type": "boolean"}},
    "default": {"enabled": True},
}
CLI_PROFILE.edge_types = [
    EdgeType(
        "depends",
        [("Task", "Task")],
        {"properties": {"weight": {"type": "integer", "default": 2}}},
    )
]
CLI_PROFILE.validators = [reject_forbidden]
CLI_PROFILE.event_hooks = [record_event]

EVENTS_PROFILE = deepcopy(RESEARCH_PROFILE)
EVENTS_PROFILE.name = "test-events"
EVENTS_PROFILE.event_hooks = [record_event]

INVALID_LOCATION_PROFILE = deepcopy(CLI_PROFILE)
INVALID_LOCATION_PROFILE.name = "test-invalid-location"
INVALID_LOCATION_PROFILE.node_types[0].schema["allOf"] = [{"default": {}}]

INVALID_DEFAULT_PROFILE = deepcopy(CLI_PROFILE)
INVALID_DEFAULT_PROFILE.name = "test-invalid-default"
INVALID_DEFAULT_PROFILE.node_types[0].schema["properties"]["mode"] = {
    "type": "integer",
    "default": "fast",
}

INVALID_NULL_DEFAULT_PROFILE = deepcopy(CLI_PROFILE)
INVALID_NULL_DEFAULT_PROFILE.name = "test-invalid-null-default"
INVALID_NULL_DEFAULT_PROFILE.node_types[0].schema["properties"]["mode"] = {
    "type": ["string", "null"],
    "default": None,
}

COMPOSED_PROFILE = deepcopy(CLI_PROFILE)
COMPOSED_PROFILE.name = "test-composed-defaults"
COMPOSED_PROFILE.node_types[0].schema["allOf"] = [
    {"properties": {"mode": {"const": "safe"}}},
]


def unsupported(name, schema):
    profile = deepcopy(CLI_PROFILE)
    profile.name = name
    profile.node_types[0].schema = schema
    return profile


NESTED_PROFILE = unsupported(
    "test-default-nested",
    {"properties": {"settings": {"properties": {"enabled": {"default": True}}}}},
)
ITEMS_PROFILE = unsupported(
    "test-default-items",
    {"properties": {"values": {"type": "array", "items": {"default": 1}}}},
)
DEFS_PROFILE = unsupported("test-default-defs", {"$defs": {"value": {"default": 1}}})
ROOT_PROFILE = unsupported("test-default-root", {"default": {}})
ANYOF_PROFILE = unsupported(
    "test-default-anyof",
    {
        "anyOf": [
            {"properties": {"mode": {"default": "fast"}}},
            {"properties": {"mode": {"default": "safe"}}},
        ]
    },
)
ONEOF_PROFILE = unsupported("test-default-oneof", {"oneOf": [{"default": {}}, {}]})
IF_PROFILE = unsupported("test-default-if", {"if": {"default": {}}})
THEN_PROFILE = unsupported("test-default-then", {"then": {"default": {}}})
ELSE_PROFILE = unsupported("test-default-else", {"else": {"default": {}}})
