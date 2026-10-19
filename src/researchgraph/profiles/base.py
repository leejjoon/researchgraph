"""Declarative profiles and validated, isolated profile loading."""

import re
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from importlib import metadata
from typing import ClassVar

from jsonschema import SchemaError, ValidationError
from jsonschema.validators import validator_for

from researchgraph.model import RuleViolation, Write


@dataclass
class NodeType:
    name: str
    prefix: str
    schema: dict
    claimable: bool = False


@dataclass
class EdgeType:
    name: str
    allowed: list[tuple[str, str]]
    schema: dict = field(default_factory=dict)
    inherit: str = "outgoing"


@dataclass
class Gate:
    transition: str = "activate"
    node_type: str = "*"
    roles: list[str] = field(default_factory=lambda: ["human"])
    agents_allowed: bool = False


# Wildcards describe endpoint types, not graph invariants: supersedes additionally
# requires equal endpoint types, enforced by the core's write validation.
CORE_EDGE_TYPES = (
    EdgeType("blocks", [("*", "*")], inherit="both"),
    EdgeType(
        "tests", [("Prediction", "Hypothesis"), ("Task", "Hypothesis")], inherit="both"
    ),
    EdgeType("supersedes", [("*", "*")], inherit="none"),
    EdgeType("derived-from", [("*", "*")]),
    EdgeType("supports", [("Claim", "Hypothesis")]),
    EdgeType("contradicts", [("Claim", "Hypothesis")]),
    EdgeType("discovered-from", [("*", "*")]),
    EdgeType("instructs", [("Instruction", "*")], inherit="both"),
    EdgeType("explains", [("Failure", "*")]),
    EdgeType("cites", [("Claim", "Result"), ("Claim", "Reference")]),
    EdgeType("answers", [("Hypothesis", "Question")], inherit="both"),
    EdgeType("part-of", [("*", "*")], inherit="both"),
    EdgeType("addresses", [("Task", "Prediction")], inherit="both"),
    EdgeType("about", [("Note", "*")]),
    EdgeType(
        "pins",
        [("Hypothesis", "Result")],
        {
            "properties": {"artifact": {"type": "string"}},
            "required": ["artifact"],
        },
    ),
    EdgeType("duplicate-of", [("*", "*")], inherit="both"),
    EdgeType("relates-to", [("*", "*")], inherit="both"),
)


def version_tuple(version: str) -> tuple[int, ...]:
    """Parse the dotted integer ordering used for pinned profile versions."""
    if (
        not isinstance(version, str)
        or re.fullmatch(r"[0-9]+(?:\.[0-9]+)*", version) is None
    ):
        raise RuleViolation("schema", "Profile version must be dotted integers")
    return tuple(int(part) for part in version.split("."))


# Only these JSON Schema keywords contain schemas. In particular, enum, const,
# examples and default contain instance data and must never be walked as schemas.
_SCHEMA_MAPS = {
    "properties",
    "patternProperties",
    "$defs",
    "definitions",
    "dependentSchemas",
}
_SCHEMA_LISTS = {"allOf", "anyOf", "oneOf", "prefixItems"}
_SCHEMA_SINGLE = {
    "additionalProperties",
    "unevaluatedProperties",
    "propertyNames",
    "additionalItems",
    "unevaluatedItems",
    "contains",
    "not",
    "if",
    "then",
    "else",
    "contentSchema",
}


def _check_default_locations(schema, path=()):
    if not isinstance(schema, dict):
        return
    if "default" in schema and not (len(path) == 2 and path[0] == "properties"):
        raise RuleViolation(
            "unsupported_schema_default",
            "Defaults must be direct root property schemas",
        )
    for keyword, value in schema.items():
        if keyword in _SCHEMA_MAPS and isinstance(value, dict):
            for name, child in value.items():
                _check_default_locations(child, (*path, keyword, name))
        elif keyword in _SCHEMA_LISTS and isinstance(value, list):
            for index, child in enumerate(value):
                _check_default_locations(child, (*path, keyword, index))
        elif keyword == "items":
            if isinstance(value, list):
                for index, child in enumerate(value):
                    _check_default_locations(child, (*path, keyword, index))
            else:
                _check_default_locations(value, (*path, keyword))
        elif keyword == "dependencies" and isinstance(value, dict):
            for name, child in value.items():
                if isinstance(child, (dict, bool)):
                    _check_default_locations(child, (*path, keyword, name))
        elif keyword in _SCHEMA_SINGLE:
            _check_default_locations(value, (*path, keyword))


def _prepare_schema(schema):
    if not isinstance(schema, dict):
        raise RuleViolation("schema", "Fields schema must be a mapping")
    schema.setdefault("type", "object")
    schema["additionalProperties"] = False
    try:
        cls = validator_for(schema)
        cls.check_schema(schema)
        validator = cls(schema)
    except SchemaError as exc:
        raise RuleViolation("schema", f"Invalid fields schema: {exc.message}") from exc
    for name, prop in schema.get("properties", {}).items():
        if isinstance(prop, dict) and "default" in prop:
            default = prop["default"]
            if (
                default is None
                or next(validator.descend(default, prop), None) is not None
            ):
                raise RuleViolation(
                    "invalid_schema_default", f"Invalid default for {name}"
                )


@dataclass
class Profile:
    name: str
    version: str
    node_types: list[NodeType]
    edge_types: list[EdgeType]
    gates: list[Gate]
    roles: list[str]
    validators: list[Callable[[object, Write], None]]
    expansion_hook: Callable | None = None
    renderers: dict[str, Callable] = field(default_factory=dict)
    event_hooks: list[Callable[[object, Write, str], None]] = field(
        default_factory=list
    )
    agent_guidance: str = ""
    migrations: dict[str, Callable] = field(default_factory=dict)

    _registry: ClassVar[dict[str, "Profile"]] = {}

    @classmethod
    def register(cls, profile: "Profile") -> None:
        """Validate and snapshot a runtime profile; registration wins over metadata."""
        validated = cls._validated_copy(profile)
        cls._registry[validated.name] = validated

    @classmethod
    def load(cls, name: str) -> "Profile":
        """Resolve a registered or installed profile, returning an isolated copy."""
        if name in cls._registry:
            return cls._validated_copy(cls._registry[name])
        for entry in metadata.entry_points(group="researchgraph.profiles"):
            if entry.name == name:
                return cls._validated_copy(entry.load())
        raise RuleViolation("unknown_profile", f"Unknown profile: {name}")

    @classmethod
    def _validated_copy(cls, profile: "Profile") -> "Profile":
        if not isinstance(profile, cls):
            raise RuleViolation(
                "schema", "Profile entry point must expose a Profile object"
            )
        result = deepcopy(profile)
        if not isinstance(result.name, str) or not result.name:
            raise RuleViolation("schema", "Profile name must be nonempty")
        version_tuple(result.version)
        names, prefixes = set(), set()
        for node in result.node_types:
            if (
                not isinstance(node, NodeType)
                or not isinstance(node.name, str)
                or not node.name
            ):
                raise RuleViolation("schema", "Invalid node type")
            if node.name in names or node.prefix in prefixes:
                raise RuleViolation("schema", "Node names and prefixes must be unique")
            if (
                not isinstance(node.prefix, str)
                or re.fullmatch(r"[a-z]{2,4}", node.prefix) is None
            ):
                raise RuleViolation(
                    "schema", "Node prefixes must be 2–4 lowercase letters"
                )
            if not isinstance(node.claimable, bool):
                raise RuleViolation("schema", "Claimable must be boolean")
            names.add(node.name)
            prefixes.add(node.prefix)
        core = {edge.name: deepcopy(edge) for edge in CORE_EDGE_TYPES}
        declared = set()
        for edge in result.edge_types:
            if (
                not isinstance(edge, EdgeType)
                or not isinstance(edge.name, str)
                or not edge.name
                or edge.name in declared
            ):
                raise RuleViolation("schema", "Edge names must be nonempty and unique")
            declared.add(edge.name)
            if (
                not isinstance(edge.allowed, list)
                or not edge.allowed
                or any(
                    not isinstance(pair, tuple)
                    or len(pair) != 2
                    or any(not isinstance(name, str) or not name for name in pair)
                    for pair in edge.allowed
                )
            ):
                raise RuleViolation("schema", f"Invalid endpoints for {edge.name}")
            if edge.name in core and set(edge.allowed) != set(core[edge.name].allowed):
                raise RuleViolation(
                    "schema", f"Cannot change core endpoints: {edge.name}"
                )
            if edge.inherit not in ("both", "outgoing", "none"):
                raise RuleViolation("schema", f"Invalid inheritance for {edge.name}")
            core[edge.name] = edge
        result.edge_types = list(core.values())
        for gate in result.gates:
            if not isinstance(gate, Gate):
                raise RuleViolation("schema", "Invalid gate")
            if gate.transition != "activate":
                raise RuleViolation(
                    "unsupported_gate_transition",
                    f"Unsupported gate transition: {gate.transition}",
                )
            if gate.node_type != "*" and gate.node_type not in names:
                raise RuleViolation(
                    "schema", f"Unknown gate node type: {gate.node_type}"
                )
            if (
                not isinstance(gate.roles, list)
                or any(not isinstance(role, str) or not role for role in gate.roles)
                or not isinstance(gate.agents_allowed, bool)
            ):
                raise RuleViolation("schema", "Invalid gate roles or agent permission")
        if not isinstance(result.roles, list) or any(
            not isinstance(role, str) or not role for role in result.roles
        ):
            raise RuleViolation("schema", "Invalid profile roles")
        if any(not callable(validator) for validator in result.validators):
            raise RuleViolation("schema", "Profile validators must be callable")
        schemas = [item.schema for item in [*result.node_types, *result.edge_types]]
        # Complete the location pass across all types before inspecting any value.
        for schema in schemas:
            _check_default_locations(schema)
        for schema in schemas:
            _prepare_schema(schema)
        return result

    def node_type(self, name: str) -> NodeType:
        for node in self.node_types:
            if node.name == name:
                return node
        raise RuleViolation("unknown_type", f"Unknown node type: {name}")

    def edge_type(self, name: str) -> EdgeType:
        for edge in self.edge_types:
            if edge.name == name:
                return edge
        raise RuleViolation("unknown_type", f"Unknown edge type: {name}")

    def normalize_fields(
        self, type_name: str, fields: Mapping, *, edge: bool = False
    ) -> dict:
        """Drop root nulls, copy absent root defaults, validate the whole mapping."""
        schema = (
            self.edge_type(type_name) if edge else self.node_type(type_name)
        ).schema
        if not isinstance(fields, Mapping):
            raise RuleViolation("schema", "Fields must be a mapping")
        result = deepcopy(
            {key: value for key, value in fields.items() if value is not None}
        )
        for name, prop in schema.get("properties", {}).items():
            if name not in result and isinstance(prop, dict) and "default" in prop:
                result[name] = deepcopy(prop["default"])
        try:
            validator_for(schema)(schema).validate(result)
        except ValidationError as exc:
            raise RuleViolation(
                "schema", f"Invalid fields for {type_name}: {exc.message}"
            ) from exc
        return result

    def gates_for(self, type_name: str) -> list[Gate]:
        self.node_type(type_name)
        return [gate for gate in self.gates if gate.node_type in ("*", type_name)]
