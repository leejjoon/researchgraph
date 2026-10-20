"""Parent ownership of one fixed isolated discovery/hash supervisor session."""

import json
import os
import signal
import struct
import subprocess
import sys
import threading
import weakref

from researchgraph.model import RuleViolation
from researchgraph.store.hashing import ERROR_LIMIT, _read_exact, _write_all
from researchgraph.store.source_worker import IO_ERROR, RESULT, RULE_ERROR, SCAN


def _signal_group(process, sig):
    try:
        os.killpg(process.pid, sig)
    except ProcessLookupError:
        pass


def _shutdown(process):
    """Static finalizer: own only this new session, never the calling process."""
    try:
        try:
            process.stdin.close()
        except (OSError, ValueError):
            pass
        try:
            code = process.wait(timeout=0.25)
        except subprocess.TimeoutExpired:
            code = None
        if code != 0:
            # The supervisor may already be dead while its children survive.
            _signal_group(process, signal.SIGTERM)
            try:
                process.wait(timeout=0.25)
            except subprocess.TimeoutExpired:
                pass
            finally:
                _signal_group(process, signal.SIGKILL)
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired as exc:
                raise OSError("Source supervisor did not exit after kill") from exc
    finally:
        process.stdout.close()


class SourceScanner:
    """Own one isolated fresh-discovery/hash supervisor rooted at root.

    Construction starts a new process session with private pipes; scan requests
    are serialized by a lock. The supervisor owns its bounded hashing helpers,
    not graph task runners. Protocol/scan errors terminally close this owner and
    never retry or fall back to serial IO. Explicit close or finalization shuts
    down this session without targeting the caller's process group.
    """

    def __init__(self, root):
        self._lock = threading.Lock()
        self._closed = False
        self.file_count = None
        self.process = subprocess.Popen(
            [
                sys.executable,
                "-I",
                "-m",
                "researchgraph.store.source_worker",
                os.path.abspath(root),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            bufsize=0,
            start_new_session=True,
        )
        self._finalizer = weakref.finalize(self, _shutdown, self.process)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        """Terminally close this supervisor session under the scan lock.

        Close input and wait, escalating to session-group termination and kill
        when needed, then close output. The finalizer runs at most once and
        shutdown errors propagate. Previously returned fingerprint tuples are
        unchanged; subsequent scan calls raise OSError.
        """
        with self._lock:
            self._closed = True
            self._finalizer()

    def _read_frame(self):
        length = struct.unpack(">I", _read_exact(self.process.stdout, 4))[0]
        if length < 1 or length > ERROR_LIMIT + 1:
            raise OSError("Invalid source supervisor frame length")
        status = _read_exact(self.process.stdout, 1)[0]
        if status == RESULT:
            if length not in (42, 82, 106):
                raise OSError("Invalid source supervisor success length")
            return status, _read_exact(self.process.stdout, length - 1)
        elif status in (RULE_ERROR, IO_ERROR):
            try:
                message = _read_exact(self.process.stdout, length - 1).decode("utf-8")
            except UnicodeError as exc:
                raise OSError("Invalid source supervisor error encoding") from exc
            if status == RULE_ERROR:
                try:
                    refusal = json.loads(message)
                    if (
                        not isinstance(refusal, dict)
                        or set(refusal) != {"rule", "message", "ids"}
                        or not isinstance(refusal["rule"], str)
                        or not refusal["rule"]
                        or not isinstance(refusal["message"], str)
                        or not isinstance(refusal["ids"], list)
                        or any(not isinstance(value, str) for value in refusal["ids"])
                    ):
                        raise ValueError("Invalid refusal fields")
                except (ValueError, TypeError) as exc:
                    raise OSError("Invalid source supervisor refusal") from exc
                raise RuleViolation(refusal["rule"], refusal["message"], refusal["ids"])
            raise OSError(f"Source scan failed: {message}")
        else:
            raise OSError("Invalid source supervisor status")

    def scan(self, *, before_wait=None):
        """Return a fresh (hex fingerprint, HEAD-or-None) tuple for this root.

        Request fresh discovery and full-byte hashing, including fresh HEAD.
        Optional before_wait defaults to None; when supplied, call it after
        sending the request and before reading the reply to overlap parent work.
        Success updates file_count. Closed/process/protocol/IO failures raise
        OSError; structured schema refusals raise RuleViolation. Any exception,
        including callback failures, terminally closes the owner without retry
        or serial fallback, preserving the original exception during cleanup.
        """
        with self._lock:
            if self._closed:
                raise OSError("Source scanner is closed")
            try:
                if self.process.poll() is not None:
                    raise OSError("Source supervisor exited unexpectedly")
                _write_all(self.process.stdin, SCAN)
                self.process.stdin.flush()
                if before_wait is not None:
                    before_wait()
                status, payload = self._read_frame()
                if status != RESULT:
                    raise OSError("Expected source supervisor digest")
                if len(payload) < 41:
                    raise OSError("Invalid source supervisor result")
                head_length = payload[8]
                if head_length not in (0, 40, 64) or len(payload) != 41 + head_length:
                    raise OSError("Invalid source supervisor HEAD length")
                encoded_head = payload[9 : 9 + head_length]
                if any(char not in b"0123456789abcdefABCDEF" for char in encoded_head):
                    raise OSError("Invalid source supervisor HEAD encoding")
                current_head = encoded_head.decode("ascii") if head_length else None
                if self.process.poll() is not None:
                    raise OSError("Source supervisor exited unexpectedly")
                self.file_count = int.from_bytes(payload[:8], "big")
                return payload[-32:].hex(), current_head
            except BaseException:
                self._closed = True
                try:
                    self._finalizer()
                except OSError:
                    # Preserve caller exceptions, schema refusals and IO failures.
                    pass
                raise
