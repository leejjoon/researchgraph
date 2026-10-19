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

from researchgraph.model import Approval, Edge, Lease, RuleViolation, Status
from researchgraph.project import Project
from researchgraph.store.io import dump_edge, dump_node
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
    # Query fixtures are restored source snapshots, not synthetic propose calls.
    for value in (parent, child, worker, zero):
        (project.path / f"graph/nodes/{value.id}.yaml").write_text(
            dump_node(value.to_dict())
        )
    (project.path / f"graph/edges/{edge.id}.yaml").write_text(dump_edge(edge.to_dict()))
    other = Project.open(project.path)
    assert [n.id for n in project.index.ready(NOW)] == [child.id]
    assert [n.id for n in other.index.ready(NOW + timedelta(seconds=10))] == [
        child.id,
        worker.id,
    ]
    assert [r[0] for r in project.index.connection.execute("select id from ready")] == [
        child.id
    ]
    edge = Edge("ed-bbbbbbbb", "part-of", child.id, parent.id, ALICE, NOW)
    project.apply(write(edges=[edge], op="link"), commit=False)
    assert project.index.ready(NOW) == []


def test_apply_failures_recover_from_source(project, monkeypatch):
    project.apply(write(node()))
    changed = project.get_node("ta-aaaaaaaa")
    changed.status = Status.RUNNING
    changed.lease = Lease(ALICE, NOW, NOW + timedelta(seconds=7200))
    changed.approvals.append(Approval(ALICE, NOW, "claim"))
    project.apply(write(changed, op="claim", args={"node": changed.id}))
    changed.status = Status.DONE
    changed.lease = None
    changed.updated = NOW + timedelta(seconds=1)
    changed.approvals.append(Approval(ALICE, changed.updated, "complete"))

    def fail(*args, **kwargs):
        raise OSError("injected")

    monkeypatch.setattr(project._git, "commit", fail)
    with pytest.raises(RuleViolation):
        project.apply(
            write(
                changed, now=changed.updated, op="complete", args={"node": changed.id}
            )
        )
    assert project.get_node(changed.id).status == Status.DONE
    original = project.index.refresh
    monkeypatch.setattr(project.index, "refresh", fail)
    with pytest.raises(RuleViolation):
        project.apply(
            write(
                changed, now=changed.updated, op="complete", args={"node": changed.id}
            ),
            commit=False,
        )
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
            value = node("ta-" + chr(97 + i) * 8)
            value.created = value.updated = NOW + timedelta(seconds=i)
            store.apply(write(value, now=value.updated), commit=False)
            if status in {Status.RUNNING, Status.DONE}:
                value.status = Status.RUNNING
                value.lease = Lease(
                    ALICE, value.updated, value.updated + timedelta(seconds=7200)
                )
                value.approvals.append(Approval(ALICE, value.updated, "claim"))
                store.apply(
                    write(
                        value, op="claim", now=value.updated, args={"node": value.id}
                    ),
                    commit=False,
                )
            if status == Status.DONE:
                value.status, value.lease = Status.DONE, None
                value.approvals.append(Approval(ALICE, value.updated, "complete"))
                store.apply(
                    write(
                        value, op="complete", now=value.updated, args={"node": value.id}
                    ),
                    commit=False,
                )
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


@pytest.mark.parametrize("mode", ["same", "external", "reopened"])
@pytest.mark.parametrize(
    "corruption", ["payload", "column", "view", "approval", "interval"]
)
def test_semantic_cache_corruption_rebuilds_from_source(project, mode, corruption):
    import json
    import sqlite3

    project.apply(write(node()))
    expected = project.get_node("ta-aaaaaaaa").to_dict()
    source = project.path / "graph/nodes/ta-aaaaaaaa.yaml"
    before_bytes, before_head = source.read_bytes(), project._git.head()
    cache = project.path / ".researchgraph/index.sqlite"
    if mode == "reopened":
        project.close()
    database = project.index.connection if mode == "same" else sqlite3.connect(cache)
    with database:
        if corruption == "payload":
            payload = dict(expected, title="corrupted cache")
            database.execute("UPDATE nodes SET payload=?", (json.dumps(payload),))
        elif corruption == "column":
            database.execute("UPDATE nodes SET status='retired'")
        elif corruption == "view":
            database.execute("DROP VIEW ready")
            database.execute("CREATE VIEW ready AS SELECT * FROM nodes WHERE 0")
        elif corruption == "approval":
            database.execute(
                "INSERT INTO approvals VALUES (?,0,'{}','corrupt')", (expected["id"],)
            )
        else:
            database.execute("UPDATE status_intervals SET status='retired'")
    if mode != "same":
        database.close()
    reader = Project.open(project.path) if mode == "reopened" else project
    try:
        assert reader.get_node(expected["id"]).to_dict() == expected
        connection = reader.index.connection
        assert (
            json.loads(connection.execute("SELECT payload FROM nodes").fetchone()[0])
            == expected
        )
        assert connection.execute("SELECT status FROM nodes").fetchone()[0] == "active"
        assert [n.id for n in reader.index.ready(NOW)] == [expected["id"]]
        assert connection.execute("SELECT COUNT(*) FROM approvals").fetchone()[0] == 0
        assert (
            connection.execute("SELECT status FROM status_intervals").fetchone()[0]
            == "active"
        )
        cache.unlink()
        assert reader.get_node(expected["id"]).to_dict() == expected
        assert source.read_bytes() == before_bytes
        assert reader._git.head() == before_head
    finally:
        if reader is not project:
            reader.close()


@pytest.mark.parametrize("missing", [True, False])
def test_concurrent_missing_or_stale_cache_rebuild_is_atomic(
    project, monkeypatch, missing
):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from researchgraph.store.index import Index

    project.apply(write(node()))
    project.close()
    if missing:
        (project.path / ".researchgraph/index.sqlite").unlink()
    else:
        source = project.path / "graph/nodes/ta-aaaaaaaa.yaml"
        source.write_bytes(source.read_bytes().replace(b"A task", b"B task"))
    barrier, connected = Barrier(2), set()
    original = Index._connect

    def connect(index):
        original(index)
        if id(index) not in connected:
            connected.add(id(index))
            barrier.wait(timeout=10)

    monkeypatch.setattr(Index, "_connect", connect)

    def read():
        with Project.open(project.path) as reader:
            return reader.get_node("ta-aaaaaaaa").title

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(read), pool.submit(read)]
        expected = "A task" if missing else "B task"
        assert [future.result(timeout=20) for future in futures] == [expected, expected]


@pytest.mark.parametrize("coded", [True, False])
def test_cache_resource_errors_do_not_unlink_or_mask(project, monkeypatch, coded):
    import sqlite3

    project.apply(write(node()))
    cache = project.path / ".researchgraph/index.sqlite"
    before = cache.read_bytes()
    error = sqlite3.OperationalError("database is locked")
    if coded:
        error.sqlite_errorcode = sqlite3.SQLITE_BUSY

    def fail():
        raise error

    monkeypatch.setattr(project.index, "_connect", fail)
    with pytest.raises(sqlite3.OperationalError) as caught:
        project.get_nodes()
    assert caught.value is error
    assert cache.read_bytes() == before


@pytest.mark.parametrize("alias", ["symlink", "hardlink"])
def test_cache_alias_does_not_modify_unrelated_database(project, alias):
    import sqlite3

    project.close()
    unrelated = project.path / "unrelated.sqlite"
    with sqlite3.connect(unrelated) as database:
        database.execute("CREATE TABLE sentinel(value TEXT)")
        database.execute("INSERT INTO sentinel VALUES ('user work')")
    before = unrelated.read_bytes()
    cache = project.path / ".researchgraph/index.sqlite"
    cache.unlink()
    if alias == "symlink":
        cache.symlink_to(unrelated)
    else:
        os.link(unrelated, cache)
    with pytest.raises(RuleViolation) as refused:
        Project.open(project.path)
    assert refused.value.rule == "schema"
    assert unrelated.read_bytes() == before
    with sqlite3.connect(unrelated) as database:
        assert (
            database.execute("SELECT value FROM sentinel").fetchone()[0] == "user work"
        )
