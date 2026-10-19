"""Shared fixtures. T03 adds strategies in tests/strategies.py; T05 adds a `project` fixture
that `rg init`s a temporary git repository with the research profile and actors alice (owner),
bob (reviewer), agent1, agent2. Every acceptance test must be parametrised over
index_mode in ("kept", "deleted"), deleting .researchgraph/index.sqlite before the When step
when mode == "deleted" (spec/store-layout.md, Index)."""
import pytest

@pytest.fixture(params=["kept", "deleted"])
def index_mode(request):
    return request.param
