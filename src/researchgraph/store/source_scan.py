"""Fresh authoritative file discovery and the shared content signature codec."""

import hashlib
import os
from pathlib import Path

from researchgraph.model import RuleViolation
from researchgraph.store import _native_scan
from researchgraph.store.hashing import hash_file


class SourceFiles:
    """Reuse immutable path framing only after a fresh complete directory walk."""

    def __init__(self, root):
        self.root = Path(os.path.abspath(root))
        self._files = None
        self._discovered = None
        self._records = {}
        self.frames = ()
        self.paths = ()
        self._frame_descriptor = _native_scan.compile_frames(self.frames, self.paths)

    def discover(self, *, _batch_sink=None):
        try:
            discovered = _native_scan.discover(
                str(self.root), self._records, _batch_sink
            )
        except _native_scan.PathError as exc:
            raise RuleViolation("schema", str(exc)) from exc
        if discovered != self._discovered:
            files = sorted(discovered)
            if files != self._files:
                try:
                    names = [relative.encode("utf-8") for relative, _ in files]
                except UnicodeError as exc:
                    raise RuleViolation(
                        "schema", "Graph source paths must be UTF-8"
                    ) from exc
                frames = tuple(len(name).to_bytes(8, "big") + name for name in names)
                paths = tuple(path for _, path in files)
                descriptor = _native_scan.compile_frames(frames, paths)
            else:
                frames, paths, descriptor = (
                    self.frames,
                    self.paths,
                    self._frame_descriptor,
                )
            records = {record[0]: record for record in files}
            # Discovery, encoding and compilation must all succeed before publication.
            (
                self._files,
                self.frames,
                self.paths,
                self._frame_descriptor,
                self._records,
                self._discovered,
            ) = files, frames, paths, descriptor, records, discovered
        return len(self.paths)

    def digest(self, scanner=None, *, file_batches=None):
        """Hash all discovered file bytes; the caller appends a later fresh HEAD."""
        if file_batches is None:
            payload = (
                b"".join(hash_file(path) for path in self.paths)
                if scanner is None
                else scanner.hash_files(self.paths)
            )
            file_batches = [(self.paths, payload)] if self.paths else []
        return hashlib.sha256(
            _native_scan.frame_hashes(self._frame_descriptor, file_batches)
        )
