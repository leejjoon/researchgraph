"""Canonical YAML examples and complete mapping round trips."""

import math
import subprocess
from pathlib import Path

import pytest
import yaml
from hypothesis import given, settings

from researchgraph.store.io import (
    ACTOR_KEYS,
    APPROVAL_KEYS,
    ARTIFACT_KEYS,
    BUDGET_KEYS,
    EDGE_KEYS,
    LEASE_KEYS,
    NODE_KEYS,
    USAGE_KEYS,
    dump_edge,
    dump_node,
    load_edge,
    load_node,
)
from tests.strategies import complete_edges, complete_nodes

FIXTURES = Path(__file__).parent / "fixtures" / "canonical"


def assert_same_scalars(actual, expected):
    """Check exact scalar types, treating NaNs as equal and preserving signed zero."""
    assert type(actual) is type(expected)
    if isinstance(expected, dict):
        assert actual.keys() == expected.keys()
        for key in expected:
            assert_same_scalars(actual[key], expected[key])
    elif isinstance(expected, list):
        assert len(actual) == len(expected)
        for left, right in zip(actual, expected):
            assert_same_scalars(left, right)
    elif isinstance(expected, float) and math.isnan(expected):
        assert math.isnan(actual)
    else:
        assert actual == expected
        if isinstance(expected, float) and expected == 0:
            assert math.copysign(1, actual) == math.copysign(1, expected)


@pytest.mark.parametrize("name", ["node.yaml", "edge.yaml", "scalars.yaml"])
def test_canonical_fixture_roundtrip(name):
    path = FIXTURES / name
    load, dump = (
        (load_edge, dump_edge) if name == "edge.yaml" else (load_node, dump_node)
    )
    original = path.read_bytes()
    mapping = load(path)
    assert dump(mapping).encode("utf-8") == original
    assert_same_scalars(mapping, yaml.safe_load(original))
    assert b"\r" not in original
    assert original.endswith(b"\n") and not original.endswith(b"\n\n")
    assert b"---\n" not in original and b"...\n" not in original


def test_scalar_fixture_values():
    fields = load_node(FIXTURES / "scalars.yaml")["fields"]
    assert fields["literal_strip"] == "first\nsecond"
    assert fields["literal_clip"] == "first\nsecond\n"
    assert fields["literal_keep"] == "first\nsecond\n\n\n"
    assert fields["carriage_return"] == "first\r\nsecond\r"
    assert fields["trailing_spaces"] == "first \nsecond  \n"
    assert fields["unicode"] == "별 α café 😀"
    assert all(type(value) is str for value in fields["quoted"])
    assert all(type(value) is float for value in fields["floats"])
    assert all(type(value) is int for value in fields["integers"])


def reversed_mappings(value):
    if isinstance(value, dict):
        return {key: reversed_mappings(value[key]) for key in reversed(value)}
    if isinstance(value, list):
        return [reversed_mappings(item) for item in value]
    return value


def test_declared_orders_and_null_subkeys():
    node = load_node(FIXTURES / "node.yaml")
    node["author"]["email"] = None
    node["approvals"][0]["role"] = None
    node["approvals"][0]["note"] = None
    node["artifacts"][0]["added_by"]["version"] = None
    node["budget"]["tokens"] = None
    node["usage"]["compute_seconds"] = None
    dumped = dump_node(reversed_mappings(node))
    result = yaml.safe_load(dumped)
    assert tuple(result) == NODE_KEYS
    assert tuple(result["author"]) == tuple(
        key for key in ACTOR_KEYS if node["author"].get(key) is not None
    )
    assert tuple(result["approvals"][0]) == ("actor", "at", "what")
    assert tuple(result["approvals"][1]) == tuple(
        key for key in APPROVAL_KEYS if key in result["approvals"][1]
    )
    assert tuple(result["artifacts"][0]) == ARTIFACT_KEYS
    assert tuple(result["budget"]) == ("compute_seconds",)
    assert tuple(result["usage"]) == ("tokens",)
    assert tuple(result["lease"]) == LEASE_KEYS
    assert BUDGET_KEYS == USAGE_KEYS == ("tokens", "compute_seconds")
    assert "version" not in result["artifacts"][0]["added_by"]
    edge = load_edge(FIXTURES / "edge.yaml")
    assert tuple(yaml.safe_load(dump_edge(reversed_mappings(edge)))) == EDGE_KEYS
    assert dump_edge(reversed_mappings(edge)) == dump_edge(edge)


def test_absent_top_level_keys_and_empty_accounting():
    result = yaml.safe_load(dump_node({"budget": {}, "usage": {"tokens": None}}))
    assert tuple(result) == NODE_KEYS
    assert all(value is None for value in result.values())
    assert tuple(yaml.safe_load(dump_edge({}))) == EDGE_KEYS


def test_shared_objects_do_not_emit_aliases():
    node = load_node(FIXTURES / "node.yaml")
    shared = {"z": ["yes"], "a": 1}
    node["fields"] = {"second": shared, "first": shared}
    dumped = dump_node(node)
    assert "&id" not in dumped and "*id" not in dumped
    assert yaml.safe_load(dumped)["fields"] == node["fields"]


@pytest.mark.parametrize(
    "value", ["\n", "\n\n", " x\ny\n\n", "x\n\ny\n", "x\u0085y", "x\u2028y", "x\u2029y"]
)
def test_multiline_edge_cases(value, tmp_path):
    node = load_node(FIXTURES / "scalars.yaml")
    node["body"] = value
    node["fields"] = {"last": value}
    dumped = dump_node(node)
    path = tmp_path / "node.yaml"
    path.write_text(dumped, encoding="utf-8", newline="\n")
    assert_same_scalars(load_node(path), node)
    assert dump_node(load_node(path)) == dumped
    assert "\n...\n" not in dumped


def test_noop_rewrite_has_no_git_diff(tmp_path):
    def git(*args):
        return subprocess.run(
            ["git", "-C", str(tmp_path), *args], check=True, capture_output=True
        )

    git("init", "--quiet")
    for source in FIXTURES.glob("*.yaml"):
        (tmp_path / source.name).write_bytes(source.read_bytes())
    git("add", "--", "node.yaml", "edge.yaml", "scalars.yaml")
    git(
        "-c",
        "user.name=Codec test",
        "-c",
        "user.email=codec@example.org",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "--quiet",
        "-m",
        "Canonical fixtures",
    )
    for path in tmp_path.glob("*.yaml"):
        load, dump = (
            (load_edge, dump_edge)
            if path.name == "edge.yaml"
            else (load_node, dump_node)
        )
        path.write_text(dump(load(path)), encoding="utf-8", newline="\n")
    assert git("diff", "--exit-code").stdout == b""
    assert git("status", "--porcelain").stdout == b""


@settings(max_examples=1000, deadline=None)
@given(node=complete_nodes(), edge=complete_edges())
def test_complete_mapping_roundtrip(node, edge, tmp_path_factory):
    directory = tmp_path_factory.mktemp("canonical")
    for mapping, load, dump, name in (
        (node, load_node, dump_node, "node.yaml"),
        (edge, load_edge, dump_edge, "edge.yaml"),
    ):
        text = dump(mapping)
        path = directory / name
        path.write_text(text, encoding="utf-8", newline="\n")
        loaded = load(path)
        assert_same_scalars(loaded, mapping)
        assert dump(loaded) == text
        assert dump(reversed_mappings(mapping)) == text
        assert text.endswith("\n") and not text.endswith("\n\n")


@pytest.mark.parametrize("text", ["[]\n", "null\n", "scalar\n"])
def test_load_rejects_non_mapping_documents(text, tmp_path):
    path = tmp_path / "invalid.yaml"
    path.write_text(text)
    with pytest.raises(TypeError, match="must be a mapping"):
        load_node(path)
    with pytest.raises(TypeError, match="must be a mapping"):
        load_edge(path)


@pytest.mark.parametrize(
    "loader", [yaml.SafeLoader] + ([yaml.CSafeLoader] if yaml.__with_libyaml__ else [])
)
def test_safe_reader_backend_parity(loader, monkeypatch):
    from researchgraph.store import io

    monkeypatch.setattr(io, "_SAFE_LOADER", loader)
    for name in ("node.yaml", "scalars.yaml", "edge.yaml"):
        path = FIXTURES / name
        load, dump = (
            (load_edge, dump_edge) if name == "edge.yaml" else (load_node, dump_node)
        )
        assert dump(load(path)).encode() == path.read_bytes()
    with pytest.raises(yaml.YAMLError):
        io._load_mapping('!!python/object/apply:os.system ["false"]')
