"""Bulk graph and safe profile reads share public historical/cache semantics."""

import json
from datetime import timedelta
from itertools import pairwise
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from researchgraph.model import Actor
from researchgraph.profiles import Profile
from researchgraph.project import Project
from tests.helpers import ALICE, NOW, git, repository
from tests.test_cli import invoke, succeeds
from tests.test_operations import discard_index, snapshot


@pytest.fixture(name="invoke")
def scenario_invoke(door):
    if door == "mcp":
        from tests.door_helpers import invoke_mcp

        return invoke_mcp
    return invoke


@pytest.mark.parametrize(
    "door", ["api", pytest.param("cli", marks=pytest.mark.cli), "mcp"]
)
def test_graph_is_sorted_show_projection_including_retired(
    tmp_path, index_mode, door, monkeypatch, invoke
):
    monkeypatch.setenv("RG_TEST_CLI_INDEX_MODE", index_mode)
    with Project.init(repository(tmp_path), "tasks", actor=ALICE, now=NOW) as project:
        first = project.propose("Task", "first", actor=ALICE, now=NOW)
        later = NOW + timedelta(seconds=1)
        second = project.propose("Task", "second", actor=ALICE, now=later)
        edge = project.link("blocks", first["id"], second["id"], actor=ALICE, now=later)
        last = NOW + timedelta(seconds=2)
        replacement = project.supersede(
            first["id"], title="replacement", actor=ALICE, now=last
        )
        before = snapshot(project)
        for selector in (None, NOW, later):
            discard_index(project, index_mode)
            args = {} if selector is None else {"as_of": selector}
            if door == "api":
                graph = project.graph(actor=ALICE, now=last, **args)
            else:
                options = () if selector is None else ("--as-of", selector.isoformat())
                graph = succeeds(invoke(tmp_path, "graph", *options, now=last))
            assert set(graph) == {"nodes", "edges"}
            assert graph["nodes"] == sorted(
                graph["nodes"], key=lambda n: (n["created"], n["id"])
            )
            assert graph["edges"] == sorted(
                graph["edges"], key=lambda e: (e["created"], e["id"])
            )
            for node in graph["nodes"]:
                shown = project.show(node["id"], actor=ALICE, now=last, **args)
                assert node == {key: shown[key] for key in first}
            if selector is None:
                assert {n["id"] for n in graph["nodes"]} == {
                    first["id"],
                    second["id"],
                    replacement["id"],
                }
                assert (
                    next(n for n in graph["nodes"] if n["id"] == first["id"])["status"]
                    == "retired"
                )
                assert (
                    next(e for e in graph["edges"] if e["id"] == edge["id"])["retired"]
                    is True
                )
            elif selector == NOW:
                assert [n["id"] for n in graph["nodes"]] == [first["id"]]
                assert graph["nodes"][0]["status"] == "active"
                assert graph["edges"] == []
            else:
                assert {n["id"] for n in graph["nodes"]} == {first["id"], second["id"]}
                assert graph["edges"] == [edge]
        assert snapshot(project) == before
        assert git(tmp_path, "status", "--porcelain") == ""


@pytest.mark.parametrize(
    "door", ["api", pytest.param("cli", marks=pytest.mark.cli), "mcp"]
)
def test_profile_read_preserves_unused_declarations_without_callables(
    tmp_path, index_mode, door, monkeypatch, invoke
):
    monkeypatch.setenv("RG_TEST_CLI_INDEX_MODE", index_mode)
    with Project.init(
        repository(tmp_path), "test-conditional", actor=ALICE, now=NOW
    ) as project:
        before = snapshot(project)
        discard_index(project, index_mode)
        info = (
            project.profile_info(actor=ALICE, now=NOW)
            if door == "api"
            else succeeds(invoke(tmp_path, "profile"))
        )
        assert set(info) == {
            "name",
            "version",
            "installed_version",
            "node_types",
            "edge_types",
            "gates",
            "roles",
            "agent_guidance",
        }
        profile = project.profile
        assert info["name"] == "test-conditional"
        assert [n["name"] for n in info["node_types"]] == [
            n.name for n in profile.node_types
        ]
        assert all(
            set(n) == {"name", "prefix", "claimable", "schema"}
            for n in info["node_types"]
        )
        assert [e["name"] for e in info["edge_types"]] == [
            e.name for e in profile.edge_types
        ]
        assert all(
            set(e) == {"name", "allowed", "inherit", "schema"}
            for e in info["edge_types"]
        )
        assert info["roles"] == ["owner", "reviewer"]
        assert [
            g["when_fields"] for g in info["gates"] if g["node_type"] == "Task"
        ] == [{"review": True}, {"review": True}]
        assert json.loads(json.dumps(info)) == info
        assert snapshot(project) == before
        info["node_types"][0]["schema"]["mutated"] = True
        assert (
            "mutated"
            not in project.profile_info(actor=ALICE, now=NOW)["node_types"][0]["schema"]
        )


@pytest.mark.parametrize("door", ["api", "mcp"])
def test_profile_reports_loaded_version_under_as_of_and_never_calls_hooks(
    tmp_path, monkeypatch, door, invoke
):
    value = Profile.load("tasks")
    value.name = "safe-metadata"
    Profile.register(value)
    root = repository(tmp_path)
    with Project.init(root, value.name, actor=ALICE, now=NOW):
        pass
    value.version = "1.1"
    value.agent_guidance = "Data-only profile guidance."

    def forbidden(*args):
        pytest.fail("Metadata reads cannot execute a profile callback")

    value.validators = [forbidden]
    value.event_hooks = [forbidden]
    value.expansion_hook = forbidden
    value.renderers = {"unsafe": forbidden}
    value.migrations = {"1.0": forbidden}
    Profile.register(value)
    with Project.open(root, read_only=True) as project:
        monkeypatch.setattr("researchgraph.project.write_lock", forbidden)
        info = (
            project.profile_info(actor=ALICE, now=NOW, as_of=NOW)
            if door == "api"
            else succeeds(
                invoke(root, "profile", "--as-of", NOW.isoformat(), "--read-only")
            )
        )
        assert (info["name"], info["version"], info["installed_version"]) == (
            "safe-metadata",
            "1.0",
            "1.1",
        )
        assert info["agent_guidance"] == value.agent_guidance
        assert (
            not {
                "validators",
                "event_hooks",
                "renderers",
                "migrations",
                "expansion_hook",
            }
            & info.keys()
        )
        graph = (
            project.graph(actor=ALICE, now=NOW)
            if door == "api"
            else succeeds(invoke(root, "graph", "--read-only"))
        )
        assert graph == {"nodes": [], "edges": []}


@settings(max_examples=12, deadline=None)
@given(
    titles=st.lists(
        st.text(
            alphabet="abcdefghijklmnopqrstuvwxyz0123456789", min_size=1, max_size=20
        ),
        min_size=2,
        max_size=5,
    ),
    retire=st.booleans(),
    agent=st.booleans(),
)
def test_bulk_read_invariants_hold_for_append_only_graphs(titles, retire, agent):
    with TemporaryDirectory() as directory:
        root = repository(Path(directory))
        with Project.init(root, "tasks", actor=ALICE, now=NOW) as project:
            nodes = [
                project.propose("Note", title, actor=ALICE, now=NOW) for title in titles
            ]
            for source, target in pairwise(nodes):
                project.link(
                    "part-of", source["id"], target["id"], actor=ALICE, now=NOW
                )
            if retire:
                project.retire(
                    nodes[0]["id"], "property test retirement", actor=ALICE, now=NOW
                )
        # Reads admit either actor on a read-only owner; repeated cache loss
        # cannot change membership, ordering, metadata or authoritative bytes.
        actor = Actor("agent", "reader") if agent else ALICE
        with Project.open(root, read_only=True) as project:
            before = snapshot(project)
            graph = project.graph(actor=actor, now=NOW)
            info = project.profile_info(actor=actor, now=NOW)
            assert {n["id"] for n in graph["nodes"]} == {n["id"] for n in nodes}
            assert len(graph["edges"]) == len(nodes) - 1
            discard_index(project, "deleted")
            assert project.graph(actor=actor, now=NOW) == graph
            assert project.profile_info(actor=actor, now=NOW) == info
            assert snapshot(project) == before
