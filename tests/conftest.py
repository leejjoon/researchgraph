"""Shared fixtures. T03 adds strategies in tests/strategies.py; T05 adds a `project` fixture
that `rg init`s a temporary git repository with the research profile and actors alice (owner),
bob (reviewer), agent1, agent2. Every acceptance test must be parametrised over
index_mode in ("kept", "deleted"), deleting .researchgraph/index.sqlite before the When step
when mode == "deleted" (spec/store-layout.md, Index)."""

import pytest

from researchgraph.model import Actor
from researchgraph.project import Project
from tests.helpers import ALICE, NOW, repository


@pytest.fixture(params=["kept", "deleted"])
def index_mode(request):
    return request.param


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
