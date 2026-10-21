"""Shared fixtures. T03 adds strategies in tests/strategies.py; T05 adds a `project` fixture
that `rg init`s a temporary git repository with the research profile and actors alice (owner),
bob (reviewer), agent1, agent2. Every acceptance test must be parametrised over
index_mode in ("kept", "deleted"), deleting .researchgraph/index.sqlite before the When step
when mode == "deleted" (spec/store-layout.md, Index)."""

import pytest

from researchgraph.model import Actor
from researchgraph.project import Project
from tests.helpers import ALICE, NOW, repository


def pytest_addoption(parser):
    parser.addoption(
        "--protocol-fixtures",
        choices=("core", "mcp", "all"),
        default="all",
        help="Select shared protocol scenario fixtures; unrelated native checks are unchanged",
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
                [native, "mcp"]
                if selection == "all"
                else [native if selection == "core" else "mcp"]
            )
            parameters = [
                pytest.param(door, marks=pytest.mark.mcp) if door == "mcp" else door
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


@pytest.fixture
def index_mode(request):
    return request.param


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
