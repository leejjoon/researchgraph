"""Fixed isolated source scanner; no graph operations or consumer callbacks."""

import json
import struct
import subprocess
import sys

from researchgraph.model import RuleViolation
from researchgraph.store.git import Git
from researchgraph.store.hashing import ERROR_LIMIT, HashScanner, _write_all
from researchgraph.store.source_scan import SourceFiles

RESULT = 1
RULE_ERROR = 2
IO_ERROR = 3
SCAN = b"SCAN\n"


def _rule_payload(exc):
    payload = json.dumps(
        {"rule": exc.rule, "message": exc.message, "ids": exc.ids},
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(payload) > ERROR_LIMIT:
        raise OSError("Source refusal exceeds protocol bound")
    return payload


def _reply(destination, status, payload):
    _write_all(
        destination, struct.pack(">I", len(payload) + 1) + bytes([status]) + payload
    )
    destination.flush()


def main():
    """Serve the private current-byte scan protocol for the root in argv[1].

    Read only fixed SCAN lines from binary stdin, using a bounded request read;
    this is not a generic command executor. Hash discovered live files before
    resolving HEAD, then reply on binary stdout with a length-prefixed status
    frame containing the file count, optional HEAD, and combined digest.

    Encode scan RuleViolations as bounded rule-error frames and handled I/O,
    value, type, or subprocess failures as bounded I/O-error frames, then stop.
    EOF ends the request loop. Close the owned scanner on loop exit; setup and
    reply-write failures can propagate rather than producing a further frame.
    """
    source, destination = sys.stdin.buffer, sys.stdout.buffer
    files = SourceFiles(sys.argv[1])
    git = Git(files.root)
    scanner = HashScanner()
    try:
        while line := source.readline(len(SCAN) + 1):
            try:
                if line != SCAN:
                    raise OSError("Invalid source scan request")
                digest = None
                with scanner.stream() as stream:
                    count = files.discover(_batch_sink=stream.submit)
                    file_batches = stream.finish()
                del stream

                def assemble(file_batches=file_batches):
                    nonlocal digest
                    digest = files.digest(file_batches=file_batches)

                # HEAD starts only after every live byte hash completed.
                # Its process overlaps independent, non-I/O frame assembly.
                head = git.head(before_wait=assemble)
                del file_batches, assemble
                encoded_head = (head or "").encode("ascii")
                if head is not None and (
                    len(encoded_head) not in (40, 64)
                    or any(
                        char not in b"0123456789abcdefABCDEF" for char in encoded_head
                    )
                ):
                    raise OSError("Invalid git HEAD result")
                digest.update(b"HEAD\0" + encoded_head)
                _reply(
                    destination,
                    RESULT,
                    count.to_bytes(8, "big")
                    + bytes([len(encoded_head)])
                    + encoded_head
                    + digest.digest(),
                )
                del digest, head, encoded_head
            except RuleViolation as exc:
                try:
                    payload = _rule_payload(exc)
                except OSError as failure:
                    _reply(destination, IO_ERROR, str(failure).encode("utf-8"))
                else:
                    _reply(destination, RULE_ERROR, payload)
                return
            except (OSError, ValueError, TypeError, subprocess.SubprocessError) as exc:
                payload = str(exc).encode("utf-8", errors="replace")[:ERROR_LIMIT]
                payload = payload.decode("utf-8", errors="ignore").encode("utf-8")
                _reply(destination, IO_ERROR, payload)
                return
    finally:
        scanner.close()


if __name__ == "__main__":
    main()
