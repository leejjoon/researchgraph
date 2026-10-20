"""Complete file hashes, optionally computed by fixed isolated Python helpers.

Helpers are private index computation resources, not graph task runners. Every
request freshly checks every supplied path and hashes every live byte. Helpers
may retain bounded read-only mappings, never file-content hashes or freshness
results. Only identical path request encodings are reused by this owner.
"""

import hashlib
import json
import os
import struct
import subprocess
import sys
import threading
import weakref
from contextlib import contextmanager

ERROR_LIMIT = 4096


def hash_file(path):
    """Hash all content with the same no-follow, bounded-read codec everywhere."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        digest = hashlib.sha256()
        while chunk := os.read(descriptor, 8192):
            digest.update(chunk)
        return digest.digest()
    finally:
        os.close(descriptor)


def _read_exact(stream, size):
    chunks = []
    remaining = size
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            raise OSError("Hash helper returned an incomplete reply")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _write_all(stream, data):
    view = memoryview(data)
    while view:
        written = stream.write(view)
        if not written:
            raise OSError("Hash helper pipe closed during write")
        view = view[written:]


def _shutdown(processes):
    """Static finalizer callback: never retain the scanner or its owning Index."""
    for process in processes:
        try:
            process.stdin.close()
        except OSError:
            pass
    for process in processes:
        try:
            process.wait(timeout=0.25)
        except subprocess.TimeoutExpired:
            try:
                process.terminate()
            except ProcessLookupError:
                pass
    for process in processes:
        try:
            process.wait(timeout=0.25)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
            except ProcessLookupError:
                pass
    failure = None
    for process in processes:
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired as exc:
            # Still clean up every other helper before exposing an OS-level failure.
            failure = exc
        finally:
            process.stdout.close()
    if failure is not None:
        raise OSError("Hash helper did not exit after kill") from failure


class HashScanner:
    """Bounded persistent helpers, with explicit close and best-effort finalization.

    A protocol/process/file error closes the entire scanner and raises OSError;
    it is never retried or hidden by a serial scan. A closed scanner is terminal.
    """

    def __init__(self):
        self._processes = []
        self._closed = False
        self._request_paths = None
        self._encodings = {}
        self._lock = threading.Lock()
        self._finalizer = weakref.finalize(self, _shutdown, self._processes)

    def close(self):
        with self._lock:
            self._closed = True
            self._request_paths = None
            self._encodings = {}
            self._finalizer()

    def hash_files(self, paths):
        """Return fresh hashes in input order; cache only path request encodings."""
        current = tuple(paths)
        with self.stream(_whole_paths=current) as stream:
            if current:
                self._start(len(current))
                count = min(len(current), len(self._processes))
                for i in range(count):
                    stream.submit(
                        current[
                            len(current) * i // count : len(current) * (i + 1) // count
                        ]
                    )
            return b"".join(payload for _, payload in stream.finish())

    def _start(self, count):
        if not self._processes:
            for _ in range(min(os.cpu_count() or 1, 8, count)):
                self._processes.append(
                    subprocess.Popen(
                        [sys.executable, "-I", "-m", "researchgraph.store.hash_worker"],
                        stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE,
                        bufsize=0,
                    )
                )

    @contextmanager
    def stream(self, *, _whole_paths=None):
        """Locked current-scan file batches; at most one request per helper."""
        with self._lock:
            if self._closed:
                raise OSError("Hash scanner is closed")
            if _whole_paths is not None and _whole_paths != self._request_paths:
                self._encodings = {}
            self._request_paths = _whole_paths
            stream = _HashStream(self)
            try:
                yield stream
                stream.finish()
                self._encodings = stream.encodings
            except (OSError, ValueError) as exc:
                self._closed = True
                self._request_paths = None
                self._encodings = {}
                try:
                    self._finalizer()
                except OSError:
                    pass
                raise OSError(f"Complete content hashing failed: {exc}") from exc
            except BaseException:
                # An interrupted protocol exchange cannot be reused: its old
                # reply would otherwise be mistaken for a later file snapshot.
                self._closed = True
                self._request_paths = None
                self._encodings = {}
                try:
                    self._finalizer()
                except OSError:
                    pass
                raise
            finally:
                stream.active = False
                stream.batches = []
                stream.pending.clear()


class _HashStream:
    def __init__(self, owner):
        self.owner = owner
        self.pending = {}
        self.batches = []
        self.encodings = {}
        self.next_worker = 0
        self.active = True

    def submit(self, paths):
        if not self.active:
            raise OSError("Hash stream is closed")
        batch = tuple(paths)
        if not batch:
            return
        self.owner._start(8)
        index = self.next_worker % len(self.owner._processes)
        self.next_worker += 1
        if index in self.pending:
            self._collect(index)
        request = self.owner._encodings.get(batch)
        if request is None:
            request = (json.dumps(batch, separators=(",", ":")) + "\n").encode("utf-8")
        self.encodings[batch] = request
        process = self.owner._processes[index]
        if process.poll() is not None:
            raise OSError("Hash helper exited unexpectedly")
        _write_all(process.stdin, request)
        process.stdin.flush()
        self.pending[index] = batch

    def _collect(self, index):
        batch = self.pending.pop(index)
        process = self.owner._processes[index]
        length = struct.unpack(">I", _read_exact(process.stdout, 4))[0]
        expected = 32 * len(batch)
        if length < 1 or length > max(expected + 1, ERROR_LIMIT + 1):
            raise OSError("Invalid hash helper frame length")
        status = _read_exact(process.stdout, 1)[0]
        if status == 0:
            if length != expected + 1:
                raise OSError("Invalid hash helper success length")
            payload = _read_exact(process.stdout, expected)
        elif status == 1:
            if length > ERROR_LIMIT + 1:
                raise OSError("Invalid hash helper error length")
            message = _read_exact(process.stdout, length - 1).decode("utf-8")
            raise OSError(f"Hash helper failed: {message}")
        else:
            raise OSError("Invalid hash helper reply status")
        if process.poll() is not None:
            raise OSError("Hash helper exited unexpectedly")
        self.batches.append((batch, payload))

    def finish(self):
        if not self.active:
            raise OSError("Hash stream is closed")
        for index in tuple(self.pending):
            self._collect(index)
        return self.batches
