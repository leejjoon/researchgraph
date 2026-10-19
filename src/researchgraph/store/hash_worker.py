"""Private isolated content-hash helper; invoked only via Python's -I -m."""

import json
import struct
import sys

from researchgraph.store.hashing import ERROR_LIMIT, _write_all, hash_file


def main():
    source, destination = sys.stdin.buffer, sys.stdout.buffer
    for line in source:
        try:
            paths = json.loads(line)
            if not isinstance(paths, list) or any(
                not isinstance(path, str) for path in paths
            ):
                raise ValueError("Hash request must be an array of paths")
            payload = b"".join(hash_file(path) for path in paths)
            status = b"\x00"
        except (OSError, ValueError, TypeError) as exc:
            # Keep error frames bounded and valid UTF-8 even at a codepoint boundary.
            payload = str(exc).encode("utf-8", errors="replace")[:ERROR_LIMIT]
            payload = payload.decode("utf-8", errors="ignore").encode("utf-8")
            status = b"\x01"
        _write_all(destination, struct.pack(">I", len(payload) + 1) + status + payload)
        destination.flush()


if __name__ == "__main__":
    main()
