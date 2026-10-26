"""HTTP/SSE acceptance over real sockets and independently opened native owners."""

import json
import os
import subprocess
import sysconfig
import threading
from pathlib import Path
from time import monotonic, perf_counter

import anyio
import pytest

from researchgraph.events import ChangeSource
from researchgraph.project import Project
from tests.helpers import ALICE, NOW, git, repository
from tests.http_server_helpers import SSEStream, loopback_server
from tests.test_operations import discard_index

pytestmark = pytest.mark.http


def initialize(root, index_mode):
    with Project.init(repository(root), "tasks", actor=ALICE, now=NOW) as project:
        discard_index(project, index_mode)


def native_graph(root, index_mode):
    with Project.open(root, read_only=True) as project:
        discard_index(project, index_mode)
        return project.graph(actor=ALICE, now=NOW)


def external_write(root, title, index_mode):
    with Project.open(root) as project:
        discard_index(project, index_mode)
        return project.propose("Note", title, actor=ALICE, now=NOW, commit=False)


def snapshot(owner):
    response = owner.client.get("/api/graph")
    assert response.status_code == 200, response.text
    return response.json(), response.headers["X-Researchgraph-Cursor"]


def next_index(stream, *, maximum=100):
    events = []
    for _ in range(maximum):
        event = stream.event()
        events.append(event)
        if event["kind"] == "index":
            return events
    pytest.fail("No index envelope within the bounded event batch")


def counts(graph):
    return {kind: len(rows) for kind, rows in graph.items()}


def test_new_subscriber_observation_wakes_existing_subscriber(
    tmp_path, index_mode, monkeypatch
):
    from researchgraph.serve import _State

    initialize(tmp_path, index_mode)
    original_poll, original_changes = _State._poll, ChangeSource.changes
    paused, waiting = threading.Event(), threading.Event()
    paused.set()
    cursor = None

    async def delayed_poll(state):
        while paused.is_set():
            await anyio.sleep(0.01)
        await original_poll(state)

    def observed(source, **context):
        result = original_changes(source, **context)
        if "since" in context and context["since"] == cursor and not result["events"]:
            waiting.set()
        return result

    monkeypatch.setattr(_State, "_poll", delayed_poll)
    monkeypatch.setattr(ChangeSource, "changes", observed)
    with loopback_server(tmp_path) as owner:
        owner.authenticate()
        _, cursor = snapshot(owner)
        try:
            with SSEStream(owner, cursor) as existing:
                assert waiting.wait(1), (
                    "First subscriber did not enter its empty-cursor wait"
                )
                node = external_write(tmp_path, "observed by reconnect", index_mode)
                with SSEStream(owner, cursor) as newcomer:
                    assert next_index(newcomer)[0]["node"] == node
                    assert next_index(existing)[0]["node"] == node
        finally:
            paused.clear()


def test_successors_reconnect_without_duplicates_and_observe_external_no_commit(
    tmp_path, index_mode
):
    initialize(tmp_path, index_mode)
    head = git(tmp_path, "rev-parse", "HEAD")
    with loopback_server(tmp_path) as owner:
        owner.authenticate()
        graph, cursor = snapshot(owner)
        assert graph == native_graph(tmp_path, index_mode)
        with SSEStream(owner, cursor) as stream:
            first = external_write(tmp_path, "first external", index_mode)
            batch = next_index(stream)
            assert [event["kind"] for event in batch] == ["node", "index"]
            assert batch[0]["node"] == first
            assert batch[0]["previous_status"] is None
            assert batch[0]["change"] == "created"
            assert all(event["seq"] > int(cursor.rsplit(":", 1)[1]) for event in batch)
            assert batch[-1]["counts"] == counts(native_graph(tmp_path, index_mode))
        with SSEStream(owner, batch[0]["cursor"]) as replay:
            assert replay.event() == batch[1]
        second = external_write(tmp_path, "after disconnect", index_mode)
        with SSEStream(owner, batch[-1]["cursor"]) as resumed:
            successors = next_index(resumed)
            assert [event["kind"] for event in successors] == ["node", "index"]
            assert successors[0]["node"] == second
            assert not {event["cursor"] for event in batch} & {
                event["cursor"] for event in successors
            }
        assert snapshot(owner)[0] == native_graph(tmp_path, index_mode)
    assert git(tmp_path, "rev-parse", "HEAD") == head
    assert not any(
        "event" in path.name for path in (tmp_path / ".researchgraph").iterdir()
    )


def test_initial_subscription_and_named_edge_payloads_match_native_records(
    tmp_path, index_mode
):
    initialize(tmp_path, index_mode)
    with loopback_server(tmp_path) as owner:
        owner.authenticate()
        with SSEStream(owner) as stream:
            initial = stream.event()
            assert initial["kind"] == "index"
            assert initial["counts"] == counts(native_graph(tmp_path, index_mode))
        parent = external_write(tmp_path, "parent", index_mode)
        child = external_write(tmp_path, "child", index_mode)
        with Project.open(tmp_path) as writer:
            discard_index(writer, index_mode)
            edge = writer.link(
                "part-of", child["id"], parent["id"], actor=ALICE, now=NOW, commit=False
            )
        with SSEStream(owner, initial["cursor"]) as stream:
            batch = next_index(stream)
            nodes = [event for event in batch if event["kind"] == "node"]
            edges = [event for event in batch if event["kind"] == "edge"]
            assert {event["id"] for event in nodes} == {parent["id"], child["id"]}
            assert len(edges) == 1 and edges[0]["edge"] == edge
            assert edges[0]["change"] == "created"
            assert batch[-1]["counts"] == counts(native_graph(tmp_path, index_mode))
        assert snapshot(owner)[0] == native_graph(tmp_path, index_mode)


@pytest.mark.parametrize(
    "bad_cursor", ["", "broken", "v0:no:1", "v1:" + "0" * 32 + ":999999"]
)
def test_malformed_or_unknown_cursor_resets_then_refetches(
    tmp_path, index_mode, bad_cursor
):
    initialize(tmp_path, index_mode)
    external_write(tmp_path, "private retained state", index_mode)
    with loopback_server(tmp_path) as owner:
        owner.authenticate()
        with SSEStream(owner, bad_cursor) as stream:
            reset = stream.event()
            assert reset["kind"] == "index" and reset["reset"] is True
            assert reset["counts"] == counts(native_graph(tmp_path, index_mode))
        graph, cursor = snapshot(owner)
        assert graph == native_graph(tmp_path, index_mode)
        node = external_write(tmp_path, "after reset", index_mode)
        with SSEStream(owner, cursor) as stream:
            events = next_index(stream)
            assert events[0]["node"] == node
            assert not any(event.get("reset") for event in events)


def test_old_server_generation_and_prior_cookie_are_rejected(tmp_path, index_mode):
    initialize(tmp_path, index_mode)
    with loopback_server(tmp_path) as first:
        first.authenticate()
        _, old_cursor = snapshot(first)
        cookies = dict(first.client.cookies)
        old_token = first.app.state.launch_token
    with loopback_server(tmp_path) as second:
        second.client.cookies.update(cookies)
        assert second.client.get("/api/events").status_code == 403
        response = second.client.post(
            "/api/session", json={"token": old_token}, headers={"Origin": second.origin}
        )
        assert response.status_code == 403
        second.client.cookies.clear()
        second.authenticate()
        active_cookies = dict(second.client.cookies)
        second.client.cookies.clear()
        second.client.cookies.set(
            next(iter(active_cookies)), next(iter(cookies.values()))
        )
        assert second.client.get("/api/events").status_code == 403
        second.client.cookies.clear()
        second.client.cookies.update(active_cookies)
        with SSEStream(second, old_cursor) as stream:
            reset = stream.event()
            assert reset["kind"] == "index" and reset["reset"] is True
            assert reset["generation"] != old_cursor.split(":")[1]
        assert snapshot(second)[0] == native_graph(tmp_path, index_mode)


def reduce_retention(monkeypatch, retention):
    """Constructor instrumentation only: preserve the actual public event source."""
    original = ChangeSource.__init__
    sources = []

    def bounded(self, project, **options):
        original(self, project, **{**options, "retention": retention})
        sources.append(self)

    monkeypatch.setattr(ChangeSource, "__init__", bounded)
    return sources


def test_expired_cursor_has_one_reset_not_partial_replay(
    tmp_path, index_mode, monkeypatch
):
    initialize(tmp_path, index_mode)
    sources = reduce_retention(monkeypatch, 3)
    with loopback_server(tmp_path) as owner:
        owner.authenticate()
        _, expired = snapshot(owner)
        for number in range(3):
            external_write(tmp_path, f"retention {number}", index_mode)
            snapshot(owner)  # force a real observation, not an artificial event
        assert len(sources) == 1 and len(sources[0]._events) <= 3
        with SSEStream(owner, expired) as stream:
            reset = stream.event()
            assert reset["kind"] == "index" and reset["reset"] is True
            assert reset["counts"] == counts(native_graph(tmp_path, index_mode))
            fresh, cursor = snapshot(owner)
            assert fresh == native_graph(tmp_path, index_mode)
        node = external_write(tmp_path, "recovered", index_mode)
        with SSEStream(owner, cursor) as stream:
            assert next_index(stream)[0]["node"] == node


def test_post_snapshot_write_uses_associated_cursor_not_later_observation(
    tmp_path, index_mode, monkeypatch
):
    initialize(tmp_path, index_mode)
    before = native_graph(tmp_path, index_mode)
    original = ChangeSource.snapshot
    written = []

    def racing(self, **context):
        result = original(self, **context)
        if not written:
            written.append(
                external_write(tmp_path, "after original snapshot", index_mode)
            )
        return result

    monkeypatch.setattr(ChangeSource, "snapshot", racing)
    with loopback_server(tmp_path) as owner:
        owner.authenticate()
        graph, cursor = snapshot(owner)
        assert graph == before
        assert written and written[0]["id"] not in {
            node["id"] for node in graph["nodes"]
        }
        with SSEStream(owner, cursor) as stream:
            batch = next_index(stream)
            assert [event["kind"] for event in batch] == ["node", "index"]
            assert batch[0]["node"] == written[0]
            assert batch[0]["seq"] == int(cursor.rsplit(":", 1)[1]) + 1
            assert batch[-1]["counts"] == counts(native_graph(tmp_path, index_mode))
        assert snapshot(owner)[0] == native_graph(tmp_path, index_mode)


def test_slow_network_consumer_converges_with_bounded_retention(
    tmp_path, index_mode, monkeypatch
):
    initialize(tmp_path, index_mode)
    sources = reduce_retention(monkeypatch, 3)
    with loopback_server(tmp_path) as owner:
        owner.authenticate()
        graph, cursor = snapshot(owner)
        folded = {
            kind: {row["id"]: row for row in rows} for kind, rows in graph.items()
        }
        with SSEStream(owner, cursor) as stream:
            # Deliberately leave the real response unread while the server observes writes.
            for number in range(8):
                external_write(tmp_path, f"slow {number}", index_mode)
                snapshot(owner)
            expected = native_graph(tmp_path, index_mode)
            seen = set()
            for _ in range(100):
                event = stream.event()
                assert event["cursor"] not in seen
                seen.add(event["cursor"])
                if event.get("reset"):
                    fresh, _ = snapshot(owner)
                    folded = {
                        kind: {row["id"]: row for row in rows}
                        for kind, rows in fresh.items()
                    }
                elif event["kind"] in {"node", "edge"}:
                    kind = "nodes" if event["kind"] == "node" else "edges"
                    folded[kind][event["id"]] = event[event["kind"]]
                if event["kind"] == "index" and event["counts"] == counts(expected):
                    break
            else:
                pytest.fail("Slow consumer never reached the final persisted index")
            assert folded == {
                kind: {row["id"]: row for row in rows}
                for kind, rows in expected.items()
            }
            assert len(sources) == 1 and len(sources[0]._events) <= 3
        assert snapshot(owner)[0] == expected


def test_actual_fifteen_second_keepalive_without_graph_changes(tmp_path, index_mode):
    initialize(tmp_path, index_mode)
    with loopback_server(tmp_path) as owner:
        owner.authenticate()
        _, cursor = snapshot(owner)
        started = monotonic()
        with SSEStream(owner, cursor, timeout=17) as stream:
            frame = stream.frame()
            elapsed = monotonic() - started
            assert frame == {"comment": "keepalive"}
            assert 15 <= elapsed <= 16, elapsed


@pytest.mark.parametrize("credential", ["token", "access_token", "session_token"])
def test_sse_rejects_missing_auth_foreign_host_origin_and_query_credentials(
    tmp_path, index_mode, credential
):
    initialize(tmp_path, index_mode)
    secret = external_write(tmp_path, "undisclosed title", index_mode)
    with loopback_server(tmp_path) as owner:
        unauthenticated = owner.client.get("/api/events")
        assert unauthenticated.status_code == 403
        owner.authenticate()
        responses = [
            unauthenticated,
            owner.client.get("/api/events", headers={"Host": "evil.test"}),
            owner.client.get("/api/events", headers={"Origin": "http://evil.test"}),
            owner.client.get(
                "/api/events", params={credential: owner.app.state.launch_token}
            ),
        ]
        for response in responses:
            assert response.status_code == 403
            assert response.json() == {
                "rule": "authority",
                "message": "Forbidden",
                "ids": [],
            }
            assert (
                secret["id"] not in response.text
                and secret["title"] not in response.text
            )
            assert response.headers["cache-control"] == "no-store"
            assert response.headers["referrer-policy"] == "no-referrer"
        _, cursor = snapshot(owner)
        with SSEStream(owner, cursor, headers={"Origin": owner.origin}) as stream:
            node = external_write(tmp_path, "matching origin", index_mode)
            assert next_index(stream)[0]["node"] == node


@pytest.mark.cli
def test_installed_cli_external_no_commit_reaches_sse_within_three_seconds(
    tmp_path, index_mode
):
    initialize(tmp_path, index_mode)
    with loopback_server(tmp_path) as owner:
        owner.authenticate()
        _, cursor = snapshot(owner)
        with SSEStream(owner, cursor) as stream:
            environment = {
                key: value for key, value in os.environ.items() if key != "PYTHONPATH"
            }
            head = git(tmp_path, "rev-parse", "HEAD")
            started = monotonic()
            result = subprocess.run(
                [
                    str(Path(sysconfig.get_path("scripts")) / "rgraph"),
                    "--project",
                    str(tmp_path),
                    "--json",
                    "propose",
                    "Note",
                    "installed writer",
                    "--no-commit",
                ],
                capture_output=True,
                text=True,
                env=environment,
                timeout=10,
                check=False,
            )
            assert result.returncode == 0 and not result.stderr, (
                result.stdout,
                result.stderr,
            )
            node = json.loads(result.stdout)
            # Delete via an independent ordinary owner; the serving owner remains live.
            native_graph(tmp_path, index_mode)
            events = next_index(stream)
            assert monotonic() - started <= 3
            assert events[0]["node"] == node
            assert events[-1]["counts"] == counts(native_graph(tmp_path, index_mode))
            assert git(tmp_path, "rev-parse", "HEAD") == head


@pytest.mark.parametrize("shutdown_with_stream", [False, True])
def test_disconnect_reconnect_and_shutdown_release_exact_server_owner(
    tmp_path, index_mode, monkeypatch, shutdown_with_stream, capfd
):
    initialize(tmp_path, index_mode)
    original_project_close = Project.close
    original_source_close = ChangeSource.close
    project_closes, source_closes = [], []

    def close_project(self):
        result = original_project_close(self)
        if threading.current_thread().name == "http-test-owner":
            project_closes.append(self)
        return result

    def close_source(self):
        result = original_source_close(self)
        if threading.current_thread().name == "http-test-owner":
            source_closes.append(self)
        return result

    monkeypatch.setattr(Project, "close", close_project)
    monkeypatch.setattr(ChangeSource, "close", close_source)
    with loopback_server(tmp_path) as owner:
        owner.authenticate()
        _, cursor = snapshot(owner)
        with SSEStream(owner, cursor) as stream:
            first = external_write(tmp_path, "disconnect", index_mode)
            events = next_index(stream)
            assert events[0]["node"] == first
        assert not project_closes and not source_closes
        with SSEStream(owner, events[-1]["cursor"]) as stream:
            second = external_write(tmp_path, "reconnect", index_mode)
            assert next_index(stream)[0]["node"] == second
            if shutdown_with_stream:
                owner.close()
                assert not owner.thread.is_alive()
    assert len(project_closes) == len(source_closes) == 1
    assert source_closes[0]._project is None and not source_closes[0]._events
    assert external_write(tmp_path, "owner released", index_mode)
    diagnostic = capfd.readouterr().err
    assert "timeout graceful shutdown exceeded" not in diagnostic
    assert "Exception in ASGI application" not in diagnostic


@pytest.mark.performance
def test_twenty_cli_claims_under_fifty_running_reach_sse_within_three_seconds(
    tmp_path, index_mode, monkeypatch, record_property
):
    from tests.test_cli import invoke, succeeds

    monkeypatch.setenv("RG_TEST_CLI_INDEX_MODE", index_mode)
    with Project.init(repository(tmp_path), "tasks", actor=ALICE, now=NOW) as project:
        for number in range(50):
            node = project.propose("Task", f"background {number}", actor=ALICE, now=NOW)
            project.claim(node["id"], actor=ALICE, now=NOW)
        targets = [
            project.propose("Task", f"claim target {number}", actor=ALICE, now=NOW)[
                "id"
            ]
            for number in range(20)
        ]
        assert len(project.where(status="running", actor=ALICE, now=NOW)) == 50
    samples = []
    with loopback_server(tmp_path) as owner:
        owner.authenticate()
        _, cursor = snapshot(owner)
        with SSEStream(owner, cursor) as stream:
            for identifier in targets:
                path = tmp_path / "graph/nodes" / f"{identifier}.yaml"
                before = path.stat().st_size
                started = perf_counter()
                claimed = succeeds(invoke(tmp_path, "claim", identifier))
                while True:
                    event = stream.event()
                    if event["kind"] == "node" and event["id"] == identifier:
                        break
                latency = perf_counter() - started
                assert event["node"] == claimed and claimed["status"] == "running"
                assert event["previous_status"] == "active"
                assert claimed["approvals"][-1]["what"] == "claim"
                sample = {
                    "id": identifier,
                    "latency_seconds": latency,
                    "before_bytes": before,
                    "after_bytes": path.stat().st_size,
                }
                samples.append(sample)
                assert sample["after_bytes"] > before
                assert latency < 3, sample
    report = {
        "initial_running": 50,
        "claims": 20,
        "poll_seconds": 1,
        "index_mode": index_mode,
        "samples": samples,
    }
    print(json.dumps(report))
    record_property("http_sse_claim_latency", json.dumps(report))
    assert len(samples) == 20
