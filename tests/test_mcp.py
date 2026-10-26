"""Optional official MCP transport and fixed-authority operation dispatch."""

import json
import os
import sysconfig
import threading
import time
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory, TemporaryFile

import anyio
import pytest
import yaml
from hypothesis import given, settings
from hypothesis import strategies as st
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.shared.memory import create_client_server_memory_streams

from researchgraph.mcp import create_server
from researchgraph.model import Actor, RuleViolation, format_timestamp
from researchgraph.project import Project
from researchgraph.registry import OPERATIONS
from tests.door_helpers import invoke_mcp
from tests.helpers import ALICE, NOW, git, repository
from tests.mcp_helpers import call_memory, memory_session
from tests.test_cli import invoke, refused, succeeds
from tests.test_cli_general import (
    WRITE_NAMES,
    _assert_invariants,
    _baseline,
    _missing_arguments,
    _models,
    _root,
    _state,
    _task,
    _valid_arguments,
)
from tests.test_operations import discard_index, snapshot

pytestmark = pytest.mark.mcp

_ESSENTIAL = {
    "show",
    "ready",
    "blocked",
    "queue",
    "digest",
    "where",
    "history",
    "evidence",
    "propose",
    "link",
    "attach",
    "claim",
    "renew",
    "release",
    "complete",
    "fail",
}


def data(result):
    assert len(result.content) == 1 and result.content[0].type == "text"
    decoded = json.loads(result.content[0].text)
    assert decoded == result.structured_content
    return decoded


def _protocol_calls(root, calls, door):
    if door == "mcp":
        _, _, results = call_memory(root, calls)
        return [(result.is_error, data(result)) for result in results]
    from tests.http_helpers import call_http

    results = [
        call_http(root, name, arguments, actor=ALICE) for name, arguments in calls
    ]
    assert all(result.status_code in (200, 422) for result in results)
    return [(result.status_code != 200, result.json()) for result in results]


def test_real_sdk_lists_exact_registry_and_profile_instructions(tmp_path, index_mode):
    with Project.init(
        repository(tmp_path), "research", actor=ALICE, now=NOW
    ) as project:
        guidance = project.profile.agent_guidance
        discard_index(project, index_mode)
    initialized, listed, _ = call_memory(tmp_path)
    assert initialized.instructions == guidance
    tools = {tool.name: tool for tool in listed.tools}
    assert set(tools) == {op["name"] for op in OPERATIONS}
    for op in OPERATIONS:
        tool = tools[op["name"]]
        assert tool.annotations.read_only_hint == (op["kind"] == "read")
        properties = tool.input_schema["properties"]
        assert set(properties) == {arg["name"] for arg in op["arguments"]} | {"now"} | (
            {"commit"} if op["kind"] == "write" else set()
        )
        assert tool.input_schema["required"] == [
            arg["name"] for arg in op["arguments"] if arg["required"]
        ]
        assert tool.input_schema["additionalProperties"] is False
        for arg in op["arguments"]:
            assert properties[arg["name"]] == arg["schema"]
        assert tool.description == op["result"]


@pytest.mark.parametrize("preset", ["full", "essential"])
@pytest.mark.parametrize("read_only", [False, True])
def test_mcp_presets_exact_subset_and_hidden_refusal(
    tmp_path, index_mode, preset, read_only
):
    with Project.init(repository(tmp_path), "tasks", actor=ALICE, now=NOW) as project:
        discard_index(project, index_mode)
    before = _state(tmp_path)
    selected = {op["name"] for op in OPERATIONS} if preset == "full" else _ESSENTIAL
    expected = {
        op["name"]
        for op in OPERATIONS
        if op["name"] in selected and (not read_only or op["kind"] == "read")
    }
    excluded = [
        op
        for op in OPERATIONS
        if op["name"] not in selected or (read_only and op["kind"] == "write")
    ]
    _, listed, results = call_memory(
        tmp_path,
        [(op["name"], {"now": "bad"}) for op in excluded],
        preset=preset,
        read_only=read_only,
    )
    assert {tool.name for tool in listed.tools} == expected
    for op, result in zip(excluded, results, strict=True):
        assert result.is_error
        refused_value = data(result)
        if read_only and op["kind"] == "write":
            assert refused_value["rule"] == "read_only"
        else:
            assert refused_value["rule"] == "schema"
            assert "preset" in refused_value["message"]
    assert _state(tmp_path) == before


@pytest.mark.parametrize("preset", ["unexpected", None])
def test_invalid_mcp_preset_refuses_before_project_creation(tmp_path, preset):
    root = tmp_path / "absent"
    with pytest.raises(RuleViolation) as error:
        create_server(root, actor=ALICE, preset=preset)
    assert error.value.rule == "schema"
    assert not root.exists()


def test_native_results_preserve_arrays_null_omission_and_domain_errors(
    tmp_path, index_mode
):
    with Project.init(repository(tmp_path), "tasks", actor=ALICE, now=NOW) as project:
        node = project.propose("Task", "task", actor=ALICE, now=NOW)
        discard_index(project, index_mode)
    requests = [
        (
            "tag",
            {
                "node": node["id"],
                "priority": 3,
                "add": ["two", "one"],
                "now": format_timestamp(NOW),
            },
        ),
        ("tag", {"node": node["id"], "add": ["three"], "now": format_timestamp(NOW)}),
        ("tag", {"node": node["id"], "priority": None, "now": format_timestamp(NOW)}),
        ("where", {"now": format_timestamp(NOW)}),
        ("show", {"id": "ta-zzzzzzzz", "now": format_timestamp(NOW)}),
    ]
    _, _, results = call_memory(tmp_path, requests)
    first, omitted, cleared, rows, error = map(data, results)
    assert first["tags"] == ["one", "two"] and first["priority"] == 3
    assert omitted["priority"] == 3 and omitted["tags"] == ["one", "three", "two"]
    assert cleared["priority"] is None
    assert isinstance(rows, list) and rows == [cleared]
    assert (
        results[-1].is_error
        and error["rule"] == "not_found"
        and error["ids"] == ["ta-zzzzzzzz"]
    )
    assert all(not result.is_error for result in results[:-1])


@pytest.mark.parametrize(
    "override", ["actor", "role", "read_only", "project", "as_agent", "preset"]
)
def test_tool_arguments_cannot_override_fixed_authority(tmp_path, override):
    with Project.init(
        repository(tmp_path), "research", actor=ALICE, now=NOW
    ) as project:
        node = project.propose("Hypothesis", "pending", actor=ALICE, now=NOW)
        before = snapshot(project)
    _, _, result = call_memory(
        tmp_path,
        [
            (
                "approve",
                {"node": node["id"], override: "human", "now": format_timestamp(NOW)},
            )
        ],
        actor=Actor("agent", "worker"),
        role="human",
    )
    assert result[0].is_error and data(result[0])["rule"] == "schema"
    with Project.open(tmp_path) as project:
        assert snapshot(project) == before


def test_read_only_hides_and_refuses_every_write_including_initializers(
    tmp_path, index_mode
):
    root = repository(tmp_path)
    with Project.init(root, "tasks", actor=ALICE, now=NOW) as project:
        before = snapshot(project)
        discard_index(project, index_mode)
    requests = [
        (op["name"], {"now": "invalid"}) for op in OPERATIONS if op["kind"] == "write"
    ]
    _, listed, results = call_memory(root, requests, read_only=True)
    assert {tool.name for tool in listed.tools} == {
        op["name"] for op in OPERATIONS if op["kind"] == "read"
    }
    assert all(
        result.is_error and data(result)["rule"] == "read_only" for result in results
    )
    with Project.open(root) as project:
        assert snapshot(project) == before


def test_bootstrap_uses_only_fixed_destination_and_no_core_instructions(tmp_path):
    root = tmp_path / "new"
    initialized, _, results = call_memory(
        root, [("init", {"profile": "tasks", "now": format_timestamp(NOW)})]
    )
    assert not initialized.instructions
    assert not results[0].is_error and data(results[0])["path"] == str(root)
    elsewhere = tmp_path / "elsewhere"
    _, _, results = call_memory(
        root, [("init", {"path": str(elsewhere), "now": format_timestamp(NOW)})]
    )
    assert results[0].is_error and data(results[0])["rule"] == "schema"
    assert not elsewhere.exists()


@pytest.mark.parametrize("existing_history", [False, True])
@pytest.mark.parametrize("door", ["mcp", pytest.param("http", marks=pytest.mark.http)])
def test_protocol_init_files_history_repeat_and_cache(
    tmp_path, monkeypatch, index_mode, existing_history, door
):
    root = tmp_path / "project"
    monkeypatch.setenv("GIT_CONFIG_COUNT", "2")
    for index, (key, value) in enumerate(
        [("user.name", "Alice Git"), ("user.email", "alice@git.test")]
    ):
        monkeypatch.setenv(f"GIT_CONFIG_KEY_{index}", key)
        monkeypatch.setenv(f"GIT_CONFIG_VALUE_{index}", value)
    monkeypatch.setenv("GIT_AUTHOR_DATE", format_timestamp(NOW))
    monkeypatch.setenv("GIT_COMMITTER_DATE", format_timestamp(NOW))
    if existing_history:
        repository(root)
        (root / "keep.txt").write_bytes(b"existing history\n")
        git(root, "add", "keep.txt")
        git(root, "commit", "-qm", "earlier")
        earlier = git(root, "rev-parse", "HEAD")
    else:
        root.mkdir()
    results = _protocol_calls(
        root, [("init", {"profile": "research", "now": format_timestamp(NOW)})], door
    )
    assert not results[0][0]
    assert results[0][1] == {
        "path": str(root),
        "profile": "research",
        "version": "1.0",
    }
    assert (root / "graph/nodes").is_dir() and (root / "graph/edges").is_dir()
    pin = yaml.safe_load((root / ".researchgraph/profile.yaml").read_text())
    assert pin == {"name": "research", "version": "1.0"}
    ignored = (root / ".gitignore").read_text()
    assert ".researchgraph/index.sqlite" in ignored
    assert ".researchgraph/lock" in ignored
    assert git(root, "log", "--format=%s").splitlines() == ["rg: init"] + (
        ["earlier"] if existing_history else []
    )
    if existing_history:
        assert git(root, "rev-parse", "HEAD^") == earlier
        assert (root / "keep.txt").read_bytes() == b"existing history\n"
    before = _state(root)
    with Project.open(root) as project:
        discard_index(project, index_mode)
    results = _protocol_calls(
        root,
        [("ready", {"now": format_timestamp(NOW)}), ("init", {})],
        door,
    )
    assert not results[0][0] and results[0][1] == {"items": []}
    assert results[1][0] and results[1][1]["rule"] == "already_initialised"
    assert _state(root) == before


@pytest.mark.parametrize("door", ["mcp", pytest.param("http", marks=pytest.mark.http)])
def test_protocol_init_unknown_profile_has_no_project_files(tmp_path, door):
    root = repository(tmp_path)
    results = _protocol_calls(root, [("init", {"profile": "widgets"})], door)
    assert results[0][0] and results[0][1]["rule"] == "unknown_profile"
    assert not (root / "graph").exists()
    assert not (root / ".researchgraph").exists()


def test_legacy_negotiation_refuses_instead_of_wrapping_arrays(tmp_path):
    with Project.init(repository(tmp_path), "tasks", actor=ALICE, now=NOW):
        pass
    _, _, results = call_memory(tmp_path, [("where", {})], modern=False)
    assert results[0].is_error
    error = data(results[0])
    assert error["rule"] == "schema" and "discovery negotiation" in error["message"]


@pytest.mark.parametrize("operation", WRITE_NAMES)
@pytest.mark.parametrize("commit", [True, False])
@pytest.mark.parametrize("door", ["mcp", pytest.param("http", marks=pytest.mark.http)])
def test_all_existing_valid_write_cases_through_real_protocol(
    tmp_path, index_mode, monkeypatch, operation, commit, door
):
    if door == "http":
        from tests.http_helpers import invoke_http as protocol_invoke
    else:
        protocol_invoke = invoke_mcp
    monkeypatch.setenv("RG_TEST_CLI_INDEX_MODE", index_mode)
    log = tmp_path / "events.jsonl"
    monkeypatch.setenv("RG_TEST_EVENT_LOG", str(log))
    root = _root(tmp_path / "project", "test-events")
    arguments = _valid_arguments(root, operation)
    events, head = log.read_bytes(), git(root, "rev-parse", "HEAD")
    assert events
    value = succeeds(
        protocol_invoke(root, *arguments, "--commit" if commit else "--no-commit")
    )
    with Project.open(root) as project:
        if operation == "link":
            assert (
                next(e.to_dict() for e in project.get_edges() if e.id == value["id"])
                == value
            )
        else:
            assert project.get_node(value["id"]).to_dict() == value
    if commit:
        assert log.read_bytes().startswith(events) and len(log.read_bytes()) > len(
            events
        )
        assert git(root, "rev-parse", "HEAD") != head
    else:
        assert log.read_bytes() == events and git(root, "rev-parse", "HEAD") == head
        assert git(root, "status", "--porcelain")
        succeeds(invoke(root, "propose", "Task", "committed hook control"))
        assert log.read_bytes().startswith(events) and len(log.read_bytes()) > len(
            events
        )


def test_read_only_initializers_refuse_without_touching_absent_destination(tmp_path):
    root = tmp_path / "absent-parent" / "project"
    _, listed, results = call_memory(
        root,
        [
            ("init", {"now": "invalid"}),
            ("import", {"archive": str(tmp_path / "missing.tar.gz"), "now": "invalid"}),
        ],
        read_only=True,
    )
    assert "init" not in {tool.name for tool in listed.tools}
    assert all(
        result.is_error and data(result)["rule"] == "read_only" for result in results
    )
    assert not root.parent.exists()


def test_two_real_mcp_clients_have_one_claim_winner(tmp_path, index_mode):
    with Project.init(repository(tmp_path), "tasks", actor=ALICE, now=NOW) as project:
        node = project.propose("Task", "contended", actor=ALICE, now=NOW)
        discard_index(project, index_mode)
    results = []

    async def claim(name):
        _, _, replies = await memory_session(
            tmp_path,
            [("claim", {"node": node["id"], "now": format_timestamp(NOW)})],
            actor=Actor("agent", name),
        )
        results.append(replies[0])

    async def contend():
        async with anyio.create_task_group() as group:
            group.start_soon(claim, "first")
            group.start_soon(claim, "second")

    anyio.run(contend)
    winners = [data(reply) for reply in results if not reply.is_error]
    losers = [data(reply) for reply in results if reply.is_error]
    assert len(winners) == len(losers) == 1
    assert losers[0]["rule"] == "lease_held"
    assert winners[0]["lease"]["actor"]["name"] in {"first", "second"}
    with Project.open(tmp_path) as project:
        assert project.get_node(node["id"]).to_dict() == winners[0]


@pytest.mark.parametrize("operation", WRITE_NAMES)
@pytest.mark.parametrize("door", ["mcp", pytest.param("http", marks=pytest.mark.http)])
def test_all_existing_missing_id_cases_through_real_protocol(
    tmp_path, index_mode, monkeypatch, operation, door
):
    if door == "http":
        from tests.http_helpers import invoke_http as protocol_invoke
    else:
        protocol_invoke = invoke_mcp
    monkeypatch.setenv("RG_TEST_CLI_INDEX_MODE", index_mode)
    root = _root(tmp_path / "project")
    existing = _task(root)
    before = _state(root)
    refused(
        protocol_invoke(root, *_missing_arguments(operation, existing)), "not_found"
    )
    assert _state(root) == before


def test_empty_repeated_collectors_reach_core_without_missing_signature_errors(
    tmp_path,
):
    with Project.init(repository(tmp_path), "tasks", actor=ALICE, now=NOW) as project:
        task = project.propose("Task", "collector", actor=ALICE, now=NOW)
    _, _, results = call_memory(
        tmp_path,
        [
            ("expand", {"now": format_timestamp(NOW)}),
            (
                "attach",
                {"node": task["id"], "commit": False, "now": format_timestamp(NOW)},
            ),
        ],
    )
    assert results[0].is_error and data(results[0])["rule"] == "no_targets"
    assert not results[1].is_error and data(results[1]) == task


def test_installed_launcher_requires_declared_identity(tmp_path):
    with Project.init(repository(tmp_path), "tasks", actor=ALICE, now=NOW):
        pass
    missing_result = invoke(tmp_path, "mcp")
    refused(missing_result, "schema")
    missing = json.loads(missing_result.stderr)
    assert "--as-agent" in missing["message"] and "--as-human" in missing["message"]
    conflict_result = invoke(tmp_path, "mcp", "--as-human", agent="worker")
    refused(conflict_result, "schema")
    conflict = json.loads(conflict_result.stderr)
    assert "either" in conflict["message"].lower()


@pytest.mark.parametrize("preset", [None, "essential"])
def test_real_installed_modern_stdio_round_trip_and_shutdown(
    tmp_path, index_mode, preset
):
    with Project.init(repository(tmp_path), "tasks", actor=ALICE, now=NOW) as project:
        expected = project.digest(actor=ALICE, now=NOW)
        expected_guidance = project.profile.agent_guidance
        discard_index(project, index_mode)

    async def session(errors):
        parameters = StdioServerParameters(
            command=str(Path(sysconfig.get_path("scripts")) / "rgraph"),
            args=["--project", str(tmp_path), "--as-agent", "stdio-worker", "mcp"]
            + ([] if preset is None else ["--preset", preset]),
            env={
                key: value for key, value in os.environ.items() if key != "PYTHONPATH"
            },
        )
        async with (
            stdio_client(parameters, errlog=errors) as streams,
            ClientSession(*streams) as client,
        ):
            discovered = await client.discover()
            assert discovered.instructions == expected_guidance
            listed = await client.list_tools()
            assert {tool.name for tool in listed.tools} == (
                _ESSENTIAL
                if preset == "essential"
                else {op["name"] for op in OPERATIONS}
            )
            result = await client.call_tool("digest", {"now": format_timestamp(NOW)})
            assert not result.is_error and data(result) == expected
            result = await client.call_tool(
                "propose",
                {"type": "Task", "title": "stdio write", "now": format_timestamp(NOW)},
            )
            value = data(result)
            assert not result.is_error and value["author"]["name"] == "stdio-worker"
            hidden_override = await client.call_tool(
                "tag", {"node": value["id"], "read_only": False}
            )
            assert (
                hidden_override.is_error and data(hidden_override)["rule"] == "schema"
            )
            return value

    with TemporaryFile(mode="w+") as errors:
        value = anyio.run(session, errors)
        errors.seek(0)
        assert errors.read() == ""
    with Project.open(tmp_path) as project:
        assert project.get_node(value["id"]).to_dict() == value


def test_cancelled_native_request_closes_owner_before_next_call(tmp_path, monkeypatch):
    with Project.init(repository(tmp_path), "tasks", actor=ALICE, now=NOW):
        pass
    original_open, original_close, original_ready = (
        Project.open,
        Project.close,
        Project.ready,
    )
    active, ready_owner = set(), []
    closed = threading.Event()

    def tracked_open(cls, *args, **kwargs):
        project = original_open(*args, **kwargs)
        active.add(id(project))
        return project

    def tracked_close(self):
        try:
            return original_close(self)
        finally:
            active.discard(id(self))
            if ready_owner and ready_owner[0] == id(self):
                closed.set()

    def delayed_ready(self, *args, **kwargs):
        ready_owner.append(id(self))
        time.sleep(0.2)
        return original_ready(self, *args, **kwargs)

    monkeypatch.setattr(Project, "open", classmethod(tracked_open))
    monkeypatch.setattr(Project, "close", tracked_close)
    monkeypatch.setattr(Project, "ready", delayed_ready)

    async def session():
        server = create_server(tmp_path, actor=ALICE)
        async with (
            create_client_server_memory_streams() as (incoming, outgoing),
            anyio.create_task_group() as group,
        ):
            group.start_soon(
                server.run, *outgoing, server.create_initialization_options()
            )
            async with ClientSession(*incoming) as client:
                await client.discover()
                with anyio.move_on_after(0.05) as scope:
                    await client.call_tool("ready", {"now": format_timestamp(NOW)})
                assert scope.cancel_called
                assert await anyio.to_thread.run_sync(closed.wait, 2)
                assert not active
                result = await client.call_tool(
                    "digest", {"now": format_timestamp(NOW)}
                )
                assert not result.is_error
            group.cancel_scope.cancel()
        assert not active

    anyio.run(session)


def test_installed_stdio_cancellation_and_noisy_callback_framing(tmp_path, index_mode):
    root = repository(tmp_path / "project")
    with Project.init(root, "test-stdio", actor=ALICE, now=NOW) as project:
        discard_index(project, index_mode)
    marker = tmp_path / "validator-entered"
    environment = {
        key: value for key, value in os.environ.items() if key != "PYTHONPATH"
    }
    environment["RG_TEST_STDIO_MARKER"] = str(marker)

    async def session(errors):
        parameters = StdioServerParameters(
            command=str(Path(sysconfig.get_path("scripts")) / "rgraph"),
            args=["--project", str(root), "--as-agent", "cancel-worker", "mcp"],
            env=environment,
        )
        async with (
            stdio_client(parameters, errlog=errors) as streams,
            ClientSession(*streams) as client,
        ):
            await client.discover()
            scopes = []

            async def write():
                with anyio.CancelScope() as scope:
                    scopes.append(scope)
                    await client.call_tool(
                        "propose",
                        {
                            "type": "Task",
                            "title": "slow cancellation",
                            "now": format_timestamp(NOW),
                        },
                    )

            async with anyio.create_task_group() as requests:
                requests.start_soon(write)
                with anyio.fail_after(5):
                    while not marker.exists():
                        await anyio.sleep(0.01)
                scopes[0].cancel()
            # Cancellation is not a rollback promise. The shielded public
            # transaction may commit; observe it without bypassing the SDK.
            with anyio.fail_after(5):
                while not Path(str(marker) + ".done").exists():
                    await anyio.sleep(0.01)
            result = await client.call_tool("where", {"now": format_timestamp(NOW)})
            rows = data(result)
            assert not result.is_error and len(rows) == 1
            assert (
                rows[0]["title"] == "slow cancellation"
                and rows[0]["author"]["name"] == "cancel-worker"
            )
            digest = await client.call_tool("digest", {"now": format_timestamp(NOW)})
            assert not digest.is_error and data(digest)
            return rows[0]

    with TemporaryFile(mode="w+") as errors:
        node = anyio.run(session, errors)
        errors.seek(0)
        observed = errors.read()
        assert "RG_STDIO_ENTER" in observed and "RG_STDIO_COMMITTED" in observed
        assert "Traceback" not in observed
    with Project.open(root) as project:
        assert project.get_node(node["id"]).to_dict() == node


@settings(max_examples=6, deadline=None)
@given(
    actions=st.lists(
        st.sampled_from(
            [
                "claim",
                "renew",
                "release",
                "tag",
                "agent_budget",
                "agent_approve",
                "missing",
                "invalid_schema",
            ]
        ),
        min_size=3,
        max_size=6,
    )
)
@pytest.mark.parametrize("profile_name", ["research", "test-conditional"])
def test_real_mcp_random_writes_preserve_invariants_and_cache_parity(
    actions, profile_name
):
    with TemporaryDirectory(prefix="rg-mcp-invariants-") as directory:
        root = _root(Path(directory) / "project", profile_name)
        ids = _baseline(root, conditional=profile_name == "test-conditional")
        previous = _models(root)
        for step, action in enumerate(actions, 1):
            now = NOW + timedelta(seconds=step)
            operation = {
                "agent_budget": "budget",
                "agent_approve": "approve",
                "missing": "claim",
                "invalid_schema": "propose",
            }.get(action, action)
            arguments = {"node": ids["task"]}
            if action == "tag":
                arguments.update(add=["observed"], priority=0)
            elif action == "agent_budget":
                arguments["tokens"] = 1
            elif action == "agent_approve":
                arguments["node"] = ids["hypothesis"]
            elif action == "missing":
                arguments["node"] = "ta-zzzzzzzz"
            elif action == "invalid_schema":
                arguments = {
                    "type": "Task",
                    "title": "invalid",
                    "fields": {"unknown": True},
                }
            arguments["now"] = format_timestamp(now)
            before = _state(root)
            _, _, results = call_memory(
                root, [(operation, arguments)], actor=Actor("agent", "worker")
            )
            data(results[0])
            after = _models(root)
            if results[0].is_error:
                assert _state(root) == before
            else:
                _assert_invariants(previous, after, operation)
            previous = after
            with Project.open(root) as project:
                expected = project.graph(actor=ALICE, now=now)
                discard_index(project, "deleted")
            _, _, observed = call_memory(
                root, [("graph", {"now": format_timestamp(now)})]
            )
            assert data(observed[0]) == expected
