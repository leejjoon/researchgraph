"""Strict T12 workload; preparation is deliberately outside the public-read timer.

Reproduce: python -m pytest tests/test_performance.py -s
The helper also supports parent-owned phase instrumentation without a second fixture.
"""

import base64
import os
import platform
import subprocess
import sys
from datetime import timedelta
from time import perf_counter

from researchgraph.model import Approval, Lease, Node, Status, format_timestamp
from researchgraph.project import Project
from researchgraph.store.io import dump_node
from tests.helpers import ALICE, NOW, git, repository


def prepare_temporal_scrub(path, *, task_count=5000):
    """Return (open Project, 200 full commit IDs, ordered IDs, disclosure).

    The default unchanged stress fixture has 5,000 canonical Tasks. Diagnostic
    mixes retain 5,000 nodes, substituting non-claimable Notes only when requested.
    No edges/archive; 200 distinct claim/release commits on one Task. Its current
    lease is null, so historical running snapshots omit that Task. Diagnostic
    variants do not replace the default stress gate or establish its acceptance.
    """
    if type(task_count) is not int or not 1 <= task_count <= 5000:
        raise ValueError("task_count must be between 1 and 5000")
    started = perf_counter()
    project = Project.init(repository(path), "tasks", actor=ALICE, now=NOW)
    nodes = []
    for i in range(5000):
        suffix = base64.b32encode(i.to_bytes(5, "big")).decode().lower()
        type_ = "Task" if i < task_count else "Note"
        prefix = project.profile.node_type(type_).prefix
        node = Node(
            prefix + "-" + suffix,
            type_,
            f"{type_} {prefix}-{suffix}",
            "",
            ALICE,
            NOW,
            status=Status.ACTIVE,
        )
        (path / f"graph/nodes/{node.id}.yaml").write_text(dump_node(node.to_dict()))
        nodes.append(node)

    def commit(message, clock):
        git(path, "add", "graph/nodes")
        stamp = format_timestamp(clock)
        subprocess.run(
            [
                "git",
                "-C",
                str(path),
                "-c",
                "user.name=Alice Git",
                "-c",
                "user.email=alice@git.test",
                "commit",
                "-m",
                message,
            ],
            env={**os.environ, "GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp},
            check=True,
            capture_output=True,
        )
        return git(path, "rev-parse", "HEAD")

    commit("seed scale graph", NOW)
    commits = []
    nodes.sort(key=lambda node: node.id)
    changing = next(node for node in nodes if node.type == "Task")
    for i in range(200):
        clock = NOW + timedelta(seconds=i + 1)
        claiming = i % 2 == 0
        changing.status = Status.RUNNING if claiming else Status.ACTIVE
        changing.updated = clock
        changing.lease = (
            Lease(ALICE, clock, clock + timedelta(hours=1)) if claiming else None
        )
        changing.approvals.append(
            Approval(ALICE, clock, "claim" if claiming else "release")
        )
        (path / f"graph/nodes/{changing.id}.yaml").write_text(
            dump_node(changing.to_dict())
        )
        commits.append(commit("rg: claim" if claiming else "rg: release", clock))
    assert len(set(commits)) == 200
    project.close()
    opened = perf_counter()
    project = Project.open(path)
    disclosure = {
        "nodes": 5000,
        "active_task_seed_count": task_count,
        "nonclaimable_note_count": 5000 - task_count,
        "edges": 0,
        "history_bytes": 0,
        "source_bytes": sum(
            p.stat().st_size for p in (path / "graph/nodes").glob("*.yaml")
        ),
        "head": commits[-1],
        "commits": commits,
        "python": sys.version,
        "platform": platform.platform(),
        "prepare_seconds": opened - started,
        "open_seconds": perf_counter() - opened,
        "history_walk_seconds": project.index.last_history_seconds,
    }
    return project, commits, [n.id for n in nodes if n.type == "Task"], disclosure


def measure_temporal_scrub(project, commits, ordered_ids):
    """Time all 200 PUBLIC calls including every mandatory content/HEAD scan."""
    scans = 0.0
    started = perf_counter()
    for i, commit in enumerate(commits):
        result = project.ready(actor=ALICE, now=NOW, as_of=commit)
        scans += project.index.last_scan_seconds
        expected = ordered_ids[1:] if i % 2 == 0 else ordered_ids
        assert [row["id"] for row in result["items"]] == expected
        assert all(
            row["status"] == "active"
            and row["type"] == "Task"
            and row["title"] == f"Task {row['id']}"
            for row in result["items"]
        )
    return {"elapsed_seconds": perf_counter() - started, "scan_seconds": scans}


def test_public_temporal_scrub_budget(tmp_path):
    project, commits, ordered, disclosure = prepare_temporal_scrub(tmp_path / "scale")
    try:
        measurement = measure_temporal_scrub(project, commits, ordered)
        print({**measurement, **disclosure})
        assert measurement["elapsed_seconds"] < 2.0
    finally:
        project.close()
