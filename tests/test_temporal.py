"""Public temporal reads: inclusive clocks, disposable indexes and isolation."""

import json
from copy import deepcopy
from datetime import timedelta

import pytest

from researchgraph.model import Status, format_timestamp
from researchgraph.project import Project
from researchgraph.store.io import dump_node, load_node
from tests.helpers import ALICE, NOW, git, refusal, repository


def invoke(project, name, *args, seconds=0, **kwargs):
    return getattr(project, name)(
        *args, actor=ALICE, now=NOW + timedelta(seconds=seconds), **kwargs
    )


def discard(project, deleted):
    if deleted:
        project.index.close()
        project.index.path.unlink(missing_ok=True)


@pytest.mark.parametrize("deleted", [False, True])
def test_approval_edge_cutoffs_and_all_read_families(tmp_path, deleted, monkeypatch):
    project = Project.init(repository(tmp_path / "project"), actor=ALICE, now=NOW)
    h = invoke(project, "propose", "Hypothesis", "H", seconds=1)
    before = git(project.path, "rev-parse", "HEAD")
    invoke(project, "approve", h["id"], seconds=3, role="human")
    after = git(project.path, "rev-parse", "HEAD")
    task = invoke(project, "propose", "Task", "Task", seconds=4)
    edge = invoke(
        project,
        "link",
        "tests",
        task["id"],
        h["id"],
        seconds=5,
        fields={"expected": "yes"},
    )
    invoke(project, "retire", task["id"], "finished", seconds=7)
    original_run = project._git.run

    def no_checkout(*args, **kwargs):
        assert not set(args) & {"checkout", "switch", "reset"}
        return original_run(*args, **kwargs)

    monkeypatch.setattr(project._git, "run", no_checkout)
    for boundary, status in [
        (before, "proposed"),
        (NOW + timedelta(seconds=2), "proposed"),
        (after, "active"),
        ("HEAD", "active"),
    ]:
        discard(project, deleted)
        shown = invoke(project, "show", h["id"], as_of=boundary)
        assert shown["status"] == status
        assert len(shown["approvals"]) == (status == "active")
    for seconds, edges in [(4, []), (5, [edge["id"]]), (6, [edge["id"]]), (7, [])]:
        discard(project, deleted)
        shown = invoke(project, "show", h["id"], as_of=NOW + timedelta(seconds=seconds))
        assert [e["id"] for e in shown["edges"]] == edges
    refusal("not_found", invoke, project, "show", task["id"], as_of=before)
    reads = [
        ("ready", ()),
        ("blocked", ()),
        ("queue", ()),
        ("history", (h["id"],)),
        ("evidence", (h["id"],)),
        ("stale", ()),
        ("weakest", (h["id"],)),
        ("alternatives", (h["id"],)),
        ("predictions", (h["id"],)),
        ("matrix", (h["id"],)),
        ("notes", (h["id"],)),
        ("subtree", (h["id"],)),
        ("since", (NOW,)),
        ("where", ()),
        ("digest", ()),
    ]
    for name, args in reads:
        expected = invoke(project, name, *args, as_of=after)
        discard(project, True)
        assert invoke(project, name, *args, as_of=after) == expected


def test_historical_running_current_metadata_and_independent_outputs(tmp_path):
    project = Project.init(
        repository(tmp_path / "project"), "tasks", actor=ALICE, now=NOW
    )
    task = invoke(
        project, "propose", "Task", "Task", fields={"command": "true"}, seconds=1
    )
    invoke(project, "claim", task["id"], seconds=2)
    running = git(project.path, "rev-parse", "HEAD")
    invoke(project, "release", task["id"], seconds=3, usage={"tokens": 7})
    shown = invoke(project, "show", task["id"], as_of=running)
    assert shown["status"] == "running" and shown["lease"] is None
    assert shown["usage"] == {"tokens": 7}
    assert not shown["flags"]["ready"] and not shown["flags"]["lease_expired"]
    first = invoke(project, "ready", as_of="HEAD")
    saved = deepcopy(first)
    first["items"][0]["fields"]["command"] = "changed"
    first["items"][0]["author"]["name"] = "changed"
    first["items"][0]["approvals"].clear()
    first["items"].clear()
    assert invoke(project, "ready", as_of="HEAD") == saved
    assert project.index.get_node(task["id"]).status == Status.ACTIVE
    detached = project.index.get_nodes()
    detached[0].fields.clear()
    assert project.index.get_node(task["id"]).fields == {"command": "true"}
    history = invoke(project, "history", task["id"], as_of="HEAD")
    assert [r["ordinal"] for r in history if r["kind"] == "approval"] == [0, 1]
    assert (
        invoke(project, "show", task["id"], as_of=NOW + timedelta(seconds=1))["status"]
        == "active"
    )


def test_equal_clock_artifacts_and_source_epoch_invalidation(tmp_path):
    project = Project.init(
        repository(tmp_path / "project"), "tasks", actor=ALICE, now=NOW
    )
    task = invoke(project, "propose", "Task", "Task", seconds=1)
    before = git(project.path, "rev-parse", "HEAD")
    invoke(project, "claim", task["id"], seconds=1)
    # A commit maps to its committer time, not a tree snapshot. Equal-clock
    # commits are deliberately indistinguishable to temporal status selection.
    assert invoke(project, "show", task["id"], as_of=before)["status"] == "running"
    artifact = project.path / f"artifacts/{task['id']}/result.txt"
    artifact.parent.mkdir()
    artifact.write_text("result")
    invoke(
        project,
        "complete",
        task["id"],
        "done",
        seconds=3,
        artifacts=[artifact.relative_to(project.path).as_posix()],
    )
    assert (
        invoke(project, "show", task["id"], as_of=NOW + timedelta(seconds=2))[
            "artifacts"
        ]
        == []
    )
    shown = invoke(project, "show", task["id"], as_of=NOW + timedelta(seconds=3))
    assert shown["status"] == "done" and len(shown["artifacts"]) == 1
    path = project.path / f"graph/nodes/{task['id']}.yaml"
    current = load_node(path)
    current["tags"], current["priority"] = ["edited"], 17
    current["budget"] = {"tokens": 20}
    path.write_text(dump_node(current))
    historical = invoke(project, "show", task["id"], as_of=before)
    assert historical["tags"] == ["edited"] and historical["priority"] == 17
    assert historical["budget"] == {"tokens": 20}
    # Semantically corrupt rows must still rebuild from the source, even when
    # a long-lived source projection has already been populated.
    with project.index.connection:
        project.index.connection.execute("DELETE FROM status_intervals")
    assert invoke(project, "show", task["id"], as_of=before) == historical


def test_archived_gap_missing_nodes_and_pending_replacement(tmp_path):
    project = Project.init(
        repository(tmp_path / "project"), "tasks", actor=ALICE, now=NOW
    )
    task = invoke(project, "propose", "Task", "Task", seconds=1, commit=False)
    project._git.commit(
        [f"graph/nodes/{task['id']}.yaml"],
        "rg: import",
        ALICE,
        NOW + timedelta(seconds=10),
    )
    archive = project.path / ".researchgraph/history.jsonl"
    rows = [
        {
            "node_id": task["id"],
            "status": "proposed",
            "from_commit": "opaque",
            "from_time": format_timestamp(NOW + timedelta(seconds=1)),
            "to_commit": "opaque-end",
            "to_time": format_timestamp(NOW + timedelta(seconds=2)),
        },
        {
            "node_id": task["id"],
            "status": "active",
            "from_commit": "opaque-active",
            "from_time": format_timestamp(NOW + timedelta(seconds=8)),
            "to_commit": None,
            "to_time": None,
        },
        {
            "node_id": "ta-zzzzzzzz",
            "status": "active",
            "from_commit": None,
            "from_time": format_timestamp(NOW),
            "to_commit": None,
            "to_time": None,
        },
    ]
    archive.write_text("".join(json.dumps(row) + "\n" for row in rows))
    # Start inclusive, end exclusive; archive-only IDs cannot fabricate nodes.
    assert invoke(project, "where", as_of=NOW) == []
    assert (
        invoke(project, "where", as_of=NOW + timedelta(seconds=1))[0]["status"]
        == "proposed"
    )
    assert invoke(project, "where", as_of=NOW + timedelta(seconds=2)) == []
    assert invoke(project, "where", as_of=NOW + timedelta(seconds=5)) == []
    assert (
        invoke(project, "where", as_of=NOW + timedelta(seconds=8))[0]["status"]
        == "active"
    )
    invoke(project, "claim", task["id"], seconds=11, commit=False)
    invoke(project, "release", task["id"], seconds=12, commit=False)
    project._refresh()
    intervals = project.index.connection.execute(
        "SELECT status,to_time FROM status_intervals WHERE node_id=? AND to_time IS NULL",
        (task["id"],),
    ).fetchall()
    assert intervals == [("active", None)]


def test_empty_projection_containers_and_ready_constraint_invalidation(tmp_path):
    project = Project.init(
        repository(tmp_path / "project"), "tasks", actor=ALICE, now=NOW
    )
    a = invoke(project, "propose", "Task", "A", seconds=1)
    b = invoke(project, "propose", "Task", "B", seconds=2)
    result = invoke(project, "ready")
    expected = deepcopy(result)
    for row in result["items"]:
        row["fields"]["injected"] = []
        row["approvals"].append({"injected": []})
        row["artifacts"].append({"injected": []})
        row["tags"].append("injected")
    assert invoke(project, "ready") == expected
    edge = invoke(project, "link", "blocks", a["id"], b["id"], seconds=3)
    assert [r["id"] for r in invoke(project, "ready")["items"]] == [a["id"]]
    assert invoke(project, "ready", as_of=NOW + timedelta(seconds=2)) == expected
    invoke(project, "retire", a["id"], "stop", seconds=4)
    assert [r["id"] for r in invoke(project, "ready")["items"]] == [b["id"]]
    assert [
        r["id"]
        for r in invoke(project, "ready", as_of=NOW + timedelta(seconds=3))["items"]
    ] == [a["id"]]
    assert invoke(project, "show", edge["id"])["retired"]


def test_full_scan_name_framing_tracks_membership_and_non_yaml_bytes(tmp_path):
    project = Project.init(
        repository(tmp_path / "project"), "tasks", actor=ALICE, now=NOW
    )
    invoke(project, "propose", "Task", "A", seconds=1)
    invoke(project, "ready")
    baseline = project.index._loaded_hash
    extra = project.path / "graph/source.dat"
    extra.write_bytes(b"same bytes")
    invoke(project, "ready")
    added = project.index._loaded_hash
    assert added != baseline
    extra.write_bytes(b"new! bytes")
    invoke(project, "ready")
    edited = project.index._loaded_hash
    assert edited != added
    renamed = extra.with_name("renamed.dat")
    extra.rename(renamed)
    invoke(project, "ready")
    assert project.index._loaded_hash != edited
    renamed.unlink()
    invoke(project, "ready")
    assert project.index._loaded_hash == baseline


def test_ready_templates_detach_nested_fields_lease_and_event_actors(tmp_path):
    project = Project.init(
        repository(tmp_path / "project"), "tasks", actor=ALICE, now=NOW
    )
    task = invoke(
        project, "propose", "Task", "A", seconds=1, fields={"targets": ["target"]}
    )
    invoke(project, "claim", task["id"], seconds=2, lease_seconds=1)
    expected = invoke(project, "ready", seconds=3)
    first = invoke(project, "ready", seconds=3)
    row = first["items"][0]
    row["fields"]["targets"].append("injected")
    row["lease"]["actor"]["name"] = "injected"
    row["approvals"][0]["actor"]["name"] = "injected"
    assert invoke(project, "ready", seconds=3) == expected
    assert invoke(project, "ready", seconds=2)["items"] == []


def test_batch_mixed_rows_births_order_and_current_lease(tmp_path):
    project = Project.init(
        repository(tmp_path / "project"), "tasks", actor=ALICE, now=NOW
    )
    a = invoke(
        project, "propose", "Task", "flat", seconds=1, fields={"command": "true"}
    )
    b = invoke(
        project, "propose", "Task", "nested", seconds=2, fields={"targets": ["nested"]}
    )
    c = invoke(project, "propose", "Task", "leased", seconds=3)
    invoke(project, "claim", c["id"], seconds=4, lease_seconds=2)
    for seconds, expected in [
        (6, [a["id"], b["id"], c["id"]]),
        (1, [a["id"]]),
        (4, [a["id"], b["id"]]),
        (2, [a["id"], b["id"]]),
        (0, []),
    ]:
        result = invoke(project, "ready", as_of=NOW + timedelta(seconds=seconds))
        assert [r["id"] for r in result["items"]] == expected
    saved = invoke(project, "ready", seconds=6)
    changed = invoke(project, "ready", seconds=6)
    changed["items"][0]["fields"]["command"] = "mutated"
    changed["items"][0]["tags"].append("mutated")
    changed["items"][0]["author"]["name"] = "mutated"
    changed["items"][0]["approvals"].append({})
    changed["items"][0]["artifacts"].append({})
    changed["items"][1]["fields"]["targets"].append("mutated")
    changed["items"][2]["lease"]["actor"]["name"] = "mutated"
    assert invoke(project, "ready", seconds=6) == saved
    path = project.path / f"graph/nodes/{b['id']}.yaml"
    node = load_node(path)
    node["priority"] = 10
    path.write_text(dump_node(node))
    assert [r["id"] for r in invoke(project, "ready", seconds=6)["items"]] == [
        b["id"],
        a["id"],
        c["id"],
    ]


def test_static_interval_envelope_end_and_gap_out_of_order(tmp_path):
    project = Project.init(
        repository(tmp_path / "project"), "tasks", actor=ALICE, now=NOW
    )
    task = invoke(project, "propose", "Task", "archived", seconds=1, commit=False)
    project._git.commit(
        [f"graph/nodes/{task['id']}.yaml"],
        "rg: import",
        ALICE,
        NOW + timedelta(seconds=10),
    )
    row = {
        "node_id": task["id"],
        "status": "active",
        "from_commit": "opaque",
        "from_time": format_timestamp(NOW + timedelta(seconds=1)),
        "to_commit": "opaque-end",
        "to_time": format_timestamp(NOW + timedelta(seconds=5)),
    }
    (project.path / ".researchgraph/history.jsonl").write_text(json.dumps(row) + "\n")
    for seconds, count in [(3, 1), (5, 0), (1, 1), (8, 0), (0, 0), (4, 1)]:
        assert (
            len(
                invoke(project, "ready", as_of=NOW + timedelta(seconds=seconds))[
                    "items"
                ]
            )
            == count
        )


def test_non_json_field_collections_are_rejected_atomically(tmp_path):
    from researchgraph.profiles import NodeType, Profile

    profile = Profile(
        "temporal-value-isolation",
        "1.0",
        [NodeType("Task", "ta", {"properties": {"data": {}}}, claimable=True)],
        [],
        [],
        [],
        [],
    )
    Profile.register(profile)
    with Project.init(
        repository(tmp_path), profile.name, actor=ALICE, now=NOW
    ) as project:
        head = git(project.path, "rev-parse", "HEAD")
        refusal(
            "schema",
            invoke,
            project,
            "propose",
            "Task",
            "T",
            fields={"data": {"original"}},
        )
        assert git(project.path, "rev-parse", "HEAD") == head
        assert not list((project.path / "graph/nodes").iterdir())
        retained = invoke(project, "ready")
        discard(project, True)
        assert retained == invoke(project, "ready") == {"items": []}


def test_scrubbing_bounds_variant_and_dto_caches_per_node(tmp_path):
    from researchgraph.queries import query_source

    with Project.init(repository(tmp_path), "tasks", actor=ALICE, now=NOW) as project:
        task = invoke(project, "propose", "Task", "T", seconds=1)
        commits = []
        for step in range(20):
            invoke(
                project,
                "claim" if step % 2 == 0 else "release",
                task["id"],
                seconds=step + 2,
            )
            commits.append(git(project.path, "rev-parse", "HEAD"))
        early = None
        saved = None
        for step in list(range(20)) + list(reversed(range(20))):
            result = invoke(project, "show", task["id"], as_of=commits[step])
            assert result["status"] == ("running" if step % 2 == 0 else "active")
            assert len(result["approvals"]) == step + 1
            source = query_source(project)
            if early is None:
                early = source.at(NOW + timedelta(seconds=2))[0][task["id"]]
                saved = source.serialize(early)
            assert len(source.variants) <= len(source.dynamic_nodes)
            assert len(source.serialized) <= len(source.nodes)
        # Revisit a caller-retained old model after its cache entry was replaced.
        assert source.serialize(early) == saved
        assert (
            invoke(project, "show", task["id"], as_of=commits[-1])["status"] == "active"
        )
        assert source.serialize(early) == saved


@pytest.mark.parametrize("change", ["source", "profile", "none"])
def test_prepared_ready_rows_require_the_fresh_epoch_and_visibility(
    tmp_path, monkeypatch, change
):
    from researchgraph.queries import query_source
    from researchgraph.store.index import Index

    with Project.init(repository(tmp_path), "tasks", actor=ALICE, now=NOW) as project:
        task = invoke(project, "propose", "Task", "before", seconds=1)
        invoke(project, "ready", seconds=2)
        old = query_source(project)
        refresh = Index.refresh
        calls = []

        def refresh_with_inflight_work(index, *, before_wait=None):
            if before_wait is not None:
                before_wait()
                calls.append(True)
                if change == "source":
                    path = project.path / f"graph/nodes/{task['id']}.yaml"
                    data = load_node(path)
                    data["title"] = "after"
                    path.write_text(dump_node(data))
                elif change == "profile":
                    monkeypatch.setattr(
                        project.profile.node_type("Task"), "claimable", False
                    )
            return refresh(index)

        monkeypatch.setattr(Index, "refresh", refresh_with_inflight_work)
        if change == "none":
            assert invoke(project, "ready", as_of=NOW) == {"items": []}
            first = invoke(project, "ready", seconds=2)
            first["items"][0]["tags"].append("caller-owned")
            assert invoke(project, "ready", seconds=2)["items"][0]["tags"] == []
            assert query_source(project) is old
        else:
            result = invoke(project, "ready", seconds=2)
            assert query_source(project) is not old
            if change == "profile":
                assert result == {"items": []}
            else:
                assert result["items"][0]["title"] == "after"
        assert calls
