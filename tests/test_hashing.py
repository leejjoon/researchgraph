"""Complete-content hashing and isolated helper protocol contracts."""

import gc
import hashlib
import io
import json
import os
import struct
import subprocess
import sys
import weakref
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from researchgraph.store import hashing
from researchgraph.store.hashing import HashScanner, hash_file


def test_hash_helper_import_does_not_initialize_product_dependencies():
    result = subprocess.run(
        [
            hashing.sys.executable,
            "-I",
            "-c",
            (
                "import sys; import researchgraph.store.hash_worker; "
                "assert 'researchgraph.project' not in sys.modules; "
                "assert 'jsonschema' not in sys.modules"
            ),
        ],
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode()
    assert result.stdout == b""


def sources(tmp_path):
    paths = []
    for name, content in [("plain", b"a"), ("별\nline", b"x" * 20000 + b"end")]:
        path = tmp_path / name
        path.write_bytes(content)
        paths.append(str(path))
    return paths


def test_hash_worker_releases_completed_payload_before_next_request(
    tmp_path, monkeypatch
):
    from researchgraph.store import hash_worker

    paths = sources(tmp_path)
    digests = [hashlib.sha256(Path(path).read_bytes()).digest() for path in paths]
    payload = b"".join(digests)
    expected = struct.pack(">I", len(payload) + 1) + b"\x00" + payload
    request = (json.dumps(paths) + "\n").encode("utf-8")
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
            assert all(digest not in value for digest in digests), (
                f"Idle worker retains completed SHA bytes in {name}"
            )
        elif isinstance(value, (list, tuple)):
            for index, item in enumerate(value):
                assert_no_completed_result(item, f"{name}[{index}]")
        elif isinstance(value, dict):
            for key, item in value.items():
                assert_no_completed_result(item, f"{name}[{key!r}]")

    class Input:
        requests = 0

        def __iter__(self):
            return self

        def __next__(self):
            self.requests += 1
            if self.requests == 1:
                return request
            assert self.requests == 2
            assert output.flushes == 1
            assert output.getvalue() == expected
            caller = sys._getframe(1)
            assert caller.f_code is hash_worker.main.__code__
            owners.append(caller.f_locals["files"])
            assert owners[0]._entries, "Real small files must exercise mapped hashing"
            for name, value in caller.f_locals.items():
                assert_no_completed_result(value, name)
            boundaries.append("idle after flushed success")
            raise StopIteration

    monkeypatch.setattr(hash_worker.sys, "stdin", SimpleNamespace(buffer=Input()))
    monkeypatch.setattr(hash_worker.sys, "stdout", SimpleNamespace(buffer=output))
    hash_worker.main()
    assert boundaries == ["idle after flushed success"]
    assert output.getvalue() == expected
    assert owners[0]._closed and not owners[0]._entries


def assert_raw_batches(batches, expected):
    assert type(batches) is list
    actual = {}
    for batch in batches:
        assert type(batch) is tuple and len(batch) == 2
        paths, payload = batch
        assert type(paths) is tuple and all(type(path) is str for path in paths)
        assert paths and type(payload) is bytes
        assert len(payload) == 32 * len(paths)
        for index, path in enumerate(paths):
            assert path not in actual
            actual[path] = payload[index * 32 : (index + 1) * 32]
    assert actual == expected


def test_direct_and_helper_hashes_match_independent_contents(tmp_path):
    paths = sources(tmp_path)
    expected = b"".join(
        hashlib.sha256(Path(path).read_bytes()).digest() for path in paths
    )
    assert b"".join(map(hash_file, paths)) == expected
    scanner = HashScanner()
    try:
        assert scanner.hash_files(paths) == expected
        processes = list(scanner._processes)
        assert len(processes) == min(len(paths), 8)
        assert scanner.hash_files(paths) == expected
        assert scanner._processes == processes
    finally:
        scanner.close()
    scanner.close()
    assert all(process.poll() is not None for process in processes)
    with pytest.raises(OSError, match="closed"):
        scanner.hash_files(paths)


def test_same_size_and_mtime_edits_are_read_fully(tmp_path):
    paths = sources(tmp_path)
    scanner = HashScanner()
    try:
        before = scanner.hash_files(paths)
        path = Path(paths[1])
        stamp = path.stat()
        path.write_bytes(b"x" * 20000 + b"new")
        os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        assert scanner.hash_files(paths) != before
        assert scanner.hash_files(paths) == b"".join(map(hash_file, paths))
    finally:
        scanner.close()


def test_missing_and_symlink_files_never_produce_hashes(tmp_path):
    missing = str(tmp_path / "missing")
    with pytest.raises(OSError):
        hash_file(missing)
    scanner = HashScanner()
    with pytest.raises(OSError, match="missing"):
        scanner.hash_files([missing])
    assert all(process.poll() is not None for process in scanner._processes)
    target = tmp_path / "target"
    target.write_bytes(b"data")
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(OSError):
        hash_file(str(link))
    scanner = HashScanner()
    with pytest.raises(OSError):
        scanner.hash_files([str(link)])
    # Reading a directory fails even when tests run with elevated permissions.
    scanner = HashScanner()
    with pytest.raises(OSError):
        scanner.hash_files([str(tmp_path)])


class FakeProcess:
    def __init__(self, reply):
        self.stdin = io.BytesIO()
        self.stdout = io.BytesIO(reply)
        self.returncode = None

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.returncode = 0
        return 0

    def terminate(self):
        self.returncode = -15

    def kill(self):
        self.returncode = -9


@pytest.mark.parametrize(
    "reply",
    [
        b"",
        b"\x00\x00",
        struct.pack(">I", 33) + b"\x00short",
        struct.pack(">I", 2**32 - 1),
        struct.pack(">I", 33) + b"\x02" + b"x" * 32,
        struct.pack(">I", 2) + b"\x00x",
        struct.pack(">I", 2) + b"\x01\xff",
    ],
)
def test_broken_or_malformed_replies_fail_without_fallback(monkeypatch, reply):
    process = FakeProcess(reply)
    monkeypatch.setattr(hashing.subprocess, "Popen", lambda *args, **kwargs: process)
    scanner = HashScanner()
    with pytest.raises(OSError):
        scanner.hash_files(["must-not-be-read-in-parent"])
    assert process.poll() is not None
    scanner.close()


def test_dead_worker_is_not_restarted(tmp_path):
    scanner = HashScanner()
    paths = sources(tmp_path)
    try:
        scanner.hash_files(paths)
        process = scanner._processes[0]
        process.kill()
        process.wait(timeout=5)
        with pytest.raises(OSError):
            scanner.hash_files(paths)
        assert all(worker.poll() is not None for worker in scanner._processes)
    finally:
        scanner.close()


def test_partial_pipe_reads_and_writes_are_completed(monkeypatch):
    class ShortReader(io.BytesIO):
        def read(self, size=-1):
            return super().read(min(size, 3))

    class ShortWriter(io.BytesIO):
        def write(self, data):
            return super().write(data[:2])

    process = FakeProcess(b"")
    process.stdout = ShortReader(struct.pack(">I", 33) + b"\x00" + b"x" * 32)
    process.stdin = ShortWriter()
    monkeypatch.setattr(hashing.subprocess, "Popen", lambda *args, **kwargs: process)
    scanner = HashScanner()
    try:
        assert scanner.hash_files(["별\nline"]) == b"x" * 32
        assert process.stdin.getvalue().endswith(b"\n")
        assert len(process.stdin.getvalue().splitlines()) == 1
    finally:
        scanner.close()


def test_helper_runs_from_already_threaded_caller(tmp_path):
    paths = sources(tmp_path)
    scanner = HashScanner()
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            assert executor.submit(scanner.hash_files, paths).result(
                timeout=30
            ) == b"".join(map(hash_file, paths))
        for process in scanner._processes:
            assert process.args[:4] == [
                hashing.sys.executable,
                "-I",
                "-m",
                "researchgraph.store.hash_worker",
            ]
    finally:
        scanner.close()


def test_finalizer_reaps_helpers_without_retaining_scanner(tmp_path):
    scanner = HashScanner()
    scanner.hash_files(sources(tmp_path))
    processes = list(scanner._processes)
    reference = weakref.ref(scanner)
    del scanner
    gc.collect()
    assert reference() is None
    assert all(process.poll() is not None for process in processes)


def test_shutdown_terminates_unresponsive_helper(monkeypatch):
    class Unresponsive(FakeProcess):
        def wait(self, timeout=None):
            if self.returncode is None:
                raise subprocess.TimeoutExpired("helper", timeout)
            return self.returncode

    process = Unresponsive(struct.pack(">I", 33) + b"\x00" + b"x" * 32)
    monkeypatch.setattr(hashing.subprocess, "Popen", lambda *args, **kwargs: process)
    scanner = HashScanner()
    scanner.hash_files(["fake"])
    scanner.close()
    assert process.poll() == -15


def test_shutdown_kills_helper_that_ignores_termination(monkeypatch):
    class Stubborn(FakeProcess):
        def wait(self, timeout=None):
            if self.returncode is None:
                raise subprocess.TimeoutExpired("helper", timeout)
            return self.returncode

        def terminate(self):
            pass

    process = Stubborn(struct.pack(">I", 33) + b"\x00" + b"x" * 32)
    monkeypatch.setattr(hashing.subprocess, "Popen", lambda *args, **kwargs: process)
    scanner = HashScanner()
    scanner.hash_files(["fake"])
    scanner.close()
    assert process.poll() == -9


def test_partial_startup_failure_reaps_started_helpers(monkeypatch):
    processes = []

    def start(*args, **kwargs):
        if processes:
            raise OSError("cannot start helper")
        process = FakeProcess(b"")
        processes.append(process)
        return process

    monkeypatch.setattr(hashing.os, "cpu_count", lambda: 8)
    monkeypatch.setattr(hashing.subprocess, "Popen", start)
    scanner = HashScanner()
    with pytest.raises(OSError, match="cannot start helper"):
        scanner.hash_files(["one", "two"])
    assert all(process.poll() is not None for process in processes)


@pytest.mark.parametrize("cpu_count", [1, 2, 4, 128])
def test_eight_worker_bound_and_all_requests_precede_reads(monkeypatch, cpu_count):
    processes = []

    class CheckedReader(io.BytesIO):
        def read(self, size=-1):
            assert len(processes) == 8
            assert all(
                process.stdin.getvalue().endswith(b"\n") for process in processes
            )
            return super().read(size)

    def start(*args, **kwargs):
        value = bytes([len(processes)]) * 64
        process = FakeProcess(b"")
        process.stdout = CheckedReader(struct.pack(">I", 65) + b"\x00" + value)
        processes.append(process)
        return process

    monkeypatch.setattr(hashing.os, "cpu_count", lambda: cpu_count)
    monkeypatch.setattr(hashing.subprocess, "Popen", start)
    scanner = HashScanner()
    try:
        assert scanner.hash_files([str(i) for i in range(16)]) == b"".join(
            bytes([i]) * 64 for i in range(8)
        )
    finally:
        scanner.close()


def test_interrupted_reply_closes_helpers_without_swallowing_interrupt(monkeypatch):
    class Interrupted(io.BytesIO):
        def read(self, size=-1):
            raise KeyboardInterrupt

    process = FakeProcess(b"")
    process.stdout = Interrupted()
    monkeypatch.setattr(hashing.subprocess, "Popen", lambda *args, **kwargs: process)
    scanner = HashScanner()
    with pytest.raises(KeyboardInterrupt):
        scanner.hash_files(["not-read-in-parent"])
    assert process.poll() is not None
    with pytest.raises(OSError, match="closed"):
        scanner.hash_files(["not-read-in-parent"])


def test_path_request_encoding_reused_but_all_bytes_rehashed(tmp_path, monkeypatch):
    paths = sources(tmp_path)
    original = hashing.json.dumps
    encoded = []

    def encode(value, **kwargs):
        encoded.append(tuple(value))
        return original(value, **kwargs)

    monkeypatch.setattr(hashing.json, "dumps", encode)
    scanner = HashScanner()
    try:
        first = scanner.hash_files(paths)
        count = len(encoded)
        target = Path(paths[1])
        stamp = target.stat()
        target.write_bytes(b"x" * 20000 + b"new")
        os.utime(target, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        result = scanner.hash_files(list(paths))
        assert result != first and result == b"".join(map(hash_file, paths))
        assert len(encoded) == count
        for changed in [list(reversed(paths)), paths[:1], paths]:
            previous = len(encoded)
            assert scanner.hash_files(changed) == b"".join(map(hash_file, changed))
            assert len(encoded) > previous
        Path(paths[0]).unlink()
        with pytest.raises(OSError, match="failed"):
            scanner.hash_files(paths)
        assert all(process.poll() is not None for process in scanner._processes)
    finally:
        scanner.close()


def test_stream_many_batches_reuses_encoding_but_never_hashes(tmp_path, monkeypatch):
    paths = sources(tmp_path)
    encoded = []
    original = hashing.json.dumps

    def encode(value, **kwargs):
        encoded.append(tuple(value))
        return original(value, **kwargs)

    monkeypatch.setattr(hashing.json, "dumps", encode)
    scanner = HashScanner()
    try:
        for iteration in range(2):
            with scanner.stream() as stream:
                for path in paths:
                    stream.submit([path])
                batches = stream.finish()
            assert_raw_batches(batches, dict(zip(paths, map(hash_file, paths))))
            if not iteration:
                count = len(encoded)
                Path(paths[0]).write_bytes(b"new live bytes")
            else:
                assert len(encoded) == count
        with scanner.stream() as stream:
            stream.submit(paths[:1])
            batches = stream.finish()
        assert_raw_batches(batches, {paths[0]: hash_file(paths[0])})
        assert set(scanner._encodings) == {tuple(paths[:1])}
    finally:
        scanner.close()


def test_stream_drains_worker_before_reusing_pipe(monkeypatch):
    events = []

    class Writer(io.BytesIO):
        def write(self, data):
            events.append("send")
            assert events.count("send") <= events.count("reply") + 1
            return super().write(data)

    class Reader(io.BytesIO):
        def read(self, size=-1):
            if self.tell() % 37 == 0:
                events.append("reply")
            return super().read(size)

    process = FakeProcess(b"")
    process.stdin = Writer()
    process.stdout = Reader((struct.pack(">I", 33) + b"\x00" + b"x" * 32) * 100)
    monkeypatch.setattr(hashing.subprocess, "Popen", lambda *a, **k: process)
    scanner = HashScanner()
    try:
        scanner._start(1)
        with scanner.stream() as stream:
            for i in range(100):
                stream.submit([str(i)])
            batches = stream.finish()
        assert len(batches) == 100
        assert_raw_batches(batches, {str(i): b"x" * 32 for i in range(100)})
        assert events.count("send") == events.count("reply") == 100
    finally:
        scanner.close()


def test_stream_discovery_failure_aborts_partial_requests(tmp_path):
    scanner = HashScanner()
    failure = RuntimeError("late discovery failure")
    with pytest.raises(RuntimeError) as raised, scanner.stream() as stream:
        stream.submit(sources(tmp_path))
        raise failure
    assert raised.value is failure
    assert all(process.poll() is not None for process in scanner._processes)
    assert not scanner._encodings


def test_stream_malformed_reply_closes_whole_pool(monkeypatch):
    processes = []

    def start(*args, **kwargs):
        process = FakeProcess(struct.pack(">I", 2) + b"\x00x")
        processes.append(process)
        return process

    monkeypatch.setattr(hashing.subprocess, "Popen", start)
    scanner = HashScanner()
    with pytest.raises(OSError), scanner.stream() as stream:
        stream.submit(["one"])
        stream.submit(["two"])
    assert all(process.poll() is not None for process in processes)
    with pytest.raises(OSError, match="closed"), scanner.stream():
        pytest.fail("Terminal pool must not restart")


def test_stream_results_are_current_only_fresh_and_not_retained(tmp_path):
    paths = sources(tmp_path)
    scanner = HashScanner()
    try:
        with scanner.stream() as stream:
            stream.submit(paths)
            first = stream.finish()
            assert_raw_batches(first, dict(zip(paths, map(hash_file, paths))))
            assert not hasattr(stream, "hashes")
            # Explicit finish plus context-manager finish must not duplicate replies.
            assert stream.finish() == first
        del stream
        original = list(first)
        target = Path(paths[1])
        stamp = target.stat()
        target.write_bytes(b"x" * 20000 + b"new")
        os.utime(target, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        with scanner.stream() as stream:
            stream.submit(paths[1:])
            second = stream.finish()
        assert_raw_batches(second, {paths[1]: hash_file(paths[1])})
        assert first == original
        assert first is not second
        assert second[0][1] != first[0][1][-32:]
        del stream
        with scanner.stream() as stream:
            empty = stream.finish()
        assert empty == [] and type(empty) is list
        assert not scanner._encodings
        del stream
        # The owner may keep request metadata and process resources, not replies
        # or a completed stream. Observe ownership without changing GC policy.
        getrefcount = getattr(hashing.sys, "getrefcount", None)
        if getrefcount is not None:
            control = []
            baseline = getrefcount(control)
            assert getrefcount(first) == baseline
            assert getrefcount(second) == baseline
            payload = second[0][1]
            # One additional owner is the result tuple. Argument borrowing
            # differs across CPython versions, so compare with the control.
            assert getrefcount(payload) == baseline + 1
        assert "hashes" not in vars(scanner)
    finally:
        scanner.close()
