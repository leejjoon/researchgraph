"""T16: the unsupported consumer runs commands, never the graph core."""

import hashlib
import os
import shlex
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path

from researchgraph import Project
from tests.helpers import ALICE, git, repository

RUNNER = Path(__file__).resolve().parents[1] / "examples/minimal_runner.py"


def project_at(path, profile="tasks"):
    root = repository(path)
    before = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=1)
    with Project.init(root, profile=profile, actor=ALICE, now=before):
        pass
    return root, {"actor": ALICE, "now": before}


def command(root, marker, number, exit_code=0):
    program = (
        "import os,sys,yaml; from pathlib import Path; "
        f"assert Path.cwd() == Path({str(root)!r}); "
        "node=os.environ['RESEARCHGRAPH_NODE_ID']; "
        "record=yaml.safe_load((Path('graph/nodes')/(node+'.yaml')).read_text()); "
        "assert record['status']=='running'; "
        "assert record['lease']['actor']['name']=='minimal-runner'; "
        "output=Path(os.environ['RESEARCHGRAPH_ARTIFACT_DIR']); "
        "assert output==Path.cwd()/'artifacts'/node; "
        f"log=Path({marker!r}); "
        f"log.write_text((log.read_text() if log.exists() else '')+{str(number)!r}); "
        f"(output/'payload.bin').write_bytes(bytes([0,255,{number}])); "
        "(output/'nested').mkdir(exist_ok=True); "
        f"(output/'nested/관측.dat').write_bytes('측정'.encode()+bytes([{number}])); "
        f"sys.exit({exit_code})"
    )
    return shlex.join([sys.executable, "-c", program])


def run(root, mode, *, explicit=True):
    if mode == "deleted":
        (root / ".researchgraph/index.sqlite").unlink(missing_ok=True)
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    return subprocess.run(
        [sys.executable, str(RUNNER), *([str(root)] if explicit else [])],
        cwd=root.parent if explicit else root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


def test_three_task_chain_runs_claimed_commands_and_records_every_artifact(
    tmp_path, index_mode
):
    root, context = project_at(tmp_path / "task project λ")
    with Project.open(root) as project:
        ids = [
            project.propose(
                "Task",
                str(i),
                fields={"command": command(root, "order.txt", i)},
                **context,
            )["id"]
            for i in range(1, 4)
        ]
        for source, target in pairwise(ids):
            project.link("blocks", source, target, **context)
        idle = project.propose("Task", "commandless", **context)["id"]
        project.tag(idle, priority=100, **context)
    result = run(root, index_mode)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == result.stderr == ""
    assert (root / "order.txt").read_text() == "123"
    with Project.open(root) as project:
        completed = [project.get_node(id).to_dict() for id in ids]
        assert project.get_node(idle).status.value == "active"
    tracked = set(git(root, "ls-files", "-z").split("\0"))
    for number, node in enumerate(completed, 1):
        assert node["status"] == "done" and node["lease"] is None
        assert [a["what"] for a in node["approvals"]] == ["claim", "complete"]
        base = "artifacts/" + node["id"] + "/"
        expected = {
            base + "payload.bin": bytes([0, 255, number]),
            base + "nested/관측.dat": "측정".encode() + bytes([number]),
        }
        assert {a["path"] for a in node["artifacts"]} == expected.keys()
        for record in node["artifacts"]:
            data = expected[record["path"]]
            assert (root / record["path"]).read_bytes() == data
            assert record["bytes"] == len(data)
            assert record["sha256"] == hashlib.sha256(data).hexdigest()
            assert record["added_by"]["name"] == "minimal-runner"
            assert record["path"] in tracked
    assert (
        sum(
            subject.startswith("rg: complete ")
            for subject in git(root, "log", "--format=%s").splitlines()
        )
        == 3
    )
    assert "order.txt" not in tracked
    head = git(root, "rev-parse", "HEAD")
    assert run(root, index_mode, explicit=False).returncode == 0
    assert git(root, "rev-parse", "HEAD") == head
    assert (root / "order.txt").read_text() == "123"
    assert len(RUNNER.read_text().splitlines()) <= 80


def test_failing_task_runs_once_records_exit_code_and_does_not_loop(
    tmp_path, index_mode
):
    root, context = project_at(tmp_path / "project")
    with Project.open(root) as project:
        failure = project.propose(
            "Task",
            "failing",
            fields={"command": command(root, "attempts.txt", 1, 7)},
            **context,
        )["id"]
        blocked = project.propose(
            "Task",
            "blocked",
            fields={"command": command(root, "blocked.txt", 2)},
            **context,
        )["id"]
        independent = project.propose(
            "Task",
            "independent",
            fields={"command": command(root, "independent.txt", 3)},
            **context,
        )["id"]
        project.link("blocks", failure, blocked, **context)
        project.tag(failure, priority=10, **context)
    result = run(root, index_mode)
    assert result.returncode == 1, result.stdout + result.stderr
    assert result.stdout == result.stderr == ""
    assert (root / "attempts.txt").read_text() == "1"
    assert not (root / "blocked.txt").exists()
    assert (root / "independent.txt").read_text() == "3"
    with Project.open(root) as project:
        assert project.get_node(failure).status.value == "failed"
        assert project.get_node(blocked).status.value == "active"
        assert project.get_node(independent).status.value == "done"
        failures = [node for node in project.get_nodes() if node.type == "Failure"]
        assert len(failures) == 1
        assert failures[0].fields["error"] == "7"
        explains = [edge for edge in project.get_edges() if edge.type == "explains"]
        assert [(edge.from_, edge.to) for edge in explains] == [
            (failures[0].id, failure)
        ]
    head = git(root, "rev-parse", "HEAD")
    again = run(root, index_mode)
    assert again.returncode == 0, again.stdout + again.stderr
    assert (root / "attempts.txt").read_text() == "1"
    assert git(root, "rev-parse", "HEAD") == head


def test_reference_runner_refuses_other_profiles_without_executing(
    tmp_path, index_mode
):
    root, context = project_at(tmp_path / "project", "research")
    with Project.open(root) as project:
        id = project.propose(
            "Task",
            "not supported",
            fields={"command": command(root, "should-not-run", 1)},
            **context,
        )["id"]
        before = project.get_node(id).to_dict()
    head = git(root, "rev-parse", "HEAD")
    result = run(root, index_mode)
    assert result.returncode == 2
    assert "tasks" in result.stderr
    assert not (root / "should-not-run").exists()
    with Project.open(root) as project:
        assert project.get_node(id).to_dict() == before
    assert git(root, "rev-parse", "HEAD") == head


def test_importing_example_does_not_run_commands(tmp_path):
    root, context = project_at(tmp_path / "project")
    with Project.open(root) as project:
        id = project.propose(
            "Task",
            "untouched",
            fields={"command": command(root, "should-not-run", 1)},
            **context,
        )["id"]
        before = project.get_node(id).to_dict()
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import runpy,sys; runpy.run_path(sys.argv[1], run_name='reference_runner_import')",
            str(RUNNER),
        ],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (root / "should-not-run").exists()
    with Project.open(root) as project:
        assert project.get_node(id).to_dict() == before
