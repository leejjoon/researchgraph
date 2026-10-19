"""Disposable SQLite projections over real source stores."""

import hashlib
import os
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from researchgraph.model import Edge, Lease, RuleViolation, Status
from researchgraph.project import Project
from tests.helpers import ALICE, NOW, node, repository, write


def test_typed_refresh_same_metadata_and_deleted_cache(project):
    value = node()
    project.apply(write(value), commit=False)
    index = project.index
    assert index.get_node(value.id).to_dict() == value.to_dict()
    path = project.path / f"graph/nodes/{value.id}.yaml"
    stamp = path.stat()
    before = index.content_hash()
    path.write_bytes(path.read_bytes().replace(b"A task", b"B task"))
    os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    assert project.get_node(value.id).title == "B task"
    assert before != index.content_hash()
    # Returned records cannot mutate the typed cache.
    project.get_node(value.id).title = "local"
    assert project.get_node(value.id).title == "B task"
    (project.path / ".researchgraph/index.sqlite").unlink()
    assert project.get_nodes()[0].title == "B task"
    assert index.connection.execute("select count(*) from nodes").fetchone()[0] == 1


def test_content_hash_all_files_history_and_head(project):
    before = project.index.content_hash()
    extra = project.path / "graph/other"
    extra.write_bytes(b"one")
    assert project.index.content_hash() != before
    project.get_nodes()
    history = project.path / ".researchgraph/history.jsonl"
    history.write_text("")
    before = project.index.content_hash()
    history.write_text("\n")
    assert project.index.content_hash() != before
    history.write_text("")
    before = project.index.content_hash()
    project._git.run("commit", "--allow-empty", "-m", "head only")
    assert project.index.content_hash() != before


def test_corrupt_cache_but_invalid_source_not_hidden(project):
    project.apply(write(node()), commit=False)
    project.index.connection.close()
    (project.path / ".researchgraph/index.sqlite").write_bytes(b"broken sqlite")
    assert project.get_nodes()[0].id == "ta-aaaaaaaa"
    (project.path / "graph/nodes/ta-aaaaaaaa.yaml").write_text("invalid: [")
    with pytest.raises(RuleViolation, match="Invalid graph record"):
        project.get_nodes()


def test_ready_clock_blockers_and_accounting(project):
    parent = node("ta-aaaaaaaa", budget={"tokens": 3})
    child = node("ta-bbbbbbbb", usage={"tokens": 3})
    worker = node("ta-cccccccc")
    worker.status = Status.RUNNING
    worker.lease = Lease(ALICE, NOW, NOW + timedelta(seconds=10))
    zero = node("ta-dddddddd", budget={"tokens": 0})
    edge = Edge(
        "ed-aaaaaaaa",
        "part-of",
        child.id,
        parent.id,
        ALICE,
        NOW,
        retired=True,
        retired_at=NOW,
    )
    project.apply(write(parent, child, worker, zero, edges=[edge]), commit=False)
    other = Project.open(project.path)
    assert [n.id for n in project.index.ready(NOW)] == [child.id]
    assert [n.id for n in other.index.ready(NOW + timedelta(seconds=10))] == [
        child.id,
        worker.id,
    ]
    assert [r[0] for r in project.index.connection.execute("select id from ready")] == [
        child.id
    ]
    edge.retired, edge.retired_at = False, None
    project.apply(write(edges=[edge]), commit=False)
    assert project.index.ready(NOW) == []


def test_apply_failures_recover_from_source(project, monkeypatch):
    project.apply(write(node()))
    changed = project.get_node("ta-aaaaaaaa")
    changed.status = Status.DONE
    changed.updated = NOW + timedelta(seconds=1)

    def fail(*args, **kwargs):
        raise OSError("injected")

    monkeypatch.setattr(project._git, "commit", fail)
    with pytest.raises(RuleViolation):
        project.apply(write(changed, now=changed.updated))
    assert project.get_node(changed.id).status == Status.DONE
    original = project.index.refresh
    monkeypatch.setattr(project.index, "refresh", fail)
    with pytest.raises(RuleViolation):
        project.apply(write(changed, now=changed.updated), commit=False)
    monkeypatch.setattr(project.index, "refresh", original)
    assert project.get_node(changed.id).status == Status.DONE


@given(
    st.lists(
        st.sampled_from([Status.ACTIVE, Status.RUNNING, Status.DONE]),
        min_size=1,
        max_size=8,
    )
)
@settings(max_examples=10, deadline=None)
def test_pending_projection_equals_rebuild(statuses):
    with TemporaryDirectory() as directory:
        path = repository(Path(directory))
        store = Project.init(path, actor=ALICE, now=NOW)
        for i, status in enumerate(statuses):
            value = node()
            value.status = status
            value.updated = NOW + timedelta(seconds=i)
            if status == Status.RUNNING:
                value.lease = Lease(
                    ALICE, value.updated, value.updated + timedelta(seconds=10)
                )
            store.apply(write(value, now=value.updated), commit=False)
        before = list(store.index.connection.execute("select * from status_intervals"))
        (path / ".researchgraph/index.sqlite").unlink()
        assert store.get_node(value.id).to_dict() == value.to_dict()
        assert (
            list(store.index.connection.execute("select * from status_intervals"))
            == before
        )


@pytest.mark.parametrize("version", ["1.0", "1.1"])
def test_profile_claimability_changes_do_not_depend_on_cache(
    project, monkeypatch, version
):
    from researchgraph.profiles import Profile

    project.apply(write(node()))
    assert len(project.index.ready(NOW)) == 1
    changed = deepcopy(project.profile)
    changed.version = version
    changed.node_type("Task").claimable = False
    monkeypatch.setattr(Profile, "_registry", dict(Profile._registry))
    Profile.register(changed)
    opened = Project.open(project.path)
    assert opened.index.ready(NOW) == []
    (project.path / ".researchgraph/index.sqlite").unlink()
    assert Project.open(project.path).index.ready(NOW) == []


def test_content_hash_reads_beyond_the_first_chunk(project):
    path = project.path / "graph/large-binary-source"
    path.write_bytes(b"x" * 20000 + b"a")
    before = project.index.content_hash()
    stamp = path.stat()
    path.write_bytes(b"x" * 20000 + b"b")
    os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    assert project.index.content_hash() != before


def test_large_backend_matches_independent_framing_and_small_backend(
    project, monkeypatch
):
    from researchgraph.store import index as index_module

    graph = project.path / "graph"
    for number in range(5000):
        (graph / f"extra-{number:04}").write_bytes(str(number).encode())
    (graph / "별\nline").write_bytes(b"x" * 20000 + b"end")
    history = project.path / ".researchgraph/history.jsonl"
    history.write_bytes(b"\n")
    files = [
        (path.relative_to(project.path).as_posix(), path)
        for path in graph.rglob("*")
        if path.is_file()
    ]
    files.append((".researchgraph/history.jsonl", history))
    expected = hashlib.sha256()
    for relative, path in sorted(files):
        name = relative.encode("utf-8")
        expected.update(len(name).to_bytes(8, "big") + name)
        expected.update(hashlib.sha256(path.read_bytes()).digest())
    expected.update(b"HEAD\0" + (project._git.head() or "").encode())
    try:
        assert project.index._hash_scanner is None
        assert project.index.content_hash() == expected.hexdigest()
        scanner = project.index._hash_scanner
        assert scanner is not None
        monkeypatch.setattr(index_module, "PARALLEL_HASH_THRESHOLD", 10000)
        assert project.index.content_hash() == expected.hexdigest()
        assert project.index._hash_scanner is scanner
        # Even with identical metadata, a late-byte edit must change the digest.
        path = graph / "별\nline"
        stamp = path.stat()
        path.write_bytes(b"x" * 20000 + b"new")
        os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        monkeypatch.setattr(index_module, "PARALLEL_HASH_THRESHOLD", 5000)
        assert project.index.content_hash() != expected.hexdigest()
    finally:
        project.index.close()
    project.index.close()
    assert project.index.connection is None
    assert all(process.poll() is not None for process in scanner._processes)


def test_small_index_never_starts_helpers_and_close_releases_connection(project):
    project.get_nodes()
    assert project.index._hash_scanner is None
    connection = project.index.connection
    project.index.close()
    project.index.close()
    import sqlite3

    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute("SELECT 1")


def test_parallel_index_preserves_symlink_rejection(project, monkeypatch):
    from researchgraph.store import index as index_module

    monkeypatch.setattr(index_module, "PARALLEL_HASH_THRESHOLD", 1)
    target = project.path / "graph/target"
    target.write_bytes(b"data")
    (project.path / "graph/link").symlink_to(target)
    with pytest.raises(RuleViolation, match="Symlink graph/history path"):
        project.index.content_hash()
    assert project.index._hash_scanner is None


def test_later_explicit_read_can_recover_after_reported_helper_crash(
    project, monkeypatch
):
    from researchgraph.store import index as index_module

    project.apply(write(node()), commit=False)
    monkeypatch.setattr(index_module, "PARALLEL_HASH_THRESHOLD", 1)
    try:
        project.get_node("ta-aaaaaaaa")
        previous = project.index._hash_scanner
        worker = previous._processes[0]
        worker.kill()
        worker.wait(timeout=5)
        with pytest.raises(OSError):
            project.get_node("ta-aaaaaaaa")
        assert all(process.poll() is not None for process in previous._processes)
        assert project.get_node("ta-aaaaaaaa").id == "ta-aaaaaaaa"
        assert project.index._hash_scanner is not previous
    finally:
        project.close()
