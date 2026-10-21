"""Shared fixtures. T03 adds strategies in tests/strategies.py; T05 adds a `project` fixture
that `rg init`s a temporary git repository with the research profile and actors alice (owner),
bob (reviewer), agent1, agent2. Every acceptance test must be parametrised over
index_mode in ("kept", "deleted"), deleting .researchgraph/index.sqlite before the When step
when mode == "deleted" (spec/store-layout.md, Index)."""

from functools import partial

import pytest

from researchgraph.model import Actor
from researchgraph.project import Project
from tests.helpers import ALICE, NOW, repository


def pytest_addoption(parser):
    parser.addoption(
        "--protocol-fixtures",
        choices=("core", "mcp", "http", "all"),
        default="all",
        help="Select shared protocol scenario fixtures; unrelated native checks are unchanged",
    )
    parser.addoption(
        "--query-door",
        choices=("cli", "mcp", "http", "all"),
        default="all",
        help="Select shared query scenarios; all includes CLI, MCP and HTTP",
    )
    parser.addoption(
        "--index-mode",
        choices=("both", "kept", "deleted"),
        default="both",
        help="Select the named acceptance fixture; unrelated unit/scale tests are unchanged",
    )


def pytest_generate_tests(metafunc):
    selection = metafunc.config.getoption("--protocol-fixtures")
    for name, native in (
        ("read_cli", "cli"),
        ("public_operation", "api"),
        ("archive_operations", "api"),
    ):
        if name in metafunc.fixturenames:
            doors = (
                [native, "mcp", "http"]
                if selection == "all"
                else [native if selection == "core" else selection]
            )
            query_door = metafunc.config.getoption("--query-door")
            if name == "read_cli" and query_door != "all":
                doors = [door for door in doors if door == query_door]
                if not doors:
                    raise pytest.UsageError(
                        "--query-door conflicts with --protocol-fixtures"
                    )
            parameters = [
                pytest.param(door, marks=getattr(pytest.mark, door))
                if door in {"mcp", "http"}
                else door
                for door in doors
            ]
            metafunc.parametrize(
                name,
                parameters,
                indirect=True,
                scope="function" if name == "read_cli" else "module",
            )
    if "index_mode" in metafunc.fixturenames:
        selected = metafunc.config.getoption("--index-mode")
        modes = ["kept", "deleted"] if selected == "both" else [selected]
        metafunc.parametrize("index_mode", modes, indirect=True)


def pytest_collection_modifyitems(config, items):
    """Filter explicit operation doors, never private/native profile boundaries."""
    selection = config.getoption("--protocol-fixtures")
    selected, deselected = [], []
    for item in items:
        door = getattr(item, "callspec", None)
        door = door.params.get("door") if door is not None else None
        eligible = (
            selection == "all"
            or door not in {"api", "cli", "mcp", "http"}
            or (door in {"api", "cli"} if selection == "core" else door == selection)
        )
        (selected if eligible else deselected).append(item)
    items[:] = selected
    if deselected:
        config.hook.pytest_deselected(items=deselected)


@pytest.fixture
def index_mode(request):
    return request.param


@pytest.fixture(scope="module")
def public_operation(request):
    """Inject public boundaries only; native owners remain independent oracles."""
    if request.param == "mcp":
        from tests.door_helpers import operation_mcp

        return lambda project, name: partial(operation_mcp, project, name)
    if request.param == "http":
        from tests.door_helpers import operation_http

        return lambda project, name: partial(operation_http, project, name)
    return lambda project, name: getattr(project, name)


@pytest.fixture
def cli_cache_policy(index_mode, monkeypatch):
    monkeypatch.setenv("RG_TEST_CLI_INDEX_MODE", index_mode)


@pytest.fixture
def actors():
    return {
        "alice": ALICE,
        "bob": Actor("human", "bob", "bob@example.test"),
        "agent1": Actor("agent", "agent1"),
        "agent2": Actor("agent", "agent2"),
    }


@pytest.fixture
def project(tmp_path):
    repository(tmp_path)
    with Project.init(tmp_path, actor=ALICE, now=NOW) as value:
        yield value


@pytest.fixture(scope="module")
def chromium_browser():
    """Require real Chromium only for explicitly collected browser acceptance."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            yield browser
        finally:
            browser.close()


@pytest.fixture(scope="session")
def candidate_distribution(tmp_path_factory):
    """Build tracked current bytes once, not an exact committed release clone."""
    import os
    import sys
    import zipfile

    from tests.helpers import copy_checkout
    from tests.test_packaging import execute

    tmp_path = tmp_path_factory.mktemp("candidate-distribution")
    environment = {
        key: value for key, value in os.environ.items() if key != "PYTHONPATH"
    }
    source = copy_checkout(tmp_path / "candidate source")
    distributions = tmp_path / "sdist"
    execute(
        [
            "uv",
            "build",
            "--python",
            sys.executable,
            "--sdist",
            "--out-dir",
            str(distributions),
        ],
        cwd=source,
        environment=environment,
    )
    sdists = list(distributions.glob("*.tar.gz"))
    assert len(sdists) == 1
    rebuilt = tmp_path / "wheel"
    execute(
        [
            "uv",
            "build",
            "--python",
            sys.executable,
            "--wheel",
            "--out-dir",
            str(rebuilt),
            str(sdists[0]),
        ],
        cwd=tmp_path,
        environment=environment,
    )
    wheels = list(rebuilt.glob("*.whl"))
    assert len(wheels) == 1
    wheel = wheels[0]
    with zipfile.ZipFile(wheel) as archive:
        assert {
            "researchgraph/mcp.py",
            "researchgraph/interfaces.py",
            "researchgraph/serve.py",
            "researchgraph/viewer/static/index.html",
            "researchgraph/viewer/static/bootstrap.js",
            "researchgraph/viewer/static/style.css",
            "researchgraph/viewer/static/research-graph.js",
            "researchgraph/viewer/static/inspector.js",
            "researchgraph/viewer/static/review-queue.js",
            "researchgraph/viewer/static/dashboard.js",
            "researchgraph/viewer/static/events.js",
            "researchgraph/viewer/static/api.js",
            "researchgraph/viewer/static/dom.js",
        } <= set(archive.namelist())
    return source, wheel, environment
