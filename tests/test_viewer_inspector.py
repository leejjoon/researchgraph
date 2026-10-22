"""Real browser inspector reads, deliberate actions and persisted failure outcomes."""

import json
from contextlib import contextmanager
from copy import deepcopy
from datetime import timedelta

import pytest

from researchgraph.model import Actor
from researchgraph.profiles import Profile
from researchgraph.project import Project
from tests.browser_helpers import authoritative_bytes, viewer_page
from tests.helpers import ALICE, NOW, git, repository
from tests.http_server_helpers import loopback_server
from tests.test_cli import invoke, succeeds

pytestmark = [
    pytest.mark.browser,
    pytest.mark.http,
    pytest.mark.cli,
    pytest.mark.usefixtures("cli_cache_policy"),
]


def inspector_project(root, *, profile="research"):
    """Seed actual registered fields, artifact bytes and typed incident records."""
    with Project.init(repository(root), profile, actor=ALICE, now=NOW) as project:
        task = project.propose("Task", "Failed task", actor=ALICE, now=NOW)
        active = project.propose("Task", "Untouched task", actor=ALICE, now=NOW)
        if profile == "tasks":
            return {"task": task, "active": active}
        bot = Actor("agent", "runner")
        project.claim(task["id"], actor=bot, now=NOW)
        project.fail(
            task["id"],
            "Quality failed",
            step="measure",
            check="limit",
            measured="3",
            error="<img src=x onerror=window.bad=true>",
            actor=bot,
            now=NOW,
        )
        question = project.propose("Question", "Question", actor=ALICE, now=NOW)
        hypothesis = project.propose(
            "Hypothesis",
            "<script>window.bad=true</script>",
            body="Hostile <b>body</b>",
            actor=ALICE,
            now=NOW,
        )
        alternative = project.propose("Hypothesis", "Alternative", actor=ALICE, now=NOW)
        for node in (hypothesis, alternative):
            project.link("answers", node["id"], question["id"], actor=ALICE, now=NOW)
        prediction = project.propose("Prediction", "Prediction", actor=ALICE, now=NOW)
        project.link(
            "tests",
            prediction["id"],
            hypothesis["id"],
            fields={"expected": "3"},
            actor=ALICE,
            now=NOW,
        )
        project.link(
            "tests",
            prediction["id"],
            alternative["id"],
            fields={"expected": "3"},
            actor=ALICE,
            now=NOW,
        )
        result = project.propose("Result", "Result", actor=ALICE, now=NOW)
        artifact = f"artifacts/{result['id']}/report.html"
        destination = root / artifact
        destination.parent.mkdir(parents=True)
        destination.write_text("<script>window.bad=true</script>")
        project.attach(result["id"], [artifact], actor=ALICE, now=NOW)
        project.link(
            "pins",
            hypothesis["id"],
            result["id"],
            fields={"artifact": artifact},
            actor=ALICE,
            now=NOW,
        )
        project.link("duplicate-of", task["id"], active["id"], actor=ALICE, now=NOW)
        project.link("relates-to", hypothesis["id"], task["id"], actor=ALICE, now=NOW)
        instruction = project.instruct(
            hypothesis["id"], "Earlier instruction <b>literal</b>", actor=ALICE, now=NOW
        )
        note = project.propose(
            "Note", "Annotation", body="Literal <img src=x>", actor=ALICE, now=NOW
        )
        project.link("about", note["id"], hypothesis["id"], actor=ALICE, now=NOW)
        claim = project.propose("Claim", "Claim", actor=ALICE, now=NOW)
        project.link("cites", claim["id"], result["id"], actor=ALICE, now=NOW)
        return {
            "task": task,
            "active": active,
            "hypothesis": hypothesis,
            "alternative": alternative,
            "prediction": prediction,
            "result": result,
            "artifact": artifact,
            "claim": claim,
            "instruction": instruction,
        }


@contextmanager
def selected_inspector(browser, owner, identifier):
    """Select through the actual host event bridge and await real inspector data."""
    from playwright.sync_api import Error as PlaywrightError

    with viewer_page(browser, owner) as (page, requests, errors, _):
        page.wait_for_function(
            "() => customElements.get('research-inspector') !== undefined"
        )
        page.evaluate(
            """() => {
                const graph = document.querySelector('research-graph');
                const inspector = document.querySelector('research-inspector');
                const started = performance.now();
                const events = [];
                const record = (event, detail = {}) => {
                    events.push({event, elapsed_ms: performance.now() - started,
                        selected: inspector?.getAttribute('node-id'),
                        loaded: inspector?.node?.id ?? null, ...detail});
                    if (events.length > 32) events.shift();
                };
                const selection = event => record(event.type, {
                    source: event.target.localName, id: event.detail?.id ?? null});
                const pointer = event => {
                    const path = event.composedPath();
                    const node = path.find(item =>
                        item.getAttribute?.('data-node-id'));
                    if (path.includes(graph)) record(event.type, {
                        id: node?.getAttribute('data-node-id') ?? null,
                        target: event.target.localName, trusted: event.isTrusted,
                        x: event.clientX, y: event.clientY});
                };
                const types = ['node-select', 'snapshot-reset', 'snapshot-updated'];
                const pointerTypes = ['pointerdown', 'pointerup', 'click'];
                for (const type of types) document.addEventListener(type, selection, true);
                for (const type of pointerTypes) document.addEventListener(type, pointer, true);
                record('before-click');
                window.__inspectorSelectionEvidence = {
                    snapshot: () => ({events,
                        sameGraph: graph === document.querySelector('research-graph'),
                        sameInspector: inspector === document.querySelector('research-inspector'),
                        connected: inspector?.isConnected ?? false,
                        selected: inspector?.getAttribute('node-id'),
                        loaded: inspector?.node?.id ?? null,
                        readOnly: inspector?.readOnly,
                        busy: inspector?.busy,
                        status: inspector?.shadowRoot.querySelector('[role="status"]')?.textContent}),
                    stop: () => {
                        for (const type of types) document.removeEventListener(type, selection, true);
                        for (const type of pointerTypes) document.removeEventListener(type, pointer, true);
                        delete window.__inspectorSelectionEvidence;
                    }
                };
            }"""
        )
        try:
            page.locator(f'research-graph [data-node-id="{identifier}"]').click()
            page.wait_for_function(
                "id => document.querySelector('research-inspector').node?.id === id",
                arg=identifier,
            )
        except BaseException as error:
            try:
                evidence = page.evaluate(
                    "() => window.__inspectorSelectionEvidence.snapshot()"
                )
            except PlaywrightError as diagnostic_error:
                evidence = {"unavailable": str(diagnostic_error)}
            error.add_note(
                f"Pre-action selection evidence for {identifier}: {evidence!r}"
            )
            raise
        page.evaluate("() => window.__inspectorSelectionEvidence.stop()")
        yield page, page.locator("#viewer > research-inspector"), requests, errors


def test_initial_selection_failure_retains_native_and_pointer_diagnostics(
    tmp_path, chromium_browser, monkeypatch
):
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

    from researchgraph.model import RuleViolation

    seeded = inspector_project(tmp_path)
    identifier = seeded["active"]["id"]
    message = "Dedicated initial native show unavailable"

    def unavailable(self, *args, **context):
        raise RuleViolation("schema", message)

    monkeypatch.setattr(Project, "show", unavailable)
    with (
        loopback_server(tmp_path) as owner,
        pytest.raises(PlaywrightTimeoutError) as raised,
        selected_inspector(chromium_browser, owner, identifier),
    ):
        pytest.fail("A refused native selection must not publish a node")
    notes = "\n".join(raised.value.__notes__)
    assert "Pre-action selection evidence" in notes
    assert "node-select" in notes
    assert identifier in notes
    assert message in notes
    assert "Bounded native GET lifecycle" in notes
    assert "'/api/show'" in notes
    assert "'status': 422" in notes
    assert "'id':" in notes


def choose_action(inspector, name, arguments):
    """Enter exact logical values, preserving strings and explicit JSON null."""
    inspector.get_by_role("button", name=name, exact=True).click()
    if name == "Re-run with instruction":
        assert inspector.get_by_label("Inherit edges", exact=True).is_checked()
        assert not inspector.get_by_label("Set Inherit edges", exact=True).is_checked()
    for key, value in arguments.items():
        label = key.replace("_", " ").capitalize()
        control = inspector.get_by_label(label, exact=True)
        if isinstance(value, bool):
            control.set_checked(value)
        else:
            control.fill(value if isinstance(value, str) else json.dumps(value))
    inspector.get_by_role("button", name=f"Apply {name}", exact=True).click()


def wait_action(page):
    """Await the actual mutation result plus its persisted-state read refresh."""
    page.wait_for_function(
        "() => document.querySelector('research-inspector').busy === false && document.querySelector('research-inspector').lastAction !== null"
    )


def test_inspector_reads_exact_native_views_fields_edges_and_registered_download(
    tmp_path, chromium_browser, index_mode
):
    from playwright.sync_api import expect

    seeded = inspector_project(tmp_path)
    before = authoritative_bytes(tmp_path)
    with (
        loopback_server(tmp_path) as owner,
        selected_inspector(chromium_browser, owner, seeded["hypothesis"]["id"]) as (
            page,
            inspector,
            _,
            errors,
        ),
    ):
        actual = inspector.evaluate(
            "element => ({node:element.node,related:element.related})"
        )
        with Project.open(tmp_path) as project:
            assert actual["node"] == project.show(
                seeded["hypothesis"]["id"], actor=ALICE, now=NOW
            )
            for name, key in (
                ("history", "id"),
                ("evidence", "claim"),
                ("weakest", "claim"),
                ("alternatives", "hypothesis"),
                ("predictions", "hypothesis"),
                ("notes", "id"),
                ("subtree", "id"),
            ):
                assert actual["related"][name] == getattr(project, name)(
                    **{key: seeded["hypothesis"]["id"]}, actor=ALICE, now=NOW
                )
        text = inspector.locator(".inspector-shell").inner_text()
        assert "<script>window.bad=true</script>" in text
        assert "relates-to" in text and "Earlier instruction <b>literal</b>" in text
        assert inspector.locator("script, img").count() == 0
        assert not page.evaluate("() => window.bad === true")
        inspector.evaluate("(element,id) => element.select(id)", seeded["task"]["id"])
        page.wait_for_function(
            "id => document.querySelector('research-inspector').node?.id === id",
            arg=seeded["task"]["id"],
        )
        expect(
            inspector.get_by_role("button", name="Re-run with instruction", exact=True)
        ).to_be_in_viewport()
        text = inspector.locator('[data-section="incidents"]').inner_text()
        for value in (
            "duplicate-of",
            "measure",
            "limit",
            "3",
            "<img src=x onerror=window.bad=true>",
        ):
            assert value in text
        screenshot = tmp_path.parent / f"inspector-{tmp_path.name}.png"
        page.screenshot(path=str(screenshot), full_page=True)
        print(f"inspector_screenshot={screenshot}")
        inspector.evaluate(
            "(element,id) => element.select(id)", seeded["prediction"]["id"]
        )
        page.wait_for_function(
            "id => document.querySelector('research-inspector').node?.id === id",
            arg=seeded["prediction"]["id"],
        )
        assert (
            "non_discriminating"
            in inspector.locator('[data-section="warnings"]').inner_text()
        )
        inspector.evaluate("(element,id) => element.select(id)", seeded["result"]["id"])
        with page.expect_download() as download:
            inspector.get_by_role("link", name=seeded["artifact"], exact=True).click()
        assert download.value.suggested_filename == "report.html"
        assert not errors
    assert authoritative_bytes(tmp_path) == before


@pytest.mark.parametrize(
    "name,operation,target,arguments",
    [
        ("Approve", "approve", "hypothesis", {"note": "Reviewed literal <b>note</b>"}),
        ("Reject", "reject", "hypothesis", {"reason": "Send back"}),
        ("Instruct", "instruct", "hypothesis", {"text": "Review the procedure"}),
        (
            "Re-run with instruction",
            "supersede",
            "task",
            {"instruction": "Repeat with the new check"},
        ),
        ("Retire", "retire", "active", {"reason": "Not needed"}),
        ("Budget", "budget", "active", {"tokens": 10, "compute_seconds": 4}),
        ("Tag", "tag", "active", {"add": ["review"], "priority": 0}),
        ("Expand", "expand", "hypothesis", {"hint": "One more discriminating check"}),
        ("Pin link", "link", "alternative", {}),
    ],
)
def test_each_deliberate_action_exact_payload_native_result_and_installed_history(
    tmp_path, chromium_browser, name, operation, target, arguments, index_mode
):
    seeded = inspector_project(tmp_path)
    identifier = seeded[target]["id"]
    arguments = deepcopy(arguments)
    if name == "Pin link":
        arguments = {
            "to": seeded["result"]["id"],
            "fields": {"artifact": seeded["artifact"]},
        }
    posts = []
    with (
        loopback_server(tmp_path) as owner,
        selected_inspector(chromium_browser, owner, identifier) as (
            page,
            inspector,
            _,
            errors,
        ),
    ):
        page.on(
            "request",
            lambda request: (
                posts.append(request.post_data_json)
                if request.method == "POST"
                and request.url.endswith(f"/api/{operation}")
                else None
            ),
        )
        choose_action(inspector, name, arguments)
        wait_action(page)
        assert len(posts) == 1
        expected = (
            {"nodes": [identifier]}
            if name == "Expand"
            else {"from": identifier, "type": "pins"}
            if name == "Pin link"
            else {"node": identifier}
        )
        assert posts[0] == {**expected, **arguments}
        action = inspector.evaluate("element => element.lastAction")
        assert action["error"] is None
        result = action["result"]
        with Project.open(tmp_path) as project:
            shown = project.show(identifier, actor=ALICE, now=NOW + timedelta(days=2))
            if name == "Approve":
                assert shown["status"] == "active"
                assert shown["approvals"][-1]["note"] == arguments["note"]
                assert (
                    succeeds(invoke(tmp_path, "show", identifier))["status"] == "active"
                )
            elif name in {"Reject", "Retire"}:
                assert shown["status"] == "retired"
                assert shown["edges"] == []
            elif name == "Instruct":
                assert result["id"] in [item["id"] for item in shown["instructions"]]
            elif name == "Re-run with instruction":
                assert shown["status"] == "retired" and result["id"] != identifier
                replacement = project.show(
                    result["id"], actor=ALICE, now=NOW + timedelta(days=2)
                )
                assert (
                    replacement["instructions"][0]["body"] == arguments["instruction"]
                )
                assert result["id"] in json.dumps(
                    succeeds(invoke(tmp_path, "history", identifier))
                )
            elif name == "Budget":
                assert shown["budget"] == arguments
            elif name == "Tag":
                assert shown["tags"] == ["review"] and shown["priority"] == 0
            elif name == "Expand":
                assert result["fields"]["targets"] == [identifier]
                assert result["status"] == "active"
                assert result["lease"] is None
            elif name == "Pin link":
                assert result["id"] in [item["id"] for item in shown["pins"]]
        assert not errors


def test_read_only_hides_actions_and_server_still_refuses_direct_write(
    tmp_path, chromium_browser
):
    seeded = inspector_project(tmp_path)
    before = authoritative_bytes(tmp_path)
    with (
        loopback_server(tmp_path, read_only=True) as owner,
        selected_inspector(chromium_browser, owner, seeded["hypothesis"]["id"]) as (
            page,
            inspector,
            _,
            errors,
        ),
    ):
        assert inspector.evaluate("element => element.readOnly") is True
        assert inspector.locator("[data-action]").count() == 0
        refused = page.evaluate(
            """async id => {
            const response = await fetch('/api/approve',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({node:id})});
            return {status:response.status,body:await response.json()};
        }""",
            seeded["hypothesis"]["id"],
        )
        assert refused["status"] == 422 and refused["body"]["rule"] == "read_only"
        assert not errors
    assert authoritative_bytes(tmp_path) == before


def test_duplicate_pin_displays_true_server_error_without_optimistic_success(
    tmp_path, chromium_browser
):
    seeded = inspector_project(tmp_path)
    before = authoritative_bytes(tmp_path)
    with (
        loopback_server(tmp_path) as owner,
        selected_inspector(chromium_browser, owner, seeded["hypothesis"]["id"]) as (
            page,
            inspector,
            _,
            errors,
        ),
    ):
        choose_action(
            inspector,
            "Pin link",
            {"to": seeded["result"]["id"], "fields": {"artifact": seeded["artifact"]}},
        )
        wait_action(page)
        action = inspector.evaluate("element => element.lastAction")
        assert action["result"] is None and "duplicate_edge" in action["error"]
        assert "duplicate_edge" in inspector.locator(".inspector-shell").inner_text()
        assert not errors
    assert authoritative_bytes(tmp_path) == before


def test_tag_optional_priority_omission_preserves_and_explicit_null_clears(
    tmp_path, chromium_browser
):
    seeded = inspector_project(tmp_path)
    identifier = seeded["active"]["id"]
    with Project.open(tmp_path) as project:
        project.tag(identifier, priority=7, actor=ALICE, now=NOW)
    posts = []
    with (
        loopback_server(tmp_path) as owner,
        selected_inspector(chromium_browser, owner, identifier) as (
            page,
            inspector,
            _,
            errors,
        ),
    ):
        page.on(
            "request",
            lambda request: (
                posts.append(request.post_data_json)
                if request.method == "POST" and request.url.endswith("/api/tag")
                else None
            ),
        )
        choose_action(inspector, "Tag", {"add": ["first"]})
        wait_action(page)
        assert inspector.evaluate("element => element.node.priority") == 7
        choose_action(inspector, "Tag", {"priority": None})
        wait_action(page)
        assert inspector.evaluate("element => element.node.priority") is None
        assert posts == [
            {"node": identifier, "add": ["first"]},
            {"node": identifier, "priority": None},
        ]
        assert not errors


def test_profile_declared_role_is_sent_without_actor_override(
    tmp_path, chromium_browser
):
    seeded = inspector_project(tmp_path)
    posts = []
    with (
        loopback_server(tmp_path) as owner,
        selected_inspector(chromium_browser, owner, seeded["active"]["id"]) as (
            page,
            inspector,
            _,
            errors,
        ),
    ):
        page.on(
            "request",
            lambda request: (
                posts.append(request.post_data_json)
                if request.method == "POST" and request.url.endswith("/api/budget")
                else None
            ),
        )
        inspector.get_by_label("Role", exact=True).select_option("owner")
        choose_action(inspector, "Budget", {"tokens": 12})
        wait_action(page)
        assert posts == [
            {"node": seeded["active"]["id"], "tokens": 12, "role": "owner"}
        ]
        state = inspector.evaluate(
            """element => ({node:element.node,lastAction:element.lastAction,
            readOnly:element.readOnly,status:element.shadowRoot.querySelector('[role="status"]').textContent})"""
        )
        assert state["lastAction"]["error"] is None, state
        assert ((state["node"] or {}).get("budget") or {}).get("tokens") == 12, state
        assert not errors


@pytest.mark.parametrize("write_error", [False, True])
@pytest.mark.parametrize("replacement_selection", [False, True])
def test_successful_budget_reports_delayed_native_readback_failure_without_retry(
    tmp_path, chromium_browser, monkeypatch, write_error, replacement_selection
):
    """A settled write is not successful inspector readback when native show fails."""
    import asyncio
    import threading

    import researchgraph.serve as serving
    from researchgraph.model import RuleViolation

    seeded = inspector_project(tmp_path)
    identifier = seeded["active"]["id"]
    persisted = threading.Event()
    failed = threading.Event()
    delayed = threading.Event()
    release = threading.Event()
    message = "Dedicated post-budget native show unavailable"
    write_message = "Dedicated post-persistence budget failure"
    native_budget, native_show = Project.budget, Project.show
    authority = []
    posts = []

    def budget(self, *args, **context):
        result = native_budget(self, *args, **context)
        authority.append((context["actor"], context["role"]))
        persisted.set()
        if write_error:
            raise RuleViolation("schema", write_message)
        return result

    def show(self, *args, **context):
        if persisted.is_set() and not failed.is_set():
            failed.set()
            raise RuleViolation("schema", message)
        return native_show(self, *args, **context)

    class DelayedJSON(serving.JSONResponse):
        """Hold actual native error bytes, not the Project lock or a fake route."""

        async def __call__(self, scope, receive, send):
            if (
                scope["path"] == "/api/show"
                and self.status_code == 422
                and json.loads(self.body).get("message") == message
            ):
                delayed.set()
                if not await asyncio.to_thread(release.wait, 5):
                    raise RuntimeError("Native readback delivery was not released")
            await super().__call__(scope, receive, send)

    monkeypatch.setattr(Project, "budget", budget)
    monkeypatch.setattr(Project, "show", show)
    monkeypatch.setattr(serving, "JSONResponse", DelayedJSON)
    with (
        loopback_server(tmp_path) as owner,
        selected_inspector(chromium_browser, owner, identifier) as (
            page,
            inspector,
            _,
            errors,
        ),
    ):
        page.on(
            "request",
            lambda request: (
                posts.append(request.post_data_json)
                if request.method == "POST" and request.url.endswith("/api/budget")
                else None
            ),
        )
        inspector.get_by_label("Role", exact=True).select_option("owner")
        try:
            choose_action(inspector, "Budget", {"tokens": 12})
            assert delayed.wait(5), (
                "Post-write native show error never reached delivery"
            )
            assert inspector.evaluate("element => element.busy") is True
            assert inspector.evaluate("element => element.lastAction") is None
            # The response is held outside the server's Project critical section:
            # a separate native reader already sees the one persisted write.
            with Project.open(tmp_path) as project:
                authoritative = native_show(
                    project, identifier, actor=ALICE, now=NOW + timedelta(days=2)
                )
            assert authoritative["budget"]["tokens"] == 12
            if replacement_selection:
                # Replace the failed request through the actual host selection
                # bridge while its native response is still held. Completion
                # must use this latest selection's successful terminal outcome.
                replacement = seeded["task"]["id"]
                page.locator(f'research-graph [data-node-id="{replacement}"]').click()
                page.wait_for_function(
                    "id => document.querySelector('research-inspector').node?.id === id",
                    arg=replacement,
                )
        finally:
            release.set()
        wait_action(page)
        assert posts == [{"node": identifier, "tokens": 12, "role": "owner"}]
        assert authority == [(ALICE, "owner")]
        action = inspector.evaluate("element => element.lastAction")
        assert (action["result"] is None) == write_error
        if write_error:
            assert write_message in action["error"]
        if replacement_selection:
            assert message not in (action["error"] or "")
            if not write_error:
                assert action["error"] is None
            with Project.open(tmp_path) as project:
                expected = native_show(
                    project, replacement, actor=ALICE, now=NOW + timedelta(days=2)
                )
            assert inspector.evaluate("element => element.node") == expected
            assert inspector.evaluate("element => element.readOnly") is False
        else:
            assert action["error"] is not None, (
                "Failed readback must not publish success"
            )
            assert message in action["error"]
            if not write_error:
                assert "Write succeeded" in action["error"]
            assert inspector.evaluate("element => element.node.budget") is None
            assert inspector.evaluate("element => element.readOnly") is None
            warning = inspector.get_by_role("status").inner_text()
            assert message in warning
            assert "stale" in warning.lower()
        # Recovery is a read, not a repeated POST or a wait hiding stale success.
        if replacement_selection:
            inspector.evaluate("(element,id) => element.select(id)", identifier)
        recovery = inspector.evaluate("element => element.reload()")
        assert recovery["error"] is None, recovery
        assert inspector.evaluate("element => element.node") == authoritative
        assert posts == [{"node": identifier, "tokens": 12, "role": "owner"}]
        assert not errors


def test_two_inspectors_keep_role_selection_and_detached_results_independent(
    tmp_path, chromium_browser
):
    seeded = inspector_project(tmp_path)
    with (
        loopback_server(tmp_path) as owner,
        selected_inspector(chromium_browser, owner, seeded["hypothesis"]["id"]) as (
            page,
            inspector,
            _,
            errors,
        ),
    ):
        inspector.get_by_label("Role", exact=True).select_option("owner")
        page.evaluate(
            """id => {
            const other=document.createElement('research-inspector');
            other.id='other-inspector';other.setAttribute('src','/api');other.setAttribute('node-id',id);document.body.append(other);
        }""",
            seeded["task"]["id"],
        )
        page.wait_for_function(
            "id => document.querySelector('#other-inspector').node?.id === id",
            arg=seeded["task"]["id"],
        )
        other = page.locator("#other-inspector")
        other.get_by_label("Role", exact=True).select_option("reviewer")
        assert inspector.get_by_label("Role", exact=True).input_value() == "owner"
        assert (
            inspector.evaluate("element => element.node.id")
            == seeded["hypothesis"]["id"]
        )
        assert inspector.evaluate(
            "element => {const node=element.node;node.fields.bad=true;const history=element.related.history;history.length=0;return !('bad' in element.node.fields) && element.related.history.length>0;}"
        )
        other.evaluate("element => element.remove()")
        assert (
            inspector.evaluate("element => element.node.id")
            == seeded["hypothesis"]["id"]
        )
        assert not errors


def test_vocabulary_absent_empty_states_and_profile_declared_roles(
    tmp_path, chromium_browser
):
    seeded = inspector_project(tmp_path, profile="tasks")
    with (
        loopback_server(tmp_path) as owner,
        selected_inspector(chromium_browser, owner, seeded["task"]["id"]) as (
            _,
            inspector,
            _,
            errors,
        ),
    ):
        for name in ("evidence", "weakest", "alternatives", "predictions", "notes"):
            assert (
                "No items" in inspector.locator(f'[data-section="{name}"]').inner_text()
            )
        with Project.open(tmp_path) as project:
            roles = project.profile_info(actor=ALICE, now=NOW)["roles"]
        assert (
            inspector.get_by_label("Role", exact=True)
            .locator("option")
            .evaluate_all(
                "options => options.map(option=>option.value).filter(Boolean)"
            )
            == roles
        )
        assert not errors


@pytest.mark.parametrize("failure", ["validation", "rollback", "commit", "hook"])
def test_failed_rerun_refreshes_actual_persistence_without_retry(
    tmp_path, chromium_browser, monkeypatch, failure, index_mode
):
    seeded = inspector_project(tmp_path)
    if failure == "hook":
        profile = deepcopy(Profile.load("research"))

        def broken_hook(project, write, commit):
            if write.op == "supersede":
                raise RuntimeError("post-commit browser fixture failure")

        profile.event_hooks.append(broken_hook)
        monkeypatch.setattr(
            Profile, "_registry", {**Profile._registry, "research": profile}
        )
    posts = []
    with (
        loopback_server(tmp_path) as owner,
        selected_inspector(chromium_browser, owner, seeded["task"]["id"]) as (
            page,
            inspector,
            _,
            errors,
        ),
    ):
        baseline = authoritative_bytes(tmp_path)
        previous = git(tmp_path, "rev-parse", "HEAD")
        if failure == "commit":
            hook = tmp_path / ".git/hooks/pre-commit"
            hook.write_text("#!/bin/sh\nexit 1\n")
            hook.chmod(0o755)
        if failure == "rollback":
            from researchgraph.store import transaction

            replace = transaction.os.replace
            calls = []

            def broken_replace(source, destination):
                calls.append((source, destination))
                if len(calls) == 2:
                    raise OSError("rename browser fixture failure")
                return replace(source, destination)

            monkeypatch.setattr(transaction.os, "replace", broken_replace)
        page.on(
            "request",
            lambda request: (
                posts.append(request.post_data_json)
                if request.method == "POST" and request.url.endswith("/api/supersede")
                else None
            ),
        )
        arguments = {"instruction": "Do not retry automatically"}
        if failure == "validation":
            arguments["fields"] = {"not_a_task_field": True}
        choose_action(inspector, "Re-run with instruction", arguments)
        wait_action(page)
        assert len(posts) == 1
        action = inspector.evaluate("element => element.lastAction")
        assert action["result"] is None and action["error"]
        assert ("schema" if failure == "validation" else "write_failed") in action[
            "error"
        ]
        with Project.open(tmp_path) as project:
            family = project.history(
                seeded["task"]["id"], actor=ALICE, now=NOW + timedelta(days=2)
            )
            replacements = [
                item
                for item in project.graph(actor=ALICE, now=NOW)["edges"]
                if item["type"] == "supersedes" and item["to"] == seeded["task"]["id"]
            ]
            if failure in {"validation", "rollback"}:
                assert replacements == []
                assert authoritative_bytes(tmp_path) == baseline
            else:
                assert len(replacements) == 1
                replacement = project.show(
                    replacements[0]["from"], actor=ALICE, now=NOW + timedelta(days=2)
                )
                assert (
                    replacement["instructions"][0]["body"] == arguments["instruction"]
                )
                assert replacement["id"] in json.dumps(
                    inspector.evaluate("element => element.related.history")
                )
                assert inspector.evaluate("element => element.node.status") == "retired"
                assert bool(git(tmp_path, "rev-parse", "HEAD") != previous) == (
                    failure == "hook"
                )
                assert family
        assert not errors
