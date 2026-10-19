"""Profile declarations, loading, defaults, and research validator contracts."""

import json
import subprocess
import sys
from copy import deepcopy
from datetime import UTC, datetime

import pytest

from researchgraph.model import Actor, Artifact, Edge, Node, RuleViolation, Write
from researchgraph.profiles import EdgeType, Gate, NodeType, Profile, version_tuple
from researchgraph.profiles.base import CORE_EDGE_TYPES
from researchgraph.profiles.research import claim_needs_evidence, pin_artifact_exists


@pytest.fixture(autouse=True)
def isolated_registry(monkeypatch):
    monkeypatch.setattr(Profile, "_registry", dict(Profile._registry))


def profile(schema=None, *, edge=False, name="custom"):
    return Profile(
        name=name,
        version="1.0",
        node_types=[NodeType("Task", "ta", {} if edge else (schema or {}), True)],
        edge_types=[EdgeType("extra", [("Task", "Task")], schema or {})]
        if edge
        else [],
        gates=[],
        roles=[],
        validators=[],
    )


def refusal(rule, fn, *args, **kwargs):
    with pytest.raises(RuleViolation) as exc:
        fn(*args, **kwargs)
    assert exc.value.rule == rule


def loaded(schema, edge=False):
    value = profile(schema, edge=edge)
    Profile.register(value)
    return Profile.load(value.name)


def test_bundled_declarations():
    research, tasks = Profile.load("research"), Profile.load("tasks")
    expected = {
        "Question": "qu",
        "Reference": "rf",
        "Hypothesis": "hy",
        "Prediction": "pr",
        "Task": "ta",
        "Result": "re",
        "Claim": "cl",
        "Instruction": "in",
        "Failure": "fl",
        "Note": "no",
    }
    assert {n.name: n.prefix for n in research.node_types} == expected
    assert {n.name for n in tasks.node_types} == {
        "Task",
        "Result",
        "Instruction",
        "Failure",
        "Note",
    }
    for value in (research, tasks):
        assert {n.name for n in value.node_types if n.claimable} == {"Task"}
        assert {e.name for e in value.edge_types} == {e.name for e in CORE_EDGE_TYPES}
        assert all(n.schema["additionalProperties"] is False for n in value.node_types)
        assert all(e.schema["additionalProperties"] is False for e in value.edge_types)
    assert research.roles == ["owner", "reviewer"]
    assert {g.node_type for g in research.gates} == {
        "Hypothesis",
        "Prediction",
        "Claim",
    }
    assert all(g.roles == ["human"] and not g.agents_allowed for g in research.gates)
    assert tasks.gates == tasks.roles == tasks.validators == []
    assert [v.__name__ for v in research.validators] == [
        "claim_needs_evidence",
        "pin_artifact_exists",
    ]
    assert research.normalize_fields("Question", {}) == {"closed": False}
    task_fields = {"kind": "expansion", "targets": ["hy-aaaaaaaa"], "command": "run"}
    assert research.normalize_fields("Task", task_fields) == task_fields
    assert research.normalize_fields("Result", {"held_out": True, "runs": 0}) == {
        "held_out": True,
        "runs": 0,
    }
    refusal("schema", research.normalize_fields, "Result", {"runs": -1})
    refusal("schema", research.normalize_fields, "Reference", {"bibcode": "private"})
    reference = {"doi": "x", "url": "y", "title": "z"}
    assert research.normalize_fields("Reference", reference) == reference
    for value in (research, tasks):
        failure = dict.fromkeys(["step", "check", "measured", "error"], "text")
        assert value.normalize_fields("Failure", failure) == failure
        assert value.edge_type("pins").schema["properties"]["artifact"] == {
            "type": "string"
        }
        refusal("schema", value.normalize_fields, "pins", {}, edge=True)
    refusal("schema", tasks.normalize_fields, "Result", {"runs": 1})


def test_research_edge_fields():
    p = Profile.load("research")
    refusal("schema", p.normalize_fields, "tests", {}, edge=True)
    assert p.normalize_fields("tests", {"expected": "yes"}, edge=True) == {
        "expected": "yes",
        "discriminating": True,
    }
    assert (
        p.normalize_fields(
            "tests", {"expected": "yes", "discriminating": False}, edge=True
        )["discriminating"]
        is False
    )
    for name in ("supports", "contradicts"):
        for strength in ("weak", "moderate", "strong"):
            assert p.normalize_fields(name, {"strength": strength}, edge=True) == {
                "strength": strength
            }
        refusal("schema", p.normalize_fields, name, {"strength": "certain"}, edge=True)
    cites = {"artifact": "a", "locator": "p1", "sha256": "hash"}
    assert p.normalize_fields("cites", cites, edge=True) == cites
    refusal("schema", p.normalize_fields, "blocks", {"unexpected": 1}, edge=True)


def test_unknown_names_and_registry_precedence():
    refusal("unknown_profile", Profile.load, "missing-profile")
    p = Profile.load("research")
    refusal("unknown_type", p.node_type, "Missing")
    refusal("unknown_type", p.edge_type, "Missing")
    refusal("unknown_type", p.normalize_fields, "Missing", {})
    replacement = profile(name="research")
    Profile.register(replacement)
    replacement.node_types[0].prefix = "zz"
    assert Profile.load("research").node_type("Task").prefix == "ta"
    first = Profile.load("research")
    first.node_types[0].schema["properties"] = {"leak": {}}
    assert "leak" not in Profile.load("research").node_type("Task").schema.get(
        "properties", {}
    )


def test_core_endpoints_and_inheritance():
    expected = {
        "blocks": ([("*", "*")], "both"),
        "tests": ([("Prediction", "Hypothesis"), ("Task", "Hypothesis")], "both"),
        "supersedes": ([("*", "*")], "none"),
        "derived-from": ([("*", "*")], "outgoing"),
        "supports": ([("Claim", "Hypothesis")], "outgoing"),
        "contradicts": ([("Claim", "Hypothesis")], "outgoing"),
        "discovered-from": ([("*", "*")], "outgoing"),
        "instructs": ([("Instruction", "*")], "both"),
        "explains": ([("Failure", "*")], "outgoing"),
        "cites": ([("Claim", "Result"), ("Claim", "Reference")], "outgoing"),
        "answers": ([("Hypothesis", "Question")], "both"),
        "part-of": ([("*", "*")], "both"),
        "addresses": ([("Task", "Prediction")], "both"),
        "about": ([("Note", "*")], "outgoing"),
        "pins": ([("Hypothesis", "Result")], "outgoing"),
        "duplicate-of": ([("*", "*")], "both"),
        "relates-to": ([("*", "*")], "both"),
    }
    assert {e.name: (e.allowed, e.inherit) for e in CORE_EDGE_TYPES} == expected
    p = profile()
    p.edge_types = [
        EdgeType(
            "blocks", [("*", "*")], {"properties": {"why": {"type": "string"}}}, "none"
        )
    ]
    Profile.register(p)
    assert Profile.load(p.name).edge_type("blocks").inherit == "none"
    assert Profile.load(p.name).normalize_fields(
        "blocks", {"why": "wait"}, edge=True
    ) == {"why": "wait"}
    for core in CORE_EDGE_TYPES:
        p = profile()
        p.edge_types = [EdgeType(core.name, [("Task", "Task")])]
        refusal("schema", Profile.register, p)
    assert loaded({}, edge=True).edge_type("extra").inherit == "outgoing"


@pytest.mark.parametrize("prefix", ["a", "abcde", "TA", "t1", "éé"])
def test_invalid_prefix(prefix):
    p = profile()
    p.node_types[0].prefix = prefix
    refusal("schema", Profile.register, p)


def test_duplicate_types_prefixes_gates_versions_and_schema():
    p = profile()
    p.node_types.append(NodeType("Other", "ta", {}))
    refusal("schema", Profile.register, p)
    p.node_types[-1] = NodeType("Task", "ot", {})
    refusal("schema", Profile.register, p)
    p = profile()
    p.gates = [Gate(transition="complete")]
    refusal("unsupported_gate_transition", Profile.register, p)
    p.gates = [Gate(), Gate(node_type="Task", roles=["owner"])]
    Profile.register(p)
    assert len(Profile.load(p.name).gates_for("Task")) == 2
    refusal("unknown_type", Profile.load(p.name).gates_for, "Missing")
    assert version_tuple("1.10") > version_tuple("1.9")
    assert version_tuple("1.0.2") == (1, 0, 2)
    for version in ("", "1.x", "1..0", "-1.0"):
        p.version = version
        refusal("schema", Profile.register, p)
    refusal("schema", loaded, {"properties": {"x": {"type": "not-a-type"}}})


@pytest.mark.parametrize("edge", [False, True])
def test_general_9_defaults_nulls_and_closed_schemas(edge):
    schema = {
        "additionalProperties": True,
        "properties": {"mode": {"enum": ["fast", "safe"], "default": "fast"}},
        "required": ["mode"],
    }
    original = deepcopy(schema)
    p = loaded(schema, edge)
    name = "extra" if edge else "Task"
    for fields, expected in [
        ({}, "fast"),
        ({"mode": None}, "fast"),
        ({"mode": "safe"}, "safe"),
    ]:
        before = deepcopy(fields)
        assert p.normalize_fields(name, fields, edge=edge) == {"mode": expected}
        assert fields == before
    refusal("schema", p.normalize_fields, name, {"mode": "other"}, edge=edge)
    refusal("schema", p.normalize_fields, name, {"extra": True}, edge=edge)
    refusal("schema", p.normalize_fields, name, [], edge=edge)
    assert schema == original


UNSUPPORTED = [
    {"default": {}},
    {"properties": {"x": {"properties": {"nested": {"default": 1}}}}},
    {"properties": {"x": {"items": {"default": 1}}}},
    {"$defs": {"x": {"default": 1}}},
    {"definitions": {"x": {"default": 1}}},
    *[
        {key: [{"properties": {"x": {"default": 1}}}]}
        for key in ("anyOf", "oneOf", "allOf")
    ],
    *[
        {key: {"properties": {"x": {"default": 1}}}}
        for key in ("if", "then", "else", "not")
    ],
    {
        "anyOf": [
            {"properties": {"x": {"default": 1}}},
            {"properties": {"x": {"default": 2}}},
        ]
    },
    {"properties": {"x": {"additionalProperties": {"default": 1}}}},
    {"dependentSchemas": {"x": {"default": 1}}},
    {"properties": {"x": {"prefixItems": [{"default": 1}]}}},
]


@pytest.mark.parametrize("edge", [False, True])
@pytest.mark.parametrize("schema", UNSUPPORTED)
def test_general_10_unsupported_locations_registration_and_load(schema, edge):
    refusal("unsupported_schema_default", Profile.register, profile(schema, edge=edge))
    Profile._registry["unvalidated"] = profile(schema, edge=edge, name="unvalidated")
    refusal("unsupported_schema_default", Profile.load, "unvalidated")


@pytest.mark.parametrize("edge", [False, True])
@pytest.mark.parametrize(
    "property_schema",
    [
        {"type": "integer", "default": "fast"},
        {"type": ["null", "string"], "default": None},
    ],
)
def test_general_11_invalid_defaults(edge, property_schema):
    refusal(
        "invalid_schema_default",
        loaded,
        {"properties": {"mode": property_schema}},
        edge,
    )


def test_unsupported_locations_precede_invalid_defaults_across_types():
    p = profile({"properties": {"x": {"default": None}}})
    p.edge_types = [EdgeType("extra", [("*", "*")], {"allOf": [{"default": {}}]})]
    refusal("unsupported_schema_default", Profile.register, p)


@pytest.mark.parametrize("edge", [False, True])
def test_general_11_combined_defaults_must_pass_composition(edge):
    schema = {
        "properties": {"a": {"default": True}, "b": {"default": True}},
        "not": {
            "required": ["a", "b"],
            "properties": {"a": {"const": True}, "b": {"const": True}},
        },
    }
    p = loaded(schema, edge)
    name = "extra" if edge else "Task"
    refusal("schema", p.normalize_fields, name, {}, edge=edge)
    assert p.normalize_fields(name, {"a": False, "b": None}, edge=edge) == {
        "a": False,
        "b": True,
    }


@pytest.mark.parametrize("edge", [False, True])
def test_general_12_composition_and_independent_literal_defaults(edge):
    schema = {
        "properties": {
            "payload": {
                "type": "object",
                "default": {"default": [1]},
                "properties": {
                    "default": {"type": "array", "items": {"type": "integer"}}
                },
            },
            "items": {"type": "array", "default": ["x"], "items": {"type": "string"}},
        },
        "allOf": [{"required": ["payload", "items"]}],
        "anyOf": [{"properties": {"items": {"minItems": 1}}}],
    }
    original = deepcopy(schema)
    p = loaded(schema, edge)
    name = "extra" if edge else "Task"
    first = p.normalize_fields(name, {}, edge=edge)
    first["payload"]["default"].append(2)
    first["items"].append("y")
    assert p.normalize_fields(name, {}, edge=edge) == {
        "payload": {"default": [1]},
        "items": ["x"],
    }
    assert schema == original
    assert p.normalize_fields(name, {}, edge=edge) != first
    refusal("schema", p.normalize_fields, name, {"items": []}, edge=edge)


def test_entrypoints_load_in_fresh_process():
    code = """import json
from researchgraph.profiles import Profile
Profile._registry.clear()
print(json.dumps({name: Profile.load(name).name for name in ("research", "tasks", "test-defaults")}))
p = Profile.load("test-defaults")
assert p.normalize_fields("Task", {}) == {"mode": "fast"}
assert p.normalize_fields("Task", {"mode": None}) == {"mode": "fast"}
assert p.normalize_fields("Task", {"mode": "safe"}) == {"mode": "safe"}
"""
    result = subprocess.run(
        [sys.executable, "-c", code], check=True, capture_output=True, text=True
    )
    assert json.loads(result.stdout) == {
        "research": "research",
        "tasks": "tasks",
        "test-defaults": "test-defaults",
    }


def test_entrypoint_schemas_are_validated_and_copied(monkeypatch):
    class EntryPoint:
        name = "external"

        def load(self):
            return external

    external = profile(name="external")
    monkeypatch.setattr(
        "researchgraph.profiles.base.metadata.entry_points",
        lambda **kwargs: [EntryPoint()],
    )
    first = Profile.load("external")
    first.node_types[0].prefix = "xx"
    assert external.node_types[0].prefix == "ta"
    external.node_types[0].schema = {"allOf": [{"default": {}}]}
    refusal("unsupported_schema_default", Profile.load, "external")
    external.node_types[0].schema = {
        "properties": {"x": {"type": "integer", "default": "bad"}}
    }
    refusal("invalid_schema_default", Profile.load, "external")


NOW = datetime(2026, 10, 19, tzinfo=UTC)
HUMAN = Actor("human", "reviewer")


def node(identifier, type_name, **kwargs):
    return Node(identifier, type_name, type_name, "", HUMAN, NOW, **kwargs)


def edge(identifier, type_name, source, target, **kwargs):
    return Edge(identifier, type_name, source, target, HUMAN, NOW, **kwargs)


class MemoryProject:
    """Exact validator read protocol: get_node(id), get_edges() (all edges)."""

    def __init__(self, nodes=(), edges=()):
        self.nodes = {n.id: n for n in nodes}
        self.edges = list(edges)

    def get_node(self, identifier):
        return self.nodes[identifier]

    def get_edges(self):
        return list(self.edges)


def write(op, nodes=(), edges=()):
    return Write(op, HUMAN, None, NOW, {}, list(nodes), list(edges))


def test_claim_validator_approve_only_and_overlay():
    claim = node("cl-aaaaaaaa", "Claim")
    result = node("re-aaaaaaaa", "Result")
    cites = edge("ed-aaaaaaaa", "cites", claim.id, result.id)
    project = MemoryProject([claim, result])
    claim_needs_evidence(project, write("propose", [claim]))
    claim_needs_evidence(project, write("approve", [result]))
    refusal(
        "claim_needs_evidence", claim_needs_evidence, project, write("approve", [claim])
    )
    claim_needs_evidence(project, write("approve", [claim], [cites]))
    project.edges = [cites]
    claim_needs_evidence(project, write("approve", [claim]))
    retired = deepcopy(cites)
    retired.retired = True
    refusal(
        "claim_needs_evidence",
        claim_needs_evidence,
        project,
        write("approve", [claim], [retired]),
    )
    project.edges = [edge("ed-bbbbbbbb", "cites", result.id, claim.id)]
    refusal(
        "claim_needs_evidence", claim_needs_evidence, project, write("approve", [claim])
    )


def test_pin_validator_existing_and_write_target_artifacts():
    hypothesis = node("hy-aaaaaaaa", "Hypothesis")
    artifact = Artifact("artifacts/re-aaaaaaaa/output", "hash", 1, NOW, HUMAN)
    result = node("re-aaaaaaaa", "Result", artifacts=[artifact])
    pin = edge(
        "ed-aaaaaaaa",
        "pins",
        hypothesis.id,
        result.id,
        fields={"artifact": artifact.path},
    )
    project = MemoryProject([hypothesis, result])
    pin_artifact_exists(project, write("link", edges=[pin]))
    project.nodes[result.id] = node(result.id, "Result")
    refusal("schema", pin_artifact_exists, project, write("link", edges=[pin]))
    pin_artifact_exists(project, write("attach", [result], [pin]))
    pin.retired = True
    pin_artifact_exists(project, write("retire", edges=[pin]))
