"""Real conditional review queue, native parity and nonoptimistic refusals."""

from contextlib import contextmanager
from datetime import timedelta

import pytest

from researchgraph.model import Actor
from researchgraph.project import Project
from tests.browser_helpers import authoritative_bytes, viewer_page
from tests.helpers import ALICE, NOW, repository
from tests.http_server_helpers import loopback_server
from tests.test_cli import invoke, succeeds

pytestmark = [
    pytest.mark.browser,
    pytest.mark.http,
    pytest.mark.cli,
    pytest.mark.usefixtures("cli_cache_policy"),
]


def review_project(root):
    """Create oldest human gate, conditional multigate and genuinely blocked work."""
    with Project.init(
        repository(root), "test-conditional", actor=ALICE, now=NOW
    ) as project:
        hypothesis = project.propose(
            "Hypothesis",
            "Oldest review <img src=x>",
            body="Read literal <script>window.bad=true</script>",
            actor=ALICE,
            now=NOW,
        )
        multi = project.propose(
            "Task",
            "Conditional owner and reviewer",
            actor=ALICE,
            now=NOW + timedelta(seconds=1),
        )
        ready = project.propose(
            "Task",
            "No review",
            fields={"review": False},
            actor=ALICE,
            now=NOW + timedelta(seconds=2),
        )
        project.link(
            "blocks",
            multi["id"],
            ready["id"],
            actor=ALICE,
            now=NOW + timedelta(seconds=2),
        )
        claim = project.propose(
            "Claim", "Evidence navigation", actor=ALICE, now=NOW + timedelta(seconds=3)
        )
        return hypothesis, multi, ready, claim


@contextmanager
def review_page(browser, owner):
    """Await the real custom element and native queue snapshot on authenticated host."""
    with viewer_page(browser, owner) as (page, requests, errors, _):
        page.wait_for_function(
            "() => customElements.get('research-review-queue') !== undefined && document.querySelector('research-review-queue')?.items !== null"
        )
        yield page, page.locator("research-review-queue"), requests, errors


def wait_review_action(page):
    """Await one genuine outcome and authoritative post-action queue readback."""
    page.wait_for_function(
        "() => document.querySelector('research-review-queue').busy === false && document.querySelector('research-review-queue').lastAction !== null"
    )


def assert_installed_queue(root, queue):
    """Compare exact detached queue records and badge with an installed rg process."""
    expected = succeeds(invoke(root, "queue"))["items"]
    assert queue.evaluate("element => element.items") == expected
    assert int(queue.locator("[data-review-badge]").inner_text()) == len(expected)
    assert int(queue.page.locator("[data-review-nav-badge]").inner_text()) == len(
        expected
    )
    return expected


def test_oldest_conditional_multigate_roles_approvals_and_badge_native_parity(
    tmp_path, chromium_browser, index_mode
):
    hypothesis, multi, _, claim = review_project(tmp_path)
    with (
        loopback_server(tmp_path) as owner,
        review_page(chromium_browser, owner) as (page, queue, _, errors),
    ):
        items = assert_installed_queue(tmp_path, queue)
        assert [item["id"] for item in items] == [
            hypothesis["id"],
            multi["id"],
            claim["id"],
        ]
        card = queue.locator(f'[data-review-id="{multi["id"]}"]')
        text = card.inner_text()
        assert "owner" in text and "reviewer" in text and '"review": true' in text
        assert "1 blocked behind" in text
        assert "Waiting" in text
        screenshot = tmp_path.parent / f"review-{tmp_path.name}.png"
        page.screenshot(path=str(screenshot), full_page=True)
        print(f"review_screenshot={screenshot}")
        assert queue.locator("script,img").count() == 0
        assert not page.evaluate("() => window.bad === true")
        queue.locator(f'[data-review-id="{hypothesis["id"]}"]').get_by_role(
            "button", name="Approve", exact=True
        ).click()
        wait_review_action(page)
        assert queue.evaluate("element => element.lastAction.error") is None
        assert len(assert_installed_queue(tmp_path, queue)) == len(items) - 1
        queue.get_by_label("Review role", exact=True).select_option("owner")
        card.get_by_label("Approval note", exact=True).fill("First gate")
        card.get_by_role("button", name="Approve", exact=True).click()
        wait_review_action(page)
        partial = assert_installed_queue(tmp_path, queue)
        current = next(item for item in partial if item["id"] == multi["id"])
        assert current["status"] == "proposed"
        assert current["approvals"][0]["role"] == "owner"
        assert len(partial) == len(items) - 1
        queue.get_by_label("Review role", exact=True).select_option("reviewer")
        card.get_by_role("button", name="Approve", exact=True).click()
        wait_review_action(page)
        assert "already_approved" in queue.evaluate(
            "element => element.lastAction.error"
        )
        with (
            loopback_server(
                tmp_path, actor=Actor("human", "bob", "bob@example.test")
            ) as second_owner,
            review_page(chromium_browser, second_owner) as (
                second_page,
                second_queue,
                _,
                second_errors,
            ),
        ):
            second_queue.get_by_label("Review role", exact=True).select_option(
                "reviewer"
            )
            second_queue.locator(f'[data-review-id="{multi["id"]}"]').get_by_role(
                "button", name="Approve", exact=True
            ).click()
            wait_review_action(second_page)
            assert second_queue.evaluate("element => element.lastAction.error") is None
            assert not second_errors
        queue.evaluate("element => element.reload()")
        assert multi["id"] not in [
            item["id"] for item in assert_installed_queue(tmp_path, queue)
        ]
        assert succeeds(invoke(tmp_path, "show", multi["id"]))["status"] == "active"
        assert not errors


def test_wrong_role_and_send_back_show_actual_server_state(
    tmp_path, chromium_browser, index_mode
):
    _, multi, _, _ = review_project(tmp_path)
    posts = []
    with (
        loopback_server(tmp_path) as owner,
        review_page(chromium_browser, owner) as (page, queue, _, errors),
    ):
        page.on(
            "request",
            lambda request: (
                posts.append(request.post_data_json)
                if request.method == "POST" and request.url.endswith("/api/approve")
                else None
            ),
        )
        card = queue.locator(f'[data-review-id="{multi["id"]}"]')
        card.get_by_role("button", name="Approve", exact=True).click()
        wait_review_action(page)
        assert queue.evaluate("element => element.lastAction.result") is None
        assert "wrong_role" in queue.evaluate("element => element.lastAction.error")
        assert len(posts) == 1
        assert multi["id"] in [
            item["id"] for item in assert_installed_queue(tmp_path, queue)
        ]
        card.get_by_label("Reject reason", exact=True).fill("Send back with a reason")
        card.get_by_role("button", name="Reject", exact=True).click()
        wait_review_action(page)
        assert queue.evaluate("element => element.lastAction.error") is None
        assert multi["id"] not in [
            item["id"] for item in assert_installed_queue(tmp_path, queue)
        ]
        shown = succeeds(invoke(tmp_path, "show", multi["id"]))
        assert shown["status"] == "retired" and shown["edges"] == []
        assert not errors


def test_human_only_agent_refusal_is_error_not_optimistic_success(
    tmp_path, chromium_browser
):
    hypothesis, _, _, _ = review_project(tmp_path)
    with (
        loopback_server(tmp_path, actor=Actor("agent", "review-agent")) as owner,
        review_page(chromium_browser, owner) as (page, queue, _, errors),
    ):
        queue.locator(f'[data-review-id="{hypothesis["id"]}"]').get_by_role(
            "button", name="Approve", exact=True
        ).click()
        wait_review_action(page)
        assert queue.evaluate("element => element.lastAction.result") is None
        assert "human_only_approval" in queue.evaluate(
            "element => element.lastAction.error"
        )
        assert hypothesis["id"] in [
            item["id"] for item in assert_installed_queue(tmp_path, queue)
        ]
        assert not errors


def test_readonly_hides_review_actions_and_direct_write_is_refused(
    tmp_path, chromium_browser
):
    hypothesis, _, _, _ = review_project(tmp_path)
    before = authoritative_bytes(tmp_path)
    with (
        loopback_server(tmp_path, read_only=True) as owner,
        review_page(chromium_browser, owner) as (page, queue, _, errors),
    ):
        assert queue.locator("[data-review-action]").count() == 0
        response = page.evaluate(
            """async id=>{const r=await fetch('/api/reject',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({node:id,reason:'Forbidden'})});return {status:r.status,body:await r.json()};}""",
            hypothesis["id"],
        )
        assert response["status"] == 422 and response["body"]["rule"] == "read_only"
        assert_installed_queue(tmp_path, queue)
        assert not errors
    assert authoritative_bytes(tmp_path) == before


def test_concurrent_review_error_refetches_and_evidence_navigation_is_real(
    tmp_path, chromium_browser, index_mode
):
    hypothesis, _, _, claim = review_project(tmp_path)
    with (
        loopback_server(tmp_path) as owner,
        review_page(chromium_browser, owner) as (page, queue, _, errors),
    ):
        succeeds(
            invoke(
                tmp_path, "approve", hypothesis["id"], now=NOW + timedelta(seconds=4)
            )
        )
        queue.locator(f'[data-review-id="{hypothesis["id"]}"]').get_by_role(
            "button", name="Approve", exact=True
        ).click()
        wait_review_action(page)
        assert queue.evaluate("element=>element.lastAction.result") is None
        assert queue.evaluate("element=>element.lastAction.error")
        assert hypothesis["id"] not in [
            item["id"] for item in assert_installed_queue(tmp_path, queue)
        ]
        queue.locator(f'[data-review-id="{claim["id"]}"]').get_by_role(
            "button", name="Open evidence", exact=True
        ).click()
        page.wait_for_function(
            "id=>document.querySelector('research-inspector').node?.id===id",
            arg=claim["id"],
        )
        evidence = page.locator('research-inspector [data-section="evidence"]')
        assert "No items" in evidence.inner_text()
        assert not errors


def test_inspector_approval_refreshes_same_host_review_badge(
    tmp_path, chromium_browser
):
    from tests.test_viewer_inspector import choose_action, wait_action

    hypothesis, _, _, _ = review_project(tmp_path)
    with (
        loopback_server(tmp_path) as owner,
        review_page(chromium_browser, owner) as (page, queue, _, errors),
    ):
        queue.locator(f'[data-review-id="{hypothesis["id"]}"]').get_by_role(
            "button", name="Open node", exact=True
        ).click()
        page.wait_for_function(
            "id=>document.querySelector('research-inspector').node?.id===id",
            arg=hypothesis["id"],
        )
        choose_action(
            page.locator("research-inspector"),
            "Approve",
            {"note": "Reviewed through inspector"},
        )
        wait_action(page)
        page.wait_for_function(
            "id=>!document.querySelector('research-review-queue').items.some(item=>item.id===id)",
            arg=hypothesis["id"],
        )
        assert_installed_queue(tmp_path, queue)
        assert not errors
