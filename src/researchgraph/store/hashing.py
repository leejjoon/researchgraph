"""Complete file hashes, optionally computed by fixed isolated Python helpers.

Helpers are private index computation resources, not graph task runners. Every
request opens and reads every supplied file; no filesystem metadata is cached.
"""

import hashlib
import json
import os
import struct
import subprocess
import sys
import threading
import weakref

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
        self._lock = threading.Lock()
        self._finalizer = weakref.finalize(self, _shutdown, self._processes)

    def close(self):
        with self._lock:
            self._closed = True
            self._finalizer()

    def hash_files(self, paths):
        """Return contiguous 32-byte hashes in input order, without caching."""
        with self._lock:
            if self._closed:
                raise OSError("Hash scanner is closed")
            if not paths:
                return b""
            try:
                if not self._processes:
                    for _ in range(min(os.cpu_count() or 1, 8, len(paths))):
                        self._processes.append(
                            subprocess.Popen(
                                [
                                    sys.executable,
                                    "-I",
                                    "-m",
                                    "researchgraph.store.hash_worker",
                                ],
                                stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE,
                                bufsize=0,
                            )
                        )
                count = min(len(paths), len(self._processes))
                # Contiguous deterministic slices preserve the sorted source order.
                batches = [
                    paths[len(paths) * i // count : len(paths) * (i + 1) // count]
                    for i in range(count)
                ]
                workers = self._processes[:count]
                # Send every request before reading any replies: helpers run together.
                for process, batch in zip(workers, batches):
                    if process.poll() is not None:
                        raise OSError("Hash helper exited unexpectedly")
                    request = (json.dumps(batch, separators=(",", ":")) + "\n").encode(
                        "utf-8"
                    )
                    _write_all(process.stdin, request)
                    process.stdin.flush()
                results = []
                for process, batch in zip(workers, batches):
                    length = struct.unpack(">I", _read_exact(process.stdout, 4))[0]
                    expected = 32 * len(batch)
                    # Read only a bounded status byte before trusting a payload size.
                    if length < 1 or length > max(expected + 1, ERROR_LIMIT + 1):
                        raise OSError("Invalid hash helper frame length")
                    status = _read_exact(process.stdout, 1)[0]
                    if status == 0:
                        if length != expected + 1:
                            raise OSError("Invalid hash helper success length")
                        results.append(_read_exact(process.stdout, expected))
                    elif status == 1:
                        if length > ERROR_LIMIT + 1:
                            raise OSError("Invalid hash helper error length")
                        message = _read_exact(process.stdout, length - 1).decode(
                            "utf-8"
                        )
                        raise OSError(f"Hash helper failed: {message}")
                    else:
                        raise OSError("Invalid hash helper reply status")
                    if process.poll() is not None:
                        raise OSError("Hash helper exited unexpectedly")
                return b"".join(results)
            except (OSError, ValueError) as exc:
                self._closed = True
                self._finalizer()
                raise OSError(f"Complete content hashing failed: {exc}") from exc
            except BaseException:
                # An interrupted protocol exchange cannot be reused: its old
                # reply would otherwise be mistaken for a later file snapshot.
                self._closed = True
                self._finalizer()
                raise
