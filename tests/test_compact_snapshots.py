"""Private epoch compaction must never leak aliases through public snapshots."""

import json
import sys
from copy import deepcopy
from datetime import timedelta

import pytest

from researchgraph.model import Actor, Approval, Artifact, Lease, Status
from researchgraph.profiles import Profile
from researchgraph.project import Project
from researchgraph.queries import query_source
from researchgraph.store.io import dump_node
from tests.helpers import ALICE, NOW
from tests.test_queries import call, record, relation, restore


@pytest.fixture
def compact_profile(monkeypatch):
    monkeypatch.setattr(Profile, "_registry", dict(Profile._registry))
    profile = Profile.load("research")
    profile.name = "compact-snapshots"
    profile.node_type("Task").schema["properties"]["nested"] = {}
    profile.edge_type("tests").schema["properties"]["expected"] = {}
    Profile.register(profile)
    return profile.name


def mutate_json(value):
    """Poison every mutable container, including repeated nested projections."""
    if isinstance(value, dict):
        for item in list(value.values()):
            mutate_json(item)
        value["poison"] = []
    elif isinstance(value, list):
        for item in value:
            mutate_json(item)
        value.append({"poison": []})


def test_empty_typed_getters_detach_records_and_fields(tmp_path):
    nodes = [record("Task", letter) for letter in "abc"]
    edges = [
        relation("a", "relates-to", nodes[0], nodes[1]),
        relation("b", "relates-to", nodes[0], nodes[2]),
    ]
    project = restore(tmp_path, nodes, edges)
    expected_nodes = [n.to_dict() for n in project.get_nodes()]
    expected_edges = [e.to_dict() for e in project.get_edges()]
    returned = project.get_nodes()
    containers = [
        getattr(n, field)
        for n in returned
        for field in ("fields", "tags", "approvals", "artifacts")
    ]
    assert len({id(value) for value in containers}) == len(containers)
    returned[0].fields["local"] = []
    returned[0].tags.append("local")
    returned[0].approvals.append(Approval(ALICE, NOW, "local"))
    returned[0].artifacts.append(Artifact("local", "a" * 64, 1, NOW, ALICE))
    assert [n.to_dict() for n in returned[1:]] == expected_nodes[1:]
    returned_edges = project.get_edges()
    returned_edges[0].fields["local"] = []
    assert returned_edges[1].fields == {}
    single = project.get_node(nodes[0].id)
    assert len({id(single.tags), id(single.approvals), id(single.artifacts)}) == 3
    single.tags.append("single")
    assert single.approvals == single.artifacts == []
    assert [n.to_dict() for n in project.get_nodes()] == expected_nodes
    assert [e.to_dict() for e in project.get_edges()] == expected_edges


def test_private_empty_graph_and_templates_are_compacted(tmp_path):
    nodes = [record("Task", letter) for letter in "abcdefgh"]
    edges = [
        relation(letter, "relates-to", nodes[0], target)
        for letter, target in zip("abcdefg", nodes[1:])
    ]
    project = restore(tmp_path, nodes, edges)
    source_bytes = {
        path: path.read_bytes() for path in (project.path / "graph").rglob("*.yaml")
    }
    head = project._git.head()
    before = call(project, "ready")
    source = query_source(project)
    private = list(project.index._nodes.values())
    templates = [source._template(node) for node in private]
    for field in ("fields", "approvals", "artifacts", "tags"):
        assert len({id(getattr(n, field)) for n in private}) == 1
        assert all(t[field] is getattr(private[0], field) for t in templates)
    for template in templates:
        assert list(template) == list(nodes[0].to_dict())
        assert template["author"] == ALICE.to_dict()
    assert source.clone_ready_batch() == before["items"]
    assert all(list(row) == list(nodes[0].to_dict()) for row in before["items"])
    assert type(private[0].fields) is dict
    assert all(
        type(getattr(private[0], field)) is list
        for field in ("approvals", "artifacts", "tags")
    )
    assert (
        len({id(private[0].approvals), id(private[0].artifacts), id(private[0].tags)})
        == 3
    )
    assert (
        len(
            {id(n.author) for n in private}
            | {id(e.author) for e in project.index._edges.values()}
        )
        == 1
    )
    assert (
        len(
            {id(e.fields) for e in project.index._edges.values()}
            | {id(n.fields) for n in private}
        )
        == 1
    )
    assert len({id(t["author"]) for t in templates}) == 1
    assert {n.id: n.to_dict() for n in private} == {n.id: n.to_dict() for n in nodes}
    assert json.dumps(before, sort_keys=True) == json.dumps(
        call(project, "ready"), sort_keys=True
    )
    mutate_json(before)
    assert call(project, "ready") == {"items": [n.to_dict() for n in nodes]}
    assert {path: path.read_bytes() for path in source_bytes} == source_bytes
    assert project._git.head() == head


def test_valid_sqlite_reload_uses_new_index_scoped_pools(tmp_path):
    nodes = [record("Task", letter) for letter in "ab"]
    project = restore(tmp_path, nodes)
    expected = call(project, "ready")
    old = query_source(project)
    with Project.open(project.path) as reopened:
        assert call(reopened, "ready") == expected
        source = query_source(reopened)
        a, b = source.nodes.values()
        assert a.fields is b.fields
        assert a.approvals is b.approvals
        assert a.artifacts is b.artifacts
        assert a.tags is b.tags
        assert a.author is b.author
        assert a.fields is not old.nodes[a.id].fields
        assert a.author is not old.nodes[a.id].author
        assert source._template(a)["author"] is source._template(b)["author"]


def test_nonempty_containers_and_model_events_are_not_interned(
    tmp_path, compact_profile
):
    nodes = [
        record("Task", letter, fields={"nested": {"items": []}}, tags=["tag"])
        for letter in "ab"
    ]
    for n in nodes:
        n.approvals = [Approval(ALICE, NOW, "approve")]
        n.artifacts = [Artifact(f"artifacts/{n.id}/shared", "a" * 64, 1, NOW, ALICE)]
    project = restore(tmp_path, nodes, profile=compact_profile)
    source = query_source(project)
    a, b = source.nodes.values()
    for field in ("fields", "tags", "approvals", "artifacts"):
        assert getattr(a, field) is not getattr(b, field)
    assert a.fields["nested"] is not b.fields["nested"]
    assert a.approvals[0] is not b.approvals[0]
    assert a.artifacts[0] is not b.artifacts[0]
    returned = project.get_nodes()
    returned[0].approvals[0].note = "local"
    returned[0].artifacts[0].bytes = 99
    returned[0].fields["nested"]["items"].append("local")
    assert returned[1].to_dict() == nodes[1].to_dict()
    assert project.get_node(nodes[0].id).to_dict() == nodes[0].to_dict()


def test_actor_interning_uses_all_metadata_in_nodes_leases_and_events(tmp_path):
    actors = [
        Actor("agent", "same", email, model, version)
        for email, model, version in [
            ("a@x", "m1", "v1"),
            ("b@x", "m1", "v1"),
            ("a@x", "m2", "v1"),
            ("a@x", "m1", "v2"),
        ]
    ]
    nodes = [record("Task", letter) for letter in "abcd"]
    for n, actor in zip(nodes, actors):
        n.author = actor
        n.lease = Lease(actor, NOW, NOW + timedelta(seconds=10))
        n.status = Status.RUNNING
        n.approvals = [Approval(actor, NOW, "claim")]
        n.artifacts = [Artifact(f"artifacts/{n.id}/shared", "a" * 64, 1, NOW, actor)]
    project = restore(tmp_path, nodes)
    source = query_source(project)
    private = list(source.nodes.values())
    assert len({id(n.author) for n in private}) == 4
    for original, n in zip(nodes, private):
        assert (
            n.author is n.lease.actor is n.approvals[0].actor is n.artifacts[0].added_by
        )
        assert n.to_dict() == original.to_dict()
        actual = source.serialize(n)
        assert actual == original.to_dict()
        template = source.serialized[n.id][0]
        assert (
            template["author"]
            is template["lease"]["actor"]
            is template["approvals"][0]["actor"]
            is template["artifacts"][0]["added_by"]
        )
        assert (
            len(
                {
                    id(actual["author"]),
                    id(actual["lease"]["actor"]),
                    id(actual["approvals"][0]["actor"]),
                    id(actual["artifacts"][0]["added_by"]),
                }
            )
            == 4
        )
        mutate_json(actual)
        assert source.serialize(n) == original.to_dict()
        history = call(project, "history", n.id)
        for event in history:
            if event["kind"] == "approval":
                assert event["actor"] == original.author.to_dict()
            elif event["kind"] == "artifact":
                assert event["added_by"] == original.author.to_dict()
    assert len(source._actor_templates) == 4


def test_compacted_source_public_write_changes_only_target(tmp_path):
    nodes = [record("Task", letter) for letter in "ab"]
    project = restore(tmp_path, nodes)
    call(project, "ready")
    old = query_source(project)
    project.claim(nodes[0].id, actor=ALICE, now=NOW + timedelta(seconds=1))
    assert project.get_node(nodes[0].id).status == Status.RUNNING
    assert project.get_node(nodes[1].id).to_dict() == nodes[1].to_dict()
    assert old.nodes[nodes[0].id].to_dict() == nodes[0].to_dict()
    call(project, "ready")
    assert query_source(project) is not old


@pytest.mark.parametrize("change", ["source", "delete", "corrupt", "profile"])
def test_epoch_changes_drop_private_pools_and_derived_templates(
    tmp_path, change, monkeypatch
):
    nodes = [record("Task", letter) for letter in "ab"]
    project = restore(tmp_path, nodes)
    call(project, "ready")
    old = query_source(project)
    old.serialize(old.nodes[nodes[0].id])
    old_fields = old.nodes[nodes[0].id].fields
    if change == "source":
        nodes[0].title = "new source title"
        (project.path / f"graph/nodes/{nodes[0].id}.yaml").write_text(
            dump_node(nodes[0].to_dict())
        )
    elif change == "delete":
        project.index.path.unlink()
    elif change == "corrupt":
        with project.index.connection:
            project.index.connection.execute("UPDATE nodes SET status='retired'")
    else:
        monkeypatch.setattr(project, "profile", deepcopy(project.profile))
        project.profile.node_type("Task").claimable = False
    result = call(project, "ready")
    current = query_source(project)
    assert current is not old
    assert current.nodes[nodes[0].id].fields is not old_fields
    assert current.nodes[nodes[0].id].author is not old.nodes[nodes[0].id].author
    assert not current.serialized
    assert result == {
        "items": [] if change == "profile" else [n.to_dict() for n in nodes]
    }
    assert old.nodes[nodes[0].id].title == "Task a"


def test_public_projection_families_and_history_detach_all_mutables(
    tmp_path, compact_profile
):
    q = record("Question", "a")
    h = record("Hypothesis", "a")
    p = record("Task", "a", fields={"nested": []})
    p.approvals = [Approval(ALICE, NOW, "approve"), Approval(ALICE, NOW, "approve")]
    p.artifacts = [
        Artifact(f"artifacts/{p.id}/shared", "a" * 64, 1, NOW, ALICE),
        Artifact(f"artifacts/{p.id}/shared", "a" * 64, 1, NOW, ALICE),
    ]
    edges = [
        relation("a", "answers", h, q),
        relation("b", "tests", p, h, fields={"expected": {"values": []}}),
    ]
    project = restore(tmp_path, [q, h, p], edges, profile=compact_profile)
    detached = project.get_node(p.id)
    assert detached.approvals[0] is not detached.approvals[1]
    assert detached.artifacts[0] is not detached.artifacts[1]
    detached.approvals[0].note = "only first"
    detached.artifacts[0].bytes = 99
    assert detached.approvals[1].note is None
    assert detached.artifacts[1].bytes == 1
    assert project.get_node(p.id).to_dict() == p.to_dict()
    for name, args in [
        ("ready", ()),
        ("show", (p.id,)),
        ("matrix", (q.id,)),
        ("predictions", (h.id,)),
        ("history", (p.id,)),
    ]:
        for boundary in (None, NOW):
            result = call(project, name, *args, as_of=boundary)
            expected = deepcopy(result)
            mutate_json(result)
            assert call(project, name, *args, as_of=boundary) == expected
    history = call(project, "history", p.id)
    for kind in ("approval", "artifact"):
        events = [event for event in history if event["kind"] == kind]
        assert [event["ordinal"] for event in events] == [0, 1]
        assert all(event["commit"] for event in events)
    predictions = call(project, "predictions", h.id)
    predictions[0]["expected"]["values"].append("local")
    assert predictions[0]["fields"]["expected"]["values"] == []
    matrix = call(project, "matrix", q.id)
    matrix["rows"][0]["source"]["author"]["name"] = "local"
    assert matrix["columns"][0]["author"]["name"] == ALICE.name


def test_historical_overlays_keep_base_read_only_and_empty_fields_separate(tmp_path):
    task = record("Task", "a")
    project = restore(tmp_path, [task])
    project.claim(task.id, actor=ALICE, now=NOW + timedelta(seconds=1))
    project.release(task.id, actor=ALICE, now=NOW + timedelta(seconds=3))
    call(project, "ready", now=NOW + timedelta(seconds=4), as_of=NOW)
    source = query_source(project)
    base = source.nodes[task.id]
    expected_base = base.to_dict()
    earlier, _ = source.at(NOW)
    running, _ = source.at(NOW + timedelta(seconds=2))
    assert earlier[task.id] is not base
    assert earlier[task.id].approvals is source._empty_approvals
    assert earlier[task.id].artifacts is source._empty_artifacts
    assert earlier[task.id].tags is source._empty_tags
    assert (
        len(
            {
                id(earlier[task.id].approvals),
                id(earlier[task.id].artifacts),
                id(earlier[task.id].tags),
            }
        )
        == 3
    )
    assert running[task.id].status == Status.RUNNING
    assert len(running[task.id].approvals) == 1
    assert len(base.approvals) == 2
    first = source.serialize(earlier[task.id])
    saved = deepcopy(first)
    mutate_json(first)
    assert source.serialize(earlier[task.id]) == saved
    assert base.to_dict() == expected_base


def assert_closed_epoch_owner(index, old_pools):
    assert index._query_source is None
    assert index._nodes == {}
    assert index._edges == {}
    for name, old in old_pools.items():
        current = getattr(index, name)
        assert current is None
        assert old == ({} if name == "_empty_fields" else [])
        assert current is not old


def test_close_releases_owned_epoch_models_without_a_gc_policy_change(tmp_path):
    import weakref

    nodes = [record("Task", letter) for letter in "ab"]
    edges = [relation("a", "relates-to", *nodes)]
    project = restore(tmp_path, nodes, edges)
    snapshot = call(project, "ready")
    expected = deepcopy(snapshot)
    source = query_source(project)
    index = project.index
    assert index._query_source is source
    assert index._nodes and index._edges
    old_pools = {
        name: getattr(index, name)
        for name in (
            "_empty_fields",
            "_empty_approvals",
            "_empty_artifacts",
            "_empty_tags",
        )
    }
    source_ref = weakref.ref(source)
    node_ref = weakref.ref(next(iter(source.nodes.values())))
    del source
    project.close()
    # A closed owner may remain referenced by its caller. It must not keep an
    # entire graph alive until the unrelated Project/Index cycle is collected.
    assert_closed_epoch_owner(index, old_pools)
    assert snapshot == expected
    # Immediate weakref death is a CPython refcount observation, not a GC rule.
    if sys.implementation.name == "cpython":
        assert source_ref() is None
        assert node_ref() is None


def test_index_close_reopens_with_fresh_owned_models_without_mutating_old_source(
    tmp_path,
):
    project = restore(tmp_path, [record("Task", "a")])
    old = query_source(project)
    expected = call(project, "ready")
    identifier = next(iter(old.nodes))
    old_record = old.nodes[identifier].to_dict()
    old_nodes = project.index._nodes
    old_edges = project.index._edges
    old_pools = {
        name: getattr(project.index, name)
        for name in (
            "_empty_fields",
            "_empty_approvals",
            "_empty_artifacts",
            "_empty_tags",
        )
    }
    project.index.close()
    assert_closed_epoch_owner(project.index, old_pools)
    assert project.index._nodes is not old_nodes
    assert project.index._edges is not old_edges
    assert old.nodes is old_nodes
    assert old.nodes[identifier].to_dict() == old_record
    assert old.clone_ready_batch() == expected["items"]
    assert call(project, "ready") == expected
    new = query_source(project)
    assert new is not old
    assert new.nodes[identifier] is not old.nodes[identifier]
    for name, previous in old_pools.items():
        assert getattr(project.index, name) is not previous
    new_snapshot = call(project, "ready")
    mutate_json(new_snapshot)
    assert call(project, "ready") == expected
    assert old.clone_ready_batch() == expected["items"]
    assert old.nodes[identifier].to_dict() == old_record


def test_static_visibility_does_not_retain_redundant_dynamic_timelines(tmp_path):
    nodes = [record("Task", letter) for letter in "abc"]
    nodes[-1].approvals = [Approval(ALICE, NOW, "approve")]
    with restore(tmp_path, nodes) as project:
        source = query_source(project)
        assert set(source.timelines) == {nodes[-1].id}
        assert set(source.starts) == {nodes[-1].id}
        assert set(source.static_bounds) == {n.id for n in nodes[:-1]}
        assert source.at(NOW - timedelta(seconds=1))[0] == {}
        assert set(source.at(NOW)[0]) == {n.id for n in nodes}
        assert set(source.at(NOW + timedelta(seconds=1))[0]) == {n.id for n in nodes}


def test_published_model_epoch_already_owns_its_query_projection(tmp_path):
    nodes = [record("Task", "a")]
    with restore(tmp_path, nodes) as project:
        old = project.index._query_source
        assert old is not None
        assert old.nodes is project.index._nodes
        path = project.path / f"graph/nodes/{nodes[0].id}.yaml"
        updated = nodes[0].to_dict()
        updated["title"] = "new epoch"
        path.write_text(dump_node(updated))
        assert project.get_node(nodes[0].id).title == "new epoch"
        new = project.index._query_source
        assert new is not None and new is not old
        assert new.nodes is project.index._nodes
        assert old.nodes[nodes[0].id].title == "Task a"
