"""Actual Chromium graph behavior, layout invariants and credential hygiene."""

from datetime import timedelta
from functools import partial
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from researchgraph.model import Actor
from researchgraph.project import Project
from tests.browser_helpers import authoritative_bytes, viewer_page
from tests.helpers import ALICE, NOW, repository
from tests.http_server_helpers import loopback_server
from tests.test_operations import discard_index

pytestmark = [pytest.mark.browser, pytest.mark.http]
TITLES = st.lists(
    st.text(st.characters(blacklist_categories=("Cs", "Cc")), min_size=1, max_size=20),
    min_size=1,
    max_size=4,
)


def populated(root, titles):
    """Build a valid mixed-edge cycle and independent retired/history controls."""
    with Project.init(repository(root), "research", actor=ALICE, now=NOW) as project:
        question = project.propose(
            "Question", "Which explanation?", actor=ALICE, now=NOW
        )
        hypothesis = project.propose(
            "Hypothesis",
            "Candidate",
            edges=[{"type": "answers", "to": question["id"]}],
            actor=ALICE,
            now=NOW,
        )
        prediction = project.propose(
            "Prediction",
            "Discriminating observation",
            edges=[
                {
                    "type": "tests",
                    "to": hypothesis["id"],
                    "fields": {"expected": "Candidate behavior"},
                }
            ],
            actor=ALICE,
            now=NOW,
        )
        project.link(
            "derived-from", hypothesis["id"], prediction["id"], actor=ALICE, now=NOW
        )
        old = project.propose("Task", "Earlier plan", actor=ALICE, now=NOW)
        replacement = project.supersede(
            old["id"], title="Current plan", actor=ALICE, now=NOW
        )
        archived = project.propose(
            "Task", "Archived independent task", actor=ALICE, now=NOW
        )
        project.retire(archived["id"], "not pursued", actor=ALICE, now=NOW)
        for title in titles:
            project.propose("Task", title, actor=ALICE, now=NOW)
        bob = project.propose(
            "Task",
            "Later author",
            actor=Actor("human", "bob", "bob@example.test"),
            now=NOW + timedelta(days=1),
        )
        return (
            project.graph(actor=ALICE, now=NOW + timedelta(days=1)),
            old,
            replacement,
            bob,
        )


@settings(max_examples=5, deadline=None)
@given(titles=TITLES)
def test_strategy_built_project_two_independent_components_and_mixed_cycle(
    chromium_browser, titles
):
    for mode in ("kept", "deleted"):
        with TemporaryDirectory(prefix="rg-browser-graph-") as directory:
            root = Path(directory)
            graph, old, replacement, _ = populated(root, titles)
            with Project.open(root) as project:
                discard_index(project, mode)
            before = authoritative_bytes(root)
            with (
                loopback_server(root) as owner,
                viewer_page(chromium_browser, owner) as (page, requests, errors, token),
            ):
                first = page.locator("research-graph").first
                assert first.locator("[data-node-id]").count() == sum(
                    node["status"] != "retired" for node in graph["nodes"]
                )
                page.evaluate("""() => {
                    const second = document.createElement('research-graph');
                    second.setAttribute('src', '/api'); document.body.append(second);
                    window.selections = [];
                    document.addEventListener('node-select', event => window.selections.push(event.detail.id));
                }""")
                page.wait_for_function(
                    "() => document.querySelectorAll('research-graph')[1].graph !== null"
                )
                second = page.locator("research-graph").nth(1)
                original_second = second.locator("[data-node-id]").count()
                first.get_by_label("Show retired", exact=True).check()
                assert (
                    first.locator("[data-node-id]").count() == len(graph["nodes"]) - 1
                )
                assert not first.locator(f'[data-node-id="{old["id"]}"]').count()
                first.get_by_label("Expand history", exact=True).check()
                assert first.locator("[data-node-id]").count() == len(graph["nodes"])
                assert first.locator("[data-edge-id]").count() == len(graph["edges"])
                for node in graph["nodes"]:
                    first.locator(f'[data-node-id="{node["id"]}"]').click()
                    assert page.evaluate("window.selections.at(-1)") == node["id"]
                assert second.evaluate("element => element.selectedId") is None
                first.get_by_label("Type", exact=True).select_option("Task")
                assert all(
                    value == "Task"
                    for value in first.locator("[data-node-id]").evaluate_all(
                        "cards => cards.map(card => card.dataset.type)"
                    )
                )
                assert second.locator("[data-node-id]").count() == original_second
                ranks = first.evaluate(
                    "element => Object.fromEntries(element.layout.nodes.map(node => [node.id,node.rank]))"
                )
                first.get_by_label("Expand history", exact=True).uncheck()
                assert (
                    first.evaluate(
                        "element => Object.fromEntries(element.layout.nodes.map(node => [node.id,node.rank]))"
                    )
                    == ranks
                )
                assert (
                    first.locator(f'[data-node-id="{replacement["id"]}"]').count() == 1
                )
                first.locator(f'[data-node-id="{replacement["id"]}"]').focus()
                page.keyboard.press("Enter")
                assert (
                    first.evaluate("element => element.selectedId") == replacement["id"]
                )
                assert token not in page.url and all(
                    token not in url for url in requests
                )
                storage = page.evaluate("""async () => {
                    const attributes = [];
                    function walk(root) { for (const element of root.querySelectorAll('*')) {
                        attributes.push(...Array.from(element.attributes, attr => attr.value));
                        if (element.shadowRoot) walk(element.shadowRoot);
                    }}
                    walk(document);
                    return {attributes, local: localStorage.length, session: sessionStorage.length,
                        databases: await indexedDB.databases(), caches: await caches.keys(), cookie: document.cookie};
                }""")
                assert all(token not in value for value in storage.pop("attributes"))
                assert storage == {
                    "local": 0,
                    "session": 0,
                    "databases": [],
                    "caches": [],
                    "cookie": "",
                }
                assert not errors
            assert authoritative_bytes(root) == before


def test_metadata_columns_filters_viewport_and_text_safety(
    tmp_path, chromium_browser, index_mode
):
    graph, _, _, bob = populated(tmp_path, ["<img src=x onerror=window.bad=true>"])
    with Project.open(tmp_path) as project:
        profile = project.profile_info(actor=ALICE, now=NOW)
        discard_index(project, index_mode)
    before = authoritative_bytes(tmp_path)
    with (
        loopback_server(tmp_path) as owner,
        viewer_page(chromium_browser, owner) as (page, _, errors, _),
    ):
        component = page.locator("research-graph")
        assert component.locator("[data-column-type]").evaluate_all(
            "columns => columns.map(column => column.dataset.columnType)"
        ) == [entry["name"] for entry in profile["node_types"]]
        assert page.evaluate("window.bad") is None
        assert component.locator("img").count() == 0
        component.get_by_label("Created from", exact=True).fill(
            (NOW + timedelta(days=1)).date().isoformat()
        )
        assert component.locator("[data-node-id]").evaluate_all(
            "cards => cards.map(card => card.dataset.nodeId)"
        ) == [bob["id"]]
        component.get_by_label("Created from", exact=True).fill("")
        component.get_by_label("Created through", exact=True).fill(
            NOW.date().isoformat()
        )
        assert component.locator(f'[data-node-id="{bob["id"]}"]').count() == 0
        component.get_by_label("Created through", exact=True).fill("")
        author = component.get_by_label("Author", exact=True)
        value = author.locator("option").evaluate_all(
            "options => options.find(option => option.textContent.includes('bob')).value"
        )
        author.select_option(value)
        assert component.locator("[data-node-id]").count() == 1
        author.select_option("")
        component.get_by_label("Show retired", exact=True).check()
        component.get_by_label("Expand history", exact=True).check()
        component.get_by_label("Status", exact=True).select_option("retired")
        assert component.locator("[data-node-id]").count() == sum(
            node["status"] == "retired" for node in graph["nodes"]
        )
        component.get_by_label("Status", exact=True).select_option("")
        initial = component.evaluate("element => element.zoom")
        component.get_by_role("button", name="Zoom in", exact=True).click()
        assert component.evaluate("element => element.zoom") > initial
        component.get_by_role("button", name="Reset view", exact=True).click()
        assert component.evaluate("element => element.zoom") == initial
        canvas = component.locator("[data-viewport]")
        canvas.scroll_into_view_if_needed()
        canvas.evaluate("element => {element.scrollLeft=100; element.scrollTop=50;}")
        start = canvas.evaluate("element => [element.scrollLeft,element.scrollTop]")
        box = canvas.bounding_box()
        page.mouse.move(box["x"] + box["width"] - 20, box["y"] + box["height"] - 20)
        page.mouse.down()
        page.mouse.move(box["x"] + box["width"] - 80, box["y"] + box["height"] - 70)
        page.mouse.up()
        assert (
            canvas.evaluate("element => [element.scrollLeft,element.scrollTop]")
            != start
        )
        screenshot = tmp_path.parent / f"{tmp_path.name}.png"
        page.screenshot(path=str(screenshot), full_page=True)
        print(f"graph_screenshot={screenshot}")
        assert not errors
    assert authoritative_bytes(tmp_path) == before


def test_custom_unused_types_conditions_roles_and_claimability(
    tmp_path, chromium_browser, index_mode
):
    with Project.init(
        repository(tmp_path), "test-conditional", actor=ALICE, now=NOW
    ) as project:
        project.propose(
            "Task", "Review me", fields={"review": True}, actor=ALICE, now=NOW
        )
        profile = project.profile_info(actor=ALICE, now=NOW)
        discard_index(project, index_mode)
    with (
        loopback_server(tmp_path) as owner,
        viewer_page(chromium_browser, owner) as (page, _, errors, _),
    ):
        component = page.locator("research-graph")
        assert component.evaluate("element => element.profile") == profile
        columns = component.locator("[data-column-type]")
        assert columns.evaluate_all(
            "columns => columns.map(column => column.dataset.columnType)"
        ) == [entry["name"] for entry in profile["node_types"]]
        for definition in profile["node_types"]:
            column = component.locator(f'[data-column-type="{definition["name"]}"]')
            assert (
                column.get_attribute("data-claimable")
                == str(definition["claimable"]).lower()
            )
        text = component.locator(".metadata").inner_text()
        assert all(role in text for role in profile["roles"])
        assert "review" in text and "true" in text
        assert not errors


def test_layout_i1_only_invalid_cycle_fallback_and_non_mutation(
    tmp_path, chromium_browser
):
    populated(tmp_path, ["One task"])
    with (
        loopback_server(tmp_path) as owner,
        viewer_page(chromium_browser, owner) as (page, _, errors, _),
    ):
        output = page.evaluate("""async () => {
            const {layoutGraph} = await import('/assets/research-graph.js');
            const nodes = ['b','a','c'].map(id => ({id,created:'2026-10-19T00:00:00Z',type:'Entry'}));
            const edge = (id,type,from,to,retired=false) => ({id,type,from,to,retired});
            const unconstrained = {nodes,edges:[edge('e1','tests','a','b'),edge('e2','addresses','b','a'),edge('e3','blocks','b','c',true)]};
            const before = JSON.stringify(unconstrained);
            const independent = layoutGraph(unconstrained);
            const cyclic = layoutGraph({nodes,edges:[edge('e1','blocks','a','b'),edge('e2','cites','b','a')]});
            const constraints = Object.fromEntries(['blocks','derived-from','supersedes','cites','part-of'].map(type => {
                const ranked = layoutGraph({nodes,edges:[edge('edge',type,'a','b')]});
                const retired = layoutGraph({nodes,edges:[edge('edge',type,'a','b',true)]});
                return [type, {ranked:Object.fromEntries(ranked.nodes.map(node => [node.id,node.rank])),retired:retired.nodes.map(node=>node.rank)}];
            }));
            return {independent, cyclic, constraints, unchanged:JSON.stringify(unconstrained)===before};
        }""")
        assert output["unchanged"]
        assert {node["rank"] for node in output["independent"]["nodes"]} == {0}
        assert [node["id"] for node in output["cyclic"]["nodes"]] == ["c", "a", "b"]
        assert all(isinstance(node["rank"], int) for node in output["cyclic"]["nodes"])
        assert len(output["independent"]["edges"]) == 3
        for item in output["constraints"].values():
            assert item["ranked"]["b"] > item["ranked"]["a"]
            assert set(item["retired"]) == {0}
        assert not errors


def test_registered_html_svg_download_without_origin_execution_or_referrer(
    tmp_path, chromium_browser, index_mode
):
    payloads = {
        "unsafe.html": b"<script>window.__artifact_executed=true</script>",
        "unsafe.svg": b'<svg xmlns="http://www.w3.org/2000/svg"><script>window.__artifact_executed=true</script></svg>',
    }
    with Project.init(repository(tmp_path), "tasks", actor=ALICE, now=NOW) as project:
        node = project.propose("Task", "Inert evidence", actor=ALICE, now=NOW)
        paths = []
        for name, payload in payloads.items():
            path = tmp_path / "artifacts" / node["id"] / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
            paths.append(path.relative_to(tmp_path).as_posix())
        project.attach(node["id"], paths, actor=ALICE, now=NOW)
        discard_index(project, index_mode)
    before = authoritative_bytes(tmp_path)
    with (
        loopback_server(tmp_path) as owner,
        viewer_page(chromium_browser, owner) as (page, _, errors, _),
    ):
        page_url = page.url
        artifact_requests = []
        page.on(
            "request",
            lambda request: (
                artifact_requests.append(request)
                if "/artifacts/" in request.url
                else None
            ),
        )
        for name, payload in payloads.items():
            page.evaluate(
                """href => {
                document.querySelector('#artifact-probe')?.remove();
                const link = document.createElement('a'); link.id='artifact-probe';
                link.href=href; link.textContent='Download evidence'; document.body.append(link);
            }""",
                f"/artifacts/{node['id']}/{name}",
            )
            with page.expect_download() as downloaded:
                page.locator("#artifact-probe").click()
            assert downloaded.value.suggested_filename == name
            assert Path(downloaded.value.path()).read_bytes() == payload
            assert page.url == page_url
            assert page.evaluate("() => Boolean(window.__artifact_executed)") is False
        assert len(artifact_requests) == 2
        assert all("referer" not in request.headers for request in artifact_requests)
        assert all(
            cookie["httpOnly"]
            and cookie["sameSite"] == "Strict"
            and cookie["expires"] == -1
            for cookie in page.context.cookies()
        )
        assert not errors
    assert authoritative_bytes(tmp_path) == before


def test_real_expired_cursor_reset_refetches_profile_and_preserves_selection(
    tmp_path, chromium_browser, monkeypatch
):
    import researchgraph.serve as serving
    from researchgraph.events import ChangeSource

    monkeypatch.setattr(serving, "ChangeSource", partial(ChangeSource, retention=1))
    _, _, replacement, _ = populated(tmp_path, ["Existing"])
    with (
        loopback_server(tmp_path) as owner,
        viewer_page(chromium_browser, owner) as (page, requests, errors, _),
    ):
        component = page.locator("research-graph")
        assert page.evaluate("() => navigator.onLine") is True, (
            "Offline/reconnect scenario requires an initially connected browser "
            "with actual offline and online transitions; loopback-only namespaces "
            "may leave navigator.onLine false."
        )
        page.wait_for_function(
            "() => document.querySelector('research-graph').shadowRoot.querySelector('[role=status]').textContent === 'Snapshot refreshed.'"
        )
        component.locator(f'[data-node-id="{replacement["id"]}"]').click()
        profiles = sum(url.endswith("/api/profile") for url in requests)
        cookies = page.context.cookies()
        page.context.set_offline(True)
        with Project.open(tmp_path) as project:
            new = project.propose(
                "Task",
                "Observed after offline",
                actor=ALICE,
                now=NOW + timedelta(days=2),
            )
        for cookie in cookies:
            owner.client.cookies.set(cookie["name"], cookie["value"])
        assert owner.client.get("/api/graph").status_code == 200
        page.context.set_offline(False)
        try:
            component.locator(f'[data-node-id="{new["id"]}"]').wait_for()
        except Exception:
            print(
                "reset_diagnostics",
                component.evaluate(
                    "element => ({status:element.shadowRoot.querySelector('[role=status]').textContent,ids:element.graph?.nodes.map(node=>node.id)})"
                ),
                requests,
                errors,
            )
            raise
        assert sum(url.endswith("/api/profile") for url in requests) > profiles
        assert component.evaluate("element => element.selectedId") == replacement["id"]
        assert not errors


def test_real_failed_api_prefix_is_visible_not_successful_empty_graph(
    tmp_path, chromium_browser
):
    populated(tmp_path, ["Existing"])
    before = authoritative_bytes(tmp_path)
    with (
        loopback_server(tmp_path) as owner,
        viewer_page(chromium_browser, owner) as (page, _, errors, _),
    ):
        component = page.locator("research-graph")
        component.evaluate("element => element.setAttribute('src', '/not-the-api')")
        page.wait_for_function(
            "() => document.querySelector('research-graph').shadowRoot.querySelector('[role=status]').textContent.startsWith('Unable to load graph:')"
        )
        assert component.evaluate("element => element.graph") is None
        assert component.locator("[data-node-id]").count() == 0
        assert "404" in component.get_by_role("status").inner_text()
        assert not errors
    assert authoritative_bytes(tmp_path) == before
