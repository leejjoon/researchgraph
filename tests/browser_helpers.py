"""Real served-browser helpers; never fake graph endpoints or persistence."""

from collections import deque
from contextlib import contextmanager
from time import monotonic
from urllib.parse import parse_qsl, urlsplit

from tests.helpers import git


def authoritative_bytes(root):
    """Include managed bytes and Git state while allowing disposable index caches."""
    files = [root / ".researchgraph/profile.yaml", root / ".gitignore"]
    for directory in ("graph", "artifacts"):
        files.extend(path for path in (root / directory).rglob("*") if path.is_file())
    return (
        {path.relative_to(root).as_posix(): path.read_bytes() for path in files},
        git(root, "rev-parse", "HEAD"),
        git(root, "status", "--porcelain"),
    )


@contextmanager
def viewer_page(browser, owner):
    """Use the actual fragment exchange and isolate each browser session."""
    context = browser.new_context(
        accept_downloads=True, viewport={"width": 1440, "height": 1000}
    )
    page = context.new_page()
    page.set_default_timeout(5000)
    requests, errors = [], []
    failed_requests = deque(maxlen=128)
    native_reads = deque(maxlen=256)
    started = monotonic()

    def record_read(event, request, **details):
        url = urlsplit(request.url)
        if request.method != "GET" or not url.path.startswith("/api/"):
            return
        native_reads.append(
            {
                "event": event,
                "elapsed_ms": round((monotonic() - started) * 1000, 1),
                "path": url.path,
                "port": url.port,
                "selection": {
                    key: value
                    for key, value in parse_qsl(url.query)
                    if key in {"id", "claim", "hypothesis"}
                },
                **details,
            }
        )

    page.on("request", lambda request: requests.append(request.url))
    page.on("request", lambda request: record_read("request", request))
    page.on(
        "response",
        lambda response: record_read(
            "response", response.request, status=response.status
        ),
    )
    page.on("requestfinished", lambda request: record_read("finished", request))
    page.on(
        "requestfailed",
        lambda request: record_read("failed", request, failure=request.failure),
    )
    page.on(
        "requestfailed",
        lambda request: failed_requests.append(
            {
                "method": request.method,
                "path": urlsplit(request.url).path,
                "port": urlsplit(request.url).port,
                "failure": request.failure,
            }
        ),
    )
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on(
        "console",
        lambda message: (
            errors.append(message.text)
            if message.type == "error" and "Content Security Policy" in message.text
            else None
        ),
    )
    token = owner.app.state.launch_token
    try:
        page.goto(f"{owner.origin}/#token={token}")
        page.wait_for_function(
            "() => customElements.get('research-graph') !== undefined"
        )
        page.locator("research-graph").wait_for()
        page.wait_for_function(
            "() => document.querySelector('research-graph').graph !== null"
        )
        yield page, requests, errors, token
    except BaseException as error:
        error.add_note(f"Actual browser request failures: {list(failed_requests)!r}")
        error.add_note(f"Bounded native GET lifecycle: {list(native_reads)!r}")
        raise
    finally:
        context.close()
