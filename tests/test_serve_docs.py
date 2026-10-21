"""Execute published graph/events/serve+curl recipes in isolated wheel consumers."""

import json
import shutil

import pytest

from researchgraph.project import Project
from tests.helpers import ALICE
from tests.test_docs import (
    clear_manual_caches,
    execute_manual_parts,
    published_manual_blocks,
)
from tests.test_serve_packaging import consumer

pytestmark = [pytest.mark.http, pytest.mark.cli]


def test_exact_live_interface_recipe(tmp_path, candidate_distribution, index_mode):
    assert shutil.which("curl"), "curl is required for the published HTTP recipe"
    _, python, console, environment = consumer(
        tmp_path, candidate_distribution, viewer=True
    )
    environment = dict(environment, RG=str(console))
    results = execute_manual_parts(
        published_manual_blocks(),
        "live-interfaces",
        cwd=tmp_path,
        environment=environment,
        python=python,
        before_part=(lambda: clear_manual_caches(tmp_path))
        if index_mode == "deleted"
        else None,
    )
    receipt = json.loads(results[-1].stdout.splitlines()[-1])
    assert receipt["event_kind"] == "index"
    assert receipt["http_matches_cli"] is True
    root = tmp_path / "live-demo"
    with Project.open(root, read_only=True) as project:
        graph = project.graph(actor=ALICE, now=project.get_nodes()[0].created)
        assert len(graph["nodes"]) == receipt["graph_nodes"] == 1
        assert graph["nodes"][0]["id"] == receipt["task"]
        assert graph["nodes"][0]["type"] == "Task"
        assert graph["nodes"][0]["status"] == "active"
    assert (tmp_path / "session.cookies").is_file()
