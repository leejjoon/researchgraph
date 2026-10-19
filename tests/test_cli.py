"""Real mutation CLI doors, structured arguments and source-observable refusals."""

import json
from datetime import timedelta

import pytest
from click.testing import CliRunner

from researchgraph.cli import main
from researchgraph.model import format_timestamp
from researchgraph.project import Project
from tests.helpers import ALICE, NOW, git, repository


@pytest.fixture
def root(tmp_path):
    path = repository(tmp_path / "project")
    with Project.init(path, actor=ALICE, now=NOW):
        pass
    return path


def invoke(root, *arguments, agent=None, role=None, now=NOW):
    options = ["--json", "--now", format_timestamp(now)]
    if root is not None:
        options += ["--project", str(root)]
    if agent is not None:
        options += ["--as-agent", agent]
    if role is not None:
        options += ["--role", role]
    return CliRunner().invoke(main, [*options, *arguments])


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
    result = CliRunner().invoke(
        main, ["--now", format_timestamp(NOW), "propose", "Task", "human table"]
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
