"""Private isolated content-hash helper; invoked only via Python's -I -m."""

import hashlib
import json
import mmap
import os
import resource
import stat
import struct
import sys
from collections import OrderedDict
from dataclasses import dataclass

from researchgraph.store.hashing import ERROR_LIMIT, _write_all, hash_file


@dataclass
class _MappedFile:
    mapping: mmap.mmap
    identity: tuple
    charge: int

    def close(self):
        self.mapping.close()


class MappedFiles:
    """Bounded live read-only mappings, never cached content or digests.

    Each lookup stats the path and hashes every mapped byte anew. The mapping
    keeps its inode alive, so a matching path identity cannot refer to a reused
    inode. Python 3.11's mmap owns the sole retained FD for each entry.
    Private limit overrides only reduce the production resource bounds.
    """

    def __init__(self, *, _capacity=1024, _byte_budget=16 * 1024 * 1024):
        soft, _ = resource.getrlimit(resource.RLIMIT_NOFILE)
        available = 1024 if soft == resource.RLIM_INFINITY else max(0, soft - 32)
        self._capacity = max(0, min(1024, _capacity, available))
        self._byte_budget = max(0, min(16 * 1024 * 1024, _byte_budget))
        self._entries = OrderedDict()
        self._mapped_bytes = 0
        self._closed = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _discard(self, path):
        entry = self._entries.pop(path, None)
        if entry is not None:
            self._mapped_bytes -= entry.charge
            entry.close()

    def close(self):
        self._closed = True
        failure = None
        while self._entries:
            path = next(iter(self._entries))
            try:
                self._discard(path)
            except OSError as exc:
                failure = exc
        if failure is not None:
            raise failure

    @staticmethod
    def _identity(info):
        if not stat.S_ISREG(info.st_mode):
            raise OSError("Hash source is not a regular file")
        return info.st_dev, info.st_ino, info.st_size

    def digest(self, path):
        if self._closed:
            raise ValueError("Mapped hash resources are closed")
        try:
            identity = self._identity(os.stat(path, follow_symlinks=False))
        except OSError:
            self._discard(path)
            raise
        entry = self._entries.get(path)
        if entry is not None:
            if identity == entry.identity and len(entry.mapping) == identity[2]:
                self._entries.move_to_end(path)
                return hashlib.sha256(entry.mapping).digest()
            self._discard(path)

        size = identity[2]
        page = mmap.PAGESIZE
        charge = ((size + page - 1) // page) * page
        if not size or charge > self._byte_budget or not self._capacity:
            return hash_file(path)

        while self._entries and (
            len(self._entries) >= self._capacity
            or self._mapped_bytes + charge > self._byte_budget
        ):
            self._discard(next(iter(self._entries)))

        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        mapping = None
        try:
            try:
                if self._identity(os.fstat(descriptor)) != identity:
                    raise OSError("Hash source changed while opening")
                mapping = mmap.mmap(descriptor, size, access=mmap.ACCESS_READ)
                if self._identity(os.fstat(descriptor)) != identity:
                    raise OSError("Hash source changed while mapping")
            finally:
                os.close(descriptor)
            digest = hashlib.sha256(mapping).digest()
        except BaseException:
            if mapping is not None:
                mapping.close()
            raise
        self._entries[path] = _MappedFile(mapping, identity, charge)
        self._mapped_bytes += charge
        return digest


def main():
    source, destination = sys.stdin.buffer, sys.stdout.buffer
    previous_line, previous_paths = None, None
    with MappedFiles() as files:
        for line in source:
            try:
                if line == previous_line:
                    paths = previous_paths
                else:
                    paths = json.loads(line)
                    if not isinstance(paths, list) or any(
                        not isinstance(path, str) for path in paths
                    ):
                        raise ValueError("Hash request must be an array of paths")
                    previous_line, previous_paths = line, paths
                payload = b"".join(files.digest(path) for path in paths)
                status = b"\x00"
            except (OSError, ValueError, TypeError) as exc:
                # Bound error frames without breaking UTF-8 at a codepoint boundary.
                payload = str(exc).encode("utf-8", errors="replace")[:ERROR_LIMIT]
                payload = payload.decode("utf-8", errors="ignore").encode("utf-8")
                status = b"\x01"
            _write_all(
                destination, struct.pack(">I", len(payload) + 1) + status + payload
            )
            destination.flush()
            del payload
            if status == b"\x01":
                return


if __name__ == "__main__":
    main()
