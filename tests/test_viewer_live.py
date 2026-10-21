"""Real dashboard snapshots, clock-only reevaluation and external CLI→SSE→DOM."""

import json
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from time import perf_counter
from urllib.parse import parse_qs, urlsplit

import pytest

from researchgraph.model import RuleViolation
from researchgraph.project import Project
from tests.browser_helpers import viewer_page
from tests.helpers import ALICE, repository
from tests.http_server_helpers import SSEStream, loopback_server
from tests.test_cli import invoke, succeeds
from tests.test_serve_events import snapshot

pytestmark = [
    pytest.mark.browser,
    pytest.mark.http,
    pytest.mark.cli,
    pytest.mark.usefixtures("cli_cache_policy"),
]


def clock():
    """Use actual wall time at model-supported whole-second precision."""
    return datetime.now(UTC).replace(microsecond=0)


@contextmanager
def live_page(browser, owner):
    """Await a genuinely authenticated dashboard without fake handlers or eval bypass."""
    with viewer_page(browser, owner) as (page, requests, errors, _):
        page.wait_for_function(
            "() => customElements.get('research-dashboard') !== undefined && document.querySelector('research-dashboard')?.snapshot !== null"
        )
        yield page, page.locator("research-dashboard"), requests, errors


def test_dashboard_digest_stale_and_overlapping_subtree_totals_match_native(
    tmp_path, chromium_browser, index_mode
):
    now = clock()
    with Project.init(
        repository(tmp_path), "research", actor=ALICE, now=now - timedelta(days=8)
    ) as project:
        old = now - timedelta(days=8)
        past = project.propose("Task", "Old failure outside feed", actor=ALICE, now=old)
        project.claim(past["id"], actor=ALICE, now=old)
        project.fail(past["id"], "Outside window", step="old", actor=ALICE, now=old)
        first = project.propose("Hypothesis", "First parent", actor=ALICE, now=now)
        second = project.propose("Hypothesis", "Second parent", actor=ALICE, now=now)
        shared = project.propose("Task", "Shared child", actor=ALICE, now=now)
        project.link("part-of", shared["id"], first["id"], actor=ALICE, now=now)
        project.link("part-of", shared["id"], second["id"], actor=ALICE, now=now)
        project.claim(shared["id"], actor=ALICE, now=now)
        project.fail(
            shared["id"],
            "Quality failed",
            step="measure",
            error="Literal <script>window.bad=true</script>",
            check="limit",
            measured="3",
            actor=ALICE,
            now=now,
        )
        result = project.propose("Result", "Measured result", actor=ALICE, now=now)
        project.link("derived-from", result["id"], shared["id"], actor=ALICE, now=now)
        claim = project.propose("Claim", "Auditable claim", actor=ALICE, now=now)
        project.link("cites", claim["id"], result["id"], actor=ALICE, now=now)
    with (
        loopback_server(tmp_path) as owner,
        live_page(chromium_browser, owner) as (page, dashboard, requests, errors),
    ):
        data = dashboard.evaluate("element=>element.snapshot")
        calls = [
            (urlsplit(url).path, parse_qs(urlsplit(url).query)) for url in requests
        ]
        assert ("/api/ready", {}) in calls
        assert ("/api/blocked", {}) in calls
        assert ("/api/where", {"status": ['"running"']}) in calls
        assert ("/api/where", {"type": ['"Claim"']}) in calls
        assert data["ready"] == succeeds(invoke(tmp_path, "ready", now=clock()))
        assert data["blocked"] == succeeds(invoke(tmp_path, "blocked", now=clock()))
        assert data["running"] == succeeds(
            invoke(tmp_path, "where", "--status", "running", now=clock())
        )
        native_claims = succeeds(
            invoke(tmp_path, "where", "--type", "Claim", now=clock())
        )
        assert [row["node"] for row in data["claims"]] == native_claims
        evidence = succeeds(invoke(tmp_path, "evidence", claim["id"], now=clock()))
        assert {result["id"], shared["id"]} <= {
            node["id"] for node in evidence["items"]
        }
        assert data["claims"][0]["evidence"] == evidence
        dashboard.get_by_text("Claims — native where membership", exact=True).click()
        assert shared["id"] in dashboard.locator("table").inner_text()
        assert data["digest"] == succeeds(invoke(tmp_path, "digest", now=clock()))
        assert data["stale"] == succeeds(invoke(tmp_path, "stale", now=clock()))
        unique = set()
        for row in data["progress"]:
            expected = succeeds(invoke(tmp_path, "subtree", row["id"], now=clock()))
            assert set(row["members"]) == {node["id"] for node in expected["nodes"]}
            assert sum(row["counts"].values()) == len(row["members"])
            unique.update(row["members"])
        assert unique == {first["id"], second["id"], shared["id"], result["id"]}
        assert data["progressTotal"] == len(unique) == 4
        assert len(data["failures"]) == 1
        assert sum(node["type"] == "Failure" for node in data["graph"]["nodes"]) == 2
        dashboard.locator("[data-failure-feed] > summary").click()
        assert "measure" in dashboard.locator("[data-failure-feed]").inner_text()
        screenshot = tmp_path.parent / f"dashboard-{tmp_path.name}.png"
        page.screenshot(path=str(screenshot), full_page=True)
        print(f"dashboard_screenshot={screenshot}")
        assert not page.evaluate("()=>window.bad===true")
        assert dashboard.locator("script,img").count() == 0
        assert not errors


def test_failed_native_ready_read_keeps_snapshot_and_reports_error(
    tmp_path, chromium_browser, index_mode, monkeypatch
):
    now = clock()
    with Project.init(repository(tmp_path), "tasks", actor=ALICE, now=now) as project:
        project.propose("Task", "Retained snapshot", actor=ALICE, now=now)
    with (
        loopback_server(tmp_path) as owner,
        live_page(chromium_browser, owner) as (page, dashboard, _, errors),
    ):
        before = dashboard.evaluate("element=>element.snapshot")

        def unavailable(self, **context):
            raise RuleViolation("schema", "Dedicated ready read unavailable")

        monkeypatch.setattr(Project, "ready", unavailable)
        dashboard.get_by_role("button", name="Refresh monitoring", exact=True).click()
        page.wait_for_function(
            "()=>document.querySelector('research-dashboard').textContent.includes('Dedicated ready read unavailable')"
        )
        assert dashboard.evaluate("element=>element.snapshot") == before
        assert "Snapshot may be stale" in dashboard.inner_text()
        assert not errors


def test_clock_only_lease_expiry_refreshes_native_counts_without_git_write(
    tmp_path, chromium_browser, index_mode
):
    now = clock()
    with Project.init(repository(tmp_path), "tasks", actor=ALICE, now=now) as project:
        task = project.propose("Task", "Clock transition", actor=ALICE, now=now)
        project.claim(task["id"], lease_seconds=4, actor=ALICE, now=now)
    with (
        loopback_server(tmp_path) as owner,
        live_page(chromium_browser, owner) as (page, dashboard, _, errors),
    ):
        assert not dashboard.evaluate(
            "element=>element.snapshot.digest.items.find(group=>group.kind==='ready').nodes.some(node=>node.id===element.snapshot.graph.nodes.find(node=>node.title==='Clock transition').id)"
        )
        before = succeeds(invoke(tmp_path, "graph", now=clock()))
        page.wait_for_function(
            "id=>document.querySelector('research-dashboard').snapshot.digest.items.find(group=>group.kind==='ready').nodes.some(node=>node.id===id)",
            arg=task["id"],
        )
        after = succeeds(invoke(tmp_path, "graph", now=clock()))
        assert before == after
        assert dashboard.evaluate("element=>element.snapshot.digest") == succeeds(
            invoke(tmp_path, "digest", now=clock())
        )
        assert (
            dashboard.locator(f'[data-running-id="{task["id"]}"]').get_attribute(
                "data-expired"
            )
            == "true"
        )
        assert "Last index" in dashboard.inner_text()
        assert not errors


def test_actual_node_edge_events_patch_graph_before_delayed_snapshot(
    tmp_path, chromium_browser, index_mode, monkeypatch
):
    import asyncio
    import threading

    import researchgraph.serve as serving

    hold, delayed, release = threading.Event(), threading.Event(), threading.Event()
    response_type = serving.JSONResponse

    class DelayedJSON(response_type):
        """Delay delivery of native JSON bytes after the Project lock is released."""

        async def __call__(self, scope, receive, send):
            if scope["path"] == "/api/graph" and hold.is_set():
                delayed.set()
                await asyncio.to_thread(release.wait)
            await super().__call__(scope, receive, send)

    monkeypatch.setattr(serving, "JSONResponse", DelayedJSON)
    now = clock()
    with Project.init(repository(tmp_path), "tasks", actor=ALICE, now=now) as project:
        left = project.propose("Task", "Delta left", actor=ALICE, now=now)
        right = project.propose("Task", "Delta right", actor=ALICE, now=now)
    with (
        loopback_server(tmp_path) as owner,
        viewer_page(chromium_browser, owner) as (page, _, errors, _),
    ):
        page.wait_for_function(
            "()=>document.querySelector('research-graph').graph!==null"
        )
        # Establish live delivery before delaying readbacks, not just initial GETs.
        with Project.open(tmp_path) as project:
            warmup = project.propose("Task", "Stream warmup", actor=ALICE, now=clock())
        page.wait_for_function(
            "id=>document.querySelector('research-graph').graph.nodes.some(node=>node.id===id)",
            arg=warmup["id"],
        )
        hold.set()
        try:
            with Project.open(tmp_path) as project:
                project.claim(left["id"], actor=ALICE, now=clock())
                edge = project.link(
                    "blocks", left["id"], right["id"], actor=ALICE, now=clock()
                )
            page.wait_for_function(
                "args=>{const component=document.querySelector('research-graph');return component.graph.nodes.find(node=>node.id===args[0])?.status==='running' && component.graph.edges.some(edge=>edge.id===args[1])}",
                arg=[left["id"], edge["id"]],
            )
            assert delayed.is_set()
        finally:
            release.set()
        assert not errors


@pytest.mark.performance
def test_fifty_running_twenty_real_cli_writes_reach_sse_and_dom_under_three_seconds(
    tmp_path, chromium_browser, index_mode, monkeypatch, record_property
):
    now = clock()
    monkeypatch.setenv("RG_TEST_CLI_INDEX_MODE", index_mode)
    with Project.init(repository(tmp_path), "tasks", actor=ALICE, now=now) as project:
        for number in range(50):
            node = project.propose(
                "Task", f"Live background {number}", actor=ALICE, now=now
            )
            project.claim(node["id"], lease_seconds=7200, actor=ALICE, now=now)
        targets = [
            project.propose("Task", f"Live target {number}", actor=ALICE, now=now)["id"]
            for number in range(20)
        ]
        assert len(project.where(status="running", actor=ALICE, now=now)) == 50
    samples = []
    with (
        loopback_server(tmp_path) as owner,
        live_page(chromium_browser, owner) as (page, dashboard, _, errors),
    ):
        assert (
            dashboard.evaluate(
                "element=>element.snapshot.digest.items.find(group=>group.kind==='running').count"
            )
            == 50
        )
        for cookie in page.context.cookies():
            owner.client.cookies.set(cookie["name"], cookie["value"])
        _, cursor = snapshot(owner)
        with SSEStream(owner, cursor) as stream:
            for identifier in targets:
                path = tmp_path / "graph/nodes" / f"{identifier}.yaml"
                before = path.stat().st_size
                started = perf_counter()
                claimed = succeeds(invoke(tmp_path, "claim", identifier, now=clock()))
                while True:
                    event = stream.event()
                    if event["kind"] == "node" and event["id"] == identifier:
                        break
                sse_latency = perf_counter() - started
                assert event["node"] == claimed and event["previous_status"] == "active"
                page.wait_for_function(
                    "id=>document.querySelector('research-graph').shadowRoot.querySelector(`[data-node-id='${id}']`)?.dataset.status==='running' && document.querySelector('research-dashboard').snapshot.running.some(node=>node.id===id) && document.querySelector('research-dashboard').querySelector(`[data-running-id='${id}']`)!==null",
                    arg=identifier,
                )
                dom_latency = perf_counter() - started
                sample = {
                    "id": identifier,
                    "sse_seconds": sse_latency,
                    "dom_seconds": dom_latency,
                    "before_bytes": before,
                    "after_bytes": path.stat().st_size,
                }
                samples.append(sample)
                assert sample["after_bytes"] > before
                assert sse_latency < 3 and dom_latency < 3, sample
        assert dashboard.evaluate("element=>element.snapshot.digest") == succeeds(
            invoke(tmp_path, "digest", now=clock())
        )
        assert not errors
    report = {
        "initial_running": 50,
        "initial_lease_seconds": 7200,
        "writes": 20,
        "poll_seconds": 1,
        "index_mode": index_mode,
        "samples": samples,
    }
    print(json.dumps(report))
    record_property("viewer_sse_dom_latency", json.dumps(report))
    assert len(samples) == 20


def test_offline_real_refetch_preserves_open_panel_and_detaches_snapshot(
    tmp_path, chromium_browser, index_mode
):
    now = clock()
    with Project.init(repository(tmp_path), "tasks", actor=ALICE, now=now) as project:
        project.propose("Task", "Before offline", actor=ALICE, now=now)
    with (
        loopback_server(tmp_path) as owner,
        live_page(chromium_browser, owner) as (page, dashboard, _, errors),
    ):
        activity = dashboard.get_by_text(
            "Activity — native running records", exact=True
        )
        activity.click()
        page.context.set_offline(True)
        page.wait_for_function(
            "()=>document.querySelector('research-dashboard').textContent.includes('Snapshot may be stale')"
        )
        with Project.open(tmp_path) as project:
            new = project.propose(
                "Task", "Observed while offline", actor=ALICE, now=clock()
            )
        for cookie in page.context.cookies():
            owner.client.cookies.set(cookie["name"], cookie["value"])
        assert owner.client.get("/api/graph").status_code == 200
        page.context.set_offline(False)
        page.wait_for_function(
            "id=>document.querySelector('research-dashboard').snapshot?.graph.nodes.some(node=>node.id===id)",
            arg=new["id"],
        )
        assert activity.locator("..").get_attribute("open") is not None
        native = succeeds(invoke(tmp_path, "digest", now=clock()))
        dashboard.evaluate(
            "element=>{const data=element.snapshot;data.digest.items[0].count=999;data.graph.nodes.length=0;}"
        )
        assert dashboard.evaluate("element=>element.snapshot.digest") == native
        assert dashboard.evaluate("element=>element.snapshot.graph.nodes.length") == 2
        assert not errors


def test_event_log_is_bounded_literal_native_envelopes_and_bad_source_fails_closed(
    tmp_path, chromium_browser
):
    now = clock()
    with Project.init(repository(tmp_path), "tasks", actor=ALICE, now=now):
        pass
    with (
        loopback_server(tmp_path) as owner,
        live_page(chromium_browser, owner) as (page, dashboard, _, errors),
    ):
        with Project.open(tmp_path) as project:
            for number in range(110):
                project.propose(
                    "Task",
                    f"Literal <script>window.bad=true</script> {number}",
                    actor=ALICE,
                    now=clock(),
                    commit=False,
                )
        page.wait_for_function(
            "()=>document.querySelector('research-dashboard').snapshot.graph.nodes.length===110"
        )
        log = (
            dashboard.get_by_text("Event log — last 100 received envelopes", exact=True)
            .locator("..")
            .locator("pre")
            .text_content()
        )
        assert len(log.splitlines()) == 100
        assert "<script>window.bad=true</script>" in log
        assert dashboard.locator("script,img").count() == 0
        assert not page.evaluate("()=>window.bad===true")
        dashboard.evaluate("element=>element.setAttribute('src','/not-an-api')")
        page.wait_for_function(
            "()=>document.querySelector('research-dashboard').textContent.includes('404')"
        )
        assert dashboard.evaluate("element=>element.snapshot") is None
        assert not errors


def test_actual_record_disappearance_reset_refetches_all_native_snapshots(
    tmp_path, chromium_browser, index_mode
):
    now = clock()
    with Project.init(repository(tmp_path), "tasks", actor=ALICE, now=now) as project:
        project.propose("Task", "Remains", actor=ALICE, now=now)
        removed = project.propose("Task", "Raw disappearance", actor=ALICE, now=now)
    with (
        loopback_server(tmp_path) as owner,
        live_page(chromium_browser, owner) as (page, dashboard, requests, errors),
    ):
        page.locator(f'research-graph [data-node-id="{removed["id"]}"]').click()
        page.wait_for_function(
            "id=>document.querySelector('research-inspector').node?.id===id",
            arg=removed["id"],
        )
        baseline = len(requests)
        (tmp_path / "graph/nodes" / f"{removed['id']}.yaml").unlink()
        page.wait_for_function(
            "id=>{const board=document.querySelector('research-dashboard').snapshot;const graph=document.querySelector('research-graph').graph;return board && graph && !board.graph.nodes.some(node=>node.id===id) && !graph.nodes.some(node=>node.id===id)}",
            arg=removed["id"],
        )
        assert dashboard.evaluate("element=>element.snapshot.graph") == succeeds(
            invoke(tmp_path, "graph", now=clock())
        )
        calls = [
            (urlsplit(url).path, parse_qs(urlsplit(url).query))
            for url in requests[baseline:]
        ]
        assert ("/api/ready", {}) in calls
        assert ("/api/blocked", {}) in calls
        assert ("/api/where", {"status": ['"running"']}) in calls
        for operation in ("ready", "blocked"):
            assert dashboard.evaluate(
                f"element=>element.snapshot.{operation}"
            ) == succeeds(invoke(tmp_path, operation, now=clock()))
        page.wait_for_function(
            "()=>document.querySelector('research-inspector').readOnly===null"
        )
        assert (
            "not_found"
            in page.locator("research-inspector").get_by_role("status").inner_text()
        )
        assert not errors


def test_live_queue_readback_preserves_unsubmitted_human_review_draft(
    tmp_path, chromium_browser
):
    now = clock()
    with Project.init(
        repository(tmp_path), "research", actor=ALICE, now=now
    ) as project:
        hypothesis = project.propose(
            "Hypothesis", "Keep reviewer draft", actor=ALICE, now=now
        )
    with (
        loopback_server(tmp_path) as owner,
        live_page(chromium_browser, owner) as (page, _, _, errors),
    ):
        queue = page.locator("research-review-queue")
        note = queue.locator(f'[data-review-id="{hypothesis["id"]}"]').get_by_label(
            "Approval note", exact=True
        )
        note.fill("Human draft must not disappear during live index updates")
        queue.evaluate("element=>element.reload()")
        assert (
            note.input_value()
            == "Human draft must not disappear during live index updates"
        )
        assert note.evaluate("element=>element.getRootNode().activeElement===element")
        assert not errors
