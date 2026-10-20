"""Fresh source bytes and HEAD through one fixed isolated scan response."""

import gc
import hashlib
import io
import json
import os
import shutil
import struct
import subprocess
import sys
import weakref
from types import SimpleNamespace

import pytest

from researchgraph.model import RuleViolation
from researchgraph.store.git import Git
from researchgraph.store.source_scan import SourceFiles
from researchgraph.store.source_scanner import SourceScanner
from tests.helpers import git, repository


def sources(tmp_path):
    repository(tmp_path)
    (tmp_path / "graph/nodes").mkdir(parents=True)
    (tmp_path / ".researchgraph").mkdir()
    for name, content in (("first", b"a" * 20000), ("한글\nlast", b"last")):
        (tmp_path / "graph/nodes" / name).write_bytes(content)
    (tmp_path / ".researchgraph/history.jsonl").write_bytes(b"history\n")
    return tmp_path


def independent(root, head):
    digest = hashlib.sha256()
    paths = [path for path in (root / "graph").rglob("*") if path.is_file()]
    archive = root / ".researchgraph/history.jsonl"
    if archive.is_file():
        paths.append(archive)
    for path in sorted(paths, key=lambda path: path.relative_to(root).as_posix()):
        name = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(
            len(name).to_bytes(8, "big")
            + name
            + hashlib.sha256(path.read_bytes()).digest()
        )
    digest.update(b"HEAD\0" + (head or "").encode())
    return digest.hexdigest()


def test_serial_and_supervisor_exact_codec_unborn_and_changed_head(tmp_path):
    root = sources(tmp_path)
    files = SourceFiles(root)
    files.discover()
    digest = files.digest()
    digest.update(b"HEAD\0")
    assert digest.hexdigest() == independent(root, None)
    with SourceScanner(root) as scanner:
        assert scanner.scan() == (independent(root, None), None)
        assert scanner.file_count == 3
        git(root, "commit", "--allow-empty", "-m", "fresh HEAD")
        head = Git(root).head()
        assert scanner.scan() == (independent(root, head), head)
        git(root, "commit", "--allow-empty", "-m", "next HEAD")
        head = Git(root).head()
        assert scanner.scan(before_wait=lambda: sum(range(10000))) == (
            independent(root, head),
            head,
        )


def test_head_rejects_malformed_existing_branch_with_git_evidence(tmp_path):
    root = sources(tmp_path)
    checkout = Git(root)
    assert checkout.head() is None
    checkout.run("commit", "--allow-empty", "-m", "valid HEAD")
    committed = checkout.run("rev-parse", "HEAD").stdout.strip()
    assert checkout.head() == committed
    branch = checkout.run("symbolic-ref", "HEAD").stdout.strip()
    ref_path = root / checkout.run("rev-parse", "--git-path", branch).stdout.strip()
    original = ref_path.read_bytes()
    try:
        ref_path.write_bytes(b"not-an-object-id\n")
        failed = checkout.run(
            "rev-parse", "--is-inside-work-tree", "--verify", "HEAD", check=False
        )
        assert failed.returncode != 0
        assert failed.stdout.splitlines()[0] == "true"
        assert failed.stderr.strip()
        with pytest.raises((OSError, subprocess.SubprocessError)) as raised:
            checkout.head()
        error = raised.value
        evidence = str(error) + str(getattr(error, "stderr", ""))
        assert failed.stderr.strip() in evidence
        assert getattr(error, "returncode", None) == failed.returncode or str(
            failed.returncode
        ) in str(error)
    finally:
        ref_path.write_bytes(original)
    assert checkout.head() == committed


def test_head_rejects_interrupted_process_after_true_membership(tmp_path, monkeypatch):
    root = sources(tmp_path)
    checkout = Git(root)
    checkout.run("commit", "--allow-empty", "-m", "valid HEAD")
    diagnostic = "git HEAD resolution interrupted after membership"

    class InterruptedProcess:
        returncode = -15

        def __init__(self, arguments, **kwargs):
            self.args = arguments

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def communicate(self):
            return "true\n", diagnostic

    monkeypatch.setattr(subprocess, "Popen", InterruptedProcess)
    with pytest.raises((OSError, subprocess.SubprocessError)) as raised:
        checkout.head()
    error = raised.value
    assert diagnostic in str(error) + str(getattr(error, "stderr", ""))
    assert getattr(error, "returncode", None) == -15 or "-15" in str(error)


def test_source_worker_releases_completed_scan_before_next_request(
    tmp_path, monkeypatch
):
    from researchgraph.store import source_worker

    root = sources(tmp_path)
    git(root, "commit", "--allow-empty", "-m", "idle boundary HEAD")
    head = Git(root).run("rev-parse", "HEAD").stdout.strip()
    fingerprint = bytes.fromhex(independent(root, head))
    raw_digests = [
        hashlib.sha256(path.read_bytes()).digest()
        for path in [
            *(root / "graph/nodes").iterdir(),
            root / ".researchgraph/history.jsonl",
        ]
    ]
    payload = (3).to_bytes(8, "big") + bytes([len(head)]) + head.encode() + fingerprint
    expected = (
        struct.pack(">I", len(payload) + 1) + bytes([source_worker.RESULT]) + payload
    )
    owners = []
    boundaries = []
    hash_type = type(hashlib.sha256())

    class Output(io.BytesIO):
        flushes = 0

        def flush(self):
            self.flushes += 1
            super().flush()

    output = Output()

    def assert_no_completed_result(value, name):
        assert not isinstance(value, hash_type), f"Idle worker retains digest in {name}"
        if isinstance(value, bytes):
            assert fingerprint not in value, (
                f"Idle worker retains fingerprint in {name}"
            )
            assert all(digest not in value for digest in raw_digests), (
                f"Idle worker retains raw hash batch in {name}"
            )
        elif isinstance(value, (list, tuple)):
            for index, item in enumerate(value):
                assert_no_completed_result(item, f"{name}[{index}]")
        elif isinstance(value, dict):
            for key, item in value.items():
                assert_no_completed_result(item, f"{name}[{key!r}]")
        assert value != head and value != head.encode(), (
            f"Idle worker retains HEAD in {name}"
        )

    class Input:
        requests = 0

        def readline(self, limit):
            assert limit == len(source_worker.SCAN) + 1
            self.requests += 1
            if self.requests == 1:
                return source_worker.SCAN
            assert self.requests == 2
            assert output.flushes == 1
            assert output.getvalue() == expected
            caller = sys._getframe(1)
            assert caller.f_code is source_worker.main.__code__
            owners.append(caller.f_locals["scanner"])
            for name, value in caller.f_locals.items():
                assert_no_completed_result(value, name)
            boundaries.append("idle after flushed success")
            return b""

    monkeypatch.setattr(source_worker.sys, "argv", ["source_worker", str(root)])
    monkeypatch.setattr(source_worker.sys, "stdin", SimpleNamespace(buffer=Input()))
    monkeypatch.setattr(source_worker.sys, "stdout", SimpleNamespace(buffer=output))
    source_worker.main()
    assert boundaries == ["idle after flushed success"]
    assert output.getvalue() == expected
    assert owners and owners[0]._processes
    assert all(process.poll() is not None for process in owners[0]._processes)


def test_callback_after_send_before_one_complete_response(tmp_path, monkeypatch):
    root = sources(tmp_path)
    scanner = SourceScanner(root)
    original_read = scanner._read_frame
    events = []

    def read():
        assert events
        events.append("read")
        return original_read()

    def callback():
        assert scanner.process.poll() is None
        assert not events
        events.append(sum(range(10000)))

    monkeypatch.setattr(scanner, "_read_frame", read)
    try:
        result, head = scanner.scan(before_wait=callback)
        assert result == independent(root, head)
        assert events[1:] == ["read"]
    finally:
        scanner.close()


def test_live_same_size_same_time_edits_and_membership(tmp_path):
    root = sources(tmp_path)
    path = root / "graph/nodes/first"
    with SourceScanner(root) as scanner:
        before, _ = scanner.scan()
        stamp = path.stat()
        with path.open("r+b") as stream:
            stream.seek(19999)
            stream.write(b"z")
        os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        after, _ = scanner.scan()
        assert before != after == independent(root, None)
        path.unlink()
        (root / "graph/new").write_bytes(b"new")
        assert scanner.scan()[0] == independent(root, None)
        assert scanner.file_count == 3


def test_worker_head_after_full_bytes_and_before_complete_reply(tmp_path, monkeypatch):
    from researchgraph.store import hashing, source_worker

    root = sources(tmp_path)
    git(root, "commit", "--allow-empty", "-m", "head")
    current = Git(root).head()
    expected = independent(root, current)
    original_head = Git.head
    events = []
    original_digest = SourceFiles.digest
    original_finish = hashing._HashStream.finish

    def finish(self):
        result = original_finish(self)
        if not events:
            events.append("hashes")
        return result

    def digest(self, scanner=None, *, file_batches=None):
        assert events == ["hashes", "head"]
        value = original_digest(self, scanner, file_batches=file_batches)
        events.append("framed")
        return value

    def head(self, *, before_wait=None):
        assert events == ["hashes"]
        assert output.getvalue() == b""
        events.append("head")
        (root / "graph/nodes/first").write_bytes(b"later write")
        return original_head(self, before_wait=before_wait)

    output = io.BytesIO()
    monkeypatch.setattr(SourceFiles, "digest", digest)
    monkeypatch.setattr(hashing._HashStream, "finish", finish)
    monkeypatch.setattr(source_worker.Git, "head", head)
    monkeypatch.setattr(source_worker.sys, "argv", ["source_worker", str(root)])
    monkeypatch.setattr(
        source_worker.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(b"SCAN\n"))
    )
    monkeypatch.setattr(source_worker.sys, "stdout", SimpleNamespace(buffer=output))
    source_worker.main()
    frame = output.getvalue()
    assert frame[4] == source_worker.RESULT
    assert frame[-32:].hex() == expected
    assert frame[13] == len(current)
    assert frame[14:-32].decode("ascii") == current
    assert events == ["hashes", "head", "framed"]


def test_worktree_membership_checked_again_during_overlap(tmp_path):
    root = sources(tmp_path)
    with SourceScanner(root) as scanner:
        scanner.scan()
        shutil.rmtree(root / ".git")
        with pytest.raises(RuleViolation) as refused:
            scanner.scan(before_wait=lambda: sum(range(10000)))
        assert refused.value.rule == "invalid_project"
        assert scanner.process.poll() is not None


@pytest.mark.parametrize(
    "failure",
    [
        RuleViolation("invalid_project", "refused", ["affected"]),
        OSError("git unavailable"),
        subprocess.SubprocessError("git failed"),
    ],
)
def test_worker_head_failure_has_no_success_or_partial_publication(
    tmp_path, monkeypatch, failure
):
    from researchgraph.store import source_worker

    root = sources(tmp_path)
    output = io.BytesIO()

    def head(self, *, before_wait=None):
        assert output.getvalue() == b""
        raise failure

    monkeypatch.setattr(source_worker.Git, "head", head)
    monkeypatch.setattr(source_worker.sys, "argv", ["source_worker", str(root)])
    monkeypatch.setattr(
        source_worker.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(b"SCAN\n"))
    )
    monkeypatch.setattr(source_worker.sys, "stdout", SimpleNamespace(buffer=output))
    source_worker.main()
    result = output.getvalue()
    assert int.from_bytes(result[:4], "big") == len(result) - 4
    if isinstance(failure, RuleViolation):
        assert result[4] == source_worker.RULE_ERROR
        assert json.loads(result[5:]) == {
            "rule": failure.rule,
            "message": failure.message,
            "ids": failure.ids,
        }
    else:
        assert result[4] == source_worker.IO_ERROR
        assert result[5:].decode() == str(failure)


@pytest.mark.parametrize("kind", ["symlink", "utf8"])
def test_schema_errors_repeat_without_partial_cache(tmp_path, kind):
    root = sources(tmp_path)
    files = SourceFiles(root)
    files.discover()
    previous = (
        files._files,
        files.frames,
        files.paths,
        files._frame_descriptor,
        files._records,
        files._discovered,
    )
    invalid = root / "graph" / (os.fsdecode(b"bad-\xff") if kind == "utf8" else "link")
    if kind == "symlink":
        invalid.symlink_to(root / "graph/nodes/first")
    else:
        invalid.write_bytes(b"invalid")
    for _ in range(2):
        with pytest.raises(RuleViolation) as refused:
            files.discover(_batch_sink=lambda paths: None)
        assert refused.value.rule == "schema"
        assert all(
            old is current
            for old, current in zip(
                previous,
                (
                    files._files,
                    files.frames,
                    files.paths,
                    files._frame_descriptor,
                    files._records,
                    files._discovered,
                ),
                strict=True,
            )
        )
        with SourceScanner(root) as scanner:
            with pytest.raises(RuleViolation) as refused:
                scanner.scan()
            assert refused.value.rule == "schema"
            assert scanner.process.poll() is not None
    invalid.unlink()
    files.discover()
    with SourceScanner(root) as scanner:
        assert scanner.scan()[0] == independent(root, None)


def test_missing_discovered_file_never_uses_cached_bytes(tmp_path):
    root = sources(tmp_path)
    files = SourceFiles(root)
    files.discover()
    (root / "graph/nodes/first").unlink()
    with pytest.raises(OSError):
        files.digest()


def test_discovery_batches_start_before_walk_finishes_and_publish_atomically(tmp_path):
    root = sources(tmp_path)
    for i in range(1030):
        (root / "graph/nodes" / str(i)).write_bytes(b"x")
    nested = root / "graph/중첩/더 깊이/경로\n파일"
    nested.parent.mkdir(parents=True)
    nested.write_bytes(b"nested")
    expected = {str(path) for path in (root / "graph").rglob("*") if path.is_file()} | {
        str(root / ".researchgraph/history.jsonl")
    }
    files = SourceFiles(root)
    batches = []

    def sink(paths):
        assert files._files is published
        assert type(paths) is tuple
        assert 0 < len(paths) <= 512
        assert all(type(path) is str and os.path.isabs(path) for path in paths)
        batches.append(paths)

    for _ in range(2):
        published = files._files
        records = files._records
        descriptor = files._frame_descriptor
        batches.clear()
        assert files.discover(_batch_sink=sink) == len(expected)
        flattened = [path for batch in batches for path in batch]
        assert len(flattened) == len(expected)
        assert set(flattened) == expected
        assert len(batches) == 3
        assert type(files._discovered) is list
        assert all(
            type(record) is tuple
            and len(record) == 2
            and record == (os.path.relpath(record[1], root), record[1])
            for record in files._discovered
        )
        assert {record[1] for record in files._discovered} == expected
        if published is not None:
            assert files._files is published
            assert files._frame_descriptor is descriptor
            assert all(record is records[record[0]] for record in files._discovered)
    previous = (
        files._files,
        files.frames,
        files.paths,
        files._records,
        files._discovered,
    )
    invalid = root / "graph" / os.fsdecode(b"bad-\xff")
    invalid.write_bytes(b"bad")
    with pytest.raises(RuleViolation):
        files.discover(_batch_sink=lambda paths: None)
    assert previous == (
        files._files,
        files.frames,
        files.paths,
        files._records,
        files._discovered,
    )


@pytest.mark.parametrize("warm", [False, True])
@pytest.mark.parametrize(
    "failure",
    [
        RuntimeError("sink failed"),
        KeyboardInterrupt("sink interrupted"),
        UnicodeDecodeError("utf-8", b"\xff", 0, 1, "sink decode failed"),
    ],
)
def test_discovery_sink_failure_propagates_without_partial_state(
    tmp_path, warm, failure
):
    root = sources(tmp_path)
    for i in range(520):
        (root / "graph/nodes" / str(i)).write_bytes(b"x")
    files = SourceFiles(root)
    if warm:
        files.discover()
    previous = vars(files).copy()
    (root / "graph/new-current-file").write_bytes(b"new")
    batches = []

    def sink(paths):
        batches.append(paths)
        if len(batches) == 2:
            raise failure

    with pytest.raises(type(failure)) as raised:
        files.discover(_batch_sink=sink)
    assert raised.value is failure
    assert len(batches) == 2
    assert vars(files).keys() == previous.keys()
    assert all(value is previous[name] for name, value in vars(files).items())
    assert files.discover() == 524


def test_pipeline_many_batches_exact_sorted_codec_and_late_errors(tmp_path):
    root = sources(tmp_path)
    for i in range(4200):
        (root / "graph/nodes" / str(i)).write_bytes(str(i).encode())
    with SourceScanner(root) as scanner:
        assert scanner.scan()[0] == independent(root, None)
        (root / "graph/nodes/20").unlink()
        assert scanner.scan()[0] == independent(root, None)
        (root / "graph/late-link").symlink_to(root / "graph/nodes/1")
        with pytest.raises(RuleViolation):
            scanner.scan()
        assert scanner.process.poll() is not None


def test_empty_large_many_paths(tmp_path):
    repository(tmp_path)
    (tmp_path / "graph").mkdir()
    with SourceScanner(tmp_path) as scanner:
        assert scanner.scan() == (independent(tmp_path, None), None)
        assert scanner.file_count == 0
        for i in range(40):
            (tmp_path / "graph" / str(i)).write_bytes(bytes([i]) * 100)
        (tmp_path / "graph/large").write_bytes(b"z" * (16 * 1024 * 1024 + 1))
        assert scanner.scan()[0] == independent(tmp_path, None)
        assert scanner.file_count == 41


@pytest.mark.parametrize("exception", [RuntimeError, KeyboardInterrupt])
def test_callback_exception_closes_inflight_owner(tmp_path, exception):
    scanner = SourceScanner(sources(tmp_path))
    failure = exception("caller failed")

    def fail():
        raise failure

    with pytest.raises(exception) as raised:
        scanner.scan(before_wait=fail)
    assert raised.value is failure
    assert scanner.process.poll() is not None
    with pytest.raises(OSError):
        scanner.scan()


class FakeProcess:
    def __init__(self, reply):
        self.stdin = io.BytesIO()
        self.stdout = io.BytesIO(reply)
        self.returncode = None
        self.pid = 987654321

    def poll(self):
        return self.returncode

    def wait(self, timeout):
        self.returncode = 0
        return 0


def frame(status, payload):
    return struct.pack(">I", len(payload) + 1) + bytes([status]) + payload


@pytest.mark.parametrize(
    "reply",
    [
        b"",
        struct.pack(">I", 0),
        struct.pack(">I", 999999),
        frame(255, b"?"),
        frame(0, b"?"),
        frame(3, b"\xff"),
        frame(1, b"x" * 32),
        frame(1, b"\0" * 8 + b"\x28" + b"z" * 40 + b"x" * 32),
        frame(1, b"\0" * 8 + b"\x01" + b"0" + b"x" * 32),
        frame(2, b"{}"),
    ],
)
def test_malformed_frames_terminal_no_retry(tmp_path, monkeypatch, reply):
    from researchgraph.store import source_scanner

    process = FakeProcess(reply)
    launches = []

    def start(*args, **kwargs):
        launches.append(kwargs)
        return process

    monkeypatch.setattr(source_scanner.subprocess, "Popen", start)
    monkeypatch.setattr(source_scanner.os, "killpg", lambda *args: None)
    scanner = SourceScanner(tmp_path)
    with pytest.raises(OSError):
        scanner.scan()
    assert len(launches) == 1 and launches[0]["start_new_session"]
    assert process.stdin.closed and process.stdout.closed
    with pytest.raises(OSError):
        scanner.scan()


@pytest.mark.parametrize("status,error", [(2, RuleViolation), (3, OSError)])
def test_error_frames_preserve_rule_message_and_ids(
    tmp_path, monkeypatch, status, error
):
    from researchgraph.store import source_scanner

    payload = (
        json.dumps(
            {"rule": "invalid_project", "message": "source refused", "ids": ["one"]}
        ).encode()
        if status == 2
        else b"source refused"
    )
    process = FakeProcess(frame(status, payload))
    monkeypatch.setattr(source_scanner.subprocess, "Popen", lambda *a, **k: process)
    scanner = SourceScanner(tmp_path)
    with pytest.raises(error, match="source refused") as refused:
        scanner.scan()
    if status == 2:
        assert refused.value.rule == "invalid_project"
        assert refused.value.ids == ["one"]
    assert process.stdin.closed and process.stdout.closed


@pytest.mark.parametrize("length", [0, 40, 64])
def test_fixed_success_codec_and_only_scan_request(tmp_path, monkeypatch, length):
    from researchgraph.store import source_scanner

    head = "a" * length
    payload = (123).to_bytes(8, "big") + bytes([length]) + head.encode() + b"x" * 32
    process = FakeProcess(frame(1, payload))
    monkeypatch.setattr(source_scanner.subprocess, "Popen", lambda *a, **k: process)
    with SourceScanner(tmp_path) as scanner:
        assert scanner.scan() == ((b"x" * 32).hex(), head or None)
        assert scanner.file_count == 123
        assert process.stdin.getvalue() == b"SCAN\n"


def test_controlled_callback_writes_precede_scan_and_fresh_head(tmp_path, monkeypatch):
    from researchgraph.store import source_scanner
    from researchgraph.store.source_worker import RESULT

    root = sources(tmp_path)
    process = FakeProcess(b"")
    original_popen = subprocess.Popen
    monkeypatch.setattr(source_scanner.subprocess, "Popen", lambda *a, **k: process)
    scanner = SourceScanner(root)

    def callback():
        assert process.stdin.getvalue() == b"SCAN\n"
        (root / "graph/added").write_bytes(b"added")
        with monkeypatch.context() as patch:
            patch.setattr(source_scanner.subprocess, "Popen", original_popen)
            git(root, "commit", "--allow-empty", "-m", "callback HEAD")

    def read():
        files = SourceFiles(root)
        count = files.discover()
        digest = files.digest()
        with monkeypatch.context() as patch:
            patch.setattr(source_scanner.subprocess, "Popen", original_popen)
            head = Git(root).head()
        digest.update(b"HEAD\0" + head.encode())
        return RESULT, count.to_bytes(8, "big") + bytes(
            [len(head)]
        ) + head.encode() + digest.digest()

    monkeypatch.setattr(scanner, "_read_frame", read)
    try:
        result, head = scanner.scan(before_wait=callback)
        assert result == independent(root, head)
        assert scanner.file_count == 4
    finally:
        scanner.close()


def test_dead_supervisor_not_restarted(tmp_path):
    scanner = SourceScanner(sources(tmp_path))
    scanner.scan()
    scanner.process.kill()
    scanner.process.wait(timeout=2)
    for _ in range(2):
        with pytest.raises(OSError):
            scanner.scan()
    assert scanner.process.stdout.closed


def test_finalizer_does_not_retain_owner(tmp_path):
    scanner = SourceScanner(sources(tmp_path))
    scanner.scan()
    process = scanner.process
    reference = weakref.ref(scanner)
    del scanner
    gc.collect()
    assert reference() is None
    assert process.poll() is not None


def test_shutdown_escalates_only_owned_group(tmp_path, monkeypatch):
    import signal

    from researchgraph.store import source_scanner

    class Stubborn(FakeProcess):
        def wait(self, timeout):
            if self.returncode is None:
                raise subprocess.TimeoutExpired("fixed source worker", timeout)
            return self.returncode

    process = Stubborn(b"")
    signals = []

    def killpg(pid, sig):
        signals.append((pid, sig))
        if sig == signal.SIGKILL:
            process.returncode = -sig

    monkeypatch.setattr(source_scanner.subprocess, "Popen", lambda *a, **k: process)
    monkeypatch.setattr(source_scanner.os, "killpg", killpg)
    SourceScanner(tmp_path).close()
    assert signals == [(process.pid, signal.SIGTERM), (process.pid, signal.SIGKILL)]
    assert process.stdout.closed


def test_index_parallel_only_callback_and_retained_owner(project, monkeypatch):
    from researchgraph.store import index

    events = []
    monkeypatch.setattr(index, "PARALLEL_HASH_THRESHOLD", 0)
    project.index.content_hash(before_wait=lambda: events.append("overlap"))
    owner = project.index._hash_scanner
    monkeypatch.setattr(index, "PARALLEL_HASH_THRESHOLD", 10000)
    expected = project.index.content_hash(
        before_wait=lambda: events.append("not parallel")
    )
    assert events == ["overlap"]
    assert project.index._hash_scanner is owner
    monkeypatch.setattr(index, "PARALLEL_HASH_THRESHOLD", 0)
    assert project.index.content_hash() == expected


def test_index_callback_failure_detaches_owner(project, monkeypatch):
    from researchgraph.store import index

    monkeypatch.setattr(index, "PARALLEL_HASH_THRESHOLD", 0)

    def fail():
        raise RuntimeError("callback")

    with pytest.raises(RuntimeError, match="callback"):
        project.index.refresh(before_wait=fail)
    assert project.index._hash_scanner is None
    project.index.refresh()
    assert project.index._hash_scanner is not None


def test_warm_large_discovery_exclusively_worker_and_shrink(project, monkeypatch):
    from researchgraph.store import index

    monkeypatch.setattr(index, "PARALLEL_HASH_THRESHOLD", 2)
    paths = [project.path / "graph" / name for name in ("one", "two")]
    for path in paths:
        path.write_bytes(b"content")
    project.index.content_hash()
    owner = project.index._hash_scanner

    def no_parent_walk():
        pytest.fail("Warm large scans must discover in supervisor")

    with monkeypatch.context() as patch:
        patch.setattr(project.index._source_files, "discover", no_parent_walk)
        paths[0].unlink()
        assert project.index.content_hash() == independent(
            project.path, project._git.head()
        )
    assert project.index._last_file_count == 1
    assert project.index.content_hash() == independent(
        project.path, project._git.head()
    )
    assert project.index._hash_scanner is owner


def test_large_index_does_not_call_parent_git_and_checks_projection(
    project, monkeypatch
):
    from researchgraph.store import index

    monkeypatch.setattr(index, "PARALLEL_HASH_THRESHOLD", 0)
    first = project.index.content_hash()
    project._git.run("commit", "--allow-empty", "-m", "new HEAD")
    expected_head = project._git.head()

    def forbidden():
        pytest.fail("Large scans must acquire HEAD inside supervisor")

    monkeypatch.setattr(project._git, "head", forbidden)
    assert project.index.content_hash() != first
    assert project.index._scanned_head == expected_head
    with project.index.connection:
        project.index.connection.execute("DROP VIEW ready")
        project.index.connection.execute(
            "CREATE VIEW ready AS SELECT * FROM nodes WHERE 0"
        )
    project.index.refresh(before_wait=lambda: sum(range(1000)))
    definition = project.index.connection.execute(
        "SELECT sql FROM sqlite_master WHERE name='ready'"
    ).fetchone()[0]
    assert "WHERE 0" not in definition


def test_head_computes_after_spawn_before_wait(tmp_path, monkeypatch):
    root = sources(tmp_path)
    Git(root).run("commit", "--allow-empty", "-m", "resolvable HEAD")
    expected = Git(root).head()
    events = []
    original = subprocess.Popen

    def start(*args, **kwargs):
        process = original(*args, **kwargs)
        events.append("spawn")
        communicate = process.communicate

        def wait(*args, **kwargs):
            events.append("wait")
            return communicate(*args, **kwargs)

        process.communicate = wait
        return process

    def assemble():
        assert events == ["spawn"]
        events.append("assemble")

    monkeypatch.setattr(subprocess, "Popen", start)
    assert Git(root).head(before_wait=assemble) == expected
    assert events == ["spawn", "assemble", "wait"]


def test_head_closes_spawned_process_when_assembly_fails(tmp_path, monkeypatch):
    root = sources(tmp_path)
    processes = []
    original = subprocess.Popen
    failure = RuntimeError("signature assembly failed")

    def start(*args, **kwargs):
        process = original(*args, **kwargs)
        processes.append(process)
        return process

    def assemble():
        raise failure

    monkeypatch.setattr(subprocess, "Popen", start)
    with pytest.raises(RuntimeError) as raised:
        Git(root).head(before_wait=assemble)
    assert raised.value is failure
    assert len(processes) == 1
    assert processes[0].poll() is not None
    assert processes[0].stdout.closed and processes[0].stderr.closed
