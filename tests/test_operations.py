"""Public lifecycle operations, file authority and transaction boundaries."""

import fcntl
import os
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from researchgraph.model import NODE_IMMUTABLE_KEYS, Actor, RuleViolation
from researchgraph.profiles import Gate, NodeType, Profile
from researchgraph.project import Project
from researchgraph.store.io import _canonical_equal, dump_node, load_edge, load_node
from researchgraph.validation import validate_graph
from tests.helpers import ALICE, NOW, git, refusal, repository

BOT = Actor("agent", "agent1", model="model", version="1")
BOB = Actor("human", "bob", "bob@example.test")
CALL = {"actor": ALICE, "now": NOW}


def snapshot(project):
    return (
        {
            p.relative_to(project.path).as_posix(): p.read_bytes()
            for p in (project.path / "graph").rglob("*.yaml")
        },
        git(project.path, "rev-parse", "HEAD"),
    )


def discard_index(project, mode):
    if mode == "deleted":
        project.index.close()
        (project.path / ".researchgraph/index.sqlite").unlink(missing_ok=True)


def custom(tmp_path, *, gates=(), validators=(), hooks=(), schema=None):
    profile = Profile(
        "lifecycle",
        "1.0",
        [NodeType("Task", "ta", {} if schema is None else schema, claimable=True)],
        [],
        list(gates),
        ["owner", "reviewer"],
        list(validators),
        event_hooks=list(hooks),
    )
    Profile.register(profile)
    repository(tmp_path)
    return Project.init(tmp_path, profile="lifecycle", **CALL)


def test_propose_metadata_canonical_and_defaults(project, index_mode):
    discard_index(project, index_mode)
    value = project.propose(
        "Hypothesis", "H1", body="a body\n", fields={}, role="owner", **CALL
    )
    assert value["status"] == "proposed"
    assert value["author"] == ALICE.to_dict()
    assert value["created"] == value["updated"]
    for key, expected in {
        "budget": None,
        "usage": None,
        "tags": [],
        "priority": None,
        "lease": None,
    }.items():
        assert value[key] == expected
    path = project.path / f"graph/nodes/{value['id']}.yaml"
    assert load_node(path) == value
    assert dump_node(load_node(path)).encode() == path.read_bytes()
    assert git(project.path, "log", "-1", "--format=%s") == f"rg: propose {value['id']}"
    task = project.propose("Task", "T1", actor=BOT, now=NOW)
    assert task["status"] == "active"
    assert task["author"] == BOT.to_dict()
    assert [n["id"] for n in project.ready(**CALL)["items"]] == [task["id"]]
    question = project.propose("Question", "Q", fields={"closed": None}, **CALL)
    assert question["fields"] == {"closed": False}


def test_propose_edges_direction_defaults_and_inputs(project, index_mode):
    h = project.propose("Hypothesis", "H", **CALL)
    specs = [
        {
            "type": "tests",
            "to": h["id"],
            "fields": {"expected": "weak absorbed", "discriminating": None},
        }
    ]
    original = deepcopy(specs)
    discard_index(project, index_mode)
    p = project.propose("Prediction", "P", edges=specs, actor=BOT, now=NOW)
    assert specs == original
    edge = project.show(p["id"], **CALL)["edges"][0]
    assert edge["from"] == p["id"] and edge["to"] == h["id"]
    assert edge["fields"] == {"expected": "weak absorbed", "discriminating": True}
    assert load_edge(project.path / f"graph/edges/{edge['id']}.yaml") == edge
    task = project.propose(
        "Task", "T", edges=[{"type": "blocks", "from": p["id"]}], **CALL
    )
    assert project.show(task["id"], **CALL)["flags"]["blocked"]


@pytest.mark.parametrize(
    "type_,fields,edges,rule",
    [
        ("Widget", {}, None, "unknown_type"),
        ("Hypothesis", {"undeclared": 1}, None, "schema"),
        ("Task", [], None, "schema"),
        ("Task", False, None, "schema"),
        ("Task", {}, {}, "schema"),
        ("Task", {}, False, "schema"),
        ("Task", {}, [{"type": "blocks"}], "schema"),
        ("Task", {}, [{"type": "blocks", "from": "TARGET", "to": "TARGET"}], "schema"),
        ("Task", {}, [{"type": "blocks", "to": "TARGET", "extra": 1}], "schema"),
        ("Task", {}, [{"type": "answers", "to": "TARGET"}], "edge_endpoints"),
        ("Prediction", {}, [{"type": "tests", "to": "TARGET"}], "schema"),
        (
            "Task",
            {},
            [{"type": "blocks", "to": "TARGET", "fields": {"x": 1}}],
            "schema",
        ),
        ("Note", {}, [{"type": "supersedes", "to": "TARGET"}], "reserved_edge_type"),
    ],
)
def test_invalid_proposals_are_atomic(project, type_, fields, edges, rule):
    target = project.propose("Question", "Q", **CALL)
    if isinstance(edges, list):
        edges = [
            {k: target["id"] if v == "TARGET" else v for k, v in e.items()}
            for e in edges
        ]
    before = snapshot(project)
    refusal(rule, project.propose, type_, "title", fields=fields, edges=edges, **CALL)
    assert snapshot(project) == before


@pytest.mark.parametrize("kind", ["supersedes", "explains", "instructs"])
def test_reserved_link_precedes_endpoints_and_fields(project, kind):
    a = project.propose("Task", "A", **CALL)
    b = project.propose("Question", "B", **CALL)
    before = snapshot(project)
    refusal(
        "reserved_edge_type", project.link, kind, a["id"], b["id"], {"bad": 1}, **CALL
    )
    assert snapshot(project) == before


def test_link_metadata_rules_and_evidence(project, index_mode):
    a = project.propose("Task", "A", **CALL)
    b = project.propose("Task", "B", **CALL)
    discard_index(project, index_mode)
    edge = project.link("blocks", a["id"], b["id"], actor=BOT, now=NOW, role="reviewer")
    assert edge["author"] == BOT.to_dict()
    assert load_edge(project.path / f"graph/edges/{edge['id']}.yaml") == edge
    assert git(project.path, "log", "-1", "--format=%s") == f"rg: link {edge['id']}"
    assert project.show(b["id"], **CALL)["flags"]["blocked"]
    before = snapshot(project)
    for kind, source, target, fields, rule in [
        ("blocks", b["id"], a["id"], {}, "cycle"),
        ("blocks", a["id"], b["id"], {}, "duplicate_edge"),
        ("blocks", a["id"], b["id"], {"x": 1}, "schema"),
        ("answers", a["id"], b["id"], {"x": 1}, "edge_endpoints"),
        ("blocks", a["id"], b["id"], [], "schema"),
    ]:
        refusal(rule, project.link, kind, source, target, fields, **CALL)
        assert snapshot(project) == before
    claim = project.propose("Claim", "C", **CALL)
    refusal("claim_needs_evidence", project.approve, claim["id"], **CALL)
    result = project.propose("Result", "R", **CALL)
    project.link("cites", claim["id"], result["id"], **CALL)
    assert project.evidence(claim["id"], **CALL)["items"][0]["id"] == result["id"]
    assert project.approve(claim["id"], **CALL)["status"] == "active"


def test_approve_and_reject(project, index_mode):
    h = project.propose("Hypothesis", "H", **CALL)
    discard_index(project, index_mode)
    refusal("human_only_approval", project.approve, h["id"], actor=BOT, now=NOW)
    refusal("unknown_what", project.approve, h["id"], "run", **CALL)
    later = NOW + timedelta(seconds=1)
    approved = project.approve(
        h["id"], note="reviewed", actor=ALICE, role="owner", now=later
    )
    assert approved["status"] == "active" and approved["updated"] != h["updated"]
    assert approved["approvals"] == [
        {
            "actor": ALICE.to_dict(),
            "role": "owner",
            "at": "2026-10-20T00:00:01Z",
            "what": "approve",
            "note": "reviewed",
        }
    ]
    assert load_node(project.path / f"graph/nodes/{h['id']}.yaml") == approved
    refusal("not_proposed", project.approve, h["id"], actor=BOT, now=later)
    refusal("not_proposed", project.reject, h["id"], "no", actor=ALICE, now=later)
    p = project.propose("Prediction", "P", actor=ALICE, now=later)
    task = project.propose("Task", "T", actor=ALICE, now=later)
    edge = project.link("blocks", p["id"], task["id"], actor=ALICE, now=later)
    refusal("human_only_approval", project.reject, p["id"], "no", actor=BOT, now=later)
    retired = project.reject(
        p["id"], "duplicate", actor=ALICE, role="reviewer", now=later
    )
    assert retired["status"] == "retired"
    assert retired["approvals"][-1]["what"] == "reject"
    assert retired["approvals"][-1]["note"] == "duplicate"
    stored = project.show(edge["id"], actor=ALICE, now=later)
    assert stored["retired"] and stored["retired_at"] == retired["updated"]
    assert task["id"] in [
        n["id"] for n in project.ready(actor=ALICE, now=later)["items"]
    ]
    # Linking retired endpoints remains legal.
    assert (
        project.link("relates-to", p["id"], task["id"], actor=ALICE, now=later)[
            "retired"
        ]
        is False
    )


def test_reject_removes_alternatives_and_all_incident_edges(project):
    q = project.propose("Question", "Q", **CALL)
    a = project.propose(
        "Hypothesis", "A", edges=[{"type": "answers", "to": q["id"]}], **CALL
    )
    b = project.propose(
        "Hypothesis", "B", edges=[{"type": "answers", "to": q["id"]}], **CALL
    )
    note = project.propose(
        "Note", "N", edges=[{"type": "about", "to": a["id"]}], **CALL
    )
    project.reject(a["id"], "no", **CALL)
    assert project.alternatives(b["id"], **CALL) == []
    assert project.show(note["id"], **CALL)["edges"] == []


def test_multiple_gates_identity_metadata_and_callbacks(tmp_path):
    calls = []
    with custom(
        tmp_path,
        gates=[
            Gate(node_type="Task", roles=["owner"]),
            Gate(node_type="Task", roles=["reviewer"]),
        ],
        validators=[lambda p, w: calls.append(deepcopy(w))],
    ) as project:
        calls.clear()
        value = project.propose("Task", "T", **CALL)
        first = project.approve(value["id"], role="owner", **CALL)
        assert first["status"] == "proposed"
        changed_identity_metadata = Actor(
            "human", "alice", "new@example.test", "new", "2"
        )
        refusal(
            "already_approved",
            project.approve,
            value["id"],
            role="reviewer",
            actor=changed_identity_metadata,
            now=NOW,
        )
        second = project.approve(value["id"], actor=BOB, role="reviewer", now=NOW)
        assert second["status"] == "active" and len(second["approvals"]) == 2
        assert len(calls) == 3
        assert calls[-1].args == {"node": value["id"], "what": "activate", "note": None}
        assert calls[-1].role == "reviewer"


def test_agent_gate_and_wrong_role(tmp_path):
    with custom(
        tmp_path,
        gates=[Gate(node_type="Task", roles=["human", "agent"], agents_allowed=True)],
    ) as project:
        value = project.propose("Task", "T", **CALL)
        result = project.approve(value["id"], actor=BOT, role="agent", now=NOW)
        assert result["status"] == "active"
        assert result["approvals"][0]["actor"] == BOT.to_dict()
    with custom(
        tmp_path / "other", gates=[Gate(node_type="Task", roles=["owner"])]
    ) as project:
        value = project.propose("Task", "T", **CALL)
        refusal(
            "wrong_role",
            project.approve,
            value["id"],
            actor=BOB,
            role="reviewer",
            now=NOW,
        )


@pytest.mark.parametrize("profile", ["research", "tasks"])
@pytest.mark.parametrize("actor", [ALICE, BOT])
def test_bundled_profile_creation_defaults(tmp_path, profile, actor):
    with Project.init(repository(tmp_path), profile=profile, **CALL) as project:
        value = project.propose("Task", "T", actor=actor, now=NOW)
        assert value["status"] == "active" and value["fields"] == {}
        assert value["author"] == actor.to_dict()
        assert value["approvals"] == []


def test_long_lived_builder_reads_same_size_same_mtime_source_metadata(project):
    value = project.propose("Hypothesis", "H", **CALL)
    path = project.path / f"graph/nodes/{value['id']}.yaml"
    stat = path.stat()
    original = path.read_bytes()
    path.write_bytes(original.replace(b"alice@example.test", b"alicz@example.test"))
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    approved = project.approve(value["id"], **CALL)
    assert approved["author"]["email"] == "alicz@example.test"
    assert approved["approvals"][0]["actor"] == ALICE.to_dict()
    assert load_node(path) == approved


def test_ids_retry_source_and_pending_collisions(project, monkeypatch):
    from researchgraph import model

    choices = iter("a" * 8)
    monkeypatch.setattr(model.secrets, "choice", lambda alphabet: next(choices))
    first = project.propose("Task", "A", **CALL)
    choices = iter("a" * 8 + "b" * 8)
    second = project.propose("Task", "B", **CALL)
    assert first["id"] == "ta-aaaaaaaa" and second["id"] == "ta-bbbbbbbb"
    # Node id followed by two edge ids: the second edge retries the pending id.
    choices = iter("c" * 8 + "d" * 16 + "e" * 8)
    third = project.propose(
        "Task",
        "C",
        edges=[
            {"type": "blocks", "to": first["id"]},
            {"type": "blocks", "to": second["id"]},
        ],
        **CALL,
    )
    assert {e["id"] for e in project.show(third["id"], **CALL)["edges"]} == {
        "ed-dddddddd",
        "ed-eeeeeeee",
    }


def test_role_and_operation_precedence_with_existing_targets(project):
    h = project.propose("Hypothesis", "H", **CALL)
    a = project.propose("Task", "A", **CALL)
    refusal(
        "human_only_approval",
        project.reject,
        a["id"],
        "no",
        actor=BOT,
        role="unknown",
        now=NOW,
    )
    refusal(
        "wrong_role",
        project.approve,
        h["id"],
        "run",
        actor=ALICE,
        role="unknown",
        now=NOW,
    )
    refusal(
        "wrong_role",
        project.link,
        "supersedes",
        a["id"],
        h["id"],
        actor=ALICE,
        role="unknown",
        now=NOW,
    )
    refusal(
        "unknown_type",
        project.propose,
        "Widget",
        "T",
        edges=[{"type": "supersedes", "to": h["id"]}],
        **CALL,
    )
    refusal(
        "reserved_edge_type",
        project.propose,
        "Task",
        "T",
        fields={"bad": 1},
        edges=[{"type": "explains", "to": h["id"]}],
        **CALL,
    )
    (project.path / ".researchgraph/profile.yaml").write_text(
        'name: research\nversion: "9.0"\n'
    )
    refusal(
        "profile_too_new",
        project.reject,
        a["id"],
        "no",
        actor=BOT,
        role="unknown",
        now=NOW,
    )


def test_public_builder_proposal_and_link_row_precedence(project):
    a = project.propose("Task", "A", **CALL)
    b = project.propose("Question", "Q", **CALL)
    before = snapshot(project)
    # Type lookup must beat both reserved and malformed specifications.
    refusal(
        "unknown_type",
        project.propose,
        "Widget",
        "bad\ntitle",
        fields=[],
        edges=[{"type": "supersedes"}],
        **CALL,
    )
    # Reserved edges beat schema errors on nodes and on *any* edge spec.
    refusal(
        "reserved_edge_type",
        project.propose,
        "Task",
        "bad\ntitle",
        fields=[],
        edges=[
            {"type": "blocks"},
            {"type": "explains", "from": a["id"], "to": b["id"]},
        ],
        **CALL,
    )
    # Proposal schema precedes endpoint-type checks, unlike link.
    refusal(
        "schema",
        project.propose,
        "Task",
        "T",
        fields={"bad": 1},
        edges=[{"type": "answers", "to": b["id"]}],
        **CALL,
    )
    refusal(
        "schema",
        project.propose,
        "Task",
        "T",
        edges=[{"type": "answers", "to": b["id"], "fields": {"bad": 1}}],
        **CALL,
    )
    refusal(
        "edge_endpoints",
        project.propose,
        "Task",
        "T",
        edges=[{"type": "answers", "to": b["id"]}],
        **CALL,
    )
    refusal(
        "reserved_edge_type", project.link, "supersedes", a["id"], b["id"], [], **CALL
    )
    refusal("edge_endpoints", project.link, "answers", a["id"], b["id"], [], **CALL)
    refusal("schema", project.link, "blocks", a["id"], b["id"], [], **CALL)
    assert snapshot(project) == before


def test_no_commit_hooks_and_scoped_commit(tmp_path):
    hooks = []
    with custom(
        tmp_path, hooks=[lambda p, w, c: hooks.append((deepcopy(w), c))]
    ) as project:
        hooks.clear()
        (tmp_path / "unrelated").write_text("staged")
        git(tmp_path, "add", "unrelated")
        before = git(tmp_path, "rev-parse", "HEAD")
        value = project.propose("Task", "T", commit=False, **CALL)
        assert hooks == [] and git(tmp_path, "rev-parse", "HEAD") == before
        assert project.ready(**CALL)["items"][0]["id"] == value["id"]
        assert f"graph/nodes/{value['id']}.yaml" in git(
            tmp_path, "status", "--porcelain", "--untracked-files=all"
        )
        other = project.propose("Task", "other", **CALL)
        assert len(hooks) == 1
        assert hooks[0][0].nodes[0].id == other["id"]
        committed = git(
            tmp_path, "show", "--format=", "--name-only", "HEAD"
        ).splitlines()
        assert committed == [f"graph/nodes/{other['id']}.yaml"]
        assert "unrelated" in git(tmp_path, "diff", "--cached", "--name-only")


def test_all_lifecycle_no_commit_callbacks_and_visibility(tmp_path):
    hooks, validations = [], []
    with custom(
        tmp_path,
        gates=[Gate(node_type="Task")],
        hooks=[lambda p, w, c: hooks.append(c)],
        validators=[lambda p, w: validations.append(deepcopy(w))],
    ) as project:
        hooks.clear()
        validations.clear()
        head = git(tmp_path, "rev-parse", "HEAD")
        a = project.propose("Task", "A", commit=False, **CALL)
        b = project.propose("Task", "B", commit=False, **CALL)
        project.link("blocks", a["id"], b["id"], commit=False, **CALL)
        approved = project.approve(a["id"], note="yes", commit=False, **CALL)
        rejected = project.reject(b["id"], "no", commit=False, **CALL)
        assert approved["status"] == "active" and rejected["status"] == "retired"
        assert project.get_edges()[0].retired
        assert hooks == [] and git(tmp_path, "rev-parse", "HEAD") == head
        assert [w.op for w in validations] == [
            "propose",
            "propose",
            "link",
            "approve",
            "reject",
        ]
        assert validations[2].args == {
            "type": "blocks",
            "from": a["id"],
            "to": b["id"],
            "fields": None,
        }
        assert validations[3].args == {
            "node": a["id"],
            "what": "activate",
            "note": "yes",
        }
        assert validations[4].args == {"node": b["id"], "reason": "no"}


@pytest.mark.parametrize(
    "operation",
    [
        "propose",
        "link",
        "approve",
        "reject",
        "supersede",
        "instruct",
        "retire",
        "claim",
        "renew",
        "release",
        "complete",
        "fail",
    ],
)
def test_shared_error_precedence(project, operation):
    missing = "ta-zzzzzzzz"
    args = {
        "propose": ("Widget", "T"),
        "link": ("supersedes", missing, missing),
        "approve": (missing,),
        "reject": (missing, "no"),
        "supersede": (missing,),
        "instruct": (missing, "text"),
        "retire": (missing, "reason"),
        "claim": (missing,),
        "renew": (missing,),
        "release": (missing,),
        "complete": (missing, "done"),
        "fail": (missing, "bad"),
    }[operation]
    kwargs = (
        {"edges": [{"type": "blocks", "to": missing}]} if operation == "propose" else {}
    )
    project.read_only = True
    refusal(
        "read_only",
        getattr(project, operation),
        *args,
        actor=BOT,
        now=NOW - timedelta(seconds=1),
        **kwargs,
    )
    project.read_only = False
    refusal(
        "clock_skew",
        getattr(project, operation),
        *args,
        actor=BOT,
        now=NOW - timedelta(seconds=1),
        **kwargs,
    )
    refusal(
        "not_found",
        getattr(project, operation),
        *args,
        actor=BOT,
        role="bad",
        now=NOW,
        **kwargs,
    )
    (project.path / ".researchgraph/profile.yaml").write_text(
        'name: research\nversion: "9.0"\n'
    )
    refusal(
        "not_found", getattr(project, operation), *args, actor=BOT, now=NOW, **kwargs
    )


@pytest.mark.parametrize(
    "value,expected",
    [({}, "fast"), ({"mode": None}, "fast"), ({"mode": "safe"}, "safe")],
)
def test_stored_direct_defaults(tmp_path, value, expected):
    schema = {"properties": {"mode": {"enum": ["fast", "safe"], "default": "fast"}}}
    original = deepcopy(schema)
    with custom(tmp_path, schema=schema) as project:
        result = project.propose("Task", "T", fields=value, **CALL)
        assert result["fields"] == {"mode": expected}
        assert load_node(tmp_path / f"graph/nodes/{result['id']}.yaml") == result
    assert schema == original


def test_mutable_defaults_are_isolated_and_composition_validates(tmp_path):
    schema = {
        "properties": {"data": {"type": "array", "default": [{"x": 1}]}},
        "allOf": [{"required": ["data"]}],
    }
    original = deepcopy(schema)
    with custom(tmp_path, schema=schema) as project:
        first = project.propose("Task", "A", **CALL)
        second = project.propose("Task", "B", **CALL)
        first["fields"]["data"][0]["x"] = 9
        assert second["fields"]["data"] == [{"x": 1}]
        assert project.get_node(first["id"]).fields == second["fields"]
        assert (
            project.profile.node_type("Task").schema["properties"]
            == schema["properties"]
        )
    assert schema == original
    bad = {"properties": {"mode": {"default": "fast"}}, "not": {"required": ["mode"]}}
    with custom(tmp_path / "bad", schema=bad) as project:
        before = snapshot(project)
        refusal("schema", project.propose, "Task", "T", **CALL)
        assert snapshot(project) == before


def test_competing_approvals_are_built_under_one_lock(tmp_path, monkeypatch):
    import researchgraph.project as project_module

    build = project_module._approve

    def locked_builder(project, write, nodes, edges):
        with (
            (project.path / ".researchgraph/lock").open("a+b") as lock,
            pytest.raises(BlockingIOError),
        ):
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return build(project, write, nodes, edges)

    monkeypatch.setattr(project_module, "_approve", locked_builder)
    gates = [
        Gate(node_type="Task", roles=["owner"]),
        Gate(node_type="Task", roles=["reviewer"]),
    ]
    with custom(tmp_path, gates=gates) as project:
        value = project.propose("Task", "T", **CALL)
        barrier = Barrier(2)

        def approve(actor, role):
            # Each connection belongs to its calling thread.
            with Project.open(tmp_path) as p:
                barrier.wait(timeout=10)
                return p.approve(value["id"], actor=actor, role=role, now=NOW)

        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(approve, ALICE, "owner")
            b = pool.submit(approve, BOB, "reviewer")
            results = [a.result(timeout=30), b.result(timeout=30)]
        assert sorted(len(r["approvals"]) for r in results) == [1, 2]
        stored = project.get_node(value["id"])
        assert stored.status == "active"
        assert {a.actor.name for a in stored.approvals} == {"alice", "bob"}


@settings(max_examples=8, deadline=None)
@given(
    st.lists(
        st.sampled_from(["blocks", "derived-from", "part-of"]), min_size=2, max_size=5
    )
)
def test_public_mixed_cycles_duplicates_and_rebuild_invariants(kinds):
    with TemporaryDirectory() as directory:
        path = repository(Path(directory))
        with Project.init(path, **CALL) as project:
            nodes = [
                project.propose("Task", str(i), commit=False, **CALL)
                for i in range(len(kinds))
            ]
            for i, kind in enumerate(kinds[:-1]):
                project.link(
                    kind, nodes[i]["id"], nodes[i + 1]["id"], commit=False, **CALL
                )
            before = snapshot(project)
            refusal(
                "cycle",
                project.link,
                kinds[-1],
                nodes[-1]["id"],
                nodes[0]["id"],
                commit=False,
                **CALL,
            )
            assert snapshot(project) == before
            refusal(
                "duplicate_edge",
                project.link,
                kinds[0],
                nodes[0]["id"],
                nodes[1]["id"],
                commit=False,
                **CALL,
            )
            assert snapshot(project) == before
            # A proposed new node would close the same mixed-relation cycle.
            refusal(
                "cycle",
                project.propose,
                "Task",
                "cycle",
                edges=[
                    {"type": kinds[0], "from": nodes[-1]["id"]},
                    {"type": kinds[-1], "to": nodes[0]["id"]},
                ],
                commit=False,
                **CALL,
            )
            assert snapshot(project) == before
            # Cycle wins even when the candidate also contains a duplicate.
            refusal(
                "cycle",
                project.propose,
                "Task",
                "cycle and duplicate",
                edges=[
                    {"type": kinds[0], "from": nodes[-1]["id"]},
                    {"type": kinds[-1], "to": nodes[0]["id"]},
                    {"type": kinds[-1], "to": nodes[0]["id"]},
                ],
                commit=False,
                **CALL,
            )
            assert snapshot(project) == before
            refusal(
                "duplicate_edge",
                project.propose,
                "Task",
                "duplicate",
                edges=[
                    {"type": "blocks", "to": nodes[0]["id"]},
                    {"type": "blocks", "to": nodes[0]["id"]},
                ],
                commit=False,
                **CALL,
            )
            assert snapshot(project) == before
            validate_graph(
                project.profile,
                {n.id: n for n in project.get_nodes()},
                {e.id: e for e in project.get_edges()},
            )
            kept = project.digest(**CALL)
            discard_index(project, "deleted")
            assert project.digest(**CALL) == kept


def query_snapshot(project, ids):
    return {
        "ready": project.ready(**CALL),
        "blocked": project.blocked(**CALL),
        "queue": project.queue(**CALL),
        "stale": project.stale(**CALL),
        "where": project.where(**CALL),
        "digest": project.digest(**CALL),
        "since": project.since(NOW - timedelta(seconds=1), **CALL),
        "show": [project.show(id_, **CALL) for id_ in ids.values()],
        "history": project.history(ids["h"], **CALL),
        "notes": project.notes(ids["h"], **CALL),
        "subtree": project.subtree(ids["h"], **CALL),
        "evidence": project.evidence(ids["c"], **CALL),
        "weakest": project.weakest(ids["c"], **CALL),
        "alternatives": project.alternatives(ids["h"], **CALL),
        "predictions": project.predictions(ids["h"], **CALL),
        "matrix": project.matrix(ids["q"], **CALL),
    }


@settings(max_examples=5, deadline=None)
@given(
    actions=st.lists(
        st.sampled_from(["approve", "reject", "agent", "link", "invalid", "propose"]),
        min_size=2,
        max_size=6,
    ),
    profile_name=st.sampled_from(["research", "test-conditional"]),
)
def test_random_public_lifecycle_sequences_preserve_invariants_and_all_queries(
    actions, profile_name
):
    with (
        TemporaryDirectory() as directory,
        Project.init(
            repository(Path(directory)), profile=profile_name, **CALL
        ) as project,
    ):
        q = project.propose("Question", "Q", commit=False, **CALL)
        h = project.propose(
            "Hypothesis",
            "H",
            edges=[{"type": "answers", "to": q["id"]}],
            commit=False,
            **CALL,
        )
        r = project.propose("Result", "R", commit=False, **CALL)
        c = project.propose(
            "Claim", "C", edges=[{"type": "cites", "to": r["id"]}], commit=False, **CALL
        )
        task = project.propose("Task", "T", commit=False, **CALL)
        ids = {"q": q["id"], "h": h["id"], "r": r["id"], "c": c["id"], "t": task["id"]}
        for action in actions:
            old_nodes = {n.id: n.to_dict() for n in project.get_nodes()}
            old_edges = {e.id: e.to_dict() for e in project.get_edges()}
            before = snapshot(project)
            try:
                if action == "approve":
                    project.approve(h["id"], commit=False, **CALL)
                elif action == "reject":
                    project.reject(h["id"], "discard", commit=False, **CALL)
                elif action == "agent":
                    project.approve(h["id"], actor=BOT, now=NOW, commit=False)
                elif action == "link":
                    project.link("blocks", h["id"], task["id"], commit=False, **CALL)
                elif action == "invalid":
                    project.link("blocks", "ta-zzzzzzzz", h["id"], commit=False, **CALL)
                else:
                    project.propose(
                        "Note",
                        "N",
                        edges=[{"type": "about", "to": h["id"]}],
                        commit=False,
                        **CALL,
                    )
            except RuleViolation as exc:
                assert exc.rule in {
                    "not_proposed",
                    "human_only_approval",
                    "duplicate_edge",
                    "not_found",
                }
                assert snapshot(project) == before
            nodes = {n.id: n for n in project.get_nodes()}
            edges = {e.id: e for e in project.get_edges()}
            validate_graph(project.profile, nodes, edges)
            assert old_nodes.keys() <= nodes.keys() and old_edges.keys() <= edges.keys()
            for id_, previous in old_nodes.items():
                current = nodes[id_].to_dict()
                assert _canonical_equal(
                    {k: previous[k] for k in NODE_IMMUTABLE_KEYS},
                    {k: current[k] for k in NODE_IMMUTABLE_KEYS},
                )
                assert (
                    current["approvals"][: len(previous["approvals"])]
                    == previous["approvals"]
                )
                assert current["artifacts"] == previous["artifacts"]
            for id_, previous in old_edges.items():
                current = edges[id_].to_dict()
                assert {
                    k: v
                    for k, v in current.items()
                    if k not in {"retired", "retired_at"}
                } == {
                    k: v
                    for k, v in previous.items()
                    if k not in {"retired", "retired_at"}
                }
                assert not previous["retired"] or current == previous
            kept = query_snapshot(project, ids)
            discard_index(project, "deleted")
            assert query_snapshot(project, ids) == kept
