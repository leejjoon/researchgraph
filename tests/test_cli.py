"""Real mutation CLI doors, structured arguments and source-observable refusals."""

import json
import os
import subprocess
import sysconfig
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from researchgraph.model import format_timestamp
from researchgraph.project import Project
from tests.helpers import ALICE, NOW, git, repository

pytestmark = [pytest.mark.cli, pytest.mark.usefixtures("cli_cache_policy")]


@pytest.fixture
def root(tmp_path):
    path = repository(tmp_path / "project")
    with Project.init(path, actor=ALICE, now=NOW):
        pass
    return path


def prepare_console(arguments):
    if os.environ.get("RG_TEST_CLI_INDEX_MODE") != "deleted":
        return
    projects = []
    for index, value in enumerate(arguments):
        if value == "--project" and index + 1 < len(arguments):
            projects.append(arguments[index + 1])
        elif value.startswith("--project="):
            projects.append(value.split("=", 1)[1])
    root = (
        Path(projects[-1]).expanduser()
        if projects
        else next(
            (
                path
                for path in (Path.cwd(), *Path.cwd().parents)
                if (path / ".researchgraph/profile.yaml").is_file()
            ),
            None,
        )
    )
    if root is not None and root.is_dir():
        (root / ".researchgraph/index.sqlite").unlink(missing_ok=True)


def run_console(arguments):
    prepare_console(arguments)
    executable = (Path(sysconfig.get_path("scripts")) / "rg").resolve()
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    process = subprocess.run(
        [str(executable), *arguments],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        env=environment,
    )
    return SimpleNamespace(
        stdout=process.stdout,
        stderr=process.stderr,
        output=process.stdout + process.stderr,
        exit_code=process.returncode,
    )


def invoke(root, *arguments, agent=None, role=None, now=NOW):
    options = ["--json", "--now", format_timestamp(now)]
    if root is not None:
        options += ["--project", str(root)]
    if agent is not None:
        options += ["--as-agent", agent]
    if role is not None:
        options += ["--role", role]
    return run_console([*options, *arguments])


def succeeds(result):
    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    return json.loads(result.stdout)


def refused(result, rule):
    assert result.exit_code == 2, result.output
    assert result.stdout == ""
    value = json.loads(result.stderr)
    assert value["rule"] == rule
    assert isinstance(value["message"], str)
    assert isinstance(value["ids"], list)


def snapshot(root):
    return (
        {
            p.relative_to(root).as_posix(): p.read_bytes()
            for p in (root / "graph").rglob("*.yaml")
        },
        git(root, "rev-parse", "HEAD"),
    )


def test_invoke_executes_installed_console_not_click_runner(root, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("CLI acceptance must execute the installed console")

    monkeypatch.setattr(CliRunner, "invoke", forbidden)
    value = succeeds(invoke(root, "propose", "Task", "real subprocess"))
    assert value["title"] == "real subprocess"
    with Project.open(root) as project:
        assert project.get_node(value["id"]).to_dict() == value


def test_deleted_cli_mode_removes_cache_before_executing_console(root, monkeypatch):
    with Project.open(root) as project:
        project.ready(actor=ALICE, now=NOW)
    index = root / ".researchgraph/index.sqlite"
    assert index.is_file()
    monkeypatch.setenv("RG_TEST_CLI_INDEX_MODE", "deleted")
    observed = []
    original = subprocess.run

    def execute(*args, **kwargs):
        observed.append(index.exists())
        return original(*args, **kwargs)

    monkeypatch.setattr(subprocess, "run", execute)
    assert succeeds(invoke(root, "ready")) == {"items": []}
    assert observed == [False]
    assert index.is_file()


def test_propose_actor_and_canonical_json(root):
    value = succeeds(
        invoke(
            root,
            "propose",
            "Task",
            "fit",
            "--body",
            "run carefully",
            "--fields",
            '{"kind":"fit"}',
            agent="worker:model:v1",
            role="owner",
        )
    )
    assert value["status"] == "active"
    assert value["body"] == "run carefully"
    assert value["fields"] == {"kind": "fit"}
    assert value["author"] == {
        "kind": "agent",
        "name": "worker",
        "model": "model",
        "version": "v1",
    }
    assert "role" not in value["author"]
    with Project.open(root) as project:
        assert project.get_node(value["id"]).to_dict() == value
    assert git(root, "log", "-1", "--format=%s") == "rg: propose " + value["id"]


def test_propose_edges_materialize_profile_defaults(root):
    hypothesis = succeeds(invoke(root, "propose", "Hypothesis", "H"))
    prediction = succeeds(
        invoke(
            root,
            "propose",
            "Prediction",
            "P",
            "--edge",
            json.dumps(
                {
                    "type": "tests",
                    "to": hypothesis["id"],
                    "fields": {"expected": "weak"},
                }
            ),
            agent="worker",
        )
    )
    with Project.open(root) as project:
        shown = project.show(prediction["id"], actor=ALICE, now=NOW)
    assert shown["edges"][0]["fields"]["discriminating"] is True
    assert shown["edges"][0]["author"] == {"kind": "agent", "name": "worker"}


def test_link_approve_reject_and_structured_errors(root):
    claim = succeeds(invoke(root, "propose", "Claim", "C"))
    result = succeeds(invoke(root, "propose", "Result", "R"))
    before = snapshot(root)
    refused(invoke(root, "approve", claim["id"]), "claim_needs_evidence")
    assert snapshot(root) == before
    linked = succeeds(
        invoke(root, "link", "cites", claim["id"], result["id"], agent="worker")
    )
    assert linked["from"] == claim["id"] and linked["to"] == result["id"]
    assert git(root, "log", "-1", "--format=%s") == "rg: link " + linked["id"]
    active = succeeds(
        invoke(root, "approve", claim["id"], "--note", "checked", role="owner")
    )
    assert active["status"] == "active"
    assert active["approvals"][-1]["what"] == "approve"
    assert active["approvals"][-1]["role"] == "owner"
    assert active["approvals"][-1]["note"] == "checked"
    refused(invoke(root, "approve", claim["id"]), "not_proposed")
    hypothesis = succeeds(invoke(root, "propose", "Hypothesis", "duplicate"))
    retired = succeeds(invoke(root, "reject", hypothesis["id"], "duplicate"))
    assert retired["status"] == "retired"
    assert retired["approvals"][-1]["what"] == "reject"
    assert retired["approvals"][-1]["note"] == "duplicate"


def test_reject_retires_live_incident_edges(root):
    prediction = succeeds(invoke(root, "propose", "Prediction", "P"))
    task = succeeds(invoke(root, "propose", "Task", "T"))
    succeeds(invoke(root, "link", "blocks", prediction["id"], task["id"]))
    succeeds(invoke(root, "reject", prediction["id"], "obsolete"))
    with Project.open(root) as project:
        assert project.get_edges()[0].retired
        assert project.ready(actor=ALICE, now=NOW)["items"][0]["id"] == task["id"]


@pytest.mark.parametrize("kind", ["supersedes", "explains", "instructs"])
def test_reserved_edge_refusals_preserve_source(root, kind):
    a = succeeds(invoke(root, "propose", "Task", "A"))
    b = succeeds(invoke(root, "propose", "Task", "B"))
    before = snapshot(root)
    refused(invoke(root, "link", kind, a["id"], b["id"]), "reserved_edge_type")
    assert snapshot(root) == before


def test_cycle_and_duplicate_failures_preserve_source(root):
    a = succeeds(invoke(root, "propose", "Task", "A"))
    b = succeeds(invoke(root, "propose", "Task", "B"))
    succeeds(invoke(root, "link", "blocks", a["id"], b["id"]))
    before = snapshot(root)
    refused(invoke(root, "link", "blocks", b["id"], a["id"]), "cycle")
    refused(invoke(root, "link", "blocks", a["id"], b["id"]), "duplicate_edge")
    assert snapshot(root) == before


@pytest.mark.parametrize(
    "arguments,rule",
    [
        (("propose", "Widget", "unknown"), "unknown_type"),
        (("propose", "Task", "invalid", "--fields", '{"extra":1}'), "schema"),
        (("propose", "Task", "invalid", "--fields", "{bad"), "schema"),
        (("propose", "Task", "invalid", "--edge", "{bad"), "schema"),
        (("approve", "hy-zzzzzzzz"), "not_found"),
        (("reject", "hy-zzzzzzzz", "missing"), "not_found"),
    ],
)
def test_invalid_arguments_are_structured_and_atomic(root, arguments, rule):
    before = snapshot(root)
    refused(invoke(root, *arguments), rule)
    assert snapshot(root) == before


def test_read_only_precedes_json_and_agent_errors(root):
    before = snapshot(root)
    refused(
        invoke(
            root, "propose", "Task", "T", "--fields", "{bad", "--read-only", agent=""
        ),
        "read_only",
    )
    assert snapshot(root) == before


def test_agent_gate_and_role_refusals(root):
    hypothesis = succeeds(invoke(root, "propose", "Hypothesis", "H"))
    before = snapshot(root)
    refused(
        invoke(root, "approve", hypothesis["id"], agent="worker"), "human_only_approval"
    )
    refused(
        invoke(root, "reject", hypothesis["id"], "no", agent="worker"),
        "human_only_approval",
    )
    refused(invoke(root, "propose", "Task", "T", role="unknown"), "wrong_role")
    assert snapshot(root) == before


def test_no_commit_and_local_option_override(root):
    before = git(root, "rev-parse", "HEAD")
    value = succeeds(invoke(root, "propose", "Task", "pending", "--no-commit"))
    assert git(root, "rev-parse", "HEAD") == before
    assert (root / "graph/nodes" / (value["id"] + ".yaml")).exists()
    assert git(root, "status", "--porcelain")
    with Project.open(root) as project:
        assert project.get_node(value["id"]).status == "active"
    # Command-local common options win over group options.
    later = NOW + timedelta(seconds=1)
    committed = succeeds(
        invoke(
            root,
            "propose",
            "Task",
            "committed",
            "--now",
            format_timestamp(later),
            "--commit",
        )
    )
    assert committed["created"] == format_timestamp(later)
    assert git(root, "rev-parse", "HEAD") != before
    assert (
        git(root, "status", "--porcelain") == "?? graph/nodes/" + value["id"] + ".yaml"
    )


def test_project_discovery_and_human_output(root, monkeypatch):
    monkeypatch.chdir(root / "artifacts")
    value = succeeds(invoke(None, "propose", "Task", "inside"))
    result = run_console(
        ["--now", format_timestamp(NOW), "propose", "Task", "human table"]
    )
    assert result.exit_code == 0, result.output
    assert "ID" in result.stdout and "TYPE" in result.stdout
    assert "human table" in result.stdout
    assert (root / "graph/nodes" / (value["id"] + ".yaml")).exists()


def test_unknown_project_exit_three(tmp_path):
    result = invoke(tmp_path / "absent", "propose", "Task", "T")
    assert result.exit_code == 3
    assert json.loads(result.stderr)["rule"] == "project_not_found"


def test_explicit_empty_clock_is_not_replaced_by_wall_time(root):
    before = snapshot(root)
    refused(invoke(root, "propose", "Task", "T", "--now", ""), "schema")
    assert snapshot(root) == before


def test_init_read_only_precedes_invalid_clock(root):
    refused(invoke(root, "init", "--read-only", "--now", "invalid"), "read_only")


def test_revision_cli_supersession_transfers_edges(root):
    old = succeeds(
        invoke(
            root,
            "propose",
            "Task",
            "old",
            "--body",
            "original",
            "--fields",
            '{"kind":"old"}',
        )
    )
    target = succeeds(invoke(root, "propose", "Task", "downstream"))
    edge = succeeds(invoke(root, "link", "blocks", old["id"], target["id"]))
    replacement = succeeds(
        invoke(
            root,
            "supersede",
            old["id"],
            "--title",
            "new",
            "--body",
            "",
            "--fields",
            '{"kind":"new"}',
            agent="worker:model:v2",
        )
    )
    assert replacement["title"] == "new" and replacement["body"] == ""
    assert replacement["fields"] == {"kind": "new"}
    assert replacement["author"]["version"] == "v2"
    with Project.open(root) as project:
        previous = project.get_node(old["id"])
        edges = project.get_edges()
        assert previous.title == "old" and previous.body == "original"
        assert previous.status == "retired"
        assert previous.approvals[-1].note == replacement["id"]
        assert next(e for e in edges if e.id == edge["id"]).retired
        assert any(
            e.type == "blocks"
            and e.from_ == replacement["id"]
            and e.to == target["id"]
            and not e.retired
            for e in edges
        )
    assert git(root, "log", "-1", "--format=%s") == "rg: supersede " + old["id"]


def test_revision_cli_instruction_and_supersede_instruction(root):
    task = succeeds(invoke(root, "propose", "Task", "T"))
    instruction = succeeds(
        invoke(root, "instruct", task["id"], "first line\nsecond line")
    )
    assert instruction["title"] == "first line"
    assert instruction["body"] == "first line\nsecond line"
    replacement = succeeds(
        invoke(root, "supersede", task["id"], "--instruction", "retry", agent="worker")
    )
    with Project.open(root) as project:
        shown = project.show(replacement["id"], actor=ALICE, now=NOW)
        assert {n["body"] for n in shown["instructions"]} == {
            "first line\nsecond line",
            "retry",
        }
        assert all(n["type"] == "Instruction" for n in shown["instructions"])


def test_revision_cli_no_inheritance_and_no_commit(root):
    old = succeeds(invoke(root, "propose", "Task", "old"))
    target = succeeds(invoke(root, "propose", "Task", "target"))
    edge = succeeds(invoke(root, "link", "blocks", old["id"], target["id"]))
    head = git(root, "rev-parse", "HEAD")
    replacement = succeeds(
        invoke(root, "supersede", old["id"], "--no-inherit-edges", "--no-commit")
    )
    assert git(root, "rev-parse", "HEAD") == head
    with Project.open(root) as project:
        edges = project.get_edges()
        assert next(e for e in edges if e.id == edge["id"]).retired
        assert not any(
            e.type == "blocks" and e.from_ == replacement["id"] for e in edges
        )
        assert any(
            e.type == "supersedes" and e.from_ == replacement["id"] for e in edges
        )


def test_revision_cli_retirement_retires_incident_edges(root):
    hypothesis = succeeds(invoke(root, "propose", "Hypothesis", "H"))
    succeeds(invoke(root, "approve", hypothesis["id"]))
    task = succeeds(invoke(root, "propose", "Task", "T"))
    succeeds(invoke(root, "link", "blocks", hypothesis["id"], task["id"]))
    retired = succeeds(
        invoke(root, "retire", hypothesis["id"], "abandoned", role="owner")
    )
    assert retired["status"] == "retired"
    assert retired["approvals"][-1]["what"] == "retire"
    assert retired["approvals"][-1]["note"] == "abandoned"
    with Project.open(root) as project:
        assert project.get_edges()[0].retired
        assert project.ready(actor=ALICE, now=NOW)["items"][0]["id"] == task["id"]


@pytest.mark.parametrize(
    "operation,argument", [("instruct", "text"), ("retire", "reason")]
)
def test_revision_cli_human_permissions_are_structured(root, operation, argument):
    task = succeeds(invoke(root, "propose", "Task", "T"))
    before = snapshot(root)
    refused(
        invoke(root, operation, task["id"], argument, agent="worker"),
        "human_only_approval",
    )
    assert snapshot(root) == before


def test_revision_cli_agent_hypothesis_supersession_refused(root):
    hypothesis = succeeds(invoke(root, "propose", "Hypothesis", "H"))
    before = snapshot(root)
    refused(
        invoke(root, "supersede", hypothesis["id"], agent="worker"),
        "agent_cannot_supersede_type",
    )
    assert snapshot(root) == before


def test_execution_cli_claim_renew_complete_artifact(root):
    from hashlib import sha256

    never = root / "must-not-run"
    task = succeeds(
        invoke(
            root,
            "propose",
            "Task",
            "T",
            "--fields",
            json.dumps({"command": f"touch {never}"}),
        )
    )
    claimed = succeeds(
        invoke(
            root, "claim", task["id"], "--lease-seconds", "3600", agent="worker:old:v1"
        )
    )
    assert claimed["status"] == "running"
    assert claimed["lease"]["actor"]["version"] == "v1"
    renewed = succeeds(
        invoke(
            root,
            "renew",
            task["id"],
            "--lease-seconds",
            "30",
            agent="worker:new:v2",
            now=NOW + timedelta(seconds=1),
        )
    )
    assert renewed["lease"] == claimed["lease"]
    assert renewed["approvals"][-1]["actor"]["version"] == "v2"
    path = root / "artifacts" / task["id"] / "연구-\noutput.bin"
    path.parent.mkdir(parents=True)
    payload = b"a" * 9000 + b"late bytes"
    path.write_bytes(payload)
    relative = path.relative_to(root).as_posix()
    done = succeeds(
        invoke(
            root,
            "complete",
            task["id"],
            "finished",
            "--artifact",
            relative,
            "--usage",
            '{"tokens":1200,"compute_seconds":5}',
            agent="worker:new:v3",
            now=NOW + timedelta(seconds=2),
        )
    )
    assert done["status"] == "done" and done["lease"] is None
    assert done["usage"] == {"tokens": 1200, "compute_seconds": 5}
    assert done["artifacts"][0]["sha256"] == sha256(payload).hexdigest()
    assert done["artifacts"][0]["bytes"] == len(payload)
    assert done["approvals"][-1]["note"] == "finished"
    committed = git(root, "show", "--format=", "--name-only", "-z", "HEAD").split("\0")
    assert relative in committed
    assert not never.exists()


def test_execution_cli_expiry_reclaim_and_old_holder_rights(root):
    task = succeeds(invoke(root, "propose", "Task", "T"))
    succeeds(invoke(root, "claim", task["id"], "--lease-seconds", "1", agent="old"))
    refused(invoke(root, "claim", task["id"], agent="new"), "lease_held")
    now = NOW + timedelta(seconds=1)
    claimed = succeeds(invoke(root, "claim", task["id"], agent="new", now=now))
    assert claimed["lease"]["actor"]["name"] == "new"
    refused(invoke(root, "renew", task["id"], agent="old", now=now), "not_lease_holder")
    refused(
        invoke(root, "complete", task["id"], "done", agent="old", now=now),
        "not_lease_holder",
    )
    refused(
        invoke(root, "release", task["id"], agent="old", now=now), "not_lease_holder"
    )
    assert (
        succeeds(invoke(root, "complete", task["id"], "done", agent="new", now=now))[
            "status"
        ]
        == "done"
    )


def test_execution_cli_release_adds_usage_and_human_overrides(root):
    task = succeeds(invoke(root, "propose", "Task", "T"))
    succeeds(invoke(root, "claim", task["id"], agent="worker"))
    first = succeeds(
        invoke(
            root,
            "release",
            task["id"],
            "--note",
            "partial",
            "--usage",
            '{"tokens":100}',
            agent="worker",
        )
    )
    assert first["status"] == "active" and first["lease"] is None
    succeeds(invoke(root, "claim", task["id"], agent="worker"))
    second = succeeds(
        invoke(
            root, "release", task["id"], "--usage", '{"tokens":50,"compute_seconds":2}'
        )
    )
    assert second["usage"] == {"tokens": 150, "compute_seconds": 2}


def test_execution_cli_failure_node_and_actual_revision(root):
    task = succeeds(invoke(root, "propose", "Task", "T"))
    blocked = succeeds(invoke(root, "propose", "Task", "downstream"))
    succeeds(invoke(root, "link", "blocks", task["id"], blocked["id"]))
    succeeds(invoke(root, "claim", task["id"], agent="worker"))
    failed = succeeds(
        invoke(
            root,
            "fail",
            task["id"],
            "unexpected outcome",
            "--step",
            "compute",
            "--check",
            "threshold",
            "--measured",
            "0",
            "--error",
            "detail",
            "--usage",
            '{"tokens":5}',
            agent="worker",
            now=NOW + timedelta(seconds=1),
        )
    )
    assert failed["id"] == task["id"] and failed["status"] == "failed"
    assert failed["lease"] is None and failed["usage"] == {"tokens": 5}
    with Project.open(root) as project:
        edge = next(e for e in project.get_edges() if e.type == "explains")
        failure = project.get_node(edge.from_)
        assert failure.body == "unexpected outcome"
        assert failure.fields == {
            "step": "compute",
            "check": "threshold",
            "measured": "0",
            "error": "detail",
        }
        assert project.ready(actor=ALICE, now=NOW + timedelta(seconds=1))["items"] == []
    replacement = succeeds(
        invoke(
            root,
            "supersede",
            task["id"],
            "--instruction",
            "retry",
            agent="worker",
            now=NOW + timedelta(seconds=2),
        )
    )
    with Project.open(root) as project:
        assert (
            project.ready(actor=ALICE, now=NOW + timedelta(seconds=2))["items"][0]["id"]
            == replacement["id"]
        )


def test_execution_cli_artifact_error_precedence_and_atomicity(root):
    task = succeeds(invoke(root, "propose", "Task", "T"))
    succeeds(invoke(root, "claim", task["id"], agent="worker"))
    before = snapshot(root)
    missing = f"artifacts/{task['id']}/missing"
    refused(
        invoke(
            root,
            "complete",
            task["id"],
            "done",
            "--artifact",
            missing,
            "--artifact",
            "../escape",
            agent="worker",
        ),
        "artifact_path",
    )
    refused(
        invoke(
            root,
            "complete",
            task["id"],
            "done",
            "--artifact",
            missing,
            "--usage",
            '{"tokens":-1}',
            agent="worker",
        ),
        "artifact_missing",
    )
    refused(
        invoke(
            root,
            "complete",
            task["id"],
            "done",
            "--artifact",
            "artifacts/ta-bbbbbbbb/x",
            agent="worker",
        ),
        "artifact_path",
    )
    refused(
        invoke(
            root,
            "complete",
            task["id"],
            "done",
            "--usage",
            '{"tokens":-1}',
            agent="worker",
        ),
        "negative",
    )
    assert snapshot(root) == before


@pytest.mark.parametrize(
    "operation,tail",
    [
        ("claim", ("--lease-seconds", "0")),
        ("renew", ("--lease-seconds", "0")),
        ("release", ("--usage", '{"tokens":-1}')),
    ],
)
def test_execution_cli_state_errors_precede_bad_usage(root, operation, tail):
    task = succeeds(invoke(root, "propose", "Task", "T"))
    before = snapshot(root)
    refused(
        invoke(root, operation, task["id"], *tail, agent="worker"),
        "negative" if operation == "claim" else "not_running",
    )
    assert snapshot(root) == before


def test_execution_cli_pending_state_replaces_intermediate_intervals(root):
    task = succeeds(invoke(root, "propose", "Task", "T"))
    head = git(root, "rev-parse", "HEAD")
    succeeds(invoke(root, "claim", task["id"], "--no-commit", agent="worker"))
    succeeds(
        invoke(
            root,
            "renew",
            task["id"],
            "--no-commit",
            agent="worker",
            now=NOW + timedelta(seconds=1),
        )
    )
    succeeds(
        invoke(
            root,
            "complete",
            task["id"],
            "done",
            "--no-commit",
            agent="worker",
            now=NOW + timedelta(seconds=2),
        )
    )
    assert git(root, "rev-parse", "HEAD") == head
    with Project.open(root) as project:
        events = project.history(
            task["id"], actor=ALICE, now=NOW + timedelta(seconds=2)
        )
        assert [e["status"] for e in events if e["kind"] == "interval"] == [
            "active",
            "done",
        ]
        assert all(e["commit"] is None for e in events if e["kind"] == "approval")
        (root / ".researchgraph/index.sqlite").unlink()
        assert project.get_node(task["id"]).status == "done"
