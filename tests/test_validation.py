"""Candidate-write contracts, independent of future public operation builders."""

from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from researchgraph.model import (
    Actor,
    Approval,
    Artifact,
    Edge,
    Lease,
    Node,
    Status,
    Write,
)
from researchgraph.profiles import Gate, Profile
from researchgraph.project import Project
from researchgraph.validation import (
    validate_changes,
    validate_graph,
    validate_permissions,
)
from tests.helpers import ALICE, NOW, git, node, refusal, repository, write

BOT = Actor("agent", "worker", model="old")


def relation(kind, source, target, suffix="aaaaaaaa", **kwargs):
    return Edge("ed-" + suffix, kind, source, target, ALICE, NOW, **kwargs)


def event(before, op, status=None, actor=ALICE, role=None, now=NOW, **args):
    after = deepcopy(before)
    after.status = status or before.status
    after.updated = now
    after.approvals.append(
        Approval(actor, now, "superseded" if op == "supersede" else op, role)
    )
    return Write(op, actor, role, now, {"node": before.id, **args}, [after], [])


@given(
    st.lists(
        st.sampled_from(["blocks", "derived-from", "part-of"]), min_size=2, max_size=12
    )
)
def test_combined_relation_dag(kinds):
    nodes = {
        "ta-" + chr(97 + i) * 8: node("ta-" + chr(97 + i) * 8)
        for i in range(len(kinds))
    }
    ids = list(nodes)
    edges = [
        relation(kind, ids[i], ids[(i + 1) % len(ids)], chr(97 + i) * 8)
        for i, kind in enumerate(kinds)
    ]
    profile = Profile.load("research")
    refusal("cycle", validate_graph, profile, nodes, {e.id: e for e in edges})
    edges[-1].retired, edges[-1].retired_at = True, NOW
    validate_graph(profile, nodes, {e.id: e for e in edges})


def test_cites_and_supersedes_join_the_same_dag():
    claim = Node("cl-aaaaaaaa", "Claim", "claim", "", ALICE, NOW)
    result = Node(
        "re-aaaaaaaa", "Result", "result", "", ALICE, NOW, status=Status.ACTIVE
    )
    replacement = deepcopy(result)
    replacement.id = "re-bbbbbbbb"
    edges = [
        relation("cites", claim.id, result.id),
        relation("derived-from", result.id, replacement.id, "bbbbbbbb"),
        relation("supersedes", replacement.id, result.id, "cccccccc"),
    ]
    nodes = {n.id: n for n in [claim, result, replacement]}
    refusal(
        "cycle",
        validate_graph,
        Profile.load("research"),
        nodes,
        {e.id: e for e in edges},
    )
    edges[1].to = claim.id
    edges[2].retired, edges[2].retired_at = True, NOW
    refusal(
        "cycle",
        validate_graph,
        Profile.load("research"),
        nodes,
        {e.id: e for e in edges},
    )


@pytest.mark.parametrize(
    "key,value",
    [
        ("title", "changed"),
        ("body", "changed"),
        ("fields", {"kind": "new"}),
        ("author", BOT),
        ("created", NOW + timedelta(seconds=1)),
        ("type", "Result"),
    ],
)
def test_immutable_nodes_refuse_without_observable_changes(project, key, value):
    project.apply(write(node()))
    original = project.get_node("ta-aaaaaaaa")
    changed = deepcopy(original)
    setattr(changed, key, value)
    before = snapshot(project)
    refusal(
        "schema", project.apply, write(changed, op="tag", args={"node": original.id})
    )
    assert snapshot(project) == before


def snapshot(project):
    return (
        {
            p.relative_to(project.path).as_posix(): p.read_bytes()
            for p in (project.path / "graph").rglob("*.yaml")
        },
        git(project.path, "rev-parse", "HEAD"),
        [n.to_dict() for n in project.get_nodes()],
        [e.to_dict() for e in project.get_edges()],
    )


@pytest.mark.parametrize(
    "key,value",
    [
        ("type", "blocks"),
        ("from_", "ta-bbbbbbbb"),
        ("to", "ta-aaaaaaaa"),
        ("author", BOT),
        ("created", NOW + timedelta(seconds=1)),
        ("fields", {"x": 1}),
    ],
)
def test_immutable_edges(project, key, value):
    edge = relation("relates-to", "ta-aaaaaaaa", "ta-bbbbbbbb")
    project.apply(write(node(), node("ta-bbbbbbbb"), edges=[edge]))
    setattr(edge, key, value)
    refusal("schema", project.apply, write(edges=[edge], op="link"))


@pytest.mark.parametrize("attribute", ["approvals", "artifacts"])
def test_append_only_prefix(project, attribute):
    value = node()
    if attribute == "approvals":
        value.approvals = [Approval(ALICE, NOW, "claim")]
    else:
        value.artifacts = [Artifact("artifacts/ta-aaaaaaaa/a", "0" * 64, 0, NOW, ALICE)]
    # Restored snapshots preserve historical lists; no creation bypass is needed.
    project.apply(write(value, op="import"), commit=False)
    value = project.get_node(value.id)
    setattr(value, attribute, [])
    refusal("schema", project.apply, write(value, op="tag"), commit=False)


def test_edge_retirement_recreation_and_no_resurrection(project):
    a, b = node(), node("ta-bbbbbbbb")
    edge = relation("blocks", a.id, b.id)
    project.apply(write(a, b, edges=[edge]), commit=False)
    refusal(
        "duplicate_edge",
        project.apply,
        write(edges=[relation("blocks", a.id, b.id, "bbbbbbbb")], op="link"),
        commit=False,
    )
    edge.retired, edge.retired_at = True, NOW
    change = event(a, "retire", Status.RETIRED)
    change.edges = [edge]
    project.apply(change, commit=False)
    project.apply(
        write(edges=[relation("blocks", a.id, b.id, "bbbbbbbb")], op="link"),
        commit=False,
    )
    edge.retired, edge.retired_at = False, None
    refusal("schema", project.apply, write(edges=[edge], op="link"), commit=False)


def test_endpoints_and_single_supersession():
    profile = Profile.load("research")
    nodes = {n.id: n for n in [node(), node("ta-bbbbbbbb"), node("ta-cccccccc")]}
    edge = relation("tests", "ta-aaaaaaaa", "ta-bbbbbbbb", fields={"expected": "yes"})
    refusal("edge_endpoints", validate_graph, profile, nodes, {edge.id: edge})
    edge.to = "ta-dddddddd"
    refusal("not_found", validate_graph, profile, nodes, {edge.id: edge})
    edges = [
        relation("supersedes", "ta-bbbbbbbb", "ta-aaaaaaaa"),
        relation("supersedes", "ta-cccccccc", "ta-aaaaaaaa", "bbbbbbbb"),
    ]
    refusal("schema", validate_graph, profile, nodes, {e.id: e for e in edges})
    nodes["ta-aaaaaaaa"].status = Status.RETIRED
    validate_graph(profile, nodes, {edges[0].id: edges[0]})


@pytest.mark.parametrize(
    "status,lease",
    [
        (Status.RUNNING, None),
        (Status.ACTIVE, Lease(ALICE, NOW, NOW + timedelta(seconds=1))),
        (Status.RUNNING, Lease(ALICE, NOW, NOW)),
    ],
)
def test_lease_invariant(project, status, lease):
    value = node()
    value.status, value.lease = status, lease
    refusal("schema", project.apply, write(value, op="import"), commit=False)


@pytest.mark.parametrize(
    "op", ["init", "reject", "instruct", "expand", "budget", "retire", "import"]
)
def test_human_permission_precedes_role(op):
    change = Write(op, BOT, "missing", NOW, {}, [], [])
    refusal(
        "human_only_approval",
        validate_permissions,
        Profile.load("research"),
        change,
        {},
    )


@pytest.mark.parametrize("op", ["complete", "fail", "renew", "release"])
def test_holder_identity_and_human_release(op):
    value = node()
    value.status, value.lease = (
        Status.RUNNING,
        Lease(BOT, NOW, NOW + timedelta(seconds=10)),
    )
    profile = Profile.load("research")
    same = Actor("agent", "worker", model="new", version="2")
    validate_permissions(
        profile,
        Write(op, same, "reviewer", NOW, {"node": value.id}, [], []),
        {value.id: value},
    )
    other = Actor("agent", "other")
    refusal(
        "not_lease_holder",
        validate_permissions,
        profile,
        Write(op, other, None, NOW, {"node": value.id}, [], []),
        {value.id: value},
    )
    change = Write(op, ALICE, None, NOW, {"node": value.id}, [], [])
    if op == "release":
        validate_permissions(profile, change, {value.id: value})
    else:
        refusal(
            "not_lease_holder", validate_permissions, profile, change, {value.id: value}
        )


@pytest.mark.parametrize(
    "kind,allowed,role,expected",
    [
        ("agent", False, "owner", "human_only_approval"),
        ("agent", True, "reviewer", "wrong_role"),
        ("agent", True, "owner", None),
        ("human", False, "owner", None),
    ],
)
def test_wildcard_gate_admission(kind, allowed, role, expected):
    profile = Profile.load("research")
    profile.gates = [Gate(node_type="*", roles=["owner"], agents_allowed=allowed)]
    value = node()
    value.status = Status.PROPOSED
    change = Write(
        "approve", Actor(kind, "reviewer"), role, NOW, {"node": value.id}, [], []
    )
    if expected:
        refusal(expected, validate_permissions, profile, change, {value.id: value})
    else:
        validate_permissions(profile, change, {value.id: value})
    value.status = Status.ACTIVE
    refusal(
        "wrong_role" if expected == "wrong_role" else "not_proposed",
        validate_permissions,
        profile,
        change,
        {value.id: value},
    )


@pytest.mark.parametrize(
    "type_,prefix,expected",
    [
        ("Task", "ta", None),
        ("Result", "re", None),
        ("Hypothesis", "hy", "agent_cannot_supersede_type"),
    ],
)
def test_agent_supersession_scope(type_, prefix, expected):
    value = Node(
        prefix + "-aaaaaaaa", type_, "title", "", ALICE, NOW, status=Status.ACTIVE
    )
    change = Write("supersede", BOT, None, NOW, {"node": value.id}, [], [])
    if expected:
        refusal(
            expected,
            validate_permissions,
            Profile.load("research"),
            change,
            {value.id: value},
        )
    else:
        validate_permissions(Profile.load("research"), change, {value.id: value})


def test_approve_gate_status_and_callback_once(project):
    value = Node("hy-aaaaaaaa", "Hypothesis", "H", "", ALICE, NOW)
    project.apply(write(value), commit=False)
    calls = []
    project.profile.validators.append(lambda p, w: calls.append((p, w)))
    change = event(value, "approve", Status.ACTIVE, what="activate")
    project.apply(change, commit=False)
    assert len(calls) == 1 and calls[0][0] is project
    assert project.get_node(value.id).approvals[0].what == "approve"
    refusal("not_proposed", project.apply, change, commit=False)
    assert len(calls) == 1


@pytest.mark.parametrize("op", ["propose", "instruct", "fail", "supersede", "expand"])
def test_generated_nodes_follow_gates(project, op):
    value = Node("hy-aaaaaaaa", "Hypothesis", "H", "", ALICE, NOW, status=Status.ACTIVE)
    refusal("schema", project.apply, write(value, op=op), commit=False)


@pytest.mark.parametrize(
    "op,before,after",
    [
        ("approve", Status.PROPOSED, Status.ACTIVE),
        ("reject", Status.PROPOSED, Status.RETIRED),
        ("release", Status.RUNNING, Status.ACTIVE),
        ("complete", Status.RUNNING, Status.DONE),
        ("fail", Status.RUNNING, Status.FAILED),
        ("supersede", Status.DONE, Status.RETIRED),
        ("supersede", Status.FAILED, Status.RETIRED),
        ("retire", Status.FAILED, Status.RETIRED),
    ],
)
def test_permitted_lifecycle_transitions(op, before, after):
    value = node()
    value.status = before
    if before == Status.RUNNING:
        value.lease = Lease(ALICE, NOW, NOW + timedelta(seconds=10))
    change = event(value, op, after)
    change.nodes[0].lease = None
    validate_changes(Profile.load("research"), change, {value.id: value}, {})


@pytest.mark.parametrize(
    "before,after,op",
    [
        (Status.ACTIVE, Status.DONE, "complete"),
        (Status.DONE, Status.ACTIVE, "approve"),
        (Status.FAILED, Status.RUNNING, "claim"),
        (Status.RETIRED, Status.ACTIVE, "tag"),
        (Status.RUNNING, Status.RETIRED, "retire"),
        (Status.PROPOSED, Status.ACTIVE, "propose"),
    ],
)
def test_disallowed_lifecycle_transitions(before, after, op):
    value = node()
    value.status = before
    change = event(value, op, after)
    refusal(
        "schema",
        validate_changes,
        Profile.load("research"),
        change,
        {value.id: value},
        {},
    )


def test_renewal_never_shortens_and_expired_holder_keeps_rights(project):
    project.apply(write(node()), commit=False)
    claimed = event(node(), "claim", Status.RUNNING, actor=BOT, lease_seconds=10)
    claimed.nodes[0].lease = Lease(BOT, NOW, NOW + timedelta(seconds=10))
    project.apply(claimed, commit=False)
    value = project.get_node("ta-aaaaaaaa")
    renewed = event(value, "renew", actor=BOT, lease_seconds=1)
    project.apply(renewed, commit=False)
    renewed = event(
        project.get_node(value.id),
        "renew",
        actor=BOT,
        now=NOW + timedelta(seconds=20),
        lease_seconds=5,
    )
    renewed.nodes[0].lease.expires_at = NOW + timedelta(seconds=25)
    project.apply(renewed, commit=False)
    shortened = event(
        project.get_node(value.id), "renew", actor=BOT, now=renewed.now, lease_seconds=1
    )
    shortened.nodes[0].lease.expires_at -= timedelta(seconds=1)
    refusal("schema", project.apply, shortened, commit=False)


def test_unsupported_reserved_type_and_wrong_role_order():
    profile = Profile.load("tasks")
    profile.node_types = [n for n in profile.node_types if n.name != "Instruction"]
    change = Write("instruct", ALICE, "unknown", NOW, {}, [], [])
    refusal("wrong_role", validate_permissions, profile, change, {})
    change.role = None
    refusal("unsupported_by_profile", validate_permissions, profile, change, {})


def test_shared_not_found_before_newer_profile(project):
    (project.path / ".researchgraph/profile.yaml").write_text(
        'name: research\nversion: "9.0"\n'
    )
    refusal(
        "not_found",
        project.apply,
        write(op="tag", args={"node": "ta-aaaaaaaa"}),
        commit=False,
    )
    refusal(
        "clock_skew",
        project.apply,
        write(now=NOW - timedelta(seconds=1), op="tag", args={"node": "ta-aaaaaaaa"}),
    )


@given(st.lists(st.booleans(), min_size=1, max_size=8))
@settings(max_examples=8, deadline=None)
def test_valid_invalid_sequence_cache_deletion_equivalence(accepted):
    with TemporaryDirectory() as directory:
        project = Project.init(repository(Path(directory)), actor=ALICE, now=NOW)
        project.apply(write(node()), commit=False)
        for i, valid in enumerate(accepted):
            changed = project.get_node("ta-aaaaaaaa")
            changed.priority = i
            changed.updated = NOW + timedelta(seconds=i)
            if not valid:
                changed.title = "immutable mutation"
                before = snapshot(project)
                refusal(
                    "schema",
                    project.apply,
                    write(changed, op="tag", now=changed.updated),
                    commit=False,
                )
                assert snapshot(project) == before
            else:
                project.apply(
                    write(changed, op="tag", now=changed.updated), commit=False
                )
            before = snapshot(project)
            (project.path / ".researchgraph/index.sqlite").unlink()
            assert snapshot(project) == before
        project.close()


@pytest.mark.parametrize("op", ["link", "propose"])
def test_cycle_precedes_duplicate_edge(project, op):
    a, b = node(), node("ta-bbbbbbbb")
    project.apply(write(a, b, edges=[relation("blocks", a.id, b.id)]), commit=False)
    duplicate = relation("blocks", a.id, b.id, "bbbbbbbb")
    cycle = relation("part-of", b.id, a.id, "cccccccc")
    before = snapshot(project)
    refusal(
        "cycle", project.apply, write(edges=[duplicate, cycle], op=op), commit=False
    )
    assert snapshot(project) == before


@pytest.mark.parametrize(
    "op,expected", [("link", "edge_endpoints"), ("propose", "schema")]
)
def test_operation_specific_endpoint_schema_precedence(project, op, expected):
    a, b = node(), node("ta-bbbbbbbb")
    project.apply(write(a, b), commit=False)
    invalid = relation("tests", a.id, b.id, fields={"expected": 1})
    refusal(expected, project.apply, write(edges=[invalid], op=op), commit=False)


@pytest.mark.parametrize(
    "metadata",
    [{"email": "wrong@example.test"}, {"model": "wrong"}, {"version": "wrong"}],
)
def test_generated_node_requires_exact_actor_metadata(project, metadata):
    value = node()
    value.author = Actor(ALICE.kind, ALICE.name, **metadata)
    refusal("schema", project.apply, write(value), commit=False)


@pytest.mark.parametrize("mutation", ["identity", "metadata", "created"])
def test_generated_edge_requires_exact_actor_and_time(project, mutation):
    a, b = node(), node("ta-bbbbbbbb")
    project.apply(write(a, b), commit=False)
    edge = relation("relates-to", a.id, b.id)
    if mutation == "identity":
        edge.author = BOT
    elif mutation == "metadata":
        edge.author = Actor(ALICE.kind, ALICE.name, email="wrong@example.test")
    else:
        edge.created -= timedelta(seconds=1)
    refusal("schema", project.apply, write(edges=[edge], op="link"), commit=False)


@pytest.fixture
def numeric_project(tmp_path, monkeypatch):
    from researchgraph.profiles import EdgeType, NodeType

    profile = Profile(
        "numeric",
        "1.0",
        [
            NodeType(
                "Task",
                "ta",
                {"properties": {"value": {"type": ["number", "boolean"]}}},
                claimable=True,
            )
        ],
        [
            EdgeType(
                "relates-to",
                [("*", "*")],
                {"properties": {"value": {"type": ["number", "boolean"]}}},
            )
        ],
        [],
        [],
        [],
    )
    monkeypatch.setattr(Profile, "_registry", dict(Profile._registry))
    Profile.register(profile)
    with Project.init(
        repository(tmp_path), profile="numeric", actor=ALICE, now=NOW
    ) as project:
        yield project


def test_nan_immutable_fields_survive_tag_and_status_writes(numeric_project):
    project = numeric_project
    a, b = node(), node("ta-bbbbbbbb")
    a.fields = {"value": float("nan")}
    edge = relation("relates-to", a.id, b.id, fields={"value": float("nan")})
    project.apply(write(a, b, edges=[edge]), commit=False)
    changed = project.get_node(a.id)
    changed.fields["value"] = float("nan")
    changed.tags = ["tagged"]
    stored_edge = project.get_edges()[0]
    stored_edge.fields["value"] = float("nan")
    project.apply(write(changed, edges=[stored_edge], op="tag"), commit=False)
    claimed = event(project.get_node(a.id), "claim", Status.RUNNING)
    claimed.nodes[0].fields["value"] = float("nan")
    claimed.nodes[0].lease = Lease(ALICE, NOW, NOW + timedelta(seconds=7200))
    project.apply(claimed, commit=False)
    assert project.get_node(a.id).status == Status.RUNNING


@pytest.mark.parametrize("before,after", [(1, True), (1, 1.0), (0.0, -0.0)])
@pytest.mark.parametrize("record", ["node", "edge"])
def test_canonical_numeric_changes_are_not_immutable_equal(
    numeric_project, before, after, record
):
    project = numeric_project
    a, b = node(), node("ta-bbbbbbbb")
    a.fields = {"value": before}
    edge = relation("relates-to", a.id, b.id, fields={"value": before})
    project.apply(write(a, b, edges=[edge]), commit=False)
    if record == "node":
        changed = project.get_node(a.id)
        changed.fields["value"] = after
        change = write(changed, op="tag")
    else:
        changed = project.get_edges()[0]
        changed.fields["value"] = after
        change = write(edges=[changed], op="link")
    refusal("schema", project.apply, change, commit=False)


@pytest.mark.parametrize("before,after", [(1, True), (1, 1.0), (0.0, -0.0)])
def test_append_only_prefix_uses_canonical_scalar_equality(before, after):
    value = node()
    value.artifacts = [
        Artifact("artifacts/ta-aaaaaaaa/a", "0" * 64, before, NOW, ALICE)
    ]
    changed = deepcopy(value)
    changed.artifacts[0].bytes = after
    refusal(
        "schema",
        validate_changes,
        Profile.load("research"),
        write(changed, op="attach"),
        {value.id: value},
        {},
    )


@pytest.mark.parametrize(
    "invalid,expected", [("endpoint", "edge_endpoints"), ("schema", "schema")]
)
def test_graph_endpoint_and_schema_errors_precede_cycles_and_duplicates(
    invalid, expected
):
    profile = Profile.load("research")
    a, b = node(), node("ta-bbbbbbbb")
    edges = [
        relation("blocks", a.id, b.id),
        relation("blocks", a.id, b.id, "bbbbbbbb"),
        relation("part-of", b.id, a.id, "cccccccc"),
    ]
    edges.append(
        relation(
            "tests" if invalid == "endpoint" else "relates-to",
            a.id,
            b.id,
            "dddddddd",
            fields={"invalid": True},
        )
    )
    refusal(
        expected,
        validate_graph,
        profile,
        {a.id: a, b.id: b},
        {edge.id: edge for edge in edges},
    )
