"""Typed contracts bridge to the mapping-only codec, without validation policy."""

import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import patch

import pytest
import yaml
from hypothesis import given

from researchgraph.model import (
    EDGE_IMMUTABLE_KEYS,
    NODE_IMMUTABLE_KEYS,
    Actor,
    Approval,
    Artifact,
    Edge,
    Inheritance,
    Lease,
    Node,
    Status,
    Write,
    format_timestamp,
    new_id,
    parse_timestamp,
)
from researchgraph.store.io import EDGE_KEYS, NODE_KEYS, dump_edge, dump_node
from tests.strategies import (
    TIMESTAMPS,
    approvals,
    artifacts,
    edges,
    inheritances,
    leases,
    nodes,
    rule_violations,
    statuses,
    typed_actors,
    writes,
)


@given(node=nodes(), edge=edges())
def test_model_mapping_codec_roundtrip(node, edge):
    for model, cls, dump, keys in (
        (node, Node, dump_node, NODE_KEYS),
        (edge, Edge, dump_edge, EDGE_KEYS),
    ):
        mapping = model.to_dict()
        assert tuple(mapping) == keys
        loaded = yaml.safe_load(dump(mapping))
        assert loaded == mapping
        assert cls.from_dict(loaded) == model
        assert cls.from_dict(loaded).to_dict() == mapping
    assert "from_" not in edge.to_dict()
    assert edge.to_dict()["from"] == edge.from_
    assert (node.lease is not None) == (node.status == Status.RUNNING)


@given(actor=typed_actors(), approval=approvals(), artifact=artifacts(), lease=leases())
def test_nested_record_roundtrips(actor, approval, artifact, lease):
    for value, cls in (
        (actor, Actor),
        (approval, Approval),
        (artifact, Artifact),
        (lease, Lease),
    ):
        assert cls.from_dict(value.to_dict()).to_dict() == value.to_dict()
    assert lease.expires_at > lease.acquired_at


@given(write=writes())
def test_write_roundtrip(write):
    mapping = write.to_dict()
    restored = Write.from_dict(mapping)
    assert restored == write
    assert restored.to_dict() == mapping
    assert mapping["nodes"] == [node.to_dict() for node in write.nodes]
    assert mapping["edges"] == [edge.to_dict() for edge in write.edges]


@given(actor=typed_actors())
def test_actor_identity_ignores_metadata(actor):
    changed = replace(actor, email="new@example.org", model="other", version="next")
    assert actor == changed
    assert hash(actor) == hash(changed)
    assert len({actor, changed}) == 1
    assert actor != replace(actor, name=actor.name + "x")
    assert actor != replace(actor, kind="agent" if actor.kind == "human" else "human")


@given(status=statuses(), inheritance=inheritances())
def test_enums_are_wire_strings(status, inheritance):
    assert Status(str(status)) is status
    assert Inheritance(str(inheritance)) is inheritance
    assert status in {"proposed", "active", "running", "done", "failed", "retired"}
    assert inheritance in {"both", "outgoing", "none"}


@given(error=rule_violations())
def test_rule_violation_retains_diagnostics(error):
    assert str(error) == error.message
    assert error.args == (error.message,)
    assert isinstance(error.rule, str)
    assert isinstance(error.ids, list)


@given(value=TIMESTAMPS)
def test_timestamp_roundtrip(value):
    text = format_timestamp(value)
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", text)
    assert parse_timestamp(text) == value
    offset = value.astimezone(timezone(timedelta(hours=5, minutes=30)))
    assert format_timestamp(offset) == text
    assert parse_timestamp(offset.isoformat()) == value
    assert parse_timestamp(offset.isoformat()).tzinfo is UTC


@pytest.mark.parametrize(
    "text",
    [
        "2026-10-19T14:06:19",
        "2026-10-19",
        "2026-10-19 14:06:19Z",
        "2026-10-19T14:06:19.001Z",
        "2026-10-19T14:06:19.000Z",
        "2026-10-19T14:06:60Z",
        "2026-10-19T14:06:19+24:00",
        "2026-10-19T14:06:19+00:60",
    ],
)
def test_timestamp_rejects_non_whole_or_unaware_forms(text):
    with pytest.raises(ValueError):
        parse_timestamp(text)


@pytest.mark.parametrize(
    "value",
    [
        datetime(2026, 10, 19),
        datetime(2026, 10, 19, microsecond=1, tzinfo=UTC),
    ],
)
def test_timestamp_format_rejects_lossy_values(value):
    with pytest.raises(ValueError):
        format_timestamp(value)


def test_nested_null_omission_and_top_level_null_retention():
    actor = Actor("human", "Ada")
    now = parse_timestamp("2026-10-19T14:06:19Z")
    approval = Approval(actor=actor, at=now, what="claim")
    node = Node(
        "ta-abcdefgh",
        "Task",
        "Run",
        "body",
        actor,
        now,
        approvals=[approval],
        budget={"tokens": None},
        usage={},
    )
    mapping = node.to_dict()
    assert mapping["budget"] is None and mapping["usage"] is None
    assert mapping["priority"] is None and mapping["lease"] is None
    assert mapping["author"] == {"kind": "human", "name": "Ada"}
    assert mapping["approvals"] == [
        {"actor": actor.to_dict(), "at": format_timestamp(now), "what": "claim"}
    ]
    assert mapping["updated"] == mapping["created"]
    assert Node.from_dict(mapping).updated == now


def test_collections_are_independent_and_mapping_conversion_is_detached():
    actor = Actor("human", "Ada")
    now = parse_timestamp("2026-10-19T14:06:19Z")
    first = Node("ta-abcdefgh", "Task", "First", "", actor, now)
    second = Node("ta-bcdefghi", "Task", "Second", "", actor, now)
    first.fields["nested"] = [1]
    first.tags.append("urgent")
    first.approvals.append(Approval(actor=actor, at=now, what="approve"))
    first.artifacts.append(Artifact("artifacts/ta-abcdefgh/a", "a" * 64, 1, now, actor))
    assert second.fields == {} and second.tags == []
    assert second.approvals == [] and second.artifacts == []
    mapping = first.to_dict()
    mapping["fields"]["nested"].append(2)
    assert first.fields["nested"] == [1]
    restored = Node.from_dict(mapping)
    mapping["fields"]["nested"].append(3)
    assert restored.fields["nested"] == [1, 2]


def test_immutable_key_contracts():
    assert NODE_IMMUTABLE_KEYS == frozenset(
        {"id", "type", "title", "body", "author", "created", "fields"}
    )
    assert EDGE_IMMUTABLE_KEYS == frozenset(
        {"id", "type", "from", "to", "author", "created", "fields"}
    )


@pytest.mark.parametrize("prefix", ["a", "abcde", "AB", "a2", "ab-", "éé"])
def test_id_rejects_invalid_prefix(prefix):
    with pytest.raises(ValueError):
        new_id(prefix, lambda candidate: False)


def test_id_retries_collision():
    seen = []

    def exists(candidate):
        seen.append(candidate)
        return len(seen) == 1

    with patch(
        "researchgraph.model.secrets.choice", side_effect=list("aaaaaaaa22222222")
    ):
        assert new_id("ta", exists) == "ta-22222222"
    assert seen == ["ta-aaaaaaaa", "ta-22222222"]


@pytest.mark.parametrize("prefix", ["ta", "abc", "abcd"])
def test_id_uses_lowercase_base32(prefix):
    generated = [new_id(prefix, lambda candidate: False) for _ in range(20)]
    assert all(re.fullmatch(r"[a-z]{2,4}-[a-z2-7]{8}", value) for value in generated)
    assert all(value.startswith(prefix + "-") for value in generated)
