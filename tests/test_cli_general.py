"""GENERAL1–8 acceptance through the installed console and real git checkouts."""

import json
import os
import re
import subprocess
import sys
import sysconfig
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from researchgraph.model import format_timestamp
from researchgraph.project import Project
from tests.helpers import ALICE, NOW, git, repository
from tests.test_cli import invoke, prepare_console, refused, snapshot, succeeds

pytestmark = [pytest.mark.cli, pytest.mark.usefixtures("cli_cache_policy")]

MISSING = "ta-zzzzzzzz"
WRITE_NAMES = (
    "propose",
    "link",
    "approve",
    "reject",
    "supersede",
    "instruct",
    "retire",
    "claim",
    "renew",
    "release",
    "complete",
    "fail",
    "expand",
    "budget",
    "tag",
    "attach",
)


def _root(path, profile="research"):
    root = repository(path)
    succeeds(invoke(root, "init", "--profile", profile))
    return root


def _state(root):
    # Cache files are derived, not graph source. Include artifacts, profile pins,
    # guidance and the git index/status as well as the shared graph/HEAD snapshot.
    source = {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
        and ".git" not in path.relative_to(root).parts
        and not (
            path.parent == root / ".researchgraph"
            and path.name.startswith("index.sqlite")
        )
    }
    return (
        source,
        snapshot(root),
        git(root, "status", "--porcelain"),
        git(root, "diff", "--cached", "--binary"),
    )


def _task(root, title="Task"):
    return succeeds(invoke(root, "propose", "Task", title))["id"]


def _artifact(root, node):
    path = root / "artifacts" / node / "result.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"retained experimental result\n")
    return path.relative_to(root).as_posix()


def _missing_arguments(operation, existing):
    if operation == "propose":
        return (
            "propose",
            "Task",
            "missing endpoint",
            "--edge",
            json.dumps({"type": "blocks", "to": MISSING}),
        )
    if operation == "link":
        return "link", "blocks", existing, MISSING
    if operation in {"reject", "retire", "instruct", "complete", "fail"}:
        return operation, MISSING, "reason or summary"
    if operation == "expand":
        return "expand", existing, MISSING
    if operation == "budget":
        return "budget", MISSING, "--tokens", "10"
    if operation == "tag":
        return "tag", MISSING, "--add", "checked"
    if operation == "attach":
        return "attach", MISSING, "--artifact", f"artifacts/{MISSING}/result.txt"
    return operation, MISSING


@pytest.mark.parametrize("operation", WRITE_NAMES)
def test_general1_missing_ids_are_atomic(tmp_path, operation):
    root = _root(tmp_path / "project")
    existing = _task(root)
    before = _state(root)
    refused(invoke(root, *_missing_arguments(operation, existing)), "not_found")
    assert _state(root) == before


@pytest.mark.parametrize("missing_from", [True, False])
def test_general1_link_checks_both_endpoints(tmp_path, missing_from):
    root = _root(tmp_path / "project")
    existing = _task(root)
    endpoints = (MISSING, existing) if missing_from else (existing, MISSING)
    before = _state(root)
    refused(invoke(root, "link", "blocks", *endpoints), "not_found")
    assert _state(root) == before


@pytest.mark.parametrize("operation", (*WRITE_NAMES, "init", "import"))
def test_general2_every_write_is_read_only_before_validation(tmp_path, operation):
    root = _root(tmp_path / "project")
    existing = _task(root)
    archive = tmp_path / "does-not-exist.tar.gz"
    arguments = (
        ("init",)
        if operation == "init"
        else ("import", str(archive))
        if operation == "import"
        else _missing_arguments(operation, existing)
    )
    before = _state(root)
    refused(
        invoke(root, *arguments, "--read-only", "--now", "invalid", agent=""),
        "read_only",
    )
    assert _state(root) == before
    assert not archive.exists()


@pytest.mark.parametrize("operation", ["init", "import"])
def test_general2_initializers_refuse_before_project_or_archive_io(tmp_path, operation):
    destination = tmp_path / "absent-parent" / "project"
    archive = tmp_path / "absent-archive.tar.gz"
    arguments = ("init",) if operation == "init" else ("import", str(archive))
    refused(invoke(destination, *arguments, "--read-only"), "read_only")
    assert not destination.parent.exists()
    assert not archive.exists()


def test_general3_newer_pinned_profile_is_domain_exit_two(tmp_path):
    root = _root(tmp_path / "project")
    pin = root / ".researchgraph/profile.yaml"
    pin.write_text("name: research\nversion: '9.0'\n", encoding="utf-8")
    before = _state(root)
    refused(invoke(root, "ready"), "profile_too_new")
    assert _state(root) == before


@pytest.mark.parametrize(
    "operation", ["reject", "instruct", "expand", "budget", "retire", "import"]
)
def test_general4_human_only_writes_deny_agents(tmp_path, operation):
    root = _root(tmp_path / "project")
    task = _task(root)
    hypothesis = succeeds(invoke(root, "propose", "Hypothesis", "H"))["id"]
    archive = tmp_path / "graph.tar.gz"
    succeeds(invoke(root, "export", str(archive)))
    destination = repository(tmp_path / "import-target")
    arguments = {
        "reject": ("reject", hypothesis, "not supported"),
        "instruct": ("instruct", task, "investigate"),
        "expand": ("expand", task, "--hint", "investigate"),
        "budget": ("budget", task, "--tokens", "10"),
        "retire": ("retire", task, "obsolete"),
        "import": ("import", str(archive), str(destination)),
    }[operation]
    before = _state(root)
    destination_before = {
        p.relative_to(destination).as_posix(): p.read_bytes()
        for p in destination.rglob("*")
        if p.is_file()
    }
    refused(invoke(root, *arguments, agent="agent1"), "human_only_approval")
    assert _state(root) == before
    assert {
        p.relative_to(destination).as_posix(): p.read_bytes()
        for p in destination.rglob("*")
        if p.is_file()
    } == destination_before


def _console():
    return (Path(sysconfig.get_path("scripts")) / "rg").resolve()


def _environment():
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    return environment


def _arguments(root, *arguments, agent=None, now=NOW):
    prepare_console(["--project", str(root)])
    options = ["--json", "--project", str(root), "--now", format_timestamp(now)]
    if agent is not None:
        options.extend(["--as-agent", agent])
    return [*options, *arguments]


def _captured(returncode, stdout, stderr):
    # Adapt real child output to the shared assertions, never synthesize CLI JSON.
    return SimpleNamespace(
        exit_code=returncode,
        stdout=stdout,
        stderr=stderr,
        output=stdout + stderr,
    )


def test_general5_failed_second_rename_restores_pending_attach(tmp_path):
    root = _root(tmp_path / "project")
    task = _task(root)
    relative = _artifact(root, task)
    attached = succeeds(
        invoke(root, "attach", task, "--artifact", relative, "--no-commit")
    )
    assert len(attached["artifacts"]) == 1
    before = _state(root)
    # Only this fault test wraps the installed script. Both the script and its
    # real main execute in a child; the injected failure occurs after one graph
    # destination has actually been replaced and never fires during rollback.
    harness = """
import os
import runpy
import sys
original_replace = os.replace
replacements = 0

def failing_replace(source, destination, *args, **kwargs):
    global replacements
    name = os.fspath(destination)
    if '/graph/' in name and name.endswith('.yaml'):
        replacements += 1
        if replacements == 2:
            raise OSError('GENERAL5 deterministic second graph rename failure')
    return original_replace(source, destination, *args, **kwargs)

os.replace = failing_replace
sys.argv = sys.argv[1:]
runpy.run_path(sys.argv[0], run_name='__main__')
"""
    process = subprocess.run(
        [
            sys.executable,
            "-c",
            harness,
            str(_console()),
            *_arguments(root, "supersede", task, "--title", "replacement"),
        ],
        capture_output=True,
        text=True,
        check=False,
        env=_environment(),
    )
    refused(
        _captured(process.returncode, process.stdout, process.stderr), "write_failed"
    )
    assert _state(root) == before
    with Project.open(root) as project:
        assert project.get_node(task).to_dict()["artifacts"] == attached["artifacts"]


def test_general6_two_console_processes_claim_one_lease(tmp_path):
    root = _root(tmp_path / "project")
    task = _task(root)
    processes = []
    try:
        for agent in ("agent1", "agent2"):
            processes.append(
                subprocess.Popen(
                    [str(_console()), *_arguments(root, "claim", task, agent=agent)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    env=_environment(),
                )
            )
        results = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=60)
            results.append(_captured(process.returncode, stdout, stderr))
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.communicate()
    assert sorted(result.exit_code for result in results) == [0, 2]
    winner = succeeds(next(result for result in results if result.exit_code == 0))
    refused(next(result for result in results if result.exit_code == 2), "lease_held")
    with Project.open(root) as project:
        current = project.get_node(task).to_dict()
    assert current == winner
    assert current["status"] == "running"
    assert len([a for a in current["approvals"] if a["what"] == "claim"]) == 1
    assert current["lease"]["actor"]["name"] in {"agent1", "agent2"}


def _valid_arguments(root, operation):
    task = _task(root)
    if operation in {"approve", "reject"}:
        target = succeeds(invoke(root, "propose", "Hypothesis", "H"))["id"]
        return (
            (operation, target)
            if operation == "approve"
            else (operation, target, "obsolete")
        )
    if operation in {"renew", "release", "complete", "fail"}:
        succeeds(invoke(root, "claim", task))
    if operation == "propose":
        return "propose", "Task", "pending"
    if operation == "link":
        return "link", "blocks", task, _task(root, "downstream")
    if operation == "supersede":
        return "supersede", task, "--title", "replacement"
    if operation in {"instruct", "retire", "complete", "fail"}:
        return operation, task, "recorded explanation"
    if operation == "expand":
        return "expand", task, "--hint", "investigate"
    if operation == "budget":
        return "budget", task, "--tokens", "10"
    if operation == "tag":
        return "tag", task, "--add", "pending", "--priority", "3"
    if operation == "attach":
        return "attach", task, "--artifact", _artifact(root, task)
    if operation == "renew":
        return "renew", task, "--lease-seconds", "9000"
    return operation, task


def _log_bytes(path):
    return path.read_bytes() if path.exists() else b""


@pytest.mark.parametrize("operation", WRITE_NAMES)
def test_general7_every_mutation_no_commit_updates_source_and_index_without_event(
    tmp_path, monkeypatch, operation
):
    log = tmp_path / "events.jsonl"
    monkeypatch.setenv("RG_TEST_EVENT_LOG", str(log))
    root = _root(tmp_path / "project", "test-events")
    arguments = _valid_arguments(root, operation)
    before = snapshot(root)
    events = _log_bytes(log)
    assert events, "committed setup must exercise the installed event hook"
    value = succeeds(invoke(root, *arguments, "--no-commit"))
    after = snapshot(root)
    assert after[0] != before[0]
    assert after[1] == before[1]
    assert git(root, "status", "--porcelain")
    assert _log_bytes(log) == events
    with Project.open(root) as project:
        if operation == "link":
            assert (
                next(
                    edge.to_dict()
                    for edge in project.get_edges()
                    if edge.id == value["id"]
                )
                == value
            )
        else:
            assert project.get_node(value["id"]).to_dict() == value
    # A subsequent genuine commit proves the absence above is not an inert hook.
    succeeds(invoke(root, "propose", "Task", "committed hook control"))
    assert _log_bytes(log).startswith(events)
    assert len(_log_bytes(log)) > len(events)


@pytest.mark.parametrize("operation", ["init", "import"])
def test_general7_initializers_no_commit_do_not_emit_events(
    tmp_path, monkeypatch, operation
):
    repository(tmp_path)
    log = tmp_path / "events.jsonl"
    monkeypatch.setenv("RG_TEST_EVENT_LOG", str(log))
    source = _root(tmp_path / "source", "test-events")
    task = _task(source)
    archive = tmp_path / "graph.tar.gz"
    succeeds(invoke(source, "export", str(archive)))
    destination = tmp_path / "destination"
    events = _log_bytes(log)
    arguments = (
        ("init", "--profile", "test-events")
        if operation == "init"
        else ("import", str(archive))
    )
    succeeds(invoke(destination, *arguments, "--no-commit"))
    assert git(destination, "status", "--porcelain")
    assert _log_bytes(log) == events
    with Project.open(destination) as project:
        assert project._git.head() is None
        if operation == "import":
            assert project.get_node(task).title == "Task"
        ready = project.ready(actor=ALICE, now=NOW)
    assert ready == succeeds(invoke(destination, "ready"))
    succeeds(invoke(destination, "propose", "Task", "committed hook control"))
    assert len(_log_bytes(log)) > len(events)


def _models(root):
    with Project.open(root) as project:
        nodes = {
            path.stem: project.get_node(path.stem).to_dict()
            for path in (root / "graph/nodes").glob("*.yaml")
        }
        edges = {edge.id: edge.to_dict() for edge in project.get_edges()}
    return nodes, edges


def _assert_invariants(before, after, operation):
    old_nodes, old_edges = before
    nodes, edges = after
    assert old_nodes.keys() <= nodes.keys()
    assert old_edges.keys() <= edges.keys()
    immutable_node = ("id", "type", "title", "body", "author", "created", "fields")
    immutable_edge = ("id", "type", "from", "to", "author", "created", "fields")
    transitions = {
        "claim": {("active", "running"), ("running", "running")},
        "renew": {("running", "running")},
        "release": {("running", "active")},
    }
    for id, old in old_nodes.items():
        new = nodes[id]
        assert all(old[key] == new[key] for key in immutable_node)
        for key in ("approvals", "artifacts"):
            assert new[key][: len(old[key])] == old[key]
        if old["status"] != new["status"]:
            assert (old["status"], new["status"]) in transitions.get(operation, set())
    for id, old in old_edges.items():
        new = edges[id]
        assert all(old[key] == new[key] for key in immutable_edge)
        if old["retired"]:
            assert new["retired"] and new["retired_at"] == old["retired_at"]
    live = [edge for edge in edges.values() if not edge["retired"]]
    triples = [(e["type"], e["from"], e["to"]) for e in live]
    assert len(triples) == len(set(triples))
    superseded = [e["to"] for e in live if e["type"] == "supersedes"]
    assert len(superseded) == len(set(superseded))
    adjacency = {id: [] for id in nodes}
    acyclic = {"blocks", "derived-from", "supersedes", "cites", "part-of"}
    # The property uses only this bounded research edge vocabulary. Validate
    # endpoint pairs and closed fields explicitly rather than trusting the index.
    for edge in edges.values():
        assert edge["from"] in nodes and edge["to"] in nodes
        source, target = nodes[edge["from"]], nodes[edge["to"]]
        kind = edge["type"]
        assert kind in {"blocks", "answers", "cites", "tests", "part-of"}
        if kind == "answers":
            assert (source["type"], target["type"]) == ("Hypothesis", "Question")
        elif kind == "cites":
            assert source["type"] == "Claim" and target["type"] in {
                "Result",
                "Reference",
            }
        elif kind == "tests":
            assert (
                source["type"] in {"Prediction", "Task"}
                and target["type"] == "Hypothesis"
            )
        if kind == "tests":
            assert set(edge["fields"]) == {"expected", "discriminating"}
            assert isinstance(edge["fields"]["expected"], str)
            assert isinstance(edge["fields"]["discriminating"], bool)
        elif kind == "cites":
            assert set(edge["fields"]) <= {"artifact", "locator", "sha256"}
            assert all(isinstance(value, str) for value in edge["fields"].values())
        else:
            assert edge["fields"] == {}
        if not edge["retired"] and kind in acyclic:
            adjacency[edge["from"]].append(edge["to"])
    visiting, visited = set(), set()

    def visit(id):
        assert id not in visiting, "I1: cycle in live constrained relations"
        if id in visited:
            return
        visiting.add(id)
        for target in adjacency[id]:
            visit(target)
        visiting.remove(id)
        visited.add(id)

    for id in nodes:
        visit(id)
    for node in nodes.values():
        assert re.fullmatch(r"[a-z]{2,4}-[a-z2-7]{8}", node["id"])
        assert (node["lease"] is not None) == (node["status"] == "running")
        if node["lease"] is not None:
            assert node["lease"]["expires_at"] > node["lease"]["acquired_at"]
        assert node["tags"] == sorted(set(node["tags"]))
        for dimension in ("budget", "usage"):
            if node[dimension] is not None:
                assert node[dimension]
                assert all(
                    isinstance(v, int) and v >= 0 for v in node[dimension].values()
                )
    # I7 for the generated vocabulary: lease changes are made by the holder;
    # budgets are human-only; forbidden agent approvals are explicit failures.
    for id, node in nodes.items():
        old = old_nodes.get(id)
        if old is None:
            continue
        for approval in node["approvals"][len(old["approvals"]) :]:
            if approval["what"] == "budget":
                assert approval["actor"]["kind"] == "human"
            if approval["what"] in {"claim", "renew", "release"}:
                assert approval["actor"]["kind"] == "agent"
                assert approval["actor"]["name"] == "worker"
                if approval["what"] != "claim":
                    assert old["lease"]["actor"]["name"] == "worker"


def _baseline(root):
    ids = {}
    for name, kind in (
        ("question", "Question"),
        ("hypothesis", "Hypothesis"),
        ("claim", "Claim"),
        ("task", "Task"),
        ("result", "Result"),
        ("other", "Task"),
        ("prediction", "Prediction"),
    ):
        ids[name] = succeeds(invoke(root, "propose", kind, name))["id"]
    for kind, source, target, extra in (
        ("answers", "hypothesis", "question", ()),
        ("cites", "claim", "result", ()),
        ("tests", "prediction", "hypothesis", ("--fields", '{"expected":"positive"}')),
        ("part-of", "task", "question", ()),
        ("blocks", "task", "other", ()),
    ):
        succeeds(invoke(root, "link", kind, ids[source], ids[target], *extra))
    return ids


def _queries(ids):
    return (
        [(name, ()) for name in ("ready", "blocked", "queue", "stale", "digest")]
        + [(name, (ids["task"],)) for name in ("show", "history", "notes", "subtree")]
        + [(name, (ids["claim"],)) for name in ("evidence", "weakest")]
        + [(name, (ids["hypothesis"],)) for name in ("alternatives", "predictions")]
        + [
            ("matrix", (ids["question"],)),
            ("since", (format_timestamp(NOW - timedelta(seconds=1)),)),
            ("where", ()),
        ]
    )


def _assert_all_queries(root, ids, now):
    for as_of in (None, format_timestamp(NOW)):
        cases = _queries(ids)
        assert len(cases) == 16
        with Project.open(root) as project:
            expected = [
                getattr(project, name)(*arguments, actor=ALICE, now=now, as_of=as_of)
                for name, arguments in cases
            ]
        extra = () if as_of is None else ("--as-of", as_of)
        cached = [
            succeeds(invoke(root, name, *arguments, *extra, now=now))
            for name, arguments in cases
        ]
        assert cached == expected
        for (name, arguments), value in zip(cases, expected, strict=True):
            # No API Project owns the sqlite connection at this boundary. Each
            # family independently exercises opening with the index absent.
            (root / ".researchgraph/index.sqlite").unlink()
            assert succeeds(invoke(root, name, *arguments, *extra, now=now)) == value
            with Project.open(root) as project:
                assert (
                    getattr(project, name)(
                        *arguments, actor=ALICE, now=now, as_of=as_of
                    )
                    == value
                )


ACTIONS = st.lists(
    st.sampled_from(
        [
            "claim",
            "renew",
            "release",
            "tag",
            "budget",
            "clear_budget",
            "invalid_schema",
            "missing",
            "cycle",
            "agent_budget",
            "agent_approve",
        ]
    ),
    min_size=3,
    max_size=4,
)


@settings(max_examples=4, deadline=None)
@given(actions=ACTIONS)
def test_general8_random_cli_writes_preserve_invariants_and_all_query_indexes(actions):
    # A fresh repository per generated/shrunk example, not a function-scoped
    # pytest fixture retained across Hypothesis examples.
    with TemporaryDirectory(prefix="rg-cli-general-") as directory:
        root = _root(Path(directory) / "project")
        ids = _baseline(root)
        previous = _models(root)
        _assert_invariants(({}, {}), previous, "baseline")
        for step, action in enumerate(actions, 1):
            now = NOW + timedelta(seconds=step)
            task = previous[0][ids["task"]]
            agent = None
            expected_rule = None
            if action in {"claim", "renew", "release"}:
                agent = "worker"
                arguments = (action, ids["task"])
                if action == "claim":
                    if task["status"] == "running":
                        agent = "contender"
                        expected_rule = "lease_held"
                    elif task["budget"] is not None:
                        # Constrain generated successful claims to unbudgeted
                        # active tasks. Budget effects still participate in all
                        # sixteen API/cache/rebuild query comparisons.
                        agent = None
                        arguments = ("tag", ids["task"], "--add", f"budgeted-{step}")
                elif task["status"] != "running":
                    expected_rule = "not_running"
            elif action == "tag":
                arguments = (
                    "tag",
                    ids["task"],
                    "--add",
                    f"step-{step}",
                    "--priority",
                    str(step),
                )
            elif action in {"budget", "clear_budget", "agent_budget"}:
                arguments = ("budget", ids["task"])
                if action != "clear_budget":
                    arguments += ("--tokens", "0")
                if action == "agent_budget":
                    agent, expected_rule = "worker", "human_only_approval"
            elif action == "agent_approve":
                arguments = ("approve", ids["hypothesis"])
                agent, expected_rule = "worker", "human_only_approval"
            elif action == "invalid_schema":
                arguments = (
                    "propose",
                    "Task",
                    "invalid",
                    "--fields",
                    '{"undeclared":true}',
                )
                expected_rule = "schema"
            elif action == "missing":
                arguments = ("tag", MISSING, "--add", "absent")
                expected_rule = "not_found"
            else:
                assert action == "cycle"
                arguments = ("link", "blocks", ids["other"], ids["task"])
                expected_rule = "cycle"
            before = _state(root)
            result = invoke(root, *arguments, agent=agent, now=now)
            if expected_rule is None:
                succeeds(result)
            else:
                refused(result, expected_rule)
                assert _state(root) == before
            current = _models(root)
            _assert_invariants(previous, current, arguments[0])
            _assert_all_queries(root, ids, now)
            previous = current
