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
    """Declare a node name, id prefix, fields schema, and claimability.

    Claimable defaults to False. Registration/loading checks unique names and
    two-to-four-letter lowercase prefixes, closes the schema, and validates
    supported defaults; constructing this declaration alone does not validate it.
    """

    name: str
    prefix: str
    schema: dict
    claimable: bool = False


@dataclass
class EdgeType:
    """Declare edge name, allowed (source type, target type) pairs, and schema.

    Endpoint names may be ``*`` wildcards. Schema defaults to a fresh empty
    mapping (no fields after loading closes it); inherit defaults to outgoing
    and may be both, outgoing, or none. Loading preserves core endpoint pairs
    while allowing a replacement schema or inheritance policy.
    """

    name: str
    allowed: list[tuple[str, str]]
    schema: dict = field(default_factory=dict)
    inherit: str = "outgoing"


@dataclass
class Gate:
    """Declare an activation approval requirement for a node type or ``*``.

    Defaults are transition=activate, node_type=*, roles=[human] (a fresh list),
    and agents_allowed=False. Loading rejects other transitions. Every matching
    gate must be satisfied: human/agent role markers match actor kinds, while
    other roles match the per-call declared role; agents additionally require
    agents_allowed. Roles are declarations, not authenticated permissions.
    when_fields optionally restricts matching to an exact scalar conjunction on
    immutable, normalised fields. Loading validates direct property keys and
    rejects conditions on wildcard types. None means unconditional.
    """

    transition: str = "activate"
    node_type: str = "*"
    roles: list[str] = field(default_factory=lambda: ["human"])
    agents_allowed: bool = False
    when_fields: dict | None = None


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
    """Return integer components for ordinary tuple comparison of versions.

    Accept one or more ASCII integer components separated by dots, including
    leading zeroes. Raise RuleViolation(rule="schema") for any other input.
    No padding is added: ``1`` and ``1.0`` produce tuples of different lengths.
    """
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
    """Declarative types, gates, roles, and callbacks for a project profile.

    Name, dotted-integer version, node_types, edge_types, gates, roles, and
    validators are required. Validators receive (project, Write) and refuse by
    raising RuleViolation. Roles are self-declared labels; this loader checks
    nonempty strings, not gate-role membership in this list.

    Expansion_hook defaults to None and is not called by core. Renderers and
    migrations (from-version to callable) default to fresh mappings; event_hooks
    defaults to a fresh list of (project, Write, commit-id) callbacks run after
    committed writes. Agent_guidance defaults to an empty string. Renderers and
    migrations are reserved consumer/future integration data (see spec/profiles.md).

    Construction merely stores declarations. Register/load isolate them by deep
    copy, add core edge types, validate declarations, and normalize field schemas.
    """

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
        """Validate a deep copy and register it by name for this process; return None.

        Replace any prior registration of that name and take precedence over
        installed entry points. The supplied object is not modified. Apply the
        same core-edge, closed-schema, and default checks as load; validation
        failures raise RuleViolation without replacing the registered value.
        """
        validated = cls._validated_copy(profile)
        cls._registry[validated.name] = validated

    @classmethod
    def load(cls, name: str) -> "Profile":
        """Return a validated deep copy from the registry or an installed entry point.

        Registered names win; otherwise use the first matching entry in the
        ``researchgraph.profiles`` metadata group, whose value must be a Profile.
        Raise unknown_profile when no name matches; entry-point import errors
        propagate. Invalid declarations raise RuleViolation (usually schema).

        Include all core edges, preserving their endpoints. Close root fields
        schemas with additionalProperties=False and default their type to object.
        Reject schema defaults outside direct root properties with
        unsupported_schema_default before checking values; null or invalid
        supported defaults raise invalid_schema_default. Unsupported gate
        transitions raise unsupported_gate_transition. See spec/profiles.md.
        """
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
            if edge.name in core:
                original = set(core[edge.name].allowed)
                supplied = set(edge.allowed)
                valid = supplied == original
                if edge.name == "cites":
                    valid = original <= supplied and all(
                        source == "Claim" and target != "*"
                        for source, target in supplied
                    )
                if not valid:
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
        for gate in result.gates:
            if gate.when_fields is None:
                continue
            if gate.node_type == "*" or not isinstance(gate.when_fields, dict):
                raise RuleViolation(
                    "schema", "Gate conditions require a concrete type and mapping"
                )
            properties = result.node_type(gate.node_type).schema.get("properties", {})
            if any(
                key not in properties or type(value) not in (str, bool, int, float)
                for key, value in gate.when_fields.items()
            ):
                raise RuleViolation(
                    "schema",
                    "Gate conditions require direct properties and scalar values",
                )
        return result

    def node_type(self, name: str) -> NodeType:
        """Return the first matching node declaration, not a copy.

        Raise RuleViolation(rule="unknown_type") when name is not declared.
        """
        for node in self.node_types:
            if node.name == name:
                return node
        raise RuleViolation("unknown_type", f"Unknown node type: {name}")

    def edge_type(self, name: str) -> EdgeType:
        """Return the first matching edge declaration, not a copy.

        Loaded profiles include core edges. Raise RuleViolation(rule="unknown_type")
        when name is not declared.
        """
        for edge in self.edge_types:
            if edge.name == name:
                return edge
        raise RuleViolation("unknown_type", f"Unknown edge type: {name}")

    def normalize_fields(
        self, type_name: str, fields: Mapping, *, edge: bool = False
    ) -> dict:
        """Return normalized fields for a node type, or an edge when edge=True.

        Require a Mapping, discard root entries whose value is None, deep-copy
        the remaining values, and insert deep-copied direct-property defaults
        only for absent keys. Do not recursively materialize nested defaults or
        replace supplied non-null values. Validate the entire resulting mapping
        against the selected schema without mutating inputs or schema.

        Unknown types raise unknown_type; non-mappings and JSON Schema validation
        failures raise RuleViolation(rule="schema"). Registration/loading must
        have prepared the schema and checked default locations and values.
        """
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

    def gates_for(self, type_name: str, fields: Mapping | None = None) -> list[Gate]:
        """Return matching wildcard and type-specific gates in declaration order.

        The list is new but contains the original Gate objects, not copies.
        Check the node type first and raise unknown_type if undeclared. This
        selects requirements only; it does not evaluate approval satisfaction.
        Omitted/None fields performs type-only lookup; an explicit mapping
        filters conditions against already normalised/materialised fields.
        Numeric equality follows JSON numbers, never boolean/string coercion.
        """
        self.node_type(type_name)
        return [
            gate
            for gate in self.gates
            if gate.node_type in ("*", type_name)
            and (
                fields is None
                or gate.when_fields is None
                or all(
                    key in fields
                    and isinstance(fields[key], bool) == isinstance(value, bool)
                    and fields[key] == value
                    for key, value in gate.when_fields.items()
                )
            )
        ]
