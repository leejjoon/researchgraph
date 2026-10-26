"""Strict T12 workload; preparation is deliberately outside the public-read timer.

Reproduce: python -m pytest tests/test_performance.py -s
The helper also supports parent-owned phase instrumentation without a second fixture.
"""

import base64
import json
import os
import platform
import subprocess
import sys
import sysconfig
from datetime import timedelta
from pathlib import Path
from time import perf_counter

import pytest

from researchgraph.model import Approval, Lease, Node, Status, format_timestamp
from researchgraph.project import Project
from researchgraph.store.io import dump_node
from tests.helpers import ALICE, NOW, git, repository

pytestmark = pytest.mark.performance


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


def test_public_temporal_scrub_budget(tmp_path, record_property):
    project, commits, ordered, disclosure = prepare_temporal_scrub(tmp_path / "scale")
    try:
        measurement = measure_temporal_scrub(project, commits, ordered)
        for key, value in {**measurement, **disclosure}.items():
            record_property(key, json.dumps(value, ensure_ascii=False))
        print({**measurement, **disclosure})
        assert measurement["elapsed_seconds"] < 2.0
    finally:
        project.close()


def test_fresh_twenty_thousand_node_rebuild_budget(tmp_path, record_property):
    preparation = perf_counter()
    root = repository(tmp_path / "rebuild-20k")
    with Project.init(root, "tasks", actor=ALICE, now=NOW):
        pass
    template = dump_node(
        Node(
            "ta-aaaaaaaa",
            "Task",
            "Task 00000000",
            "",
            ALICE,
            NOW,
            status=Status.ACTIVE,
        ).to_dict()
    )
    source_bytes = 0
    for i in range(20000):
        suffix = base64.b32encode(i.to_bytes(5, "big")).decode().lower()
        identifier = "ta-" + suffix
        content = (
            template.replace("ta-aaaaaaaa", identifier)
            .replace("Task 00000000", f"Task {i:08}")
            .encode("utf-8")
        )
        (root / "graph/nodes" / (identifier + ".yaml")).write_bytes(content)
        source_bytes += len(content)
    (root / ".researchgraph/index.sqlite").unlink()
    prepared_seconds = perf_counter() - preparation
    started = perf_counter()
    project = Project.open(root)
    wall_seconds = perf_counter() - started
    try:
        walk_seconds = project.index.last_history_seconds
        rebuild_seconds = wall_seconds - walk_seconds
        disclosure = {
            "phase": "20k_fresh_rebuild",
            "nodes": 20000,
            "edges": 0,
            "history_bytes": 0,
            "source_bytes": source_bytes,
            "prepare_seconds": prepared_seconds,
            "wall_seconds": wall_seconds,
            "git_walk_seconds": walk_seconds,
            "excluding_only_git_walk_seconds": rebuild_seconds,
            "internal_rebuild_seconds": project.index.last_rebuild_seconds,
            "content_scan_seconds": project.index.last_scan_seconds,
            "budget_seconds": 5.0,
            "python": sys.version,
            "platform": platform.platform(),
        }
        for key, value in disclosure.items():
            record_property(key, json.dumps(value, ensure_ascii=False))
        print(disclosure)
        assert len(project.get_nodes()) == 20000
        assert rebuild_seconds < 5.0
        # A timing report, not a new budget: include installed process startup,
        # source refresh and JSON production, then measure parsing separately.
        started = perf_counter()
        result = subprocess.run(
            [
                str(Path(sysconfig.get_path("scripts")) / "rgraph"),
                "--project",
                str(root),
                "--json",
                "--now",
                format_timestamp(NOW),
                "graph",
            ],
            env={
                key: value for key, value in os.environ.items() if key != "PYTHONPATH"
            },
            capture_output=True,
            text=True,
            check=True,
        )
        produced = perf_counter() - started
        started = perf_counter()
        graph = json.loads(result.stdout)
        parsed = perf_counter() - started
        assert len(graph["nodes"]) == 20000 and graph["edges"] == []
        assert graph["nodes"] == sorted(
            graph["nodes"], key=lambda node: (node["created"], node["id"])
        )
        graph_report = {
            "phase": "20k_installed_graph_json",
            "nodes": 20000,
            "edges": 0,
            "production_seconds": produced,
            "parse_seconds": parsed,
            "json_bytes": len(result.stdout.encode("utf-8")),
            "index_state": "kept after disclosed fresh rebuild",
        }
        for key, value in graph_report.items():
            record_property("graph_" + key, json.dumps(value, ensure_ascii=False))
        print(graph_report)
    finally:
        project.close()
