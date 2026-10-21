"""State folds, cursor lifetime and failure convergence for polling events."""

import re
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from researchgraph.events import ChangeSource
from researchgraph.profiles import Profile
from researchgraph.project import Project
from tests.helpers import ALICE, NOW, git, refusal, repository
from tests.test_cli import invoke, succeeds
from tests.test_operations import discard_index, snapshot


def state(graph):
    return {
        kind: {row["id"]: row for row in graph[kind]} for kind in ("nodes", "edges")
    }


def observe(source, *, since=None, now=NOW):
    result = source.changes(actor=ALICE, now=now, since=since)
    for event in result["events"]:
        assert re.fullmatch(r"v1:[0-9a-f]{32}:[1-9][0-9]*", event["cursor"])
        assert event["cursor"] == f"v1:{event['generation']}:{event['seq']}"
        assert event["at"] == now.isoformat().replace("+00:00", "Z")
        assert "commit" in event
    return result


@settings(max_examples=12, deadline=None)
@given(
    actions=st.lists(
        st.sampled_from(["new", "tag", "claim", "release", "replace"]),
        min_size=1,
        max_size=12,
    ),
    retention=st.integers(1, 8),
)
def test_random_operation_folds_converge_after_cache_loss(actions, retention):
    with TemporaryDirectory() as directory:
        root = repository(Path(directory))
        with (
            Project.init(root, "tasks", actor=ALICE, now=NOW) as writer,
            Project.open(root, read_only=True) as reader,
        ):
            note = writer.propose("Note", "note", actor=ALICE, now=NOW)
            task = writer.propose("Task", "task", actor=ALICE, now=NOW)
            source = ChangeSource(reader, retention=retention)
            first = source.snapshot(actor=ALICE, now=NOW)
            folded, cursor = state(first["graph"]), first["cursor"]
            for action in actions:
                if action == "new":
                    node = writer.propose(
                        "Note", "new", actor=ALICE, now=NOW, commit=False
                    )
                    writer.link(
                        "part-of",
                        node["id"],
                        note["id"],
                        actor=ALICE,
                        now=NOW,
                        commit=False,
                    )
                elif action == "tag":
                    writer.tag(
                        note["id"],
                        add=["observed"],
                        priority=0,
                        actor=ALICE,
                        now=NOW,
                        commit=False,
                    )
                elif action == "claim" and task["status"] == "active":
                    task = writer.claim(task["id"], actor=ALICE, now=NOW, commit=False)
                elif action == "release" and task["status"] == "running":
                    task = writer.release(
                        task["id"], actor=ALICE, now=NOW, commit=False
                    )
                elif action == "replace":
                    note = writer.supersede(
                        note["id"], actor=ALICE, now=NOW, commit=False
                    )
                discard_index(reader, "deleted")
                batch = observe(source, since=cursor)
                for event in batch["events"]:
                    if event.get("reset"):
                        folded = state(reader.graph(actor=ALICE, now=NOW))
                    elif event["kind"] in {"node", "edge"}:
                        kind = "nodes" if event["kind"] == "node" else "edges"
                        folded[kind][event["id"]] = event[event["kind"]]
                cursor = batch["cursor"]
                assert folded == state(reader.graph(actor=ALICE, now=NOW))
                assert len(source._events) <= retention
            assert not any(
                "event" in p.name for p in (root / ".researchgraph").iterdir()
            )
            source.close()
            assert source._project is None and not source._events
            assert reader.graph(actor=ALICE, now=NOW)


@pytest.mark.cli
def test_external_same_second_no_commit_already_refreshed_and_head_only(
    tmp_path, index_mode, monkeypatch
):
    monkeypatch.setenv("RG_TEST_CLI_INDEX_MODE", index_mode)
    root = repository(tmp_path)
    with (
        Project.init(root, "tasks", actor=ALICE, now=NOW),
        Project.open(root, read_only=True) as reader,
    ):
        source = ChangeSource(reader)
        initial = source.snapshot(actor=ALICE, now=NOW)
        nodes = [
            succeeds(invoke(root, "propose", "Note", title, "--no-commit"))
            for title in ("first", "second")
        ]
        reader.graph(
            actor=ALICE, now=NOW
        )  # another reader's refresh is not an observation
        discard_index(reader, index_mode)
        batch = observe(source, since=initial["cursor"])
        changes = [e for e in batch["events"] if e["kind"] == "node"]
        assert {e["id"] for e in changes} == {n["id"] for n in nodes}
        assert all(
            e["change"] == "created" and e["previous_status"] is None for e in changes
        )
        assert batch["events"][-1]["kind"] == "index"
        assert observe(source)["events"] == []
        git(root, "add", "graph/nodes")
        git(root, "commit", "-m", "external retained graph")
        batch = observe(source)
        assert [e["kind"] for e in batch["events"]] == ["index"]
        assert batch["events"][0]["commit"] == git(root, "rev-parse", "HEAD")


def test_raw_source_changes_notify_without_numeric_coercion(tmp_path, monkeypatch):
    from researchgraph.store.io import dump_node, load_node

    profile = Profile.load("tasks")
    profile.name = "event-raw"
    next(node for node in profile.node_types if node.name == "Note").schema = {
        "properties": {"flag": {}}
    }
    Profile.register(profile)
    with Project.init(
        repository(tmp_path), profile.name, actor=ALICE, now=NOW
    ) as project:
        node = project.propose(
            "Note", "raw", fields={"flag": True}, actor=ALICE, now=NOW
        )
        source = ChangeSource(project)
        source.snapshot(actor=ALICE, now=NOW)

        def forbidden(*args):
            pytest.fail("Read-only observations cannot acquire the writer lock")

        monkeypatch.setattr("researchgraph.project.write_lock", forbidden)
        path = tmp_path / f"graph/nodes/{node['id']}.yaml"
        original = path.read_text()
        commented = original.replace("title: raw", "title: raw # comment", 1)
        assert commented != original
        path.write_text(commented)
        assert [e["kind"] for e in observe(source)["events"]] == ["index"]
        record = load_node(path)
        record["fields"]["flag"] = 1
        path.write_text(dump_node(record))
        events = observe(source)["events"]
        assert [e["kind"] for e in events] == ["node", "index"]
        assert events[0]["node"]["fields"]["flag"] == 1
        assert type(events[0]["node"]["fields"]["flag"]) is int
        assert events[0]["previous_status"] == node["status"]


def test_clock_only_lease_expiry_does_not_rewrite_running_record(
    tmp_path, index_mode, monkeypatch
):
    with Project.init(repository(tmp_path), "tasks", actor=ALICE, now=NOW) as project:
        node = project.propose("Task", "claim", actor=ALICE, now=NOW)
        running = project.claim(node["id"], lease_seconds=1, actor=ALICE, now=NOW)
        source = ChangeSource(project)
        source.snapshot(actor=ALICE, now=NOW)
        before = snapshot(project)
        discard_index(project, index_mode)
        batch = observe(source, now=NOW + timedelta(seconds=1))
        assert [e["kind"] for e in batch["events"]] == ["index"]
        assert project.get_node(node["id"]).to_dict() == running
        assert (
            project.ready(actor=ALICE, now=NOW + timedelta(seconds=1))["items"][0]["id"]
            == node["id"]
        )
        assert snapshot(project) == before
        assert observe(source, now=NOW + timedelta(seconds=2))["events"] == []


def test_clock_only_failure_window_and_unchanged_cache_rebuild(tmp_path, index_mode):
    with Project.init(
        repository(tmp_path), "research", actor=ALICE, now=NOW
    ) as project:
        task = project.propose("Task", "failure", actor=ALICE, now=NOW)
        project.claim(task["id"], actor=ALICE, now=NOW)
        project.fail(task["id"], "failed", actor=ALICE, now=NOW)
        source = ChangeSource(project)
        source.snapshot(actor=ALICE, now=NOW)
        discard_index(project, index_mode)
        assert observe(source)["events"] == []
        assert observe(source, now=NOW + timedelta(hours=24))["events"] == []
        late = NOW + timedelta(hours=24, seconds=1)
        assert [e["kind"] for e in observe(source, now=late)["events"]] == ["index"]


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "1",
        "v0:bad:1",
        "v1:" + "a" * 32 + ":1",
        "v1:" + "a" * 32 + ":999999",
        1,
        None,
    ],
)
def test_unknown_malformed_and_prior_generation_cursors_reset(tmp_path, bad):
    with Project.init(repository(tmp_path), "tasks", actor=ALICE, now=NOW) as project:
        source = ChangeSource(project)
        first = source.snapshot(actor=ALICE, now=NOW)
        if bad is None:
            # Restart with unchanged bytes, same first sequence, new generation.
            source = ChangeSource(project)
            source.snapshot(actor=ALICE, now=NOW)
            bad = first["cursor"]
        batch = observe(source, since=bad)
        assert len(batch["events"]) == 1 and batch["events"][0]["reset"] is True
        assert batch["events"][0]["kind"] == "index"


def test_retention_replay_disappearance_and_snapshot_catchup(tmp_path, index_mode):
    with Project.init(repository(tmp_path), "tasks", actor=ALICE, now=NOW) as project:
        source = ChangeSource(project, retention=3)
        initial = source.snapshot(actor=ALICE, now=NOW)
        node = project.propose(
            "Note", "racing write", actor=ALICE, now=NOW, commit=False
        )
        first = observe(source, since=initial["cursor"])
        assert [e["kind"] for e in first["events"]] == ["node", "index"]
        replay = observe(source, since=first["events"][0]["cursor"])
        assert replay["events"] == first["events"][1:]
        project.tag(node["id"], add=["same-second"], actor=ALICE, now=NOW, commit=False)
        assert observe(source, since=initial["cursor"])["events"][0]["reset"] is True
        (tmp_path / f"graph/nodes/{node['id']}.yaml").unlink()
        discard_index(project, index_mode)
        removed = observe(source)
        assert len(removed["events"]) == 1 and removed["events"][0]["reset"] is True
        snap = source.snapshot(actor=ALICE, now=NOW)
        assert (
            snap["graph"]
            == project.graph(actor=ALICE, now=NOW)
            == {"nodes": [], "edges": []}
        )
        assert observe(source, since=snap["cursor"])["events"] == []
        generation = snap["cursor"].split(":")[1]
        assert (
            observe(source, since=f"v1:{generation}:999999")["events"][0]["reset"]
            is True
        )


def test_faults_converge_on_persisted_bytes_without_fabricating_success(
    tmp_path, index_mode, monkeypatch
):
    from researchgraph.store import transaction

    root = repository(tmp_path)
    with (
        Project.init(root, "tasks", actor=ALICE, now=NOW) as writer,
        Project.open(root, read_only=True) as reader,
    ):
        task = writer.propose("Task", "multi-file rollback", actor=ALICE, now=NOW)
        writer.claim(task["id"], actor=ALICE, now=NOW)
        source = ChangeSource(reader)
        source.snapshot(actor=ALICE, now=NOW)
        before = snapshot(writer)
        refusal("unknown_type", writer.propose, "Unknown", "bad", actor=ALICE, now=NOW)
        assert snapshot(writer) == before and observe(source)["events"] == []
        replace = transaction.os.replace
        calls = 0

        def fail_once(source, destination):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("injected rename")
            return replace(source, destination)

        with monkeypatch.context() as patch:
            patch.setattr(transaction.os, "replace", fail_once)
            refusal(
                "write_failed",
                writer.fail,
                task["id"],
                "rollback",
                actor=ALICE,
                now=NOW,
            )
        assert calls >= 2
        assert snapshot(writer) == before and observe(source)["events"] == []
        hook = root / ".git/hooks/pre-commit"
        hook.write_text("#!/bin/sh\nexit 1\n")
        hook.chmod(0o755)
        refusal(
            "write_failed", writer.propose, "Note", "retained", actor=ALICE, now=NOW
        )
        discard_index(reader, index_mode)
        retained = observe(source)
        assert [e["kind"] for e in retained["events"]] == ["node", "index"]
        assert retained["events"][0]["node"]["title"] == "retained"
        hook.unlink()

        def fail_hook(*args):
            raise RuntimeError("post-commit failure")

        writer.profile.event_hooks = [fail_hook]
        refusal(
            "write_failed", writer.propose, "Note", "committed", actor=ALICE, now=NOW
        )
        committed = observe(source)
        assert [e["kind"] for e in committed["events"]] == ["node", "index"]
        assert committed["events"][0]["node"]["title"] == "committed"
        assert committed["events"][0]["commit"] == git(root, "rev-parse", "HEAD")
        assert state(source.snapshot(actor=ALICE, now=NOW)["graph"]) == state(
            writer.graph(actor=ALICE, now=NOW)
        )


def test_public_changes_owns_bounded_source_until_project_close(tmp_path):
    project = Project.init(repository(tmp_path), "tasks", actor=ALICE, now=NOW)
    first = project.changes(actor=ALICE, now=NOW)
    assert [e["kind"] for e in first["events"]] == ["index"]
    assert project.changes(actor=ALICE, now=NOW, since=first["cursor"])["events"] == []
    detached = deepcopy(first)
    source = project._change_source
    project.close()
    assert source._project is None and not source._events
    assert detached == first
    with pytest.raises(ValueError, match="closed"):
        project.changes(actor=ALICE, now=NOW)
