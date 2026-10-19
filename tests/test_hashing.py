"""Complete-content hashing and isolated helper protocol contracts."""

import gc
import hashlib
import io
import os
import struct
import subprocess
import weakref
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from researchgraph.store import hashing
from researchgraph.store.hashing import HashScanner, hash_file


def sources(tmp_path):
    paths = []
    for name, content in [("plain", b"a"), ("별\nline", b"x" * 20000 + b"end")]:
        path = tmp_path / name
        path.write_bytes(content)
        paths.append(str(path))
    return paths


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
        assert 1 <= len(processes) <= min(os.cpu_count() or 1, 8)
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


def test_eight_worker_bound_and_all_requests_precede_reads(monkeypatch):
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

    monkeypatch.setattr(hashing.os, "cpu_count", lambda: 128)
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
