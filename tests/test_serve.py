"""Real ASGI transport tests for the optional, launch-fixed HTTP boundary."""

import os
import subprocess
import sysconfig
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from researchgraph.model import Actor, format_timestamp
from researchgraph.project import Project
from researchgraph.registry import OPERATIONS
from researchgraph.serve import create_app
from tests.helpers import ALICE, NOW, repository
from tests.test_cli_general import _state
from tests.test_operations import discard_index

ORIGIN = "http://127.0.0.1:8765"
pytestmark = pytest.mark.http


def authenticated(client, app):
    response = client.post(
        "/api/session",
        json={"token": app.state.launch_token},
        headers={"Origin": ORIGIN},
    )
    assert response.status_code == 200
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    assert "domain=" not in cookie and "max-age=" not in cookie
    return response


def test_http_cookie_origin_host_registry_snapshot_and_scope(tmp_path, index_mode):
    with Project.init(repository(tmp_path), "tasks", actor=ALICE, now=NOW) as project:
        node = project.propose("Task", "private graph text", actor=ALICE, now=NOW)
        expected = project.graph(actor=ALICE, now=NOW)
        discard_index(project, index_mode)
    before = _state(tmp_path)
    app = create_app(tmp_path, actor=ALICE, origin=ORIGIN)
    with TestClient(app, base_url=ORIGIN) as client:
        bootstrap = client.get("/")
        assert bootstrap.status_code == 200 and node["id"] not in bootstrap.text
        assert "private graph text" not in bootstrap.text
        assert client.get("/api/profile").status_code == 403
        assert (
            client.post(
                "/api/session", json={"token": app.state.launch_token}
            ).status_code
            == 403
        )
        authenticated(client, app)
        assert (
            client.post(
                "/api/session",
                json={"token": app.state.launch_token},
                headers={"Origin": ORIGIN},
            ).status_code
            == 403
        )
        operations = client.get("/api/operations")
        assert operations.status_code == 200 and operations.json() == list(OPERATIONS)
        graph = client.get("/api/graph", params={"now": format_timestamp(NOW)})
        assert graph.status_code == 200 and graph.json() == expected
        cursor = graph.headers["X-Researchgraph-Cursor"]
        version, generation, sequence = cursor.split(":")
        assert version == "v1" and len(generation) == 32 and int(sequence) > 0
        assert (
            client.get("/api/graph", headers={"Host": "evil.test"}).status_code == 403
        )
        assert (
            client.get(
                "/api/graph", headers={"Origin": "https://evil.test"}
            ).status_code
            == 403
        )
        assert (
            client.get(
                "/api/graph", params={"token": app.state.launch_token}
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/api/propose", json={"type": "Task", "title": "forbidden"}
            ).status_code
            == 403
        )
        for override in ("actor", "read_only", "project", "as_agent"):
            response = client.post(
                "/api/propose",
                json={"type": "Task", "title": "forbidden", override: False},
                headers={"Origin": ORIGIN},
            )
            assert response.status_code == 403
        for secured in (bootstrap, operations, graph, response):
            assert secured.headers["cache-control"] == "no-store"
            assert secured.headers["referrer-policy"] == "no-referrer"
    assert _state(tmp_path) == before


def test_http_fixed_agent_retains_human_only_gate(tmp_path, index_mode):
    with Project.init(
        repository(tmp_path), "research", actor=ALICE, now=NOW
    ) as project:
        node = project.propose("Hypothesis", "pending", actor=ALICE, now=NOW)
        discard_index(project, index_mode)
    before = _state(tmp_path)
    app = create_app(tmp_path, actor=Actor("agent", "http-worker"), origin=ORIGIN)
    with TestClient(app, base_url=ORIGIN) as client:
        authenticated(client, app)
        response = client.post(
            "/api/approve",
            json={"node": node["id"], "now": format_timestamp(NOW)},
            headers={"Origin": ORIGIN},
        )
        assert response.status_code == 422
        assert response.json()["rule"] == "human_only_approval"
    assert _state(tmp_path) == before


def test_http_read_only_all_writes_precede_validation_and_absent_root(tmp_path):
    root = tmp_path / "absent" / "project"
    app = create_app(root, actor=ALICE, origin=ORIGIN, read_only=True)
    with TestClient(app, base_url=ORIGIN) as client:
        authenticated(client, app)
        for operation in OPERATIONS:
            if operation["kind"] == "write":
                response = client.post(
                    f"/api/{operation['name']}",
                    json={"now": "bad"},
                    headers={"Origin": ORIGIN},
                )
                assert response.status_code == 422
                assert response.json()["rule"] == "read_only"
    assert not root.parent.exists()


def test_installed_http_launcher_help_and_nonloopback_refusal(tmp_path):
    executable = str(Path(sysconfig.get_path("scripts")) / "rgraph")
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    help_result = subprocess.run(
        [executable, "serve", "--help"],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )
    assert help_result.returncode == 0
    assert "--no-open" in help_result.stdout and "--port" in help_result.stdout
    with Project.init(repository(tmp_path), "tasks", actor=ALICE, now=NOW):
        pass
    before = _state(tmp_path)
    result = subprocess.run(
        [
            executable,
            "serve",
            "--project",
            str(tmp_path),
            "--host",
            "0.0.0.0",
            "--no-open",
        ],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
        timeout=10,
    )
    assert result.returncode == 2 and '"rule": "schema"' in result.stderr
    assert "loopback" in result.stderr
    assert _state(tmp_path) == before


def test_http_init_no_commit_and_fixed_destination(tmp_path, index_mode):
    root = tmp_path / "project"
    forbidden = tmp_path / "forbidden" / "destination"
    app = create_app(root, actor=ALICE, origin=ORIGIN)
    with TestClient(app, base_url=ORIGIN) as client:
        authenticated(client, app)
        response = client.post(
            "/api/init",
            json={"profile": "tasks", "commit": False, "now": format_timestamp(NOW)},
            headers={"Origin": ORIGIN},
        )
        assert response.status_code == 200
        with Project.open(root) as project:
            assert project._git.head() is None
            assert response.json() == {
                "path": str(root),
                "profile": "tasks",
                "version": project.profile_version,
            }
            discard_index(project, index_mode)
            before = project.graph(actor=ALICE, now=NOW)
        pin = (root / ".researchgraph/profile.yaml").read_bytes()
        for name in ("init", "import"):
            payload = {"path": str(forbidden)}
            payload.update(
                {"archive": str(tmp_path / "missing.tar.gz")}
                if name == "import"
                else {"profile": "tasks"}
            )
            refused = client.post(
                f"/api/{name}", json=payload, headers={"Origin": ORIGIN}
            )
            assert refused.status_code == 403 and str(forbidden) not in refused.text
        assert client.get("/api/ready").json() == {"items": []}
        with Project.open(root) as project:
            assert project.graph(actor=ALICE, now=NOW) == before
            assert project._git.head() is None
        assert (root / ".researchgraph/profile.yaml").read_bytes() == pin
    assert not forbidden.parent.exists()


@pytest.mark.parametrize("pinned_newer", [False, True])
def test_http_project_open_errors_are_503_not_healthy_empty_graph(
    tmp_path, pinned_newer
):
    root = tmp_path / "project"
    if pinned_newer:
        with Project.init(repository(root), "research", actor=ALICE, now=NOW):
            pass
        (root / ".researchgraph/profile.yaml").write_text(
            "name: research\nversion: '9.0'\n"
        )
    app = create_app(root, actor=ALICE, origin=ORIGIN)
    with TestClient(app, base_url=ORIGIN) as client:
        authenticated(client, app)
        for operation in ("graph", "ready", "profile"):
            response = client.get(f"/api/{operation}")
            assert response.status_code == 503
            assert response.json()["rule"] == (
                "profile_too_new" if pinned_newer else "project_not_found"
            )
            assert "X-Researchgraph-Cursor" not in response.headers
    if not pinned_newer:
        assert not root.exists()


def test_unexpected_core_failure_is_500_not_schema_and_remains_uncacheable(
    tmp_path, monkeypatch
):
    import researchgraph.serve as serving

    with Project.init(repository(tmp_path), "tasks", actor=ALICE, now=NOW):
        pass

    def broken_dispatch(*args, **kwargs):
        raise ValueError("private unexpected decoder diagnostic")

    monkeypatch.setattr(serving, "_dispatch", broken_dispatch)
    app = create_app(tmp_path, actor=ALICE, origin=ORIGIN)
    with TestClient(app, base_url=ORIGIN, raise_server_exceptions=False) as client:
        authenticated(client, app)
        response = client.get("/api/ready")
        assert response.status_code == 500
        assert "private unexpected decoder diagnostic" not in response.text
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["referrer-policy"] == "no-referrer"
