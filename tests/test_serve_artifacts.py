"""Authenticated downloads remain inert and contained through descriptor opening."""

from urllib.parse import quote

import pytest
from starlette.testclient import TestClient

from researchgraph.project import Project
from researchgraph.serve import create_app
from tests.helpers import ALICE, NOW, repository
from tests.http_server_helpers import loopback_server
from tests.test_cli_general import _state
from tests.test_operations import discard_index
from tests.test_serve import ORIGIN, authenticated

pytestmark = pytest.mark.http


def _seed(tmp_path, filename, index_mode):
    with Project.init(repository(tmp_path), "tasks", actor=ALICE, now=NOW) as project:
        node = project.propose("Task", "download owner", actor=ALICE, now=NOW)
        other = project.propose("Task", "wrong owner", actor=ALICE, now=NOW)
        relative = f"artifacts/{node['id']}/nested/{filename}"
        path = tmp_path / relative
        path.parent.mkdir(parents=True)
        content = b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(document.cookie)"></svg>'
        path.write_bytes(content)
        project.attach(node["id"], [relative], actor=ALICE, now=NOW)
        discard_index(project, index_mode)
    return node["id"], other["id"], relative, content


@pytest.mark.parametrize(
    "filename",
    ["hostile.html", 'résumé ;".svg', "space name.txt", "literal%20name.txt"],
)
def test_registered_download_is_inert_named_and_byte_exact(
    tmp_path, filename, index_mode
):
    node, _, relative, content = _seed(tmp_path, filename, index_mode)
    before = _state(tmp_path)
    url = f"/artifacts/{node}/nested/{quote(filename, safe='')}"
    # TestClient 1.9 unquotes HTTPX's already-decoded URL.path twice. Actual
    # Uvicorn wire parsing, not a compatibility route, is the filename oracle.
    with loopback_server(tmp_path) as server:
        client = server.client
        assert client.get(url).status_code == 403
        server.authenticate()
        response = client.get(url)
        assert response.status_code == 200 and response.content == content
        assert response.headers["content-type"] == "application/octet-stream"
        assert response.headers["content-length"] == str(len(content))
        assert (
            response.headers["content-disposition"]
            == f"attachment; filename*=UTF-8''{quote(filename, safe='')}"
        )
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["referrer-policy"] == "no-referrer"
    assert (tmp_path / relative).read_bytes() == content
    assert _state(tmp_path) == before


def test_unregistered_wrong_owner_and_encoded_traversals_never_disclose(
    tmp_path, index_mode
):
    node, other, relative, _ = _seed(tmp_path, "owned.txt", index_mode)
    secret = "TOP-SECRET-NONDISCLOSURE-SENTINEL"
    (tmp_path / "private.txt").write_text(secret)
    (tmp_path / f"artifacts/{node}/unregistered.txt").write_text(secret)
    app = create_app(tmp_path, actor=ALICE, origin=ORIGIN)
    paths = [
        f"/artifacts/{other}/nested/owned.txt",
        f"/artifacts/{node}/unregistered.txt",
        f"/artifacts/{node}/../private.txt",
        f"/artifacts/{node}/%2e%2e/%2e%2e/private.txt",
        f"/artifacts/{node}/%252e%252e/%252e%252e/private.txt",
        f"/artifacts/{node}/%25252e%25252e/%25252e%25252e/private.txt",
        f"/artifacts/{node}/%2fetc/passwd",
        f"/artifacts/{node}/nested%2f..%2f..%2f..%2fprivate.txt",
        f"/artifacts/{node}/nested%252f..%252f..%252f..%252fprivate.txt",
        f"/artifacts/{node}/..%5c..%5cprivate.txt",
        f"/artifacts/{node}/..%255c..%255cprivate.txt",
        f"/artifacts/{node}/nested/%00owned.txt",
    ]
    with TestClient(app, base_url=ORIGIN) as client:
        authenticated(client, app)
        for url in paths:
            response = client.get(url)
            assert response.status_code == 404, url
            assert secret not in response.text and relative not in response.text
            assert (
                "private.txt" not in response.text
                and str(tmp_path) not in response.text
            )


@pytest.mark.parametrize("swap", ["file", "directory"])
@pytest.mark.parametrize("after_validation", [False, True])
def test_symlink_and_swap_after_validation_fail_closed(
    tmp_path, monkeypatch, swap, after_validation, index_mode
):
    node, _, relative, _ = _seed(tmp_path, "owned.txt", index_mode)
    outside = tmp_path.parent / f"outside-{tmp_path.name}"
    outside.mkdir()
    secret = "SWAPPED-OUTSIDE-SECRET"
    (outside / "owned.txt").write_text(secret)
    target = tmp_path / relative
    changed = False

    def replace():
        nonlocal changed
        changed = True
        if swap == "file":
            target.rename(target.with_name("saved.txt"))
            target.symlink_to(outside / "owned.txt")
        else:
            target.parent.rename(target.parent.with_name("saved"))
            target.parent.symlink_to(outside, target_is_directory=True)

    app = create_app(tmp_path, actor=ALICE, origin=ORIGIN)
    with TestClient(app, base_url=ORIGIN) as client:
        authenticated(client, app)
        if after_validation:
            original = Project._safe_path

            def checked(project, path):
                result = original(project, path)
                if path == relative and not changed:
                    replace()
                return result

            monkeypatch.setattr(Project, "_safe_path", checked)
        else:
            replace()
        response = client.get(f"/artifacts/{node}/nested/owned.txt")
        assert changed and response.status_code == 404
        assert secret not in response.text and str(outside) not in response.text


def test_download_closes_its_file_after_response(tmp_path, monkeypatch, index_mode):
    from researchgraph.serve import _Download

    node, _, _, _ = _seed(tmp_path, "owned.txt", index_mode)
    closed = []
    original = _Download.__call__

    async def observe(response, scope, receive, send):
        try:
            await original(response, scope, receive, send)
        finally:
            closed.append(response.file.closed)

    monkeypatch.setattr(_Download, "__call__", observe)
    app = create_app(tmp_path, actor=ALICE, origin=ORIGIN)
    with TestClient(app, base_url=ORIGIN) as client:
        authenticated(client, app)
        response = client.get(f"/artifacts/{node}/nested/owned.txt")
        assert response.status_code == 200
    assert closed == [True]
