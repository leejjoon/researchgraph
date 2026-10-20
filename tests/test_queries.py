"""Derived reads over validated, restored stores, without operation builders."""

import json
from copy import deepcopy
from datetime import timedelta

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from researchgraph.model import (
    Approval,
    Artifact,
    Edge,
    Lease,
    Node,
    Status,
    Write,
    format_timestamp,
)
from researchgraph.project import Project
from researchgraph.store.io import dump_edge
from tests.helpers import ALICE, NOW, git, refusal, repository
from tests.strategies import query_nodes


def record(kind, letter, **values):
    prefixes = {
        "Task": "ta",
        "Hypothesis": "hy",
        "Question": "qu",
        "Claim": "cl",
        "Prediction": "pr",
        "Result": "re",
        "Reference": "rf",
        "Note": "no",
        "Instruction": "in",
    }
    return Node(
        prefixes[kind] + "-" + letter * 8,
        kind,
        kind + " " + letter,
        "",
        ALICE,
        NOW,
        status=Status.ACTIVE,
        **values,
    )


def relation(letter, kind, source, target, **values):
    return Edge("ed-" + letter * 8, kind, source.id, target.id, ALICE, NOW, **values)


def restore(tmp_path, nodes, edges=(), profile="research", now=NOW):
    project = Project.init(
        repository(tmp_path / "project"), profile, actor=ALICE, now=NOW
    )
    project.apply(Write("import", ALICE, None, now, {}, list(nodes), list(edges)))
    return project


def call(project, name, *args, now=NOW, **kwargs):
    return getattr(project, name)(*args, actor=ALICE, now=now, **kwargs)


def ids(rows):
    return [row["id"] for row in rows]


def test_ready_blocked_queue_show_and_order(tmp_path):
    a, b, c, d = [record("Task", x) for x in "abcd"]
    a.priority, b.priority, c.priority = 1, 2, 2
    c.status = Status.RUNNING
    c.lease = Lease(ALICE, NOW - timedelta(seconds=10), NOW)
    d.status = Status.RUNNING
    d.lease = Lease(ALICE, NOW, NOW + timedelta(seconds=10))
    h = record("Hypothesis", "a")
    h.status = Status.PROPOSED
    note = record("Note", "a")
    block = relation("a", "blocks", a, b)
    project = restore(tmp_path, [a, b, c, d, h, note], [block])
    assert ids(call(project, "ready")["items"]) == [c.id, a.id]
    assert call(project, "blocked")["items"][0]["blocking"] == [a.id]
    queued = call(project, "queue")["items"]
    assert ids(queued) == [h.id]
    assert queued[0]["gate"]["roles"] == ["human"]
    shown = call(project, "show", c.id)
    assert shown["flags"]["ready"] and shown["flags"]["lease_expired"]
    assert call(project, "show", block.id) == block.to_dict()
    # Read evaluation times need not obey the write clock floor.
    assert ids(call(project, "ready", now=NOW - timedelta(seconds=1))["items"]) == [
        a.id
    ]


def test_evidence_stale_weakest_and_replacement_heads(tmp_path):
    c = record("Claim", "a")
    r0, r1, r2, s0, s1 = [record("Result", x) for x in "abcde"]
    r0.status = r1.status = s0.status = Status.RETIRED
    r0.fields, s0.fields = {"runs": 3}, {"runs": 1}
    good = record("Result", "f", fields={"runs": 1, "held_out": True})
    edges = [
        relation("a", "cites", c, r0),
        relation("b", "cites", c, r1),
        relation("c", "cites", c, s0),
        relation("d", "cites", c, good),
        relation("e", "derived-from", good, r0),
        relation("f", "supersedes", r1, r0),
        relation("g", "supersedes", r2, r1),
        relation("h", "supersedes", s1, s0),
    ]
    project = restore(tmp_path, [c, r0, r1, r2, s0, s1, good], edges)
    assert set(ids(call(project, "evidence", c.id)["items"])) == {
        r0.id,
        r1.id,
        s0.id,
        good.id,
    }
    assert call(project, "stale")["items"][0]["responsible"] == sorted([r2.id, s1.id])
    assert ids(call(project, "weakest", c.id)) == [r1.id, s0.id, good.id, r0.id]


def test_groups_predictions_matrix_and_all_source_edges(tmp_path):
    q, q2 = record("Question", "a"), record("Question", "b")
    a, b, outside = [record("Hypothesis", x) for x in "abc"]
    p, single = record("Prediction", "a"), record("Task", "a")
    c = record("Claim", "a")
    edges = [
        relation("a", "answers", a, q),
        relation("b", "answers", b, q),
        relation("c", "answers", a, q2),
        relation("d", "answers", outside, q2),
        relation("e", "tests", p, a, fields={"expected": "yes"}),
        relation("f", "tests", p, b, fields={"expected": "yes"}),
        relation("g", "tests", p, outside, fields={"expected": "no"}),
        relation("h", "tests", single, a, fields={"expected": "yes"}),
        relation("i", "supports", c, b),
    ]
    project = restore(tmp_path, [q, q2, a, b, outside, p, single, c], edges)
    alternatives = call(project, "alternatives", a.id)
    assert ids(alternatives) == [b.id, outside.id]
    assert ids(alternatives[0]["supports"]) == [c.id]
    predictions = call(project, "predictions", a.id)
    assert [row["source"]["id"] for row in predictions] == [p.id, single.id]
    assert all(row["expected"] == "yes" for row in predictions)
    matrix = call(project, "matrix", q.id)
    assert ids(matrix["columns"]) == [a.id, b.id]
    assert [row["source"]["id"] for row in matrix["rows"]] == [p.id, single.id]
    assert not any(row["non_discriminating"] for row in matrix["rows"])
    assert call(project, "show", p.id)["warnings"] == []


def test_notes_pins_instructions_filters_and_since(tmp_path):
    h, n, instruction = (
        record("Hypothesis", "a"),
        record("Note", "a"),
        record("Instruction", "a"),
    )
    r = record("Result", "a")
    r.artifacts = [Artifact(f"artifacts/{r.id}/figure.png", "a" * 64, 3, NOW, ALICE)]
    task = record("Task", "a", fields={"kind": "expansion"}, tags=["x"])
    task.title = "FIT model"
    task.approvals = [Approval(ALICE, NOW, "approve")]
    edges = [
        relation("a", "about", n, h),
        relation("b", "instructs", instruction, h),
        relation("c", "pins", h, r, fields={"artifact": r.artifacts[0].path}),
        relation("d", "relates-to", task, h, retired=True, retired_at=NOW),
    ]
    project = restore(tmp_path, [h, n, instruction, r, task], edges)
    assert ids(call(project, "notes", h.id)) == [n.id]
    shown = call(project, "show", h.id)
    assert ids(shown["instructions"]) == [instruction.id]
    assert ids(shown["pins"]) == [edges[2].id]
    assert len(shown["edges"]) == 3
    assert ids(
        call(
            project,
            "where",
            type="Task",
            status="active",
            tag="x",
            text="fit",
            author="alice",
            field="kind=expansion",
        )
    ) == [task.id]
    assert call(project, "where", type="Task", text="missing") == []
    changes = call(project, "since", NOW - timedelta(seconds=1))
    assert set(ids(changes)) == {x.id for x in [h, n, instruction, r, task, *edges]}
    assert call(project, "since", NOW) == []


def test_retired_accounting_progress_and_budget_boundary(tmp_path):
    h, p, old, new = (
        record("Hypothesis", "a"),
        record("Prediction", "a"),
        record("Task", "a"),
        record("Task", "b"),
    )
    h.budget = {"tokens": 10}
    old.status, old.usage = Status.RETIRED, {"tokens": 10}
    edges = [
        relation("a", "tests", p, h, fields={"expected": "x"}),
        relation("b", "addresses", old, p, retired=True, retired_at=NOW),
        relation("c", "addresses", new, p),
    ]
    project = restore(tmp_path, [h, p, old, new], edges)
    subtree = call(project, "subtree", h.id)
    assert set(ids(subtree["nodes"])) == {h.id, p.id, new.id}
    assert subtree["usage"] == {"tokens": 10, "compute_seconds": 0}
    assert subtree["status_counts"] == {"active": 3}
    assert ids(call(project, "where", over_budget=True)) == [h.id]
    assert call(project, "ready")["items"] == []


@pytest.mark.parametrize("deleted", [False, True])
@pytest.mark.parametrize(
    "fields",
    [
        ["kind=missing", "kind=expansion"],
        ["kind=expansion", "kind=missing"],
        ["missing-equals"],
        ["kind=expansion", "missing-equals"],
        [None],
        1,
    ],
)
def test_where_field_schema_errors(tmp_path, deleted, fields):
    task = record("Task", "a", fields={"kind": "expansion"})
    with restore(tmp_path, [task]) as project:
        assert ids(call(project, "where", field="kind=expansion")) == [task.id]
        project.index.close()
        if deleted:
            project.index.path.unlink()
        refusal("schema", call, project, "where", field=fields)


@pytest.mark.parametrize("deleted", [False, True])
@pytest.mark.parametrize(
    "fields",
    [
        {"kind": "expansion", "command": "a=b"},
        "command=a=b",
        ["kind=expansion", "kind=expansion", "command=a=b"],
        ("command=a=b", "command=a=b", "kind=expansion"),
    ],
)
def test_where_field_shapes_repeats_and_first_equals(tmp_path, deleted, fields):
    task = record("Task", "a", fields={"kind": "expansion", "command": "a=b"})
    other = record("Task", "b", fields={"kind": "expansion", "command": "a"})
    with restore(tmp_path, [task, other]) as project:
        assert ids(call(project, "where", field="command=a=b")) == [task.id]
        project.index.close()
        if deleted:
            project.index.path.unlink()
        assert ids(call(project, "where", field=fields)) == [task.id]


def test_digest_failed_boundary_and_history_events(tmp_path):
    task = record("Task", "a")
    task.status = Status.FAILED
    task.approvals = [Approval(ALICE, NOW, "fail", note="failed")]
    project = restore(tmp_path, [task])
    history = call(project, "history", task.id)
    assert {row["kind"] for row in history} == {"interval", "approval"}
    assert all(row["commit"] for row in history)
    summary = {
        row["kind"]: row
        for row in call(project, "digest", now=NOW + timedelta(hours=24))["items"]
    }
    assert summary["failed_24h"]["count"] == 1
    summary = {
        row["kind"]: row
        for row in call(project, "digest", now=NOW + timedelta(hours=24, seconds=1))[
            "items"
        ]
    }
    assert summary["failed_24h"]["count"] == 0


def test_as_of_status_edge_records_and_original_ordinals(tmp_path):
    task = record("Task", "a")
    first = NOW - timedelta(seconds=10)
    task.created = first
    task.status = Status.RUNNING
    task.lease = Lease(ALICE, first, NOW + timedelta(seconds=10))
    project = restore(tmp_path, [task])
    changed = deepcopy(task)
    changed.status, changed.lease = Status.ACTIVE, None
    changed.updated = NOW + timedelta(seconds=1)
    changed.approvals = [Approval(ALICE, changed.updated, "release")]
    project.apply(
        Write("release", ALICE, None, changed.updated, {"node": task.id}, [changed], [])
    )
    historical = call(project, "show", task.id, as_of=NOW)
    assert historical["status"] == "running" and historical["lease"] is None
    assert not historical["flags"]["ready"] and not historical["flags"]["lease_expired"]
    assert historical["approvals"] == []
    assert call(project, "show", task.id, as_of="HEAD")["status"] == "active"


def supersede(project, old, replacement, edge_letter, now):
    """Build a real T05 candidate, not a production operation builder."""
    before = deepcopy(old)
    before.status, before.updated = Status.RETIRED, now
    before.approvals.append(Approval(ALICE, now, "superseded", note=replacement.id))
    replacement.created = replacement.updated = now
    edge = relation(edge_letter, "supersedes", replacement, old)
    edge.created = now
    commit = project.apply(
        Write(
            "supersede",
            ALICE,
            None,
            now,
            {"node": old.id, "inherit_edges": False},
            [before, replacement],
            [edge],
        )
    )
    return edge, commit


def test_as_of_stale_chains_multicause_and_history_provenance(tmp_path):
    c, middle = record("Claim", "a"), record("Result", "f")
    r0, r1, r2, s0, s1 = [record("Result", x) for x in "abcde"]
    edges = [
        relation("a", "cites", c, r0),
        relation("b", "cites", c, s0),
        relation("c", "cites", c, middle),
        relation("d", "derived-from", middle, r0),
    ]
    project = restore(tmp_path, [c, middle, r0, s0], edges)
    t1, t2, t3 = [NOW + timedelta(seconds=i) for i in (1, 2, 3)]
    e1, commit1 = supersede(project, r0, r1, "e", t1)
    e2, commit2 = supersede(project, r1, r2, "f", t2)
    _, commit3 = supersede(project, s0, s1, "g", t3)
    assert call(project, "stale", as_of=NOW)["items"] == []
    assert call(project, "stale", as_of=commit1)["items"][0]["responsible"] == [r1.id]
    assert call(project, "stale", as_of=t2)["items"][0]["responsible"] == [r2.id]
    assert call(project, "stale", as_of=commit3)["items"][0]["responsible"] == sorted(
        [r2.id, s1.id]
    )
    assert all(
        e["id"] != e2.id for e in call(project, "show", r1.id, as_of=t1)["edges"]
    )
    events = call(project, "history", r0.id)
    assert {row["node"] for row in events} == {r0.id, r1.id, r2.id}
    assert [row["at"] for row in events] == sorted(row["at"] for row in events)
    assert {
        row["id"]: row["commit"] for row in events if row["kind"] == "supersedes"
    } == {e1.id: commit1, e2.id: commit2}
    before = call(project, "stale", as_of=t2)
    project.index.path.unlink()
    assert call(project, "stale", as_of=t2) == before
    assert call(project, "history", r0.id) == events


def test_cyclic_containment_terminates_and_usage_deduplicates(tmp_path):
    h, task = record("Hypothesis", "a"), record("Task", "a", usage={"tokens": 7})
    # tests is not an I1 DAG relation. A tests/derived-from cycle is legal.
    edges = [
        relation("a", "tests", task, h, fields={"expected": "x"}),
        relation("b", "derived-from", h, task),
    ]
    project = restore(tmp_path, [h, task], edges)
    subtree = call(project, "subtree", h.id)
    assert ids(subtree["nodes"]) == [h.id, task.id]
    assert subtree["usage"]["tokens"] == 7
    assert ids(call(project, "ready")["items"]) == [task.id]


def test_retired_supersedes_history_and_record_ordinals(tmp_path):
    old, new = record("Result", "a"), record("Result", "b")
    old.status = Status.RETIRED
    old.approvals = [
        Approval(ALICE, NOW, "superseded", note=new.id),
        Approval(ALICE, NOW, "budget", note="tokens=1"),
    ]
    old.artifacts = [Artifact(f"artifacts/{old.id}/x", "a" * 64, 1, NOW, ALICE)]
    edge = relation("a", "supersedes", new, old, retired=True, retired_at=NOW)
    project = restore(tmp_path, [old, new], [edge])
    events = call(project, "history", new.id, as_of=NOW)
    assert {e["node"] for e in events} == {old.id, new.id}
    assert [e["ordinal"] for e in events if e["kind"] == "approval"] == [0, 1]
    assert any(e["kind"] == "artifact" and e["path"].endswith("/x") for e in events)
    assert any(e["kind"] == "supersedes" and e["retired"] for e in events)
    project.index.path.unlink()
    assert call(project, "history", new.id, as_of=NOW) == events


def test_historical_retired_accounting_and_current_metadata(tmp_path, monkeypatch):
    h, old, new = record("Hypothesis", "a"), record("Task", "a"), record("Task", "b")
    h.budget = {"tokens": 5}
    old.usage = {"tokens": 5}
    edge = relation("a", "tests", old, h, fields={"expected": "x"})
    project = restore(tmp_path, [h, old, new], [edge])
    retirement = NOW + timedelta(seconds=1)
    retired = project.get_edges()[0]
    retired.retired, retired.retired_at = True, retirement
    # A hand-written historical state, not an authorized node-retirement write.
    relative = f"graph/edges/{retired.id}.yaml"
    (project.path / relative).write_text(dump_edge(retired.to_dict()))
    monkeypatch.setenv("GIT_AUTHOR_DATE", format_timestamp(retirement))
    monkeypatch.setenv("GIT_COMMITTER_DATE", format_timestamp(retirement))
    git(project.path, "add", "--", relative)
    git(project.path, "commit", "-m", "hand-written historical edge state")
    assert old.id not in ids(call(project, "subtree", h.id)["nodes"])
    assert old.id in ids(call(project, "subtree", h.id, as_of=NOW)["nodes"])
    assert call(project, "subtree", h.id)["usage"]["tokens"] == 5
    assert call(project, "subtree", h.id, as_of=NOW)["usage"]["tokens"] == 5
    assert ids(call(project, "ready")["items"]) == [old.id, new.id]
    assert ids(call(project, "ready", as_of=NOW)["items"]) == [new.id]
    assert retired.id in ids(call(project, "since", NOW))


def test_non_discriminating_and_weakest_nonstale_ties(tmp_path):
    a, b = record("Hypothesis", "a"), record("Hypothesis", "b")
    p, c = record("Prediction", "a"), record("Claim", "a")
    r1 = record("Result", "a", fields={"runs": 1})
    r2 = record("Result", "b", fields={"runs": 3})
    r3 = record("Result", "c", fields={"runs": 1, "held_out": True})
    edges = [
        relation("a", "tests", p, a, fields={"expected": "yes"}),
        relation("b", "tests", p, b, fields={"expected": "yes"}),
        relation("c", "cites", c, r2),
        relation("d", "cites", c, r3),
        relation("e", "cites", c, r1),
    ]
    project = restore(tmp_path, [a, b, p, c, r1, r2, r3], edges)
    assert call(project, "show", p.id)["warnings"] == ["non_discriminating"]
    assert ids(call(project, "weakest", c.id)) == [r1.id, r3.id, r2.id]


def test_completed_and_retired_blockers_do_not_block(tmp_path):
    a, b, c = [record("Task", x) for x in "abc"]
    a.status = Status.DONE
    edges = [
        relation("a", "blocks", a, c),
        relation("b", "blocks", b, c, retired=True, retired_at=NOW),
    ]
    project = restore(tmp_path, [a, b, c], edges)
    assert call(project, "blocked")["items"] == []
    assert ids(call(project, "ready")["items"]) == [b.id, c.id]


def test_ready_excludes_all_terminal_and_proposed_statuses(tmp_path):
    nodes = [record("Task", letter) for letter in "abcde"]
    for node, status in zip(
        nodes,
        [Status.ACTIVE, Status.PROPOSED, Status.DONE, Status.FAILED, Status.RETIRED],
    ):
        node.status = status
    project = restore(tmp_path, nodes, profile="tasks")
    assert ids(call(project, "ready")["items"]) == [nodes[0].id]


def test_reserved_queries_empty_without_reserved_node_vocabulary(tmp_path):
    task = record("Task", "a")
    project = restore(tmp_path, [task], profile="tasks")
    for name in ("weakest", "predictions", "alternatives", "notes"):
        assert call(project, name, task.id) == []
    assert call(project, "matrix", task.id) == {"rows": [], "columns": []}
    assert call(project, "stale") == call(project, "evidence", task.id) == {"items": []}
    groups = {row["kind"]: row for row in call(project, "digest")["items"]}
    assert groups["claims"] == {"kind": "claims", "count": 0, "nodes": []}


def test_since_includes_approval_artifact_and_retirement_not_just_creation(tmp_path):
    a, b = record("Task", "a"), record("Task", "b")
    a.created = a.updated = b.created = b.updated = NOW - timedelta(seconds=10)
    a.approvals = [Approval(ALICE, NOW, "approve")]
    b.artifacts = [Artifact(f"artifacts/{b.id}/x", "a" * 64, 1, NOW, ALICE)]
    edge = relation("a", "blocks", a, b, retired=True, retired_at=NOW)
    edge.created = NOW - timedelta(seconds=10)
    project = restore(tmp_path, [a, b], [edge])
    assert set(ids(call(project, "since", NOW - timedelta(seconds=1)))) == {
        a.id,
        b.id,
        edge.id,
    }


def test_all_queries_rebuild_equivalence_with_nonempty_research_results(tmp_path):
    q, h, other = (
        record("Question", "a"),
        record("Hypothesis", "a"),
        record("Hypothesis", "b"),
    )
    p, c, old, new = (
        record("Prediction", "a"),
        record("Claim", "a"),
        record("Result", "a"),
        record("Result", "b"),
    )
    old.status = Status.RETIRED
    task, blocker = record("Task", "a"), record("Task", "b")
    note, instruction = record("Note", "a"), record("Instruction", "a")
    queued = record("Hypothesis", "c")
    queued.status = Status.PROPOSED
    edges = [
        relation("a", "answers", h, q),
        relation("b", "answers", other, q),
        relation("c", "tests", p, h, fields={"expected": "yes"}),
        relation("d", "tests", p, other, fields={"expected": "no"}),
        relation("e", "cites", c, old),
        relation("f", "supersedes", new, old),
        relation("g", "about", note, h),
        relation("h", "instructs", instruction, h),
        relation("i", "blocks", blocker, task),
    ]
    project = restore(
        tmp_path,
        [q, h, other, p, c, old, new, task, blocker, note, instruction, queued],
        edges,
    )
    calls = [
        ("ready", ()),
        ("blocked", ()),
        ("queue", ()),
        ("history", (old.id,)),
        ("evidence", (c.id,)),
        ("stale", ()),
        ("weakest", (c.id,)),
        ("alternatives", (h.id,)),
        ("predictions", (h.id,)),
        ("matrix", (q.id,)),
        ("notes", (h.id,)),
        ("subtree", (h.id,)),
        ("since", (NOW - timedelta(seconds=1),)),
        ("where", ()),
        ("digest", ()),
        ("show", (h.id,)),
    ]
    for as_of in (None, NOW):
        for name, args in calls:
            before = call(project, name, *args, as_of=as_of)
            assert before
            project.index.path.unlink()
            assert call(project, name, *args, as_of=as_of) == before


QUERY_CALLS = [
    ("ready", ()),
    ("blocked", ()),
    ("queue", ()),
    ("history", ("ta-aaaaaaaa",)),
    ("evidence", ("ta-aaaaaaaa",)),
    ("stale", ()),
    ("weakest", ("ta-aaaaaaaa",)),
    ("alternatives", ("ta-aaaaaaaa",)),
    ("predictions", ("ta-aaaaaaaa",)),
    ("matrix", ("ta-aaaaaaaa",)),
    ("notes", ("ta-aaaaaaaa",)),
    ("subtree", ("ta-aaaaaaaa",)),
    ("since", (NOW,)),
    ("where", ()),
    ("digest", ()),
    ("show", ("ta-aaaaaaaa",)),
]


@pytest.mark.parametrize("name,args", QUERY_CALLS)
def test_every_query_rebuild_equivalence_and_context(tmp_path, name, args, monkeypatch):
    project = restore(tmp_path, [record("Task", "a")], profile="tasks")
    from researchgraph.store import source_scan as source_module

    hashed, heads = [], []
    original_hash, original_head = source_module.hash_file, project._git.head

    def hash_file(path):
        hashed.append(path)
        return original_hash(path)

    def head():
        heads.append(True)
        return original_head()

    monkeypatch.setattr(source_module, "hash_file", hash_file)
    monkeypatch.setattr(project._git, "head", head)
    before = call(project, name, *args)
    assert hashed and heads
    assert str(project.path / "graph/nodes/ta-aaaaaaaa.yaml") in hashed
    json.dumps(before)
    project.index.path.unlink()
    hashed.clear()
    heads.clear()
    assert call(project, name, *args) == before
    assert hashed and heads
    refusal("schema", getattr(project, name), *args, actor="alice", now=NOW)
    refusal(
        "schema",
        getattr(project, name),
        *args,
        actor=ALICE,
        now=NOW.replace(microsecond=1),
    )


@pytest.mark.parametrize(
    "name",
    [
        "history",
        "evidence",
        "weakest",
        "alternatives",
        "predictions",
        "matrix",
        "notes",
        "subtree",
        "show",
    ],
)
def test_missing_ids(tmp_path, name):
    project = restore(tmp_path, [record("Task", "a")])
    refusal("not_found", getattr(project, name), "ta-zzzzzzzz", actor=ALICE, now=NOW)


@settings(max_examples=8, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(st.lists(query_nodes(), min_size=1, max_size=5))
def test_generated_store_ready_order_and_filters(tmp_path, snapshots):
    # Each generated example gets its own real repository and validated import.
    import tempfile

    with tempfile.TemporaryDirectory(dir=tmp_path) as directory:
        from pathlib import Path

        for index, snapshot in enumerate(snapshots):
            snapshot.id = "ta-" + chr(ord("a") + index) * 8
        project = restore(Path(directory), snapshots, profile="tasks")
        expected = sorted(
            (n for n in snapshots if n.status == Status.ACTIVE),
            key=lambda n: (n.priority is None, -(n.priority or 0), n.created, n.id),
        )
        assert ids(call(project, "ready")["items"]) == [n.id for n in expected]
        assert ids(call(project, "where", tag="x")) == sorted(
            n.id for n in snapshots if "x" in n.tags
        )
        project.close()


def test_historical_interval_upper_bound_is_not_carried_across_gap(tmp_path):
    from researchgraph.model import format_timestamp

    value = record("Task", "a")
    value.status = Status.DONE
    value.updated = NOW + timedelta(seconds=3)
    project = restore(tmp_path, [value], now=value.updated)
    rows = [
        {
            "node_id": value.id,
            "status": "active",
            "from_commit": "earlier",
            "from_time": format_timestamp(NOW),
            "to_commit": "closed",
            "to_time": format_timestamp(NOW + timedelta(seconds=1)),
        },
        {
            "node_id": value.id,
            "status": "done",
            "from_commit": "later",
            "from_time": format_timestamp(value.updated),
            "to_commit": None,
            "to_time": None,
        },
    ]
    (project.path / ".researchgraph/history.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows)
    )
    assert call(project, "show", value.id, as_of=NOW)["status"] == "active"
    assert call(project, "where", as_of=NOW + timedelta(seconds=2)) == []
    assert call(project, "show", value.id, as_of=value.updated)["status"] == "done"


@pytest.mark.parametrize(
    "first,second,equal",
    [
        (1, True, False),
        (float("nan"), float("nan"), True),
        ({"x": 1}, {"x": True}, False),
    ],
)
def test_discrimination_uses_canonical_typed_expected_values(
    tmp_path, monkeypatch, first, second, equal
):
    from researchgraph.profiles import Profile

    monkeypatch.setattr(Profile, "_registry", dict(Profile._registry))
    profile = Profile.load("research")
    profile.name = "typed-outcomes"
    profile.edge_type("tests").schema["properties"]["expected"] = {}
    Profile.register(profile)
    q = record("Question", "a")
    a, b = record("Hypothesis", "a"), record("Hypothesis", "b")
    prediction = record("Prediction", "a")
    edges = [
        relation("a", "answers", a, q),
        relation("b", "answers", b, q),
        relation("c", "tests", prediction, a, fields={"expected": first}),
        relation("d", "tests", prediction, b, fields={"expected": second}),
    ]
    project = restore(tmp_path, [q, a, b, prediction], edges, profile=profile.name)
    assert call(project, "matrix", q.id)["rows"][0]["non_discriminating"] is equal
    assert (
        "non_discriminating" in call(project, "show", prediction.id)["warnings"]
    ) is equal
