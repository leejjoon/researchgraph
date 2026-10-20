"""Batched local-git history and canonical six-field interval projections.

Only status transitions are reconstructed. Mutable metadata remains current;
archive commit identifiers are preserved as opaque provenance, without bundles.
"""

import itertools
import json
import subprocess
from contextlib import closing
from datetime import UTC, datetime
from time import perf_counter

import yaml

from researchgraph.model import (
    Edge,
    Node,
    RuleViolation,
    format_timestamp,
    parse_timestamp,
)
from researchgraph.store.io import _load_mapping

INTERVAL_KEYS = (
    "node_id",
    "status",
    "from_commit",
    "from_time",
    "to_commit",
    "to_time",
)


def record_key(record):
    return json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def intervals_as_dicts(connection):
    """Return export-shaped rows with canonical times and ordered keys."""
    with closing(connection.cursor()) as cursor:
        cursor.execute(
            "SELECT node_id,status,from_commit,from_time,to_commit,to_time "
            "FROM status_intervals ORDER BY node_id,from_time,status"
        )
        return [dict(zip(INTERVAL_KEYS, row)) for row in cursor]


class History:
    def __init__(self, git):
        self.git = git
        self.head = object()
        self.transitions = []
        self.provenance = {}
        self.edge_commits = {}
        self.latest = {}
        self.last_walk_seconds = 0.0

    def _walk(self, head):
        if self.head == head:
            self.last_walk_seconds = 0.0
            return
        started = perf_counter()
        transitions, provenance, latest, edge_commits = [], {}, {}, {}
        if head is not None:
            # Pathspecs are relative to our cwd, but raw diff paths are relative
            # to the worktree root. Project may occupy only a nested directory.
            prefix = self.git.run("rev-parse", "--show-prefix").stdout.rstrip("\n")
            node_prefix = prefix + "graph/nodes/"
            edge_prefix = prefix + "graph/edges/"
            # A single walk provides changed blob identifiers and commit clocks.
            output = self.git.run(
                "log",
                "--reverse",
                "--topo-order",
                "--root",
                "--format=%x00RG %H %ct %s%x00",
                "--raw",
                "-z",
                "--no-abbrev",
                "--no-renames",
                head,
                "--",
                "graph/nodes",
                "graph/edges",
            ).stdout
            changes = []
            commit = time = subject = None
            tokens = iter(output.split("\0"))
            for token in tokens:
                record = token.lstrip("\n")
                if not record:
                    continue
                if record.startswith("RG "):
                    _, commit, seconds, subject = record.split(" ", 3)
                    time = format_timestamp(datetime.fromtimestamp(int(seconds), UTC))
                elif record.startswith(":"):
                    path = next(tokens)
                    parts = record.split()
                    if (
                        path.startswith((node_prefix, edge_prefix))
                        and path.endswith(".yaml")
                        and parts[4] != "D"
                    ):
                        changes.append(
                            (
                                commit,
                                time,
                                subject,
                                parts[3],
                                path.startswith(edge_prefix),
                            )
                        )
                else:
                    raise ValueError("Unexpected git history record")
            identifiers = list(dict.fromkeys(change[3] for change in changes))
            blobs = {}
            if identifiers:
                result = subprocess.run(
                    ["git", "-C", str(self.git.path), "cat-file", "--batch"],
                    input=("\n".join(identifiers) + "\n").encode(),
                    capture_output=True,
                    check=True,
                ).stdout
                offset = 0
                for identifier in identifiers:
                    end = result.index(b"\n", offset)
                    _, kind, size = result[offset:end].split()
                    if kind != b"blob":
                        raise ValueError("Expected git graph blob")
                    offset = end + 1
                    blobs[identifier] = _load_mapping(
                        result[offset : offset + int(size)]
                    )
                    offset += int(size) + 1
            for commit, time, subject, blob, is_edge in changes:
                if is_edge:
                    edge = Edge.from_dict(blobs[blob])
                    edge_commits.setdefault(edge.id, commit)
                    continue
                node = Node.from_dict(blobs[blob])
                if latest.get(node.id) != str(node.status):
                    transitions.append(
                        (
                            node.id,
                            str(node.status),
                            commit,
                            time,
                            subject == "rg: import"
                            or subject.startswith("rg: import "),
                        )
                    )
                latest[node.id] = str(node.status)
                for kind, records in (
                    ("approvals", node.approvals),
                    ("artifacts", node.artifacts),
                ):
                    for ordinal, record in enumerate(records):
                        provenance.setdefault(
                            (kind, node.id, ordinal, record_key(record.to_dict())),
                            commit,
                        )
        self.transitions, self.provenance, self.latest = transitions, provenance, latest
        self.edge_commits = edge_commits
        self.head = head
        self.last_walk_seconds = perf_counter() - started

    def project(self, nodes, head):
        """Recompute archive union and pending projection; cache only the HEAD walk."""
        try:
            self._walk(head)
            archive = self.git.path / ".researchgraph/history.jsonl"
            intervals = []
            if archive.exists():
                for line in archive.read_text(encoding="utf-8").splitlines():
                    record = json.loads(line)
                    if not isinstance(record, dict) or set(record) != set(
                        INTERVAL_KEYS
                    ):
                        raise ValueError("Expected six history interval keys")
                    if not isinstance(record["node_id"], str) or record[
                        "status"
                    ] not in {
                        "proposed",
                        "active",
                        "running",
                        "done",
                        "failed",
                        "retired",
                    }:
                        raise ValueError("Invalid history node/status")
                    for key in ("from_commit", "to_commit"):
                        if record[key] is not None and not isinstance(record[key], str):
                            raise ValueError("Invalid history commit")
                    for key in ("from_time", "to_time"):
                        if record[key] is not None:
                            record[key] = format_timestamp(parse_timestamp(record[key]))
                    if record["from_time"] is None or (
                        record["to_time"] is not None
                        and record["to_time"] < record["from_time"]
                    ):
                        raise ValueError("Invalid history time range")
                    intervals.append(tuple(record[key] for key in INTERVAL_KEYS))
            # Close native intervals before union so duplicate archived closed rows collapse.
            native = []
            for node_id, status, commit, time, imported in self.transitions:
                if archive.exists() and imported:
                    continue
                native.append((node_id, status, commit, time, None, None))
            pending = [
                (n.id, str(n.status), None, format_timestamp(n.updated), None, None)
                for n in nodes
                if self.latest.get(n.id) != str(n.status)
            ]

            def close(rows):
                grouped = {}
                for row in rows:
                    grouped.setdefault(row[0], []).append(list(row))
                result = []
                for group in grouped.values():
                    group.sort(key=lambda row: row[3])
                    for previous, current in itertools.pairwise(group):
                        if previous[5] is None:
                            previous[4:6] = current[2:4]
                    result.extend(tuple(row) for row in group)
                return result

            native = close(native)
            # Union by full row, retaining stable same-time commit walk order.
            combined = list(dict.fromkeys([*intervals, *native]))
            # An archive open row and its locally closed equivalent are one interval.
            starts = {}
            for row in combined:
                key = row[:4]
                if key not in starts or row[5] is not None:
                    starts[key] = row
            return close([*starts.values(), *pending]), self.provenance
        except (
            OSError,
            ValueError,
            TypeError,
            KeyError,
            yaml.YAMLError,
            subprocess.SubprocessError,
        ) as exc:
            raise RuleViolation("invalid_project", f"Invalid history: {exc}") from exc
