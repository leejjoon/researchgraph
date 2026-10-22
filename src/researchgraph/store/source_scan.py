"""Fresh authoritative file discovery and the shared content signature codec."""

import hashlib
import os
from pathlib import Path

from researchgraph.model import RuleViolation
from researchgraph.store import _native_scan
from researchgraph.store.hashing import hash_file


class SourceFiles:
    """Own source-path framing for an absolute project root, not file digests.

    Construction stores root and empty discovery state. Each discover performs
    a fresh native directory walk; unchanged paths may reuse immutable framing.
    Discovery and digest are separate: callers must discover again for freshness
    and append a later fresh HEAD to the returned content digest themselves.
    """

    def __init__(self, root):
        self.root = Path(os.path.abspath(root))
        self._files = None
        self._discovered = None
        self._records = {}
        self.frames = ()
        self.paths = ()
        self._frame_descriptor = _native_scan.compile_frames(self.frames, self.paths)

    def discover(self, *, _batch_sink=None):
        """Freshly discover sources under root and return the current file count.

        Forward optional private _batch_sink (default None) to native discovery;
        it receives tuples of absolute path strings, at most 256 paths each.
        Publish sorted paths, UTF-8 length-prefixed name frames and compiled
        framing only after discovery/encoding/compilation succeed; unchanged path
        sets reuse framing, not content hashes. Native PathError and non-UTF-8
        source names become schema RuleViolation; other errors propagate.
        """
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
        """Return a mutable SHA-256 object for framed discovered file hashes.

        Does not discover paths or include HEAD. By default read all current file
        bytes serially; an optional scanner supplies concatenated full-file hashes
        instead. Explicit file_batches bypasses both reads and scanner and must
        contain the current scan's path/hash batches for the compiled framing.
        Native framing and file/scanner errors propagate. The caller owns the
        returned digest and appends a later fresh HEAD before finalizing it.
        """
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
