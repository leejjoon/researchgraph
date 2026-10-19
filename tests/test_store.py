"""Source-store, init, atomic writes, and exact-scope real-git contracts."""

import hashlib
import json
import multiprocessing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from click.testing import CliRunner
from hypothesis import given, settings
from hypothesis import strategies as st

from researchgraph.cli import main
from researchgraph.model import (
    Actor,
    Artifact,
    Edge,
    Node,
    RuleViolation,
)
from researchgraph.profiles import Profile
from researchgraph.project import Project
from researchgraph.store import transaction
from tests.helpers import ALICE, NOW, git, node, refusal, repository, write


@given(
    st.text(st.characters(blacklist_categories=("Cs", "Cc", "Zl", "Zp")), max_size=200),
    st.text(st.characters(blacklist_categories=("Cs",)), max_size=100),
)
@settings(max_examples=25, deadline=None)
def test_valid_node_write_roundtrip(title, body):
    # Deliberately first: executable invariant before the store implementation.
    with TemporaryDirectory() as directory:
        path = repository(Path(directory))
        project = Project.init(path, actor=ALICE, now=NOW, commit=False)
        value = node()
        value.title, value.body = title, body
        project.apply(write(value), commit=False)
        assert Project.open(path).get_node(value.id).to_dict() == value.to_dict()


def test_init_layout_pin_and_commit(project):
    path = project.path
    assert (
        path / ".researchgraph/profile.yaml"
    ).read_bytes() == b'name: research\nversion: "1.0"\n'
    for name in ("graph/nodes", "graph/edges", "artifacts"):
        assert (path / name).is_dir()
    assert (path / ".researchgraph/index.sqlite").exists()
    assert Project.open(path).get_nodes() == []
    assert (
        path / ".gitignore"
    ).read_bytes() == b".researchgraph/index.sqlite\n.researchgraph/lock\n"
    assert git(path, "log", "--format=%s") == "rg: init"
    assert git(path, "log", "-1", "--format=%an <%ae>") == "Alice Git <alice@git.test>"
    assert (
        git(path, "log", "-1", "--format=%at %ct")
        == f"{int(NOW.timestamp())} {int(NOW.timestamp())}"
    )
    assert git(path, "status", "--porcelain") == ""


def test_init_new_git_repository(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig"))
    (tmp_path / "gitconfig").write_text(
        "[user]\n name = Alice\n email = alice@test\n[commit]\n gpgsign = false\n"
    )
    path = tmp_path / "new"
    Project.init(path, actor=ALICE, now=NOW)
    assert git(path, "log", "--format=%s") == "rg: init"


def test_init_existing_history_and_gitignore(tmp_path):
    repository(tmp_path)
    original = b"# keep exactly\r\nlocal-files"
    (tmp_path / ".gitignore").write_bytes(original)
    git(tmp_path, "add", ".gitignore")
    git(tmp_path, "-c", "commit.gpgsign=false", "commit", "-m", "before")
    previous = git(tmp_path, "rev-parse", "HEAD")
    # Existing history must not be newer than the explicit write clock.
    clock = datetime.fromtimestamp(
        int(git(tmp_path, "show", "-s", "--format=%ct")), UTC
    )
    Project.init(tmp_path, actor=ALICE, now=clock)
    assert git(tmp_path, "rev-parse", "HEAD^") == previous
    assert (
        tmp_path / ".gitignore"
    ).read_bytes() == original + b"\n.researchgraph/index.sqlite\n.researchgraph/lock\n"


@pytest.mark.parametrize(
    "kwargs,rule",
    [
        ({"read_only": True}, "read_only"),
        ({"actor": Actor("agent", "bot")}, "human_only_approval"),
        ({"actor": Actor("robot", "bot")}, "schema"),
        ({"profile": "widgets"}, "unknown_profile"),
        ({"role": "missing"}, "wrong_role"),
    ],
)
def test_init_validation_no_effects(tmp_path, kwargs, rule):
    path = tmp_path / "absent"
    options = {"actor": ALICE, "now": NOW, **kwargs}
    refusal(rule, Project.init, path, **options)
    assert not path.exists()


def test_already_initialised_and_read_only(project):
    refusal("already_initialised", Project.init, project.path, actor=ALICE, now=NOW)
    readonly = Project.open(project.path, read_only=True)
    lock = project.path / ".researchgraph/lock"
    lock.unlink()
    assert readonly.get_nodes() == []
    refusal("read_only", readonly.apply, write(node()))
    assert not lock.exists()


def test_pinned_versions_unchanged(project):
    pin = project.path / ".researchgraph/profile.yaml"
    for version in ("0.9", "1.0"):
        content = f'name: research\nversion: "{version}"\n'.encode()
        pin.write_bytes(content)
        opened = Project.open(project.path)
        assert opened.profile_version == version
        assert pin.read_bytes() == content
    pin.write_text('name: research\nversion: "1.0.1"\n')
    refusal("profile_too_new", Project.open, project.path)
    pin.write_text('name: research\nversion: "9.0"\n')
    refusal("profile_too_new", project.get_nodes)


def test_getters_and_normalization(project):
    question = Node("qu-aaaaaaaa", "Question", "Question", "", ALICE, NOW)
    task = node()
    relation = Edge("ed-aaaaaaaa", "relates-to", task.id, question.id, ALICE, NOW)
    project.apply(write(question, task, edges=[relation]), commit=False)
    assert question.fields == {}  # Caller-owned write is not mutated.
    assert project.get_node(question.id).fields == {"closed": False}
    assert {value.id for value in project.get_nodes()} == {task.id, question.id}
    assert project.get_edges() == [relation]
    assert Project.open(project.path).get_node(task.id) == task
    refusal("not_found", project.get_node, "ta-bbbbbbbb")
    refusal("schema", project.get_node, "../../escape")


@pytest.mark.parametrize(
    "mutate,rule",
    [
        (lambda n: setattr(n, "id", "../../outside"), "schema"),
        (lambda n: setattr(n, "id", "qu-aaaaaaaa"), "schema"),
        (lambda n: setattr(n, "title", "a\nb"), "schema"),
        (lambda n: setattr(n, "title", "a" * 201), "schema"),
        (lambda n: setattr(n, "fields", {"bad": True}), "schema"),
        (lambda n: setattr(n, "fields", []), "schema"),
        (lambda n: setattr(n, "status", "nonsense"), "schema"),
    ],
)
def test_invalid_write_changes_nothing(project, mutate, rule):
    value = node()
    mutate(value)
    before = git(project.path, "status", "--porcelain")
    refusal(rule, project.apply, write(value), commit=False)
    assert git(project.path, "status", "--porcelain") == before
    assert project.get_nodes() == []


def test_existing_fields_not_redefaulted(project):
    question = Node("qu-aaaaaaaa", "Question", "Q", "", ALICE, NOW)
    project.apply(write(question), commit=False)
    path = project.path / "graph/nodes/qu-aaaaaaaa.yaml"
    path.write_text(
        path.read_text()
        .replace("  closed: false\n", "")
        .replace("fields:\n", "fields: {}\n")
    )
    loaded = project.get_node(question.id)
    loaded.priority = 2
    project.apply(write(loaded), commit=False)
    assert project.get_node(question.id).fields == {}


def test_profile_validator_refuses_before_files(project):
    claim = Node("cl-aaaaaaaa", "Claim", "Claim", "", ALICE, NOW)
    refusal(
        "claim_needs_evidence", project.apply, write(claim, op="approve"), commit=False
    )
    assert project.get_nodes() == []


def test_missing_endpoint_and_edge_schema(project):
    relation = Edge(
        "ed-aaaaaaaa", "relates-to", "ta-aaaaaaaa", "ta-bbbbbbbb", ALICE, NOW
    )
    refusal("not_found", project.apply, write(node(), edges=[relation]), commit=False)
    assert project.get_nodes() == []


def test_edge_defaults_and_read_source_identity(project):
    hypothesis = Node("hy-aaaaaaaa", "Hypothesis", "H", "", ALICE, NOW)
    relation = Edge(
        "ed-aaaaaaaa",
        "tests",
        "ta-aaaaaaaa",
        hypothesis.id,
        ALICE,
        NOW,
        {"expected": "positive"},
    )
    project.apply(write(node(), hypothesis, edges=[relation]), commit=False)
    stored = project.get_edges()[0]
    assert stored.fields == {"expected": "positive", "discriminating": True}
    assert relation.fields == {"expected": "positive"}
    path = project.path / "graph/nodes/ta-aaaaaaaa.yaml"
    path.write_text(path.read_text().replace("priority: null", "priority: 42"))
    assert project.get_node("ta-aaaaaaaa").priority == 42
    assert Project.open(project.path).get_edges() == [stored]


def test_invalid_edge_fields_write_nothing(project):
    hypothesis = Node("hy-aaaaaaaa", "Hypothesis", "H", "", ALICE, NOW)
    relation = Edge("ed-aaaaaaaa", "tests", "ta-aaaaaaaa", hypothesis.id, ALICE, NOW)
    refusal(
        "schema",
        project.apply,
        write(node(), hypothesis, edges=[relation]),
        commit=False,
    )
    assert project.get_nodes() == [] and project.get_edges() == []


def test_open_missing_and_invalid_projects(tmp_path):
    refusal("project_not_found", Project.open, tmp_path / "absent")
    refusal("invalid_project", Project.open, tmp_path)
    repository(tmp_path)
    (tmp_path / ".researchgraph").mkdir()
    (tmp_path / ".researchgraph/profile.yaml").write_text("not a pin\n")
    refusal("invalid_project", Project.open, tmp_path)


def test_symlink_destinations_rejected_before_write(project, tmp_path):
    external = tmp_path / "outside.yaml"
    external.write_bytes(b"untouched\n")
    path = project.path / "graph/nodes/ta-aaaaaaaa.yaml"
    path.symlink_to(external)
    refusal("schema", project.apply, write(node()), commit=False)
    assert external.read_bytes() == b"untouched\n"


def test_artifact_path_validation_precedes_missing(project):
    refusal(
        "artifact_path",
        project.apply,
        write(node(), args={"artifacts": ["../outside"]}),
        commit=False,
    )
    assert project.get_nodes() == []
    refusal(
        "artifact_missing",
        project.apply,
        write(node(), args={"artifacts": ["artifacts/ta-aaaaaaaa/absent"]}),
        commit=False,
    )
    assert project.get_nodes() == []


def test_clock_skew_before_effects(project):
    refusal("clock_skew", project.apply, write(node(), now=NOW - timedelta(seconds=1)))
    assert project.get_nodes() == []


@pytest.mark.parametrize("failed_rename", [1, 2, 3])
def test_rename_failure_restores_precall_bytes(project, monkeypatch, failed_rename):
    first, second = node(), node("ta-bbbbbbbb")
    project.apply(write(first, second))
    # Pending manual/--no-commit state, not HEAD, is the rollback baseline.
    pending = project.get_node(first.id)
    pending.priority = 7
    artifact = project.path / f"artifacts/{first.id}/pending.txt"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"pending attachment\n")
    pending.artifacts.append(
        Artifact(
            f"artifacts/{first.id}/pending.txt",
            hashlib.sha256(artifact.read_bytes()).hexdigest(),
            len(artifact.read_bytes()),
            NOW,
            ALICE,
        )
    )
    project.apply(write(pending), commit=False)
    before = {p: p.read_bytes() for p in (project.path / "graph/nodes").glob("*.yaml")}
    status = git(project.path, "status", "--porcelain")
    pending.priority, second.priority = 8, 9
    original = transaction.os.replace
    calls = 0

    def fail_once(source, destination):
        nonlocal calls
        calls += 1
        if calls == failed_rename:
            raise OSError("injected rename failure")
        return original(source, destination)

    monkeypatch.setattr(transaction.os, "replace", fail_once)
    error = refusal(
        "write_failed", project.apply, write(pending, node("ta-cccccccc"), second)
    )
    assert error.ids
    assert {
        p: p.read_bytes() for p in (project.path / "graph/nodes").glob("*.yaml")
    } == before
    assert git(project.path, "status", "--porcelain") == status
    assert project.get_node(first.id).artifacts == pending.artifacts
    assert artifact.read_bytes() == b"pending attachment\n"
    assert not list((project.path / "graph/nodes").glob("*.tmp"))


def test_commit_failure_preserves_files(project):
    hook = project.path / ".git/hooks/pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    previous = git(project.path, "rev-parse", "HEAD")
    events = []
    project.profile.event_hooks.append(lambda *args: events.append(args))
    refusal("write_failed", project.apply, write(node()))
    assert project.get_node("ta-aaaaaaaa") == node()
    assert git(project.path, "rev-parse", "HEAD") == previous
    assert events == []


def test_exact_commit_scope_preserves_staged_and_unstaged(project):
    path = project.path
    (path / "delete-me").write_text("tracked\n")
    (path / "edit-me").write_text("base\n")
    git(path, "add", "delete-me", "edit-me")
    git(path, "commit", "-m", "unrelated base")
    (path / "delete-me").unlink()
    git(path, "add", "delete-me")
    (path / "added").write_text("staged addition\n")
    git(path, "add", "added")
    (path / "added").write_text("unstaged addition bytes\n")
    (path / "edit-me").write_text("unstaged existing bytes\n")
    staged = git(path, "diff", "--cached", "--binary")
    unstaged = git(path, "diff", "--binary")
    clock = datetime.fromtimestamp(int(git(path, "show", "-s", "--format=%ct")), UTC)
    artifact = path / "artifacts/ta-aaaaaaaa/output.txt"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("artifact\n")
    project.apply(
        write(
            node(), now=clock, args={"artifacts": ["artifacts/ta-aaaaaaaa/output.txt"]}
        )
    )
    assert git(path, "diff", "--cached", "--binary") == staged
    assert git(path, "diff", "--binary") == unstaged
    assert set(
        git(
            path, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"
        ).splitlines()
    ) == {"graph/nodes/ta-aaaaaaaa.yaml", "artifacts/ta-aaaaaaaa/output.txt"}


def test_events_real_commit_only_and_agent_identity(project):
    events = []
    project.profile.event_hooks.append(lambda p, w, c: events.append((p, w, c)))
    assert project.apply(write(node()), commit=False) is None
    assert events == []
    change = write(project.get_node("ta-aaaaaaaa"))
    change.nodes[0].priority = 4
    change.actor = Actor("agent", "robot", model="test")
    commit = project.apply(change)
    assert commit == git(project.path, "rev-parse", "HEAD")
    assert len(events) == 1 and events[0][2] == commit
    assert (
        git(project.path, "show", "-s", "--format=%an <%ae>")
        == "robot <robot@agents.researchgraph>"
    )


def _locked_write(path, started, done):
    started.set()
    Project.open(path).apply(write(node()), commit=False)
    done.set()


def test_flock_waits_across_processes(project):
    import fcntl

    context = multiprocessing.get_context("spawn")
    started, done = context.Event(), context.Event()
    with (project.path / ".researchgraph/lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        process = context.Process(
            target=_locked_write, args=(project.path, started, done)
        )
        process.start()
        try:
            assert started.wait(10)
            assert not done.wait(0.25)
            # Read-only readers neither acquire nor wait for the write lock.
            assert Project.open(project.path, read_only=True).get_nodes() == []
            fcntl.flock(lock, fcntl.LOCK_UN)
            assert done.wait(10)
        finally:
            process.join(10)
            if process.is_alive():
                process.terminate()
                process.join()
        assert process.exitcode == 0


def test_cli_init_and_errors(tmp_path):
    repository(tmp_path)
    runner = CliRunner()
    arguments = [
        "--project",
        str(tmp_path),
        "--json",
        "--now",
        "2026-10-20T00:00:00Z",
        "init",
    ]
    result = runner.invoke(main, arguments)
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["profile"] == "research"
    result = runner.invoke(main, arguments)
    assert result.exit_code == 2
    assert json.loads(result.stderr)["rule"] == "already_initialised"
    result = runner.invoke(
        main, ["--project", str(tmp_path / "missing"), "--read-only", "init"]
    )
    assert result.exit_code == 2
    assert json.loads(result.stderr)["rule"] == "read_only"
    result = runner.invoke(main, ["--project", str(tmp_path), "--now", "bad", "init"])
    assert result.exit_code == 2
    assert json.loads(result.stderr)["rule"] == "schema"


def test_cli_agent_refusal_and_no_commit_profile(tmp_path):
    repository(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "init",
            "--project",
            str(tmp_path),
            "--as-agent",
            "bot:model:1",
            "--no-commit",
        ],
    )
    assert result.exit_code == 2
    assert json.loads(result.stderr)["rule"] == "human_only_approval"
    assert not (tmp_path / ".researchgraph").exists()
    result = runner.invoke(
        main, ["init", str(tmp_path), "--profile", "tasks", "--json", "--no-commit"]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["profile"] == "tasks"
    assert (
        tmp_path / ".researchgraph/profile.yaml"
    ).read_bytes() == b'name: tasks\nversion: "1.0"\n'
    assert git(tmp_path, "rev-list", "--all", "--count") == "0"


def test_cli_invalid_project_exit_three(tmp_path):
    repository(tmp_path)
    destination = tmp_path / "not-a-directory"
    destination.write_bytes(b"keep\n")
    result = CliRunner().invoke(main, ["--project", str(destination), "init"])
    assert result.exit_code == 3
    assert json.loads(result.stderr)["rule"] == "invalid_project"
    assert destination.read_bytes() == b"keep\n"


def test_init_clock_skew_and_no_commit_events(tmp_path, monkeypatch):
    repository(tmp_path)
    (tmp_path / "existing").write_bytes(b"base\n")
    git(tmp_path, "add", "existing")
    git(tmp_path, "commit", "-m", "existing history")
    clock = datetime.fromtimestamp(
        int(git(tmp_path, "show", "-s", "--format=%ct")), UTC
    )
    refusal(
        "clock_skew",
        Project.init,
        tmp_path,
        actor=ALICE,
        now=clock - timedelta(seconds=1),
    )
    assert not (tmp_path / ".researchgraph").exists()
    events = []
    custom = Profile.load("research")
    custom.event_hooks.append(lambda p, w, c: events.append((w.op, c)))
    monkeypatch.setattr(Profile, "_registry", {**Profile._registry, "research": custom})
    Project.init(tmp_path, actor=ALICE, now=clock, commit=False)
    assert events == []
    assert git(tmp_path, "log", "--format=%s") == "existing history"


def test_init_event_has_real_commit_id(tmp_path, monkeypatch):
    repository(tmp_path)
    events = []
    custom = Profile.load("research")
    custom.event_hooks.append(lambda p, w, c: events.append((p.path, w.op, c)))
    monkeypatch.setattr(Profile, "_registry", {**Profile._registry, "research": custom})
    Project.init(tmp_path, actor=ALICE, now=NOW)
    assert events == [(tmp_path, "init", git(tmp_path, "rev-parse", "HEAD"))]


def test_empty_git_clone_opens_without_writing_directories(project, tmp_path):
    clone = tmp_path / "clone"
    git(project.path, "clone", "--quiet", str(project.path), str(clone))
    assert not (clone / "graph").exists()
    opened = Project.open(clone, read_only=True)
    assert opened.get_nodes() == []
    assert opened.get_edges() == []
    assert not (clone / "graph").exists()
    git(clone, "config", "user.name", "Alice Git")
    git(clone, "config", "user.email", "alice@git.test")
    writable = Project.open(clone)
    writable.apply(write(node()), commit=False)
    assert writable.get_node("ta-aaaaaaaa").title == "A task"


def test_root_public_project_and_actor_contracts():
    import researchgraph

    assert researchgraph.Project is Project
    assert researchgraph.Actor is Actor
    assert researchgraph.RuleViolation is RuleViolation


def test_read_rejects_removed_git_metadata_even_with_warm_index(project):
    project.get_nodes()
    (project.path / ".git").rename(project.path / ".git-removed")
    refusal("invalid_project", project.get_nodes)


def test_project_context_releases_index_and_is_terminal(project):
    import sqlite3

    with Project.open(project.path) as opened:
        connection = opened.index.connection
        assert opened.get_nodes() == []
    opened.close()
    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute("select 1")
    with pytest.raises(ValueError, match="closed"):
        opened.get_nodes()
