"""Whole-write checkout locking and per-file atomic, pre-call rollback."""

import fcntl
import os
import tempfile
from collections.abc import Mapping
from contextlib import contextmanager
from pathlib import Path

from researchgraph.model import RuleViolation


@contextmanager
def write_lock(root: Path):
    """Hold the checkout lock through validation, writes, commit, and events."""
    descriptor = os.open(
        root / ".researchgraph/lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o644
    )
    with os.fdopen(descriptor, "a+b") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def _temporary(path: Path, content: bytes) -> Path:
    descriptor, name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return temporary


def atomic_write(root: Path, files: Mapping[str, bytes]) -> None:
    """Replace files, restoring pre-call bytes (never HEAD) after any failure."""
    snapshots = {}
    attempted = []
    temporaries = set()
    try:
        # Capture all snapshots before replacing any destination.
        for relative in files:
            path = root / relative
            snapshots[relative] = path.read_bytes() if path.exists() else None
        for relative, content in files.items():
            path = root / relative
            temporary = _temporary(path, content)
            temporaries.add(temporary)
            attempted.append(relative)
            os.replace(temporary, path)
            temporaries.discard(temporary)
    except Exception as exc:
        restoration_errors = []
        for relative in reversed(attempted):
            path = root / relative
            try:
                original = snapshots[relative]
                if original is None:
                    path.unlink(missing_ok=True)
                else:
                    try:
                        temporary = _temporary(path, original)
                        temporaries.add(temporary)
                        os.replace(temporary, path)
                        temporaries.discard(temporary)
                    except OSError:
                        # A persistently failing rename must not prevent restoring
                        # the other files or leave an earlier pending write lost.
                        path.write_bytes(original)
            except OSError as restore_error:
                restoration_errors.append(f"{relative}: {restore_error}")
        message = f"File write failed: {exc}"
        if restoration_errors:
            message += "; restoration failed: " + "; ".join(restoration_errors)
        raise RuleViolation("write_failed", message, list(files)) from exc
    finally:
        for temporary in temporaries:
            temporary.unlink(missing_ok=True)
