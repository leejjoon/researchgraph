"""T13 public metadata operations, relation acceptance and invariant properties."""

import hashlib
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from researchgraph.model import EDGE_IMMUTABLE_KEYS, NODE_IMMUTABLE_KEYS, Actor
from researchgraph.profiles import Gate, NodeType, Profile
from researchgraph.profiles.research import TASK_SCHEMA
from researchgraph.project import Project
from researchgraph.store.io import load_node
from researchgraph.validation import validate_graph
from tests.helpers import ALICE, NOW, git, refusal, repository
from tests.test_cli import invoke, refused, succeeds
from tests.test_execution import artifact, cold, queries

BOT = Actor("agent", "agent1", model="m", version="1")
CALL = {"actor": ALICE, "now": NOW}
LATER = NOW + timedelta(seconds=1)


def source(project):
    history = project.path / ".researchgraph/history.jsonl"
    return (
        {
            path.relative_to(project.path).as_posix(): path.read_bytes()
            for path in (project.path / "graph").rglob("*.yaml")
        },
        history.read_bytes() if history.exists() else None,
        git(project.path, "rev-parse", "HEAD"),
        git(project.path, "status", "--porcelain"),
        git(project.path, "diff", "--cached", "--binary"),
    )


def unchanged(old, new, keys=NODE_IMMUTABLE_KEYS):
    assert {key: old[key] for key in keys} == {key: new[key] for key in keys}


def active_hypothesis(project, title="H"):
    value = project.propose("Hypothesis", title, **CALL)
    return project.approve(value["id"], **CALL)


def branch(project):
    h = active_hypothesis(project)
    p = project.propose(
        "Prediction",
        "P",
        edges=[{"type": "tests", "to": h["id"], "fields": {"expected": "signal"}}],
        **CALL,
    )
    t = project.propose(
        "Task", "T", edges=[{"type": "addresses", "to": p["id"]}], **CALL
    )
    return h["id"], p["id"], t["id"]


def metadata_project(
    path,
    *,
    task_schema=TASK_SCHEMA,
    task_type=True,
    gated=False,
    expansion_hook=None,
    hooks=(),
    validators=(),
):
    types = [NodeType("Note", "no", {})]
    if task_type:
        types.append(NodeType("Task", "ta", deepcopy(task_schema), claimable=True))
    Profile.register(
        Profile(
            "metadata",
            "1.0",
            types,
            [],
            [Gate(node_type="Task")] if gated else [],
            ["owner"],
            list(validators),
            expansion_hook=expansion_hook,
            event_hooks=list(hooks),
        )
    )
    return Project.init(repository(path), profile="metadata", **CALL)


def test_expand_acceptance_only_one_task_and_no_target_mutation(project, index_mode):
    h = active_hypothesis(project)
    before_nodes = {n.id: n.to_dict() for n in project.get_nodes()}
    before_edges = project.get_edges()
    targets = [h["id"]]
    cold(project, index_mode)
    value = project.expand(
        targets, "try two-component fit", actor=ALICE, role="owner", now=LATER
    )
    assert value == load_node(project.path / f"graph/nodes/{value['id']}.yaml")
    assert value["type"] == "Task" and value["status"] == "active"
    assert value["title"] == "Expand: H"
    assert value["body"] == "try two-component fit"
    assert value["fields"] == {"kind": "expansion", "targets": [h["id"]]}
    assert value["author"] == ALICE.to_dict()
    assert value["created"] == value["updated"] == "2026-10-20T00:00:01Z"
    assert value["approvals"] == [] and value["artifacts"] == []
    assert {n.id for n in project.get_nodes()} == {*before_nodes, value["id"]}
    for id_, node in before_nodes.items():
        assert project.get_node(id_).to_dict() == node
    assert project.get_edges() == before_edges
    assert value["id"] in {
        n["id"] for n in project.ready(actor=ALICE, now=LATER)["items"]
    }
    assert git(project.path, "log", "-1", "--format=%s") == f"rg: expand {value['id']}"
    targets.clear()
    value["fields"]["targets"].clear()
    assert project.get_node(value["id"]).fields["targets"] == [h["id"]]


@pytest.mark.parametrize("hint", [None, "", "first line\nsecond line"])
def test_expand_title_order_truncation_and_optional_hint(project, index_mode, hint):
    a = project.propose("Task", "a" * 195, **CALL)
    b = project.propose("Task", "b" * 20, **CALL)
    cold(project, index_mode)
    value = project.expand([b["id"], a["id"]], hint, **CALL)
    assert value["title"] == ("Expand: " + b["title"] + ", " + a["title"])[:200]
    assert len(value["title"]) == 200
    assert value["body"] == (hint or "")
    assert value["fields"]["targets"] == [b["id"], a["id"]]


def test_expand_empty_unknown_reference_and_human_only_are_atomic(project, index_mode):
    target = project.propose("Task", "T", **CALL)["id"]
    edge = project.link(
        "part-of", target, project.propose("Task", "parent", **CALL)["id"], **CALL
    )
    cold(project, index_mode)
    for rule, nodes, actor in [
        ("no_targets", [], ALICE),
        ("not_found", [target, "ta-zzzzzzzz"], ALICE),
        ("not_found", [edge["id"]], ALICE),
        ("human_only_approval", [target], BOT),
    ]:
        before = source(project)
        refusal(rule, project.expand, nodes, actor=actor, now=NOW)
        assert source(project) == before


@pytest.mark.parametrize(
    "schema,declared",
    [
        ({}, True),
        ({"properties": {"kind": {"type": "string"}}}, True),
        ({"properties": {"targets": {"type": "array"}}}, True),
        (
            {"properties": {"kind": {"enum": ["run"]}, "targets": {"type": "array"}}},
            True,
        ),
        (
            {
                "properties": {
                    "kind": {"type": "string"},
                    "targets": {"type": "integer"},
                }
            },
            True,
        ),
        (TASK_SCHEMA, False),
    ],
)
def test_expand_reserved_vocabulary_refusal(tmp_path, index_mode, schema, declared):
    with metadata_project(tmp_path, task_schema=schema, task_type=declared) as project:
        target = project.propose("Note", "target", **CALL)["id"]
        cold(project, index_mode)
        before = source(project)
        refusal("unsupported_by_profile", project.expand, [target], **CALL)
        assert source(project) == before


def test_expand_empty_targets_remain_operation_error_with_minimum_items(
    tmp_path, index_mode
):
    schema = deepcopy(TASK_SCHEMA)
    schema["properties"]["targets"]["minItems"] = 1
    with metadata_project(tmp_path, task_schema=schema) as project:
        target = project.propose("Note", "target", **CALL)["id"]
        assert project.expand([target], **CALL)["fields"]["targets"] == [target]
        cold(project, index_mode)
        before = source(project)
        refusal("no_targets", project.expand, [], **CALL)
        assert source(project) == before


def test_expand_gate_and_never_calls_expansion_hook(tmp_path, index_mode):
    calls = []

    def forbidden(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("Core must never execute the expansion hook")

    with metadata_project(tmp_path, gated=True, expansion_hook=forbidden) as project:
        target = project.propose("Note", "target", **CALL)["id"]
        cold(project, index_mode)
        value = project.expand([target], **CALL)
        assert value["status"] == "proposed" and value["body"] == ""
        assert value["id"] not in {n["id"] for n in project.ready(**CALL)["items"]}
        assert calls == []


def test_budget_exact_replacement_clearing_events_and_no_alias(project, index_mode):
    h = active_hypothesis(project)
    cold(project, index_mode)
    for seconds, arguments, expected, note in [
        (1, {"tokens": 1000}, {"tokens": 1000}, "tokens=1000"),
        (
            2,
            {"tokens": 1000, "compute_seconds": 10},
            {"tokens": 1000, "compute_seconds": 10},
            "tokens=1000 compute_seconds=10",
        ),
        (3, {"tokens": 2000}, {"tokens": 2000}, "tokens=2000"),
        (4, {"compute_seconds": 20}, {"compute_seconds": 20}, "compute_seconds=20"),
        (5, {}, None, "none"),
        (6, {"tokens": None, "compute_seconds": None}, None, "none"),
    ]:
        previous = project.get_node(h["id"]).to_dict()
        now = NOW + timedelta(seconds=seconds)
        value = project.budget(h["id"], actor=ALICE, role="owner", now=now, **arguments)
        unchanged(previous, value)
        assert value["budget"] == expected
        assert value["approvals"][:-1] == previous["approvals"]
        assert value["approvals"][-1] == {
            "actor": ALICE.to_dict(),
            "role": "owner",
            "at": value["updated"],
            "what": "budget",
            "note": note,
        }
        assert value == load_node(project.path / f"graph/nodes/{h['id']}.yaml")
        assert project.where(over_budget=True, actor=ALICE, now=now) == []
        assert git(project.path, "log", "-1", "--format=%s") == f"rg: budget {h['id']}"
        if value["budget"] is not None:
            value["budget"]["tokens"] = 99999
            assert project.get_node(h["id"]).budget == expected


@pytest.mark.parametrize("dimension", ["tokens", "compute_seconds"])
@pytest.mark.parametrize(
    "value,rule",
    [
        (-1, "negative"),
        (True, "schema"),
        (False, "schema"),
        (1.5, "schema"),
        ("2", "schema"),
        ({}, "schema"),
    ],
)
def test_budget_invalid_values_are_atomic(project, index_mode, dimension, value, rule):
    target = project.propose("Task", "T", **CALL)["id"]
    cold(project, index_mode)
    before = source(project)
    refusal(rule, project.budget, target, **{dimension: value}, **CALL)
    assert source(project) == before


def test_budget_human_only(project, index_mode):
    target = project.propose("Task", "T", **CALL)["id"]
    cold(project, index_mode)
    before = source(project)
    refusal("human_only_approval", project.budget, target, tokens=1, actor=BOT, now=NOW)
    assert source(project) == before


@pytest.mark.parametrize("dimension", ["tokens", "compute_seconds"])
@pytest.mark.parametrize("limit,spent", [(0, 0), (1000, 1000), (1000, 1200)])
def test_budget_inclusive_limits_actual_usage_and_ancestor_exclusion(
    project, index_mode, dimension, limit, spent
):
    h, p, t = branch(project)
    sibling = project.propose("Task", "sibling", **CALL)["id"]
    project.link("addresses", sibling, p, **CALL)
    unrelated = project.propose("Task", "outside", **CALL)["id"]
    if spent:
        project.claim(t, actor=BOT, now=NOW)
        project.release(t, usage={dimension: spent}, actor=BOT, now=NOW)
    cold(project, index_mode)
    project.budget(h, **{dimension: limit}, **CALL)
    assert project.show(h, **CALL)["flags"]["over_budget"]
    assert {n["id"] for n in project.where(over_budget=True, **CALL)} == {h}
    ready = {n["id"] for n in project.ready(**CALL)["items"]}
    assert not {t, sibling} & ready and unrelated in ready
    refusal("not_ready", project.claim, t, actor=BOT, now=NOW)
    assert {n["id"] for n in project.subtree(h, **CALL)["nodes"]} == {h, p, t, sibling}
    project.budget(h, **CALL)
    assert {t, sibling} <= {n["id"] for n in project.ready(**CALL)["items"]}


def test_zero_both_budget_dimensions_stop_unspent_descendants(project, index_mode):
    h, _, t = branch(project)
    cold(project, index_mode)
    value = project.budget(h, tokens=0, compute_seconds=0, **CALL)
    assert value["budget"] == {"tokens": 0, "compute_seconds": 0}
    assert value["approvals"][-1]["note"] == "tokens=0 compute_seconds=0"
    assert project.get_node(t).usage is None
    assert project.show(h, **CALL)["flags"]["over_budget"]
    assert t not in {n["id"] for n in project.ready(**CALL)["items"]}
    refusal("not_ready", project.claim, t, actor=BOT, now=NOW)


def test_budget_retired_addresses_usage_survives_supersession(project, index_mode):
    h, p, t = branch(project)
    project.budget(h, tokens=1000, **CALL)
    project.claim(t, actor=BOT, now=NOW)
    project.complete(t, "spent", usage={"tokens": 1200}, actor=BOT, now=NOW)
    cold(project, index_mode)
    replacement = project.supersede(t, actor=BOT, now=LATER)
    call = {"actor": ALICE, "now": LATER}
    assert project.show(h, **call)["flags"]["over_budget"]
    assert not project.show(replacement["id"], **call)["flags"]["ready"]
    tree = project.subtree(h, **call)
    assert {n["id"] for n in tree["nodes"]} == {h, p, replacement["id"]}
    assert tree["usage"] == {"tokens": 1200, "compute_seconds": 0}
    assert tree["status_counts"] == {"active": 2, "proposed": 1}
    assert any(
        e.type == "addresses" and e.from_ == t and e.retired
        for e in project.get_edges()
    )
    refusal("not_ready", project.claim, replacement["id"], actor=BOT, now=LATER)


def test_accounting_diamond_never_double_counts(project, index_mode):
    h, p, t = branch(project)
    project.link("part-of", t, h, **CALL)
    project.claim(t, actor=BOT, now=NOW)
    project.complete(
        t, "spent", usage={"tokens": 600, "compute_seconds": 3}, actor=BOT, now=NOW
    )
    cold(project, index_mode)
    project.budget(h, tokens=1000, compute_seconds=6, **CALL)
    assert project.subtree(h, **CALL)["usage"] == {"tokens": 600, "compute_seconds": 3}
    assert not project.show(h, **CALL)["flags"]["over_budget"]
    assert {n["id"] for n in project.subtree(p, **CALL)["nodes"]} == {p, t}


def test_tag_sorted_unique_where_priority_omitted_null_updated_only(
    project, index_mode
):
    a = project.propose("Task", "A", **CALL)
    b = project.propose("Task", "B", **CALL)
    project.claim(b["id"], actor=BOT, now=NOW)
    original = project.release(b["id"], actor=BOT, now=NOW)
    cold(project, index_mode)
    changed = project.tag(
        b["id"], add=["z", "needs-review", "z", "a"], priority=5, actor=BOT, now=LATER
    )
    assert changed["tags"] == ["a", "needs-review", "z"]
    assert changed["priority"] == 5
    assert changed["updated"] == "2026-10-20T00:00:01Z"
    assert {
        k: v for k, v in changed.items() if k not in {"tags", "priority", "updated"}
    } == {k: v for k, v in original.items() if k not in {"tags", "priority", "updated"}}
    call = {"actor": ALICE, "now": LATER}
    assert [n["id"] for n in project.ready(**call)["items"]] == [b["id"], a["id"]]
    assert [n["id"] for n in project.where(tag="needs-review", **call)] == [b["id"]]
    changed = project.tag(
        b["id"], remove=["needs-review", "absent"], actor=BOT, now=LATER
    )
    assert changed["priority"] == 5 and changed["tags"] == ["a", "z"]
    assert project.where(tag="needs-review", **call) == []
    changed = project.tag(b["id"], priority=None, actor=ALICE, now=LATER)
    assert changed["priority"] is None
    assert changed["approvals"] == original["approvals"]
    assert [n["id"] for n in project.ready(**call)["items"]] == sorted(
        [a["id"], b["id"]]
    )


@pytest.mark.parametrize(
    "arguments",
    [
        {"priority": True},
        {"priority": 1.5},
        {"priority": "1"},
        {"add": "tag"},
        {"add": [1]},
        {"remove": "tag"},
        {"remove": [None]},
    ],
)
def test_tag_invalid_metadata_is_atomic(project, arguments):
    target = project.propose("Task", "T", **CALL)["id"]
    before = source(project)
    refusal("schema", project.tag, target, **arguments, **CALL)
    assert source(project) == before


@pytest.mark.parametrize(
    "status", ["proposed", "active", "running", "done", "failed", "retired"]
)
def test_attach_every_status_except_retired(project, index_mode, status):
    type_ = "Hypothesis" if status == "proposed" else "Task"
    target = project.propose(type_, "target", actor=BOT, now=NOW)["id"]
    if status in {"running", "done", "failed"}:
        project.claim(target, actor=BOT, now=NOW)
        if status == "done":
            project.complete(target, "done", actor=BOT, now=NOW)
        elif status == "failed":
            project.fail(target, "bad", actor=BOT, now=NOW)
    elif status == "retired":
        project.retire(target, "stop", **CALL)
    relative = artifact(project, target, content=b"first")
    previous = project.get_node(target).to_dict()
    cold(project, index_mode)
    if status == "retired":
        for actor in [ALICE, BOT]:
            before = source(project)
            refusal(
                "terminal_status",
                project.attach,
                target,
                [relative],
                actor=actor,
                now=LATER,
            )
            assert source(project) == before
    else:
        value = project.attach(target, [relative], actor=BOT, now=LATER)
        unchanged(previous, value)
        assert value["status"] == status
        assert value["lease"] == previous["lease"]
        assert value["approvals"] == previous["approvals"]
        assert value["artifacts"][-1]["path"] == relative


def test_attach_real_bytes_repeated_path_since_and_scoped_commit(project, index_mode):
    target = project.propose("Result", "R1", actor=BOT, now=NOW)["id"]
    data = b"a" * (2 * 1024 * 1024) + b"\x00\xfflast bytes"
    relative = artifact(project, target, "fig2.png", data)
    unnamed = artifact(project, target, "not-selected", b"untouched")
    (project.path / "unrelated").write_text("staged", encoding="utf-8")
    git(project.path, "add", "unrelated")
    cold(project, index_mode)
    first = project.attach(target, [relative], actor=BOT, now=LATER)
    expected = {
        "path": relative,
        "sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
        "added_at": "2026-10-20T00:00:01Z",
        "added_by": BOT.to_dict(),
    }
    assert first["artifacts"] == [expected]
    assert first == load_node(project.path / f"graph/nodes/{target}.yaml")
    assert git(project.path, "log", "-1", "--format=%s") == f"rg: attach {target}"
    paths = set(
        git(
            project.path, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"
        ).splitlines()
    )
    assert paths == {relative, f"graph/nodes/{target}.yaml"}
    assert git(project.path, "diff", "--cached", "--name-only") == "unrelated"
    assert (project.path / relative).read_bytes() == data
    assert (project.path / unnamed).read_bytes() == b"untouched"
    assert [n["id"] for n in project.since(NOW, actor=ALICE, now=LATER)] == [target]
    artifact(project, target, "fig2.png", b"replacement")
    now = NOW + timedelta(seconds=2)
    second = project.attach(target, [relative], actor=ALICE, now=now)
    assert second["artifacts"][0] == expected and len(second["artifacts"]) == 2
    assert (
        second["artifacts"][1]["sha256"] == hashlib.sha256(b"replacement").hexdigest()
    )
    assert second["artifacts"][1]["bytes"] == 11
    assert second["artifacts"][1]["added_by"] == ALICE.to_dict()
    second["artifacts"][0]["bytes"] = 0
    assert project.get_node(target).to_dict()["artifacts"][0] == expected


def test_done_task_accepts_second_artifact_in_addition_order(project, index_mode):
    target = project.propose("Task", "T", **CALL)["id"]
    first = artifact(project, target, "first", b"one")
    second = artifact(project, target, "second", b"two")
    project.claim(target, actor=BOT, now=NOW)
    project.complete(target, "done", [first], actor=BOT, now=NOW)
    cold(project, index_mode)
    value = project.attach(target, [second], actor=ALICE, now=LATER)
    assert [a["path"] for a in value["artifacts"]] == [first, second]
    assert value["status"] == "done"


@pytest.mark.parametrize(
    "bad",
    [
        "data/x",
        "../x",
        "/tmp/x",
        "artifacts/{other}/x",
        "artifacts/{id}/../x",
        "artifacts/{id}/./x",
        "artifacts/{id}//x",
        "artifacts/{id}/x\\y",
    ],
)
def test_attach_bad_path_precedes_missing_and_multifile_atomicity(
    project, index_mode, bad
):
    target = project.propose("Task", "T", **CALL)["id"]
    other = project.propose("Task", "other", **CALL)["id"]
    good = artifact(project, target)
    cold(project, index_mode)
    before = source(project)
    refusal(
        "artifact_path",
        project.attach,
        target,
        [good, f"artifacts/{target}/missing", bad.format(id=target, other=other)],
        actor=BOT,
        now=NOW,
    )
    assert source(project) == before


@pytest.mark.parametrize(
    "kind,rule",
    [
        ("missing", "artifact_missing"),
        ("directory", "artifact_missing"),
        ("symlink", "artifact_path"),
        ("parent_symlink", "artifact_path"),
    ],
)
def test_attach_missing_symlink_and_atomic_multi_file_failure(
    project, index_mode, kind, rule
):
    target = project.propose("Task", "T", **CALL)["id"]
    good = artifact(project, target, "good")
    relative = f"artifacts/{target}/nested/bad"
    path = project.path / relative
    path.parent.mkdir()
    if kind == "directory":
        path.mkdir()
    elif kind == "symlink":
        path.symlink_to(project.path / "absent")
    elif kind == "parent_symlink":
        path.parent.rmdir()
        path.parent.symlink_to(project.path / "graph", target_is_directory=True)
    cold(project, index_mode)
    before = source(project)
    refusal(rule, project.attach, target, [good, relative], actor=BOT, now=NOW)
    assert source(project) == before
    assert (project.path / good).read_bytes() == b"hello"


def test_note_about_preserves_target_and_newest_order(project, index_mode):
    h = active_hypothesis(project)
    first = project.propose(
        "Note", "older", edges=[{"type": "about", "to": h["id"]}], actor=BOT, now=NOW
    )
    cold(project, index_mode)
    second = project.propose(
        "Note",
        "looks degenerate with H2",
        edges=[{"type": "about", "to": h["id"]}],
        actor=BOT,
        now=LATER,
    )
    assert [n["id"] for n in project.notes(h["id"], actor=ALICE, now=LATER)] == [
        second["id"],
        first["id"],
    ]
    assert project.get_node(h["id"]).to_dict() == h


def test_pins_require_recorded_artifact_on_target_result(project, index_mode):
    h = active_hypothesis(project)["id"]
    result = project.propose("Result", "R", **CALL)["id"]
    other = project.propose("Result", "other", **CALL)["id"]
    relative = artifact(project, result, "fig2.png", b"figure")
    other_path = artifact(project, other, "other.png", b"other")
    project.attach(other, [other_path], **CALL)
    cold(project, index_mode)
    before = source(project)
    # Existing bytes alone are not a recorded Result artifact.
    refusal(
        "schema", project.link, "pins", h, result, fields={"artifact": relative}, **CALL
    )
    assert source(project) == before
    project.attach(result, [relative], **CALL)
    pin = project.link("pins", h, result, fields={"artifact": relative}, **CALL)
    assert pin in project.show(h, **CALL)["pins"]
    for path in [other_path, f"artifacts/{result}/unrecorded"]:
        candidate = project.propose("Hypothesis", "another pin source", **CALL)["id"]
        before = source(project)
        refusal(
            "schema",
            project.link,
            "pins",
            candidate,
            result,
            fields={"artifact": path},
            **CALL,
        )
        assert source(project) == before
    before = source(project)
    refusal(
        "schema",
        project.propose,
        "Hypothesis",
        "bad pin",
        edges=[{"type": "pins", "to": result, "fields": {"artifact": other_path}}],
        **CALL,
    )
    assert source(project) == before


def test_part_of_cycles_and_actual_subtree_accounting(project, index_mode):
    q1 = project.propose("Question", "Q1", **CALL)["id"]
    q2 = project.propose(
        "Question", "Q2", edges=[{"type": "part-of", "to": q1}], **CALL
    )["id"]
    h, p, t = branch(project)
    project.link("part-of", h, q2, **CALL)
    project.claim(t, actor=BOT, now=NOW)
    project.complete(t, "spent", usage={"tokens": 1200}, actor=BOT, now=NOW)
    cold(project, index_mode)
    tree = project.subtree(q1, **CALL)
    assert {n["id"] for n in tree["nodes"]} == {q1, q2, h, p, t}
    assert tree["usage"] == {"tokens": 1200, "compute_seconds": 0}
    assert tree["status_counts"] == {"active": 3, "done": 1, "proposed": 1}
    before = source(project)
    refusal("cycle", project.link, "part-of", q1, q2, **CALL)
    assert source(project) == before
    project.budget(h, tokens=1000, **CALL)
    assert h in {n["id"] for n in project.where(over_budget=True, **CALL)}
    assert not {n["id"] for n in project.subtree(h, **CALL)["nodes"]} & {
        n["id"] for n in project.ready(**CALL)["items"]
    }


@pytest.mark.parametrize("operation", ["expand", "budget", "tag", "attach"])
def test_metadata_shared_preflight_and_read_only_rollback(project, operation):
    target = project.propose("Task", "T", **CALL)["id"]
    relative = artifact(project, target)
    args = {
        "expand": ([target],),
        "budget": (target,),
        "tag": (target,),
        "attach": (target, [relative]),
    }[operation]
    fn = getattr(project, operation)
    before = source(project)
    refusal("wrong_role", fn, *args, actor=ALICE, role="unknown", now=NOW)
    project.read_only = True
    refusal("read_only", fn, *args, actor=ALICE, now=NOW - timedelta(seconds=1))
    project.read_only = False
    refusal("clock_skew", fn, *args, actor=ALICE, now=NOW - timedelta(seconds=1))
    assert source(project) == before


@pytest.mark.parametrize("operation", ["expand", "budget", "tag", "attach"])
@pytest.mark.parametrize("commit", [True, False])
def test_metadata_callbacks_and_no_commit_contract(tmp_path, operation, commit):
    events, validations = [], []
    with metadata_project(
        tmp_path,
        hooks=[lambda p, w, c: events.append((w.op, c))],
        validators=[lambda p, w: validations.append(w.op)],
    ) as project:
        target = project.propose("Task", "T", **CALL)["id"]
        relative = artifact(project, target)
        events.clear()
        validations.clear()
        head = git(project.path, "rev-parse", "HEAD")
        args = {
            "expand": ([target],),
            "budget": (target, 20),
            "tag": (target,),
            "attach": (target, [relative]),
        }[operation]
        kwargs = {"add": ["review"]} if operation == "tag" else {}
        value = getattr(project, operation)(*args, **kwargs, **CALL, commit=commit)
        assert validations == [operation]
        assert value == load_node(project.path / f"graph/nodes/{value['id']}.yaml")
        if commit:
            assert events == [(operation, git(project.path, "rev-parse", "HEAD"))]
        else:
            assert events == [] and git(project.path, "rev-parse", "HEAD") == head


@pytest.mark.parametrize("operation", ["expand", "budget", "tag", "attach"])
def test_metadata_midwrite_failure_rolls_back_without_event(
    project, operation, monkeypatch
):
    from researchgraph.store import transaction

    target = project.propose("Task", "T", **CALL)["id"]
    first = artifact(project, target, "first", b"first")
    second = artifact(project, target, "second", b"second")
    events = []
    project.profile.event_hooks.append(lambda *args: events.append(args))
    args = {
        "expand": ([target],),
        "budget": (target, 20),
        "tag": (target,),
        "attach": (target, [first, second]),
    }[operation]
    kwargs = {"add": ["review"]} if operation == "tag" else {}
    before = source(project)
    replace = transaction.os.replace
    calls = 0

    def fail_once(source_path, destination):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("injected metadata rename failure")
        return replace(source_path, destination)

    monkeypatch.setattr(transaction.os, "replace", fail_once)
    refusal("write_failed", getattr(project, operation), *args, **kwargs, **CALL)
    assert source(project) == before
    assert events == []
    assert (project.path / first).read_bytes() == b"first"
    assert (project.path / second).read_bytes() == b"second"


@pytest.mark.parametrize("operation", ["expand", "budget", "tag", "attach"])
@settings(max_examples=6, deadline=None)
@given(
    st.lists(
        st.tuples(st.booleans(), st.booleans(), st.integers(0, 20)),
        min_size=2,
        max_size=5,
    )
)
def test_each_metadata_operation_i1_i2_i7_i8(operation, actions):
    with (
        TemporaryDirectory() as directory,
        Project.init(repository(Path(directory)), **CALL) as project,
    ):
        ids = {
            type_[0].lower(): project.propose(type_, type_, **CALL)["id"]
            for type_ in ["Question", "Hypothesis", "Claim"]
        }
        result = project.propose("Result", "R", **CALL)["id"]
        project.link("answers", ids["h"], ids["q"], **CALL)
        project.link("cites", ids["c"], result, **CALL)
        target = project.propose("Task", "T", **CALL)["id"]
        project.link("part-of", target, ids["h"], **CALL)
        project.claim(target, actor=BOT, now=NOW)
        project.release(target, usage={"tokens": 1}, actor=BOT, now=NOW)
        relative = artifact(project, target)
        for i, (agent, commit, amount) in enumerate(actions):
            now = NOW + timedelta(seconds=i + 1)
            actor = BOT if agent else ALICE
            previous_nodes = {n.id: n.to_dict() for n in project.get_nodes()}
            previous_edges = {e.id: e.to_dict() for e in project.get_edges()}
            before = source(project)
            args = {
                "expand": ([target],),
                "budget": (target, amount),
                "tag": (target,),
                "attach": (target, [relative]),
            }[operation]
            kwargs = (
                {"priority": amount, "add": [str(amount), str(amount)]}
                if operation == "tag"
                else {}
            )
            if agent and operation in {"expand", "budget"}:
                refusal(
                    "human_only_approval",
                    getattr(project, operation),
                    *args,
                    **kwargs,
                    actor=actor,
                    now=now,
                    commit=commit,
                )
                assert source(project) == before
            else:
                value = getattr(project, operation)(
                    *args, **kwargs, actor=actor, now=now, commit=commit
                )
                assert value["id"] in {n.id for n in project.get_nodes()}
            nodes = {n.id: n for n in project.get_nodes()}
            edges = {e.id: e for e in project.get_edges()}
            validate_graph(project.profile, nodes, edges)
            assert previous_nodes.keys() <= nodes.keys()
            assert previous_edges.keys() <= edges.keys()
            for id_, old in previous_nodes.items():
                new = nodes[id_].to_dict()
                unchanged(old, new)
                for key in ["approvals", "artifacts"]:
                    assert new[key][: len(old[key])] == old[key]
                if operation in {"tag", "attach", "expand"}:
                    assert new["approvals"] == old["approvals"]
            for id_, old in previous_edges.items():
                new = edges[id_].to_dict()
                unchanged(old, new, EDGE_IMMUTABLE_KEYS)
                assert new == old
            # Exercise a permission failure for every generated operation, even
            # tag and attach whose actor permissions allow both kinds.
            before = source(project)
            project.read_only = True
            refusal(
                "read_only",
                getattr(project, operation),
                *args,
                **kwargs,
                actor=actor,
                now=now,
                commit=commit,
            )
            project.read_only = False
            assert source(project) == before
            times = [None, NOW, now, git(project.path, "rev-parse", "HEAD")]
            kept = [queries(project, ids, now, time) for time in times]
            cold(project)
            assert [queries(project, ids, now, time) for time in times] == kept


def test_metadata_cli_commands_share_public_output_and_priority_omission(
    project, index_mode
):
    target = project.propose("Task", "T", **CALL)["id"]
    relative = artifact(project, target)
    cold(project, index_mode)
    expanded = succeeds(
        invoke(project.path, "expand", target, "--hint", "try two models")
    )
    assert expanded["fields"] == {"kind": "expansion", "targets": [target]}
    assert expanded["body"] == "try two models"
    budgeted = succeeds(
        invoke(
            project.path, "budget", target, "--tokens", "5", "--compute-seconds", "3"
        )
    )
    assert budgeted["budget"] == {"tokens": 5, "compute_seconds": 3}
    assert succeeds(invoke(project.path, "budget", target))["budget"] is None
    tagged = succeeds(
        invoke(
            project.path,
            "tag",
            target,
            "--add",
            "z",
            "--add",
            "a",
            "--add",
            "z",
            "--priority",
            "7",
            agent="agent1:m:1",
        )
    )
    assert tagged["tags"] == ["a", "z"] and tagged["priority"] == 7
    omitted = succeeds(
        invoke(project.path, "tag", target, "--remove", "a", agent="agent1")
    )
    assert omitted["tags"] == ["z"] and omitted["priority"] == 7
    cleared = succeeds(invoke(project.path, "tag", target, "--priority", "null"))
    assert cleared["priority"] is None and cleared["approvals"] == tagged["approvals"]
    attached = succeeds(
        invoke(
            project.path, "attach", target, "--artifact", relative, agent="agent1:m:1"
        )
    )
    assert attached["artifacts"][-1]["path"] == relative
    assert attached == project.get_node(target).to_dict()
    assert set(attached) == set(project.propose("Task", "shape", **CALL))


@pytest.mark.parametrize(
    "command,options,agent,rule",
    [
        ("expand", [], "agent1", "human_only_approval"),
        ("budget", ["--tokens", "5"], "agent1", "human_only_approval"),
        ("budget", ["--tokens", "-1"], None, "negative"),
        ("attach", ["--artifact", "data/missing"], "agent1", "artifact_path"),
    ],
)
def test_metadata_cli_structured_errors_are_atomic(
    project, index_mode, command, options, agent, rule
):
    target = project.propose("Task", "T", **CALL)["id"]
    cold(project, index_mode)
    before = source(project)
    refused(invoke(project.path, command, target, *options, agent=agent), rule)
    assert source(project) == before
