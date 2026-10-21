"""Real served-browser helpers; never fake graph endpoints or persistence."""

from contextlib import contextmanager

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
    page.on("request", lambda request: requests.append(request.url))
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
    finally:
        context.close()
