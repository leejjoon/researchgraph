"""T14: canonical exports, preflight-only import rejection, and restored history."""

import io
import json
import os
import tarfile
from copy import deepcopy
from datetime import timedelta
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
import zstandard
from hypothesis import given, settings
from hypothesis import strategies as st

from researchgraph.model import Actor, Edge, RuleViolation, format_timestamp
from researchgraph.project import Project
from researchgraph.store.history import intervals_as_dicts
from researchgraph.store.io import dump_edge, dump_node, load_node
from researchgraph.validation import validate_graph
from tests.helpers import ALICE, NOW, git, node, refusal, repository
from tests.test_cli import invoke, refused, succeeds
from tests.test_execution import artifact, cold, queries
from tests.test_metadata_operations import source

BOT = Actor("agent", "agent1")
BOB = Actor("human", "bob", "bob@example.test")
CALL = {"actor": ALICE, "now": NOW}
STAMP = format_timestamp(NOW)
FUTURE = NOW + timedelta(hours=1)
FUTURE_STAMP = format_timestamp(FUTURE)
INTERVAL_KEYS = (
    "node_id",
    "status",
    "from_commit",
    "from_time",
    "to_commit",
    "to_time",
)


@pytest.fixture(autouse=True)
def git_identity(monkeypatch):
    # Imported destinations must be empty, so their git identity cannot be
    # installed with repository() before the import under test.
    monkeypatch.setenv("GIT_CONFIG_COUNT", "3")
    for index, (key, value) in enumerate(
        [
            ("user.name", "Alice Git"),
            ("user.email", "alice@git.test"),
            ("commit.gpgsign", "false"),
        ]
    ):
        monkeypatch.setenv(f"GIT_CONFIG_KEY_{index}", key)
        monkeypatch.setenv(f"GIT_CONFIG_VALUE_{index}", value)


def interval(identifier="ta-aaaaaaaa", **changes):
    value = dict(
        zip(INTERVAL_KEYS, [identifier, "active", "a" * 40, STAMP, None, None])
    )
    value.update(changes)
    return value


def history_bytes(rows):
    ordered = sorted(rows, key=lambda r: (r["node_id"], r["from_time"], r["status"]))
    return b"".join(
        (
            json.dumps({key: row[key] for key in INTERVAL_KEYS}, separators=(",", ":"))
            + "\n"
        ).encode()
        for row in ordered
    )


def raw_members(nodes=None, edges=(), rows=None):
    """Independent archive producer: no call into the product archive encoder."""
    nodes = [node().to_dict()] if nodes is None else nodes
    members = {
        ".researchgraph/profile.yaml": b'name: research\nversion: "1.0"\n',
        ".researchgraph/history.jsonl": history_bytes(
            [interval(n["id"], status=n["status"]) for n in nodes]
            if rows is None
            else rows
        ),
    }
    members.update(
        {f"graph/nodes/{n['id']}.yaml": dump_node(n).encode() for n in nodes}
    )
    members.update(
        {f"graph/edges/{e['id']}.yaml": dump_edge(e).encode() for e in edges}
    )
    return members


def pack(path, members, extra=()):
    """Make valid or adversarial fixtures with ordinary stdlib tar metadata."""
    directories = {".researchgraph/", "graph/", "graph/nodes/", "graph/edges/"}
    for name in members:
        directories.update(
            parent.as_posix() + "/"
            for parent in Path(name).parents
            if parent.as_posix() != "."
        )
    output = io.BytesIO()
    with tarfile.open(
        fileobj=output,
        mode="w",
        format=tarfile.PAX_FORMAT,
        encoding="utf-8",
        errors="strict",
        pax_headers={},
    ) as archive:
        for name in sorted(directories | set(members)):
            info = tarfile.TarInfo(name)
            info.mode = 0o755 if name.endswith("/") else 0o644
            info.type = tarfile.DIRTYPE if name.endswith("/") else tarfile.REGTYPE
            content = b"" if name.endswith("/") else members[name]
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
        for info, content in extra:
            archive.addfile(info, io.BytesIO(content))
    path.write_bytes(zstandard.ZstdCompressor(level=3).compress(output.getvalue()))
    return path


def unpack(path):
    with zstandard.ZstdDecompressor().stream_reader(
        io.BytesIO(path.read_bytes())
    ) as reader:
        raw = reader.read()
    with tarfile.open(
        fileobj=io.BytesIO(raw), mode="r:", encoding="utf-8", errors="strict"
    ) as archive:
        infos = archive.getmembers()
        members = {}
        for info in infos:
            if info.isfile():
                with archive.extractfile(info) as stream:
                    members[info.name] = stream.read()
    return raw, infos, members


def exported(project, path, **kwargs):
    assert project.export(path, actor=ALICE, now=FUTURE, **kwargs) == {
        "path": str(path.resolve())
    }
    return path


def restored_rows(project):
    project.get_nodes()
    return intervals_as_dicts(project.index.connection)


def valid_graph(project):
    validate_graph(
        project.profile,
        {n.id: n for n in project.get_nodes()},
        {e.id: e for e in project.get_edges()},
    )


def destination(tmp_path, exists):
    path = tmp_path / "destination"
    if exists:
        path.mkdir()
    return path


def untouched(path, existed):
    assert path.exists() == existed
    if existed:
        assert list(path.iterdir()) == []
    assert not (path / ".git").exists()


def graph_bytes(project):
    return {
        path.relative_to(project.path).as_posix(): path.read_bytes()
        for path in (project.path / "graph").rglob("*.yaml")
    }


def normalized_queries(value, import_commit):
    value = deepcopy(value)
    for chain in value["history"]:
        for event in chain:
            if event["kind"] in ("approval", "artifact", "supersedes"):
                event["commit"] = import_commit
    return value


def populated(project, count=50):
    """Use actual public writes and git history, not a synthetic one-commit import."""
    tick = 0

    def call(operation, *args, **kwargs):
        nonlocal tick
        tick += 1
        return getattr(project, operation)(
            *args, actor=ALICE, now=NOW + timedelta(seconds=tick), **kwargs
        )

    q = call("propose", "Question", "Question")
    h = call(
        "propose",
        "Hypothesis",
        "Hypothesis",
        edges=[{"type": "answers", "to": q["id"]}],
    )
    call("approve", h["id"])
    p = call(
        "propose",
        "Prediction",
        "Prediction",
        edges=[{"type": "tests", "to": h["id"], "fields": {"expected": "yes"}}],
    )
    call("approve", p["id"])
    r = call("propose", "Result", "Result", fields={"runs": 2, "held_out": True})
    payload = artifact(project, r["id"], content=b"evidence")
    call("attach", r["id"], [payload])
    c = call(
        "propose",
        "Claim",
        "Claim",
        edges=[{"type": "cites", "to": r["id"]}, {"type": "supports", "to": h["id"]}],
    )
    call("approve", c["id"])
    call("link", "pins", h["id"], r["id"], fields={"artifact": payload})
    call("propose", "Note", "Note", edges=[{"type": "about", "to": h["id"]}])
    call("instruct", h["id"], "Consider the evidence")
    task = call("propose", "Task", "Task", edges=[{"type": "addresses", "to": p["id"]}])
    call("claim", task["id"], 1)
    call("release", task["id"], usage={"tokens": 2})
    call("budget", h["id"], tokens=10)
    call("tag", task["id"], add=["review"], priority=3)
    for index in range(count - len(project.get_nodes())):
        call("propose", "Task", f"Task {index}")
    assert len(project.get_nodes()) >= count
    assert int(git(project.path, "rev-list", "--count", "HEAD")) > count
    return {
        "q": q["id"],
        "h": h["id"],
        "c": c["id"],
        "task": task["id"],
        "result": r["id"],
    }


@pytest.mark.parametrize("historical", [False, True])
def test_fifty_node_history_every_query_roundtrip(
    project, tmp_path, index_mode, historical
):
    ids = populated(project)
    boundary = NOW + timedelta(seconds=14) if historical else None
    selected_nodes = {
        record["id"]
        for record in project.where(actor=ALICE, now=FUTURE, as_of=boundary)
    }
    selected_edges = {
        edge.id
        for edge in project.get_edges()
        if (boundary is None or edge.created <= boundary)
        and edge.from_ in selected_nodes
        and edge.to in selected_nodes
    }
    expected_graph = {
        path: content
        for path, content in graph_bytes(project).items()
        if Path(path).stem in selected_nodes | selected_edges
    }
    before = source(project)
    archive = exported(
        project,
        tmp_path.parent / (tmp_path.name + "-full.tar.zst"),
        include_artifacts=True,
        as_of=boundary,
    )
    assert source(project) == before
    assert not (project.path / ".researchgraph/history.jsonl").exists()
    _, _, members = unpack(archive)
    rows = [
        json.loads(line)
        for line in members[".researchgraph/history.jsonl"].splitlines()
    ]
    assert members[".researchgraph/history.jsonl"] == history_bytes(rows)
    assert all(tuple(row) == INTERVAL_KEYS for row in rows)
    with Project.import_(
        archive, tmp_path / "imported", actor=ALICE, now=FUTURE
    ) as imported:
        assert graph_bytes(imported) == expected_graph
        assert (imported.path / ".researchgraph/profile.yaml").read_bytes() == (
            project.path / ".researchgraph/profile.yaml"
        ).read_bytes()
        assert (imported.path / ".researchgraph/history.jsonl").read_bytes() == members[
            ".researchgraph/history.jsonl"
        ]
        assert git(imported.path, "log", "--format=%s") == "rg: import"
        assert git(imported.path, "show", "-s", "--format=%at%n%ct") == "\n".join(
            [str(int(FUTURE.timestamp()))] * 2
        )
        commit = git(imported.path, "rev-parse", "HEAD")
        assert restored_rows(imported) == [
            row for row in restored_rows(project) if row["node_id"] in selected_nodes
        ]
        times = sorted(
            {
                row[key]
                for row in rows
                for key in ("from_time", "to_time")
                if row[key] is not None
                and (boundary is None or row[key] <= format_timestamp(boundary))
            }
        )
        for as_of in [None, *times] if boundary is None else times:
            cold(project, index_mode)
            cold(imported, index_mode)
            expected = normalized_queries(queries(project, ids, FUTURE, as_of), commit)
            assert queries(imported, ids, FUTURE, as_of) == expected
        # Record provenance is intentionally new; interval commit identities are not.
        events = imported.history(ids["result"], actor=ALICE, now=FUTURE)
        assert all(
            event["commit"] == commit
            for event in events
            if event["kind"] in ("approval", "artifact")
        )
        assert any(
            event["commit"] != commit for event in events if event["kind"] == "interval"
        )


@pytest.mark.parametrize("pending_then_tag", [False, True])
def test_history_supersedes_provenance_is_not_silently_lost(
    project, tmp_path, pending_then_tag
):
    original = project.propose("Task", "old", **CALL)
    replacement = project.supersede(
        original["id"],
        actor=ALICE,
        now=NOW + timedelta(seconds=1),
        commit=not pending_then_tag,
    )
    if pending_then_tag:
        project.tag(
            replacement["id"],
            add=["published"],
            actor=ALICE,
            now=NOW + timedelta(seconds=2),
        )
        events = project.history(replacement["id"], actor=ALICE, now=FUTURE)
        assert (
            next(row for row in events if row["kind"] == "supersedes")["commit"] is None
        )
        assert next(
            row
            for row in events
            if row["kind"] == "interval" and row["node"] == replacement["id"]
        )["commit"] == git(project.path, "rev-parse", "HEAD")
    archive = exported(project, tmp_path / "supersedes.tar.zst")
    with Project.import_(
        archive, tmp_path / "restored", actor=ALICE, now=FUTURE
    ) as imported:
        expected = project.history(replacement["id"], actor=ALICE, now=FUTURE)
        commit = git(imported.path, "rev-parse", "HEAD")
        for event in expected:
            if event["kind"] in ("approval", "artifact", "supersedes"):
                event["commit"] = commit
        for mode in ("kept", "deleted"):
            cold(imported, mode)
            assert (
                imported.history(replacement["id"], actor=ALICE, now=FUTURE) == expected
            )


@pytest.mark.parametrize("include_artifacts", [False, True])
def test_export_read_only_deterministic_and_does_not_rewrite_source(
    project, tmp_path, include_artifacts
):
    value = project.propose("Task", "T", **CALL)
    path = artifact(project, value["id"])
    project.attach(value["id"], [path], **CALL)
    before = source(project)
    with Project.open(project.path, read_only=True) as readonly:
        first = exported(
            readonly,
            tmp_path.parent / (tmp_path.name + "-one.tar.zst"),
            include_artifacts=include_artifacts,
        )
        second = tmp_path.parent / (tmp_path.name + "-two.tar.zst")
        assert readonly.export(
            second,
            include_artifacts=include_artifacts,
            actor=BOB,
            role="reviewer",
            now=FUTURE,
        ) == {"path": str(second.resolve())}
    assert first.read_bytes() == second.read_bytes()
    assert source(project) == before
    assert not (project.path / ".researchgraph/history.jsonl").exists()
    _, _, members = unpack(first)
    assert (path in members) == include_artifacts
    # Missing payload is legitimate when only its historical record is exported.
    with Project.import_(
        first, tmp_path / "restored", actor=ALICE, now=FUTURE
    ) as imported:
        assert (
            imported.get_node(value["id"]).artifacts
            == project.get_node(value["id"]).artifacts
        )
        assert (imported.path / path).exists() == include_artifacts


def test_reexport_imported_project_never_rewrites_source_history(tmp_path):
    archive = pack(tmp_path / "input.tar.zst", raw_members())
    with Project.import_(archive, tmp_path / "project", **CALL) as project:
        before = source(project)
        exported(project, tmp_path / "again.tar.zst")
        assert source(project) == before
        assert (
            project.path / ".researchgraph/history.jsonl"
        ).read_bytes() == raw_members()[".researchgraph/history.jsonl"]


def test_canonical_members_metadata_long_unicode_and_current_artifact_bytes(
    project, tmp_path
):
    value = project.propose("Task", "T", **CALL)
    names = ["x" * 101, "관측.png", "/".join(["segment" * 20] * 10) + "/out"]
    paths = [
        artifact(project, value["id"], name=name, content=b"old") for name in names
    ]
    project.attach(value["id"], paths, **CALL)
    for path in paths:
        (project.path / path).write_bytes(b"new bytes")
    project.attach(value["id"], paths, actor=ALICE, now=NOW + timedelta(seconds=1))
    records = load_node(project.path / f"graph/nodes/{value['id']}.yaml")["artifacts"]
    first = exported(project, tmp_path / "one.tar.zst", include_artifacts=True)
    for path in [project.path / p for p in paths] + list(
        (project.path / "graph").rglob("*.yaml")
    ):
        path.chmod(0o600)
        os.utime(path, (123456789, 123456789))
    for path in [project.path / "graph", project.path / "artifacts"]:
        path.chmod(0o700)
        os.utime(path, (123456789, 123456789))
    second = exported(project, tmp_path / "two.tar.zst", include_artifacts=True)
    assert first.read_bytes() == second.read_bytes()
    clone = tmp_path.parent / (tmp_path.name + "-clone")
    git(project.path, "clone", str(project.path), str(clone))
    with Project.open(clone) as checkout:
        for path in [clone / p for p in paths] + list(
            (clone / "graph").rglob("*.yaml")
        ):
            path.chmod(0o640)
            os.utime(path, (987654321, 987654321))
            if os.geteuid() == 0:
                os.chown(path, 12345, 12345)
        third = exported(checkout, tmp_path / "three.tar.zst", include_artifacts=True)
        assert third.read_bytes() == first.read_bytes()
    raw, infos, members = unpack(first)
    assert first.read_bytes() == zstandard.ZstdCompressor(level=3).compress(raw)
    assert len(raw) % tarfile.RECORDSIZE == 0
    assert raw.endswith(b"\0" * 1024)
    expected_files = set(graph_bytes(project)) | {
        ".researchgraph/profile.yaml",
        ".researchgraph/history.jsonl",
        *paths,
    }
    expected_dirs = {
        ".researchgraph/",
        "graph/",
        "graph/nodes/",
        "graph/edges/",
        "artifacts/",
    }
    for path in expected_files:
        expected_dirs.update(
            parent.as_posix() + "/"
            for parent in Path(path).parents
            if parent.as_posix() != "."
        )
    names_in_archive = [
        info.name.rstrip("/") + "/" if info.isdir() else info.name for info in infos
    ]
    assert names_in_archive == sorted(expected_dirs | expected_files)
    assert set(members) == expected_files  # No manifest or source index/git metadata.
    expected_members = {
        **graph_bytes(project),
        ".researchgraph/profile.yaml": (
            project.path / ".researchgraph/profile.yaml"
        ).read_bytes(),
        ".researchgraph/history.jsonl": history_bytes(restored_rows(project)),
        **{path: b"new bytes" for path in paths},
    }
    reference = pack(tmp_path / "independent-reference.tar.zst", expected_members)
    assert first.read_bytes() == reference.read_bytes()
    for info in infos:
        assert info.type in (tarfile.REGTYPE, tarfile.DIRTYPE)
        assert (info.mtime, info.uid, info.gid, info.uname, info.gname) == (
            0,
            0,
            0,
            "",
            "",
        )
        assert info.mode == (0o755 if info.isdir() else 0o644)
        assert info.size == (0 if info.isdir() else len(members[info.name]))
        assert set(info.pax_headers) <= {"path", "size"}
    with Project.import_(
        first, tmp_path / "restored", actor=ALICE, now=FUTURE
    ) as imported:
        assert (
            load_node(imported.path / f"graph/nodes/{value['id']}.yaml")["artifacts"]
            == records
        )
        for path in paths:
            assert (imported.path / path).read_bytes() == b"new bytes"
        assert all(r["sha256"] == sha256(b"old").hexdigest() for r in records[:3])
        assert all(r["sha256"] == sha256(b"new bytes").hexdigest() for r in records[3:])


@pytest.mark.parametrize(
    "name,size,digest",
    [
        (
            "artifacts/ta-aaaaaaaa/" + "x" * 101,
            3,
            "f17f233da7e28235d606113613bdf7da795753a635378ec5404d0881ca124986",
        ),
        (
            "artifacts/ta-aaaaaaaa/관측.png",
            3,
            "4bd1799d7cd2503f97ec9554a891b3afd9f85008c7b372fb171499789800b661",
        ),
        (
            "artifacts/ta-aaaaaaaa/large.bin",
            1 << 33,
            "a1bc81a89786a4534a0d913a7a9b9ca47b6267878de5ec289e377504a58aae77",
        ),
    ],
)
def test_frozen_canonical_pax_header_bytes_without_payload(name, size, digest):
    # Import inside the test: an absent implementation must not prevent unrelated
    # acceptance modules from being collected for the genuine RED run.
    from researchgraph.store.archive import _tar_info

    info = _tar_info(name, size)
    assert isinstance(info, tarfile.TarInfo)
    assert (info.name, info.size, info.type, info.mode) == (
        name,
        size,
        tarfile.REGTYPE,
        0o644,
    )
    assert (
        info.uid,
        info.gid,
        info.mtime,
        info.uname,
        info.gname,
        info.pax_headers,
    ) == (0, 0, 0, "", "", {})
    header = info.tobuf(format=tarfile.PAX_FORMAT, encoding="utf-8", errors="strict")
    assert len(header) == 1536
    assert sha256(header).hexdigest() == digest
    directory = _tar_info("graph/nodes/", directory=True)
    assert (directory.name, directory.type, directory.mode, directory.size) == (
        "graph/nodes/",
        tarfile.DIRTYPE,
        0o755,
        0,
    )


def test_since_changed_edge_both_endpoints_diamond_retired_closure_and_history(
    project, tmp_path
):
    # Diamond: Claim -> Result A/B -> shared Reference, plus a retired evidence
    # relation and a supersedes relation. All closure directions are outgoing.
    def propose(kind, title):
        return project.propose(kind, title, **CALL)["id"]

    claim = propose("Claim", "claim")
    left, right = propose("Result", "left"), propose("Result", "right")
    shared, retired_target, unrelated = [
        propose("Reference", title) for title in ("shared", "retired", "unrelated")
    ]
    links = [
        project.link("cites", claim, left, **CALL),
        project.link("cites", claim, right, **CALL),
        project.link("derived-from", left, shared, **CALL),
        project.link("derived-from", right, shared, **CALL),
        project.link("derived-from", left, retired_target, **CALL),
    ]
    project.retire(retired_target, "withdrawn", **CALL)
    old = propose("Result", "old")
    replacement = project.supersede(old, **CALL)["id"]
    links.append(project.link("derived-from", right, replacement, **CALL))
    source_id, target_id = propose("Task", "source"), propose("Task", "target")
    cutoff = NOW + timedelta(seconds=1)
    changed_at = NOW + timedelta(seconds=2)
    changed = project.link(
        "relates-to", source_id, target_id, actor=ALICE, now=changed_at
    )
    project.tag(claim, add=["changed"], actor=ALICE, now=changed_at)
    # Changed-by-artifact and changed-by-approval both seed the same closure.
    path = artifact(project, left)
    project.attach(left, [path], actor=ALICE, now=changed_at)
    archived = exported(project, tmp_path / "since.tar.zst", since=cutoff)
    _, _, members = unpack(archived)
    expected_nodes = {
        claim,
        left,
        right,
        shared,
        retired_target,
        old,
        replacement,
        source_id,
        target_id,
    }
    assert unrelated not in expected_nodes
    assert {
        Path(name).stem for name in members if name.startswith("graph/nodes/")
    } == expected_nodes
    closure_edges = {
        e.id
        for e in project.get_edges()
        if e.type in ("cites", "derived-from", "supersedes")
        and e.from_ in expected_nodes
    }
    assert {
        Path(name).stem for name in members if name.startswith("graph/edges/")
    } == closure_edges | {changed["id"]}
    for name, content in members.items():
        if name.startswith("graph/"):
            assert content == (project.path / name).read_bytes()
    rows = [
        json.loads(line)
        for line in members[".researchgraph/history.jsonl"].splitlines()
    ]
    assert {row["node_id"] for row in rows} == expected_nodes
    with Project.import_(
        archived, tmp_path / "restored", actor=ALICE, now=FUTURE
    ) as imported:
        valid_graph(imported)
        assert {n.id for n in imported.get_nodes()} == expected_nodes


def test_since_boundary_is_strict_and_no_change_archive_is_valid(project, tmp_path):
    project.propose("Task", "at boundary", **CALL)
    archive = exported(project, tmp_path / "empty.tar.zst", since=NOW)
    _, _, members = unpack(archive)
    assert set(members) == {
        ".researchgraph/profile.yaml",
        ".researchgraph/history.jsonl",
    }
    assert members[".researchgraph/history.jsonl"] == b""
    with Project.import_(
        archive, tmp_path / "restored", actor=ALICE, now=NOW - timedelta(days=1)
    ) as imported:
        assert imported.get_nodes() == []


@pytest.mark.parametrize("exists", [False, True])
def test_import_empty_or_absent_destination_preserves_raw_bytes_and_one_commit(
    tmp_path, exists
):
    members = raw_members()
    # Older valid YAML must be restored, not passed through a canonical writer.
    original = members["graph/nodes/ta-aaaaaaaa.yaml"]
    members["graph/nodes/ta-aaaaaaaa.yaml"] = original.replace(
        b"title: A task\n", b'title: "A task"\n'
    )
    archive = pack(tmp_path / "input.tar.zst", members)
    target = destination(tmp_path, exists)
    with Project.import_(archive, target, **CALL) as imported:
        assert isinstance(imported, Project)
        assert imported.path == target.resolve()
        assert git(target, "log", "--format=%s") == "rg: import"
        assert git(target, "rev-list", "--count", "HEAD") == "1"
        for name, content in members.items():
            assert (target / name).read_bytes() == content
        assert git(target, "status", "--porcelain") == ""
        assert ".researchgraph/index.sqlite" not in git(target, "ls-files")
        assert ".researchgraph/lock" not in git(target, "ls-files")


@pytest.mark.parametrize("commit", [False, True])
def test_import_no_commit_obeys_write_contract(tmp_path, commit):
    archive = pack(tmp_path / "input.tar.zst", raw_members())
    with Project.import_(
        archive, tmp_path / "restored", commit=commit, **CALL
    ) as imported:
        assert imported.get_node("ta-aaaaaaaa").status.value == "active"
        if commit:
            assert git(imported.path, "log", "--format=%s") == "rg: import"
        else:
            assert imported._git.head() is None
            assert (imported.path / ".researchgraph/history.jsonl").is_file()


@pytest.mark.parametrize("exists", [False, True])
@pytest.mark.parametrize(
    "position",
    [
        "created",
        "updated",
        "approval",
        "artifact",
        "lease_acquired",
        "lease_expires",
        "edge_created",
        "edge_retired",
        "history_from",
        "history_to",
    ],
)
def test_import_all_ten_clock_floor_positions_before_and_equal(
    tmp_path, exists, position
):
    first, second = node().to_dict(), node("ta-bbbbbbbb").to_dict()
    edge = Edge(
        "ed-aaaaaaaa", "relates-to", first["id"], second["id"], ALICE, NOW
    ).to_dict()
    rows = [interval(first["id"]), interval(second["id"])]
    if position in ("created", "updated"):
        first[position] = FUTURE_STAMP
        if position == "created":
            first["updated"] = FUTURE_STAMP
    elif position == "approval":
        first["approvals"] = [
            {
                "actor": ALICE.to_dict(),
                "at": FUTURE_STAMP,
                "what": "budget",
                "note": "none",
            }
        ]
    elif position == "artifact":
        first["artifacts"] = [
            {
                "path": f"artifacts/{first['id']}/missing",
                "sha256": "a" * 64,
                "bytes": 1,
                "added_at": FUTURE_STAMP,
                "added_by": ALICE.to_dict(),
            }
        ]
    elif position.startswith("lease_"):
        first["status"] = "running"
        first["lease"] = {
            "actor": BOT.to_dict(),
            "acquired_at": STAMP,
            "expires_at": FUTURE_STAMP,
        }
        if position == "lease_acquired":
            first["lease"]["acquired_at"] = FUTURE_STAMP
            first["lease"]["expires_at"] = format_timestamp(
                FUTURE + timedelta(seconds=1)
            )
        rows[0]["status"] = "running"
    elif position == "edge_created":
        edge["created"] = FUTURE_STAMP
    elif position == "edge_retired":
        edge.update(retired=True, retired_at=FUTURE_STAMP)
    elif position == "history_from":
        rows[0]["from_time"] = FUTURE_STAMP
    else:
        rows[0].update(to_time=FUTURE_STAMP, to_commit="b" * 40)
        rows.append(
            interval(
                first["id"],
                status="running",
                from_time=FUTURE_STAMP,
                from_commit="b" * 40,
            )
        )
        first["status"] = "running"
        first["lease"] = {
            "actor": BOT.to_dict(),
            "acquired_at": STAMP,
            "expires_at": FUTURE_STAMP,
        }
    members = raw_members([first, second], [edge], rows)
    archive = pack(tmp_path / "input.tar.zst", members)
    target = destination(tmp_path, exists)
    refusal(
        "clock_skew",
        Project.import_,
        archive,
        target,
        actor=ALICE,
        now=FUTURE - timedelta(seconds=1),
    )
    untouched(target, exists)
    floor = FUTURE + timedelta(seconds=1) if position == "lease_acquired" else FUTURE
    with Project.import_(archive, target, actor=ALICE, now=floor) as imported:
        for name, content in members.items():
            assert (imported.path / name).read_bytes() == content


@pytest.mark.parametrize(
    "position",
    [
        "created",
        "updated",
        "approval",
        "artifact",
        "lease_acquired",
        "lease_expires",
        "edge_created",
        "edge_retired",
        "history_from",
        "history_to",
    ],
)
@pytest.mark.parametrize(
    "invalid", ["not-a-time", "2026-10-20", "2026-10-20T00:00:00+00:00", 123]
)
def test_invalid_timestamp_encoding_is_bad_archive_not_clock_skew(
    tmp_path, position, invalid
):
    first, second = node().to_dict(), node("ta-bbbbbbbb").to_dict()
    edge = Edge(
        "ed-aaaaaaaa", "relates-to", first["id"], second["id"], ALICE, NOW
    ).to_dict()
    rows = [interval(first["id"]), interval(second["id"])]
    if position in ("created", "updated"):
        first[position] = invalid
    elif position == "approval":
        first["approvals"] = [
            {"actor": ALICE.to_dict(), "at": invalid, "what": "budget"}
        ]
    elif position == "artifact":
        first["artifacts"] = [
            {
                "path": f"artifacts/{first['id']}/x",
                "sha256": "a" * 64,
                "bytes": 0,
                "added_at": invalid,
                "added_by": ALICE.to_dict(),
            }
        ]
    elif position.startswith("lease_"):
        first["status"] = "running"
        first["lease"] = {
            "actor": BOT.to_dict(),
            "acquired_at": STAMP,
            "expires_at": FUTURE_STAMP,
        }
        first["lease"][
            "acquired_at" if position == "lease_acquired" else "expires_at"
        ] = invalid
        rows[0]["status"] = "running"
    elif position.startswith("edge_"):
        edge["created" if position == "edge_created" else "retired_at"] = invalid
        if position == "edge_retired":
            edge["retired"] = True
    else:
        rows[0]["from_time" if position == "history_from" else "to_time"] = invalid
        if position == "history_to":
            rows[0]["to_commit"] = "b" * 40
    archive = pack(tmp_path / "bad.tar.zst", raw_members([first, second], [edge], rows))
    target = tmp_path / "absent"
    refusal("bad_archive", Project.import_, archive, target, actor=ALICE, now=FUTURE)
    untouched(target, False)


@pytest.mark.parametrize("exists", [False, True])
@pytest.mark.parametrize(
    "problem",
    [
        "zstd",
        "truncated_zstd",
        "tar",
        "pin",
        "new_profile",
        "unknown_profile",
        "missing_pin",
        "missing_history",
        "yaml",
        "invalid_utf8",
        "history",
        "schema",
        "endpoint",
        "cycle",
        "duplicate_edge",
        "id_mismatch",
    ],
)
def test_malformed_archive_preflight_has_no_destination_effects(
    tmp_path, exists, problem
):
    members = raw_members()
    expected = "bad_archive"
    if problem == "pin":
        members[".researchgraph/profile.yaml"] = b"[]\n"
    elif problem == "new_profile":
        members[".researchgraph/profile.yaml"] = b'name: research\nversion: "9.0"\n'
        expected = "profile_too_new"
    elif problem == "unknown_profile":
        members[".researchgraph/profile.yaml"] = b'name: nonexistent\nversion: "1.0"\n'
    elif problem == "missing_pin":
        del members[".researchgraph/profile.yaml"]
    elif problem == "missing_history":
        del members[".researchgraph/history.jsonl"]
    elif problem == "yaml":
        members["graph/nodes/ta-aaaaaaaa.yaml"] = b"[unterminated\n"
    elif problem == "invalid_utf8":
        members["graph/nodes/ta-aaaaaaaa.yaml"] = b"\xff\xfe\n"
    elif problem == "history":
        members[".researchgraph/history.jsonl"] = b"{}\n"
    elif problem == "schema":
        value = node().to_dict()
        value["fields"] = {"undeclared": True}
        members["graph/nodes/ta-aaaaaaaa.yaml"] = dump_node(value).encode()
    elif problem == "id_mismatch":
        members["graph/nodes/ta-bbbbbbbb.yaml"] = members.pop(
            "graph/nodes/ta-aaaaaaaa.yaml"
        )
    elif problem in ("endpoint", "cycle", "duplicate_edge"):
        a, b = node().to_dict(), node("ta-bbbbbbbb").to_dict()
        edges = [
            Edge("ed-aaaaaaaa", "derived-from", a["id"], b["id"], ALICE, NOW).to_dict()
        ]
        if problem == "endpoint":
            members = raw_members([a], edges)
        else:
            edges.append(
                Edge(
                    "ed-bbbbbbbb",
                    "derived-from",
                    b["id"] if problem == "cycle" else a["id"],
                    a["id"] if problem == "cycle" else b["id"],
                    ALICE,
                    NOW,
                ).to_dict()
            )
            members = raw_members([a, b], edges)
    archive = pack(tmp_path / "bad.tar.zst", members)
    if problem == "zstd":
        archive.write_bytes(b"not zstd or a researchgraph export")
    elif problem == "truncated_zstd":
        archive.write_bytes(archive.read_bytes()[:12])
    elif problem == "tar":
        archive.write_bytes(zstandard.ZstdCompressor().compress(b"not tar"))
    target = destination(tmp_path, exists)
    refusal(expected, Project.import_, archive, target, actor=ALICE, now=FUTURE)
    untouched(target, exists)


@pytest.mark.parametrize(
    "kind",
    [
        "symlink",
        "hardlink",
        "fifo",
        "character",
        "block",
        "traversal",
        "absolute",
        "duplicate",
        "dot_alias",
        "directory_alias",
        "graph_subdirectory",
        "git_config",
        "duplicate_directory",
    ],
)
def test_unsafe_members_rejected_without_outside_writes(tmp_path, kind):
    members = raw_members()
    outside = tmp_path / "outside"
    outside.write_bytes(b"keep")
    name = f"artifacts/ta-aaaaaaaa/{kind}"
    info = tarfile.TarInfo(name)
    content = b""
    if kind in ("symlink", "hardlink"):
        info.type = tarfile.SYMTYPE if kind == "symlink" else tarfile.LNKTYPE
        info.linkname = str(outside)
    elif kind in ("fifo", "character", "block"):
        info.type = {
            "fifo": tarfile.FIFOTYPE,
            "character": tarfile.CHRTYPE,
            "block": tarfile.BLKTYPE,
        }[kind]
    elif kind == "duplicate_directory":
        info.name, info.type = "graph/nodes/", tarfile.DIRTYPE
    else:
        info.name = {
            "traversal": "../outside",
            "absolute": str(outside),
            "duplicate": "graph/nodes/ta-aaaaaaaa.yaml",
            "dot_alias": "graph/nodes/./ta-aaaaaaaa.yaml",
            "directory_alias": "graph/nodes/../nodes/ta-aaaaaaaa.yaml",
            "graph_subdirectory": "graph/nodes/shard/ta-aaaaaaaa.yaml",
            "git_config": ".git/config",
        }[kind]
        content = members["graph/nodes/ta-aaaaaaaa.yaml"]
        info.size = len(content)
    archive = pack(tmp_path / "unsafe.tar.zst", members, [(info, content)])
    target = tmp_path / "destination"
    refusal("bad_archive", Project.import_, archive, target, actor=ALICE, now=FUTURE)
    untouched(target, False)
    assert outside.read_bytes() == b"keep"
    assert set(tmp_path.iterdir()) == {archive, outside}


def test_import_permissions_and_nonempty_are_atomic(tmp_path):
    archive = pack(tmp_path / "input.tar.zst", raw_members())
    target = tmp_path / "absent"
    for kwargs, rule in [
        ({"actor": BOT}, "human_only_approval"),
        ({"actor": ALICE, "read_only": True}, "read_only"),
        ({"actor": ALICE, "role": "nonexistent"}, "wrong_role"),
    ]:
        refusal(rule, Project.import_, archive, target, now=NOW, **kwargs)
        untouched(target, False)
    target.mkdir()
    marker = target / "keep"
    marker.write_bytes(b"not empty")
    refusal("not_empty", Project.import_, archive, target, **CALL)
    assert list(target.iterdir()) == [marker]
    assert marker.read_bytes() == b"not empty"


@pytest.mark.parametrize("commit", [False, True])
@pytest.mark.parametrize("repeat_pending", [False, True])
def test_imported_open_interval_closed_by_committed_and_repeated_pending_claims(
    tmp_path, index_mode, commit, repeat_pending
):
    archive = pack(tmp_path / "input.tar.zst", raw_members())
    with Project.import_(archive, tmp_path / "project", **CALL) as imported:
        identifier = "ta-aaaaaaaa"
        first_time = NOW + timedelta(seconds=1)
        cold(imported, index_mode)
        imported.claim(identifier, 1, actor=BOT, now=first_time, commit=commit)
        if repeat_pending:
            imported.release(
                identifier,
                actor=BOT,
                now=first_time + timedelta(seconds=1),
                commit=False,
            )
            imported.claim(
                identifier,
                1,
                actor=BOT,
                now=first_time + timedelta(seconds=2),
                commit=False,
            )
        rows = restored_rows(imported)
        assert sum(row["to_time"] is None for row in rows) == 1
        assert rows[-1]["status"] == "running"
        assert rows[0]["from_commit"] == "a" * 40
        assert rows[0]["to_time"] == format_timestamp(
            first_time + timedelta(seconds=2)
            if repeat_pending and not commit
            else first_time
        )
        assert rows[0]["to_commit"] == (
            git(imported.path, "rev-parse", "HEAD") if commit else None
        )
        assert all(
            row["to_time"] is None or row["to_time"] >= row["from_time"] for row in rows
        )
        assert (
            imported.show(identifier, actor=ALICE, now=FUTURE, as_of=STAMP)["status"]
            == "active"
        )
        before = imported.history(identifier, actor=ALICE, now=FUTURE)
        cold(imported)
        assert restored_rows(imported) == rows
        assert imported.history(identifier, actor=ALICE, now=FUTURE) == before
        assert (
            imported.show(identifier, actor=ALICE, now=FUTURE, as_of=STAMP)["status"]
            == "active"
        )


def test_import_validates_restored_fields_without_materializing_new_defaults(tmp_path):
    from researchgraph.profiles import NodeType, Profile

    Profile.register(
        Profile(
            "archive_defaults",
            "1.0",
            [
                NodeType(
                    "Task",
                    "ta",
                    {"properties": {"mode": {"type": "string", "default": "fast"}}},
                    claimable=True,
                )
            ],
            [],
            [],
            ["owner"],
            [],
        )
    )
    members = raw_members()
    members[".researchgraph/profile.yaml"] = b'name: archive_defaults\nversion: "1.0"\n'
    archive = pack(tmp_path / "input.tar.zst", members)
    with Project.import_(archive, tmp_path / "restored", **CALL) as imported:
        assert (imported.path / "graph/nodes/ta-aaaaaaaa.yaml").read_bytes() == members[
            "graph/nodes/ta-aaaaaaaa.yaml"
        ]
        assert imported.get_node("ta-aaaaaaaa").fields == {}


def test_profile_callbacks_observe_import_and_hook_only_after_commit(tmp_path):
    from researchgraph.profiles import NodeType, Profile

    validations, events = [], []

    def validate(project, write):
        validations.append(write.op)
        assert project._before_state() == ({}, {})
        assert project.get_nodes() == []
        assert project.get_edges() == []
        assert project.ready(actor=ALICE, now=NOW) == {"items": []}
        assert project.where(actor=ALICE, now=NOW) == []
        assert not project.path.exists() or list(project.path.iterdir()) == []

    def event(project, write, commit):
        events.append(write.op)
        assert commit == git(project.path, "rev-parse", "HEAD")
        assert project.get_node("ta-aaaaaaaa").id == "ta-aaaaaaaa"

    def forbidden_expansion(*args):
        raise AssertionError("Import must not invoke expansion_hook")

    Profile.register(
        Profile(
            "archive_hooks",
            "1.0",
            [NodeType("Task", "ta", {}, claimable=True)],
            [],
            [],
            ["owner"],
            [validate],
            expansion_hook=forbidden_expansion,
            event_hooks=[event],
        )
    )
    members = raw_members()
    members[".researchgraph/profile.yaml"] = b'name: archive_hooks\nversion: "1.0"\n'
    archive = pack(tmp_path / "input.tar.zst", members)
    with Project.import_(archive, tmp_path / "committed", **CALL):
        assert validations == ["import"]
        assert events == ["import"]
    with Project.import_(archive, tmp_path / "pending", commit=False, **CALL):
        assert validations == ["import", "import"]
        assert events == ["import"]


@pytest.mark.parametrize("complete", [False, True])
def test_export_as_of_selects_current_bytes_and_complete_history(
    project, tmp_path, complete
):
    value = project.propose("Task", "T", **CALL)
    other = project.propose("Result", "R", **CALL)
    claim = project.propose("Claim", "C", **CALL)
    at = NOW + timedelta(seconds=1)
    project.claim(value["id"], 1, actor=BOT, now=at)
    relative = artifact(project, other["id"])
    project.attach(other["id"], [relative], actor=ALICE, now=at)
    if complete:
        project.complete(value["id"], "Done", actor=BOT, now=NOW + timedelta(seconds=2))
    future = project.propose(
        "Hypothesis", "H2", actor=ALICE, now=NOW + timedelta(seconds=3)
    )
    excluded_payload = artifact(project, future["id"], content=b"not selected")
    project.attach(
        future["id"], [excluded_payload], actor=ALICE, now=NOW + timedelta(seconds=3)
    )
    later_edge = project.link(
        "cites", claim["id"], other["id"], actor=ALICE, now=NOW + timedelta(seconds=4)
    )
    boundary = at if complete else NOW
    archive = exported(
        project, tmp_path / "past.tar.zst", as_of=boundary, include_artifacts=True
    )
    cold(project, "deleted")
    repeated = exported(
        project,
        tmp_path / "past-rebuilt.tar.zst",
        as_of=boundary,
        include_artifacts=True,
    )
    assert repeated.read_bytes() == archive.read_bytes()
    _, _, members = unpack(archive)
    for identifier in (value["id"], other["id"], claim["id"]):
        path = f"graph/nodes/{identifier}.yaml"
        assert members[path] == (project.path / path).read_bytes()
    assert members[relative] == (project.path / relative).read_bytes()
    assert f"graph/nodes/{future['id']}.yaml" not in members
    assert excluded_payload not in members
    assert f"graph/edges/{later_edge['id']}.yaml" not in members
    expected_rows = [
        row for row in restored_rows(project) if row["node_id"] != future["id"]
    ]
    assert members[".researchgraph/history.jsonl"] == history_bytes(expected_rows)
    with Project.import_(
        archive, tmp_path / "restored", actor=ALICE, now=FUTURE
    ) as imported:
        assert restored_rows(imported) == expected_rows
        for mode in ("kept", "deleted"):
            cold(imported, mode)
            actual = imported.show(value["id"], actor=ALICE, now=FUTURE, as_of=boundary)
            assert actual == project.show(
                value["id"], actor=ALICE, now=FUTURE, as_of=boundary
            )
            assert actual["status"] == ("running" if complete else "active")
            if complete:
                assert actual["lease"] is None
                assert actual["flags"]["lease_expired"] is False
                assert actual["flags"]["ready"] is False


@pytest.mark.parametrize("include_artifacts", [False, True])
def test_since_as_of_selection_closure_current_payloads_and_rebuilt_bytes(
    project, tmp_path, include_artifacts
):
    claim = project.propose("Claim", "C", **CALL)["id"]
    result = project.propose("Result", "R", **CALL)["id"]
    reference = project.propose("Reference", "Reference", **CALL)["id"]
    derived = project.link("derived-from", result, reference, **CALL)["id"]
    since = NOW + timedelta(seconds=1)
    boundary = NOW + timedelta(seconds=3)
    changed = project.link(
        "cites", claim, result, actor=ALICE, now=NOW + timedelta(seconds=2)
    )["id"]
    project.retire(
        reference, "future retirement", actor=ALICE, now=NOW + timedelta(seconds=4)
    )
    payload = artifact(
        project, result, content=b"current bytes after the historical window"
    )
    project.attach(result, [payload], actor=ALICE, now=NOW + timedelta(seconds=5))
    project.propose("Task", "future", actor=ALICE, now=NOW + timedelta(seconds=6))
    assert {
        record["id"]
        for record in project.since(since, actor=ALICE, now=FUTURE, as_of=boundary)
    } == {changed}
    selected = {claim, result, reference, derived, changed}
    expected = {
        path: content
        for path, content in graph_bytes(project).items()
        if Path(path).stem in selected
    }
    options = {
        "since": since,
        "as_of": boundary,
        "include_artifacts": include_artifacts,
    }
    first = exported(project, tmp_path / "kept.tar.zst", **options)
    cold(project, "deleted")
    second = exported(project, tmp_path / "rebuilt.tar.zst", **options)
    assert first.read_bytes() == second.read_bytes()
    _, _, members = unpack(first)
    assert {
        path: data for path, data in members.items() if path.startswith("graph/")
    } == expected
    assert (payload in members) == include_artifacts
    if include_artifacts:
        assert members[payload] == (project.path / payload).read_bytes()
    with Project.import_(
        first, tmp_path / "restored", actor=ALICE, now=FUTURE
    ) as imported:
        valid_graph(imported)
        assert graph_bytes(imported) == expected
        for mode in ("kept", "deleted"):
            cold(imported, mode)
            assert imported.show(
                reference, actor=ALICE, now=FUTURE, as_of=boundary
            ) == project.show(reference, actor=ALICE, now=FUTURE, as_of=boundary)


@pytest.mark.cli
@pytest.mark.usefixtures("cli_cache_policy")
@pytest.mark.parametrize("placement", ["argument", "project", "cwd"])
def test_cli_export_import_json_common_options_and_destination_fallback(
    project, tmp_path, monkeypatch, placement
):
    project.propose("Task", "T", **CALL)
    out = tmp_path / "cli.tar.zst"
    result = succeeds(
        invoke(
            project.path,
            "export",
            str(out),
            "--include-artifacts",
            "--since",
            format_timestamp(NOW - timedelta(seconds=1)),
            "--as-of",
            STAMP,
        )
    )
    assert result == {"path": str(out.resolve())}
    target = tmp_path / "destination"
    if placement == "argument":
        result = invoke(None, "import", str(out), str(target), now=FUTURE)
    elif placement == "project":
        result = invoke(target, "import", str(out), now=FUTURE)
    else:
        target.mkdir()
        monkeypatch.chdir(target)
        result = invoke(None, "import", str(out), now=FUTURE)
    succeeds(result)
    with Project.open(target) as imported:
        assert graph_bytes(imported) == graph_bytes(project)
        assert git(target, "log", "--format=%s") == "rg: import"


@pytest.mark.cli
@pytest.mark.usefixtures("cli_cache_policy")
def test_cli_export_read_only_and_import_refusals(project, tmp_path):
    out = tmp_path / "cli.tar.zst"
    succeeds(invoke(project.path, "export", str(out), "--read-only"))
    for options, rule in [
        (["--as-agent", "agent1"], "human_only_approval"),
        (["--read-only"], "read_only"),
        (["--role", "nonexistent"], "wrong_role"),
    ]:
        target = tmp_path / rule
        refused(
            invoke(None, "import", str(out), str(target), *options, now=FUTURE), rule
        )
        untouched(target, False)
    bad = tmp_path / "bad.tar.zst"
    bad.write_bytes(b"garbage")
    refused(
        invoke(None, "import", str(bad), str(tmp_path / "bad"), now=FUTURE),
        "bad_archive",
    )
    untouched(tmp_path / "bad", False)


@settings(max_examples=8, deadline=None)
@given(
    st.lists(
        st.sampled_from(
            ["tag", "attach", "claim", "release", "invalid_permission", "invalid_cycle"]
        ),
        min_size=1,
        max_size=8,
    ),
    st.sets(
        st.tuples(st.integers(0, 5), st.integers(0, 5)).filter(
            lambda pair: pair[0] < pair[1]
        ),
        max_size=8,
    ),
)
def test_hypothesis_append_only_dag_permissions_and_file_index_roundtrips(
    actions, topology
):
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        with Project.init(repository(root / "source"), actor=ALICE, now=NOW) as project:
            ids = populated(project, count=50)
            task = ids["task"]
            parent = project.propose(
                "Task", "parent", actor=ALICE, now=NOW + timedelta(minutes=2)
            )["id"]
            project.link(
                "derived-from",
                task,
                parent,
                actor=ALICE,
                now=NOW + timedelta(minutes=2),
            )
            vertices = sorted(
                n.id
                for n in project.get_nodes()
                if n.type == "Task" and n.id not in (task, parent)
            )
            for left, right in sorted(topology):
                project.link(
                    "derived-from",
                    vertices[left],
                    vertices[right],
                    actor=ALICE,
                    now=NOW + timedelta(minutes=2),
                )
            for index, action in enumerate(actions):
                at = NOW + timedelta(minutes=3, seconds=index * 3)
                before_nodes = {n.id: n.to_dict() for n in project.get_nodes()}
                before_edges = {e.id: e.to_dict() for e in project.get_edges()}
                if action == "tag":
                    project.tag(task, add=[f"tag-{index}"], actor=BOT, now=at)
                elif action == "attach":
                    relative = artifact(
                        project,
                        task,
                        name=f"record-{index}",
                        content=str(index).encode(),
                    )
                    project.attach(task, [relative], actor=BOT, now=at)
                elif action == "claim":
                    if project.get_node(task).status.value == "running":
                        project.release(task, actor=ALICE, now=at)
                    project.claim(task, 1, actor=BOT, now=at)
                elif action == "release":
                    if project.get_node(task).status.value != "running":
                        project.claim(task, 1, actor=BOT, now=at)
                    project.release(task, actor=BOT, now=at + timedelta(seconds=1))
                else:
                    before = source(project)
                    if action == "invalid_permission":
                        refusal(
                            "human_only_approval",
                            project.retire,
                            task,
                            "no",
                            actor=BOT,
                            now=at,
                        )
                    else:
                        refusal(
                            "cycle",
                            project.link,
                            "derived-from",
                            parent,
                            task,
                            actor=ALICE,
                            now=at,
                        )
                    assert source(project) == before
                from researchgraph.model import EDGE_IMMUTABLE_KEYS, NODE_IMMUTABLE_KEYS

                assert set(before_nodes) <= {n.id for n in project.get_nodes()}
                assert set(before_edges) <= {e.id for e in project.get_edges()}
                for current in project.get_nodes():
                    old = before_nodes[current.id]
                    new = current.to_dict()
                    assert {k: old[k] for k in NODE_IMMUTABLE_KEYS} == {
                        k: new[k] for k in NODE_IMMUTABLE_KEYS
                    }
                    assert new["approvals"][: len(old["approvals"])] == old["approvals"]
                    assert new["artifacts"][: len(old["artifacts"])] == old["artifacts"]
                for current in project.get_edges():
                    old = before_edges[current.id]
                    new = current.to_dict()
                    assert {k: old[k] for k in EDGE_IMMUTABLE_KEYS} == {
                        k: new[k] for k in EDGE_IMMUTABLE_KEYS
                    }
                    assert not old["retired"] or new["retired"]
                valid_graph(project)
                archive = exported(
                    project, root / f"roundtrip-{index}.tar.zst", include_artifacts=True
                )
                with Project.import_(
                    archive, root / f"restored-{index}", actor=ALICE, now=FUTURE
                ) as imported:
                    commit = git(imported.path, "rev-parse", "HEAD")
                    expected = normalized_queries(queries(project, ids, FUTURE), commit)
                    for mode in ("kept", "deleted"):
                        cold(project, mode)
                        cold(imported, mode)
                        assert queries(imported, ids, FUTURE) == expected
                        assert (
                            normalized_queries(queries(project, ids, FUTURE), commit)
                            == expected
                        )
                        assert graph_bytes(project) == graph_bytes(imported)
                        valid_graph(imported)


def test_import_nested_in_unrelated_git_worktree_creates_own_root(tmp_path):
    # Parent HEAD is deliberately later than import now: it is not this new
    # project's clock floor, history, staging area, or repository identity.
    root = repository(tmp_path / "outer")
    with Project.init(root, actor=ALICE, now=FUTURE + timedelta(days=1)):
        staged, unstaged = root / "staged.txt", root / "unstaged.txt"
        staged.write_bytes(b"staged")
        unstaged.write_bytes(b"untracked")
        git(root, "add", "staged.txt")
        staged.write_bytes(b"working tree differs")
        head = git(root, "rev-parse", "HEAD")
        cached = git(root, "diff", "--cached", "--binary")
        worktree = git(root, "diff", "--binary")
        archive = pack(tmp_path / "input.tar.zst", raw_members())
        target = root / "nested" / "restored"
        target.mkdir(parents=True)
        with Project.import_(archive, target, **CALL) as imported:
            assert (target / ".git").is_dir()
            assert git(target, "rev-parse", "--show-toplevel") == str(target.resolve())
            assert git(target, "log", "--format=%s") == "rg: import"
            assert git(target, "rev-list", "--count", "HEAD") == "1"
            assert imported.get_node("ta-aaaaaaaa").id == "ta-aaaaaaaa"
        assert git(root, "rev-parse", "HEAD") == head
        assert git(root, "diff", "--cached", "--binary") == cached
        assert git(root, "diff", "--binary") == worktree
        assert staged.read_bytes() == b"working tree differs"
        assert unstaged.read_bytes() == b"untracked"


@pytest.mark.parametrize("error_type", [RuntimeError, AttributeError])
def test_unexpected_archive_decoder_bug_is_not_hidden_as_bad_archive(
    tmp_path, monkeypatch, error_type
):
    archive = pack(tmp_path / "input.tar.zst", raw_members())

    def broken(*args, **kwargs):
        raise error_type("unexpected decoder bug")

    monkeypatch.setattr(tarfile, "open", broken)
    target = tmp_path / "destination"
    with pytest.raises(error_type, match="unexpected decoder bug"):
        Project.import_(archive, target, **CALL)
    untouched(target, False)


@pytest.mark.parametrize(
    "target",
    ["node", "profile", "history", "cache", "ignore", "git", "artifact", "alias"],
)
def test_read_only_export_cannot_overwrite_managed_source(project, tmp_path, target):
    value = project.propose("Task", "source", **CALL)
    payload = artifact(project, value["id"], content=b"original artifact")
    project.attach(value["id"], [payload], **CALL)
    record = project.path / f"graph/nodes/{value['id']}.yaml"
    alias = tmp_path / "source-alias"
    alias.symlink_to(record)
    paths = {
        "node": record,
        "profile": project.path / ".researchgraph/profile.yaml",
        "history": project.path / ".researchgraph/history.jsonl",
        "cache": project.index.path,
        "ignore": project.path / ".gitignore",
        "git": project.path / ".git/config",
        "artifact": project.path / payload,
        "alias": alias,
    }
    out = paths[target]
    before = source(project)
    original = out.read_bytes() if out.exists() else None
    with Project.open(project.path, read_only=True) as reader:
        refusal("schema", reader.export, out, **CALL)
    assert source(project) == before
    assert (out.read_bytes() if out.exists() else None) == original


def test_export_replaces_external_hardlink_without_modifying_source(project, tmp_path):
    value = project.propose("Task", "source", **CALL)
    record = project.path / f"graph/nodes/{value['id']}.yaml"
    original = record.read_bytes()
    out = tmp_path.parent / (tmp_path.name + "-hardlink.tar.zst")
    os.link(record, out)
    project.export(out, **CALL)
    assert record.read_bytes() == original
    assert out.read_bytes() != original


@pytest.mark.parametrize(
    "suffix",
    [
        b"ignored trailing bytes",
        zstandard.ZstdCompressor(level=3).compress(b"another frame"),
    ],
)
def test_import_rejects_unconsumed_compressed_archive_suffix(tmp_path, suffix):
    archive = pack(tmp_path / "input.tar.zst", raw_members())
    archive.write_bytes(archive.read_bytes() + suffix)
    target = tmp_path / "destination"
    refusal("bad_archive", Project.import_, archive, target, **CALL)
    untouched(target, False)


def test_import_preflight_closes_schema_cursor_before_profile_validation(
    tmp_path, monkeypatch
):
    import sqlite3

    from researchgraph.profiles import NodeType, Profile

    cursors = []
    connect = sqlite3.connect

    class Connection(sqlite3.Connection):
        def cursor(self, *args, **kwargs):
            result = super().cursor(*args, **kwargs)
            cursors.append(result)
            return result

        def executescript(self, *args, **kwargs):
            result = super().executescript(*args, **kwargs)
            cursors.append(result)
            return result

    def memory_connection(path, *args, **kwargs):
        if path == ":memory:":
            kwargs["factory"] = Connection
        return connect(path, *args, **kwargs)

    def validate(project, write):
        if write.op == "import":
            assert cursors
            for cursor in cursors:
                with pytest.raises(sqlite3.ProgrammingError, match="closed cursor"):
                    cursor.execute("SELECT 1")

    Profile.register(
        Profile(
            "archive_cursor",
            "1.0",
            [NodeType("Task", "ta", {}, claimable=True)],
            [],
            [],
            [],
            [validate],
        )
    )
    members = raw_members()
    members[".researchgraph/profile.yaml"] = b'name: archive_cursor\nversion: "1.0"\n'
    archive = pack(tmp_path / "input.tar.zst", members)
    monkeypatch.setattr(sqlite3, "connect", memory_connection)
    with Project.import_(archive, tmp_path / "destination", **CALL) as imported:
        assert imported.get_node("ta-aaaaaaaa").id == "ta-aaaaaaaa"


@pytest.mark.parametrize("layout", ["worktree", "separate"])
def test_export_protects_external_git_metadata(project, tmp_path, layout):
    if layout == "worktree":
        root = tmp_path.parent / (tmp_path.name + "-worktree")
        project._git.run("worktree", "add", "--detach", str(root))
        metadata = project.path / ".git"
    else:
        root = project.path
        metadata = tmp_path.parent / (tmp_path.name + "-git-store")
        (project.path / ".git").rename(metadata)
        (project.path / ".git").write_text(f"gitdir: {metadata}\n")
    config = metadata / "config"
    original = config.read_bytes()
    with Project.open(root, read_only=True) as reader:
        refusal("schema", reader.export, config, **CALL)
    assert config.read_bytes() == original


@pytest.mark.parametrize("read_after_edit", [False, True])
def test_import_preflight_reads_recheck_live_destination_before_empty_answers(
    tmp_path, read_after_edit
):
    from researchgraph.profiles import NodeType, Profile

    returned = []
    target = tmp_path / "destination"
    injected = target / "graph/nodes/ta-bbbbbbbb.yaml"
    content = dump_node(node("ta-bbbbbbbb").to_dict()).encode()

    def validate(project, write):
        if write.op == "import":
            assert project.get_nodes() == []
            injected.parent.mkdir(parents=True)
            injected.write_bytes(content)
            if read_after_edit:
                returned.append(project.get_nodes())

    Profile.register(
        Profile(
            "archive_live",
            "1.0",
            [NodeType("Task", "ta", {}, claimable=True)],
            [],
            [],
            [],
            [validate],
        )
    )
    members = raw_members()
    members[".researchgraph/profile.yaml"] = b'name: archive_live\nversion: "1.0"\n'
    archive = pack(tmp_path / "input.tar.zst", members)
    refusal("not_empty", Project.import_, archive, target, **CALL)
    assert returned == []
    assert injected.read_bytes() == content
    assert not (target / ".researchgraph").exists()
    assert not (target / ".git").exists()


@pytest.mark.parametrize("kind", ["ignore", "artifacts"])
def test_export_cannot_overwrite_outward_managed_alias(project, tmp_path, kind):
    external = tmp_path.parent / (tmp_path.name + "-external")
    external.mkdir()
    target = external / "original"
    target.write_bytes(b"preserve external source")
    if kind == "ignore":
        managed = project.path / ".gitignore"
        managed.unlink()
        managed.symlink_to(target)
        out = managed
    else:
        managed = project.path / "artifacts"
        managed.rmdir()
        managed.symlink_to(external, target_is_directory=True)
        out = managed / target.name
    with Project.open(project.path, read_only=True) as reader:
        refusal("schema", reader.export, out, **CALL)
    assert target.read_bytes() == b"preserve external source"
    assert managed.is_symlink()


@pytest.mark.parametrize("as_of", [None, FUTURE_STAMP])
def test_query_parity_helper_rejects_missing_visible_edges(project, monkeypatch, as_of):
    ids = populated(project, count=10)
    edge = project.get_edges()[0]
    show = project.show

    def broken(identifier, **kwargs):
        if identifier == edge.id:
            raise RuleViolation(
                "not_found", "Injected visible-edge failure", [identifier]
            )
        return show(identifier, **kwargs)

    monkeypatch.setattr(project, "show", broken)
    with pytest.raises(AssertionError):
        queries(project, ids, FUTURE, as_of)


@pytest.mark.parametrize(
    "problem",
    [
        "missing",
        "closed_only",
        "wrong_open_status",
        "multiple_open",
        "later_closed",
        "same_time_closed",
        "closed_equivalent",
    ],
)
def test_import_rejects_inconsistent_node_status_history(tmp_path, problem):
    if problem == "missing":
        rows = []
    elif problem == "closed_only":
        rows = [interval(to_time=FUTURE_STAMP)]
    elif problem == "multiple_open":
        rows = [interval(), interval(from_time=FUTURE_STAMP)]
    elif problem == "later_closed":
        rows = [
            interval(),
            interval(
                status="running",
                from_time=format_timestamp(NOW + timedelta(seconds=1)),
                to_time=FUTURE_STAMP,
            ),
        ]
    elif problem == "same_time_closed":
        rows = [interval(), interval(status="running", to_time=FUTURE_STAMP)]
    elif problem == "closed_equivalent":
        rows = [interval(), interval(to_time=FUTURE_STAMP, to_commit="b" * 40)]
    else:
        rows = [interval(status="failed")]
    archive = pack(tmp_path / "input.tar.zst", raw_members(rows=rows))
    target = tmp_path / "destination"
    with (
        pytest.raises(RuleViolation) as raised,
        Project.import_(archive, target, actor=ALICE, now=FUTURE),
    ):
        pass
    assert raised.value.rule == "bad_archive"
    untouched(target, False)


def test_import_collapses_identical_open_interval_duplicates(tmp_path):
    archive = pack(
        tmp_path / "input.tar.zst", raw_members(rows=[interval(), interval()])
    )
    with Project.import_(archive, tmp_path / "destination", **CALL) as imported:
        assert restored_rows(imported) == [interval()]


def test_import_preserves_stable_same_time_status_walk_order(tmp_path):
    rows = [
        interval(status="running", to_time=STAMP, to_commit="b" * 40),
        interval(from_commit="b" * 40),
    ]
    expected = sorted(
        rows, key=lambda row: (row["node_id"], row["from_time"], row["status"])
    )
    archive = pack(tmp_path / "input.tar.zst", raw_members(rows=rows))
    with Project.import_(archive, tmp_path / "destination", **CALL) as imported:
        assert restored_rows(imported) == expected
        assert imported.show("ta-aaaaaaaa", **CALL, as_of=STAMP)["status"] == "active"
        cold(imported, "deleted")
        assert restored_rows(imported) == expected


def test_export_roundtrip_preserves_same_second_status_walk_order(project, tmp_path):
    identifier = project.propose("Task", title="Same second", **CALL)["id"]
    project.claim(identifier, lease_seconds=60, actor=BOT, now=NOW)
    project.release(identifier, actor=BOT, now=NOW)
    assert project.show(identifier, **CALL, as_of=STAMP)["status"] == "active"
    rows = restored_rows(project)
    archive = exported(project, tmp_path / "snapshot.tar.zst")
    with Project.import_(
        archive, tmp_path / "restored", actor=ALICE, now=FUTURE
    ) as imported:
        assert imported.show(identifier, **CALL, as_of=STAMP)["status"] == "active"
        assert restored_rows(imported) == rows
        cold(imported, "deleted")
        assert imported.show(identifier, **CALL, as_of=STAMP)["status"] == "active"
        assert restored_rows(imported) == rows


def test_same_second_local_transitions_close_archive_at_first_commit(tmp_path):
    archive = pack(tmp_path / "input.tar.zst", raw_members())
    with Project.import_(archive, tmp_path / "destination", **CALL) as imported:
        imported.claim("ta-aaaaaaaa", lease_seconds=60, actor=BOT, now=NOW)
        claim_commit = git(imported.path, "rev-parse", "HEAD")
        imported.release("ta-aaaaaaaa", actor=BOT, now=NOW)
        rows = restored_rows(imported)
        original = next(row for row in rows if row["from_commit"] == "a" * 40)
        assert original["to_commit"] == claim_commit
        assert original["to_time"] == STAMP
        assert sum(row["to_time"] is None for row in rows) == 1
        assert imported.show("ta-aaaaaaaa", **CALL, as_of=STAMP)["status"] == "active"
        cold(imported, "deleted")
        assert restored_rows(imported) == rows
        exported(imported, tmp_path / "second.tar.zst")
    with Project.import_(
        tmp_path / "second.tar.zst", tmp_path / "restored", actor=ALICE, now=FUTURE
    ) as restored:
        assert restored_rows(restored) == rows
        assert restored.show("ta-aaaaaaaa", **CALL, as_of=STAMP)["status"] == "active"
