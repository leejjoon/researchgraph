"""T10 revision contracts over file-authoritative operation-built graphs."""

import fcntl
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from researchgraph.model import (
    EDGE_IMMUTABLE_KEYS,
    NODE_IMMUTABLE_KEYS,
    Actor,
    Edge,
    Lease,
    Node,
    RuleViolation,
    Status,
    Write,
)
from researchgraph.profiles import EdgeType, Gate, NodeType, Profile
from researchgraph.project import Project
from researchgraph.store.io import load_node
from researchgraph.validation import validate_graph
from tests.helpers import ALICE, NOW, git, refusal, repository

BOT = Actor("agent", "agent1", model="m", version="1")
CALL = {"actor": ALICE, "now": NOW}


def stored_edge(project, identifier):
    return Edge.from_dict(project.show(identifier, **CALL))


def source(project):
    history = project.path / ".researchgraph/history.jsonl"
    return (
        {
            p.relative_to(project.path).as_posix(): p.read_bytes()
            for p in (project.path / "graph").rglob("*.yaml")
        },
        history.read_bytes() if history.exists() else None,
        git(project.path, "rev-parse", "HEAD"),
    )


def restore(project, type_, status, *, edges=(), **metadata):
    """Restore T11 givens through the validated low-level import boundary."""
    definition = project.profile.node_type(type_)
    from researchgraph.model import new_id

    identifier = new_id(
        definition.prefix, lambda id: (project.path / f"graph/nodes/{id}.yaml").exists()
    )
    if status == Status.RUNNING:
        metadata["lease"] = Lease(BOT, NOW, NOW + timedelta(hours=1))
    node = Node(
        identifier,
        type_,
        "Restored",
        "original body",
        ALICE,
        NOW,
        status=status,
        **metadata,
    )
    project.apply(Write("import", ALICE, None, NOW, {}, [node], list(edges)))
    return node.to_dict()


def profile_project(
    path,
    *,
    inheritance="outgoing",
    instruction=True,
    gates=(),
    hooks=(),
    validators=(),
    schema=None,
):
    types = [NodeType("Task", "ta", schema or {}, claimable=True)]
    if instruction:
        types.append(NodeType("Instruction", "in", {}))
    profile = Profile(
        "revision",
        "1.0",
        types,
        [
            EdgeType(
                "custom",
                [("*", "*")],
                {"properties": {"data": {"type": "array"}}},
                inherit=inheritance,
            )
        ],
        list(gates),
        ["owner"],
        list(validators),
        event_hooks=list(hooks),
    )
    Profile.register(profile)
    return Project.init(repository(path), profile="revision", **CALL)


def assert_immutable(old, new, keys=NODE_IMMUTABLE_KEYS):
    assert {k: old[k] for k in keys} == {k: new[k] for k in keys}


def discard_index(project, mode):
    if mode == "deleted":
        project.index.close()
        (project.path / ".researchgraph/index.sqlite").unlink(missing_ok=True)


def test_supersede_hypothesis_acceptance_and_metadata(project, index_mode):
    q = project.propose("Question", "Q", **CALL)
    h = project.propose(
        "Hypothesis",
        "H",
        body="body",
        edges=[{"type": "answers", "to": q["id"]}],
        **CALL,
    )
    project.approve(h["id"], **CALL)
    p = project.propose(
        "Prediction",
        "P",
        edges=[
            {"type": "tests", "to": h["id"], "fields": {"expected": "weak absorbed"}}
        ],
        **CALL,
    )
    h3 = project.propose("Hypothesis", "H3", **CALL)
    untouched = project.link(
        "tests", p["id"], h3["id"], fields={"expected": "no detection"}, **CALL
    )
    before = project.get_node(h["id"]).to_dict()
    old_edges = project.show(h["id"], **CALL)["edges"]
    discard_index(project, index_mode)
    replacement = project.supersede(
        h["id"], title="H′", actor=ALICE, role="owner", now=NOW + timedelta(seconds=1)
    )
    assert replacement["status"] == "proposed"
    assert replacement["body"] == "body" and replacement["fields"] == h["fields"]
    assert replacement["created"] == replacement["updated"] == "2026-10-20T00:00:01Z"
    assert replacement["author"] == ALICE.to_dict()
    assert (
        load_node(project.path / f"graph/nodes/{replacement['id']}.yaml") == replacement
    )
    old = project.get_node(h["id"]).to_dict()
    assert_immutable(before, old)
    assert old["status"] == "retired" and old["approvals"][:-1] == before["approvals"]
    assert old["approvals"][-1]["what"] == "superseded"
    assert old["approvals"][-1]["note"] == replacement["id"]
    assert old["approvals"][-1]["role"] == "owner"
    inherited = project.show(
        replacement["id"], actor=ALICE, now=NOW + timedelta(seconds=1)
    )["edges"]
    for edge in old_edges:
        retired = stored_edge(project, edge["id"]).to_dict()
        assert retired["retired"]
        assert_immutable(edge, retired, EDGE_IMMUTABLE_KEYS)
        new = next(e for e in inherited if e["type"] == edge["type"])
        assert new["id"] != edge["id"] and new["fields"] == edge["fields"]
        assert (
            new["author"] == ALICE.to_dict()
            and new["created"] == replacement["created"]
        )
    assert stored_edge(project, untouched["id"]).to_dict() == untouched
    assert any(e["type"] == "supersedes" and e["to"] == h["id"] for e in inherited)
    before_source = source(project)
    refusal(
        "terminal_status",
        project.supersede,
        h["id"],
        actor=ALICE,
        now=NOW + timedelta(seconds=1),
    )
    assert source(project) == before_source
    assert project.history(
        h["id"], actor=ALICE, now=NOW + timedelta(seconds=1)
    ) == project.history(replacement["id"], actor=ALICE, now=NOW + timedelta(seconds=1))


@pytest.mark.parametrize("inheritance", ["both", "outgoing", "none"])
@pytest.mark.parametrize("inherit_edges", [True, False])
def test_profile_inheritance_incoming_outgoing_self_and_retired(
    tmp_path, inheritance, inherit_edges
):
    with profile_project(tmp_path, inheritance=inheritance) as project:
        old = project.propose("Task", "old", **CALL)
        peer = project.propose("Task", "peer", **CALL)
        originals = [
            project.link("custom", a, b, fields={"data": [1, 2]}, **CALL)
            for a, b in [
                (old["id"], peer["id"]),
                (peer["id"], old["id"]),
                (old["id"], old["id"]),
            ]
        ]
        replacement = project.supersede(
            old["id"], inherit_edges=inherit_edges, actor=BOT, now=NOW
        )
        live = project.show(replacement["id"], **CALL)["edges"]
        for edge in originals:
            transfer = inheritance == "both" or (
                inheritance == "outgoing" and edge["from"] == old["id"]
            )
            assert stored_edge(project, edge["id"]).retired == transfer
            matches = [
                e
                for e in live
                if e["type"] == "custom"
                and e["from"]
                == (replacement["id"] if edge["from"] == old["id"] else edge["from"])
                and e["to"]
                == (replacement["id"] if edge["to"] == old["id"] else edge["to"])
            ]
            assert len(matches) == int(transfer and inherit_edges)
            if matches:
                assert matches[0]["fields"] == edge["fields"]
                assert matches[0]["author"] == BOT.to_dict()
                assert matches[0]["id"] != edge["id"]


def test_revision_evidence_stale_chain_supports_notes_and_progress(project, index_mode):
    q = project.propose("Question", "Q", **CALL)
    hypotheses = [
        project.propose(
            "Hypothesis", title, edges=[{"type": "answers", "to": q["id"]}], **CALL
        )
        for title in ["H", "other"]
    ]
    h, other = hypotheses
    project.link("part-of", h["id"], q["id"], **CALL)
    for hypothesis in hypotheses:
        project.approve(hypothesis["id"], **CALL)
    r = project.propose("Result", "R0", **CALL)
    c = project.propose(
        "Claim",
        "C",
        edges=[{"type": "cites", "to": r["id"]}, {"type": "supports", "to": h["id"]}],
        **CALL,
    )
    project.approve(c["id"], **CALL)
    n = project.propose("Note", "N", edges=[{"type": "about", "to": h["id"]}], **CALL)
    discard_index(project, index_mode)
    r1 = project.supersede(r["id"], **CALL)
    r2 = project.supersede(r1["id"], **CALL)
    assert {x["id"] for x in project.evidence(c["id"], **CALL)["items"]} == {r["id"]}
    assert project.stale(**CALL)["items"][0]["responsible"] == [r2["id"]]
    assert (
        len(
            [
                e
                for e in project._before_state()[1].values()
                if e.type == "supersedes" and not e.retired
            ]
        )
        == 2
    )
    h1 = project.supersede(h["id"], **CALL)
    assert project.notes(h["id"], **CALL)[0]["id"] == n["id"]
    assert project.notes(h1["id"], **CALL) == []
    assert not any(
        e["type"] == "supports" for e in project.show(h1["id"], **CALL)["edges"]
    )
    assert any(x["id"] == h1["id"] for x in project.alternatives(other["id"], **CALL))
    c1 = project.supersede(c["id"], **CALL)
    assert any(
        e["type"] == "supports" and e["to"] == h["id"]
        for e in project.show(c1["id"], **CALL)["edges"]
    )
    progress = project.subtree(q["id"], **CALL)
    assert h1["id"] in {node["id"] for node in progress["nodes"]}
    assert h["id"] not in {node["id"] for node in progress["nodes"]}
    head = git(project.path, "rev-parse", "HEAD")

    def queries():
        return [
            project.stale(**CALL),
            project.evidence(c1["id"], **CALL),
            project.history(r2["id"], **CALL),
            project.show(h1["id"], **CALL),
            project.subtree(q["id"], **CALL),
            project.show(r["id"], as_of=head, **CALL),
        ]

    kept = queries()
    project.index.close()
    (project.path / ".researchgraph/index.sqlite").unlink(missing_ok=True)
    assert queries() == kept


@pytest.mark.parametrize(
    "status", [Status.PROPOSED, Status.ACTIVE, Status.FAILED, Status.DONE]
)
@pytest.mark.parametrize("node_type", ["Task", "Result"])
def test_supersede_resets_metadata_and_agent_instruction(project, status, node_type):
    old = restore(
        project,
        node_type,
        status,
        budget={"tokens": 8},
        usage={"tokens": 3},
        tags=["tag"],
        priority=9,
    )
    new = project.supersede(old["id"], instruction="use 0.4 arcsec", actor=BOT, now=NOW)
    assert new["status"] == "active" and new["author"] == BOT.to_dict()
    for key in ["budget", "usage", "priority", "lease"]:
        assert new[key] is None
    assert new["tags"] == new["artifacts"] == new["approvals"] == []
    instructions = project.show(new["id"], **CALL)["instructions"]
    assert len(instructions) == 1 and instructions[0]["body"] == "use 0.4 arcsec"
    assert instructions[0]["author"] == BOT.to_dict()
    assert project.get_node(old["id"]).usage == {"tokens": 3}


@pytest.mark.parametrize(
    "text,title",
    [
        ("", ""),
        ("\nsecond", ""),
        ("first\nsecond", "first"),
        ("x" * 250, "x" * 200),
        ("a\r\nb", "a"),
    ],
)
@pytest.mark.parametrize("node_type", ["Task", "Hypothesis"])
def test_instruction_computed_titles_and_exact_body(
    project, index_mode, text, title, node_type
):
    task = project.propose(node_type, "T", **CALL)
    discard_index(project, index_mode)
    value = project.instruct(task["id"], text, **CALL)
    assert value["title"] == title and value["body"] == text
    assert value["status"] == "active"
    assert project.show(task["id"], **CALL)["instructions"] == [value]


def test_instruction_gate_defaults_and_explicit_empty_replacements(tmp_path):
    schema = {"properties": {"data": {"type": "array", "default": [1]}}}
    original = deepcopy(schema)
    with profile_project(
        tmp_path, schema=schema, gates=[Gate(node_type="Instruction")]
    ) as project:
        old = project.propose("Task", "old", body="body", fields={"data": [2]}, **CALL)
        new = project.supersede(
            old["id"], title="", body="", fields={}, instruction="", **CALL
        )
        assert new["title"] == new["body"] == "" and new["fields"] == {"data": [1]}
        assert (
            project.show(new["id"], **CALL)["instructions"][0]["status"] == "proposed"
        )
        new["fields"]["data"].append(9)
        assert project.get_node(new["id"]).fields == {"data": [1]}
        assert project.get_node(old["id"]).fields == {"data": [2]}
    assert schema == original


@pytest.mark.parametrize("operation", ["supersede", "retire", "instruct"])
@pytest.mark.parametrize(
    "status", [Status.RUNNING, Status.RETIRED, Status.DONE, Status.FAILED]
)
def test_revision_state_refusals_and_allowed_failed_retirement(
    project, operation, status
):
    old = restore(project, "Task", status)
    args = (old["id"],) if operation == "supersede" else (old["id"], "reason")
    if operation == "supersede" and status in {Status.DONE, Status.FAILED}:
        assert getattr(project, operation)(*args, **CALL)["status"] == "active"
    elif operation == "retire" and status == Status.FAILED:
        assert project.retire(*args, **CALL)["status"] == "retired"
    else:
        rule = (
            "already_ran"
            if operation == "instruct"
            else "running"
            if status == Status.RUNNING
            else "terminal_status"
        )
        before = source(project)
        refusal(rule, getattr(project, operation), *args, **CALL)
        assert source(project) == before
        assert project.get_node(old["id"]).to_dict() == old


def test_retire_all_incident_edges_and_reason(project, index_mode):
    q = project.propose("Question", "Q", **CALL)
    h = project.propose(
        "Hypothesis", "H", edges=[{"type": "answers", "to": q["id"]}], **CALL
    )
    project.approve(h["id"], **CALL)
    project.propose(
        "Prediction",
        "P",
        edges=[{"type": "tests", "to": h["id"], "fields": {"expected": "yes"}}],
        **CALL,
    )
    project.propose("Note", "N", edges=[{"type": "about", "to": h["id"]}], **CALL)
    originals = project.show(h["id"], **CALL)["edges"]
    discard_index(project, index_mode)
    value = project.retire(h["id"], "abandoned", **CALL)
    assert value["status"] == "retired" and value["approvals"][-1]["what"] == "retire"
    assert value["approvals"][-1]["note"] == "abandoned"
    assert all(stored_edge(project, e["id"]).retired for e in originals)
    assert project.show(h["id"], **CALL)["edges"] == []


def test_inherited_explains_is_not_direct_creation(project):
    task = project.propose("Task", "T", **CALL)
    failure = project.propose("Failure", "F", fields={"error": "bad"}, **CALL)
    edge = Edge("ed-aaaaaaaa", "explains", failure["id"], task["id"], ALICE, NOW)
    project.apply(Write("import", ALICE, None, NOW, {}, [], [edge]))
    new = project.supersede(failure["id"], **CALL)
    assert stored_edge(project, edge.id).retired
    assert any(
        e["type"] == "explains" and e["to"] == task["id"] and e["fields"] == {}
        for e in project.show(new["id"], **CALL)["edges"]
    )
    refusal(
        "reserved_edge_type", project.link, "explains", new["id"], task["id"], **CALL
    )
    refusal(
        "reserved_edge_type",
        project.propose,
        "Failure",
        "F",
        edges=[{"type": "explains", "to": task["id"]}],
        **CALL,
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"title": "x" * 201},
        {"title": "a\nb"},
        {"fields": {"unknown": 1}},
        {"inherit_edges": 1},
        {"instruction": 4},
        {"body": 2},
    ],
)
def test_invalid_supersede_is_atomic(project, kwargs):
    old = project.propose("Task", "T", **CALL)
    before = source(project)
    refusal("schema", project.supersede, old["id"], **kwargs, **CALL)
    assert source(project) == before


def test_permissions_support_and_precedence(project, tmp_path):
    h = project.propose("Hypothesis", "H", **CALL)
    refusal(
        "agent_cannot_supersede_type", project.supersede, h["id"], actor=BOT, now=NOW
    )
    for operation, args in [
        ("retire", (h["id"], "why")),
        ("instruct", (h["id"], "text")),
    ]:
        refusal(
            "human_only_approval",
            getattr(project, operation),
            *args,
            actor=BOT,
            now=NOW,
        )
    refusal(
        "wrong_role", project.supersede, h["id"], actor=ALICE, role="unknown", now=NOW
    )
    with profile_project(tmp_path / "unsupported", instruction=False) as p:
        t = p.propose("Task", "T", **CALL)
        refusal("unsupported_by_profile", p.instruct, t["id"], "text", **CALL)
        refusal(
            "unsupported_by_profile", p.supersede, t["id"], instruction="text", **CALL
        )
    (project.path / ".researchgraph/profile.yaml").write_text(
        'name: research\nversion: "99.0"\n'
    )
    refusal("not_found", project.supersede, "ta-zzzzzzzz", **CALL)
    refusal("profile_too_new", project.supersede, h["id"], actor=BOT, now=NOW)
    project.read_only = True
    refusal("read_only", project.supersede, "ta-zzzzzzzz", **CALL)


@pytest.mark.parametrize("operation", ["supersede", "instruct", "retire"])
def test_revision_profile_validator_refusal_is_atomic(tmp_path, operation):
    hooks = []

    def validator(project, write):
        if write.op == operation:
            raise RuleViolation("profile_refusal", "Rejected candidate")

    with profile_project(
        tmp_path,
        validators=[validator],
        hooks=[lambda p, w, c: hooks.append(w.op)],
    ) as p:
        old = p.propose("Task", "old", **CALL)
        before = source(p)
        hooks.clear()
        args = (old["id"],) if operation == "supersede" else (old["id"], "text")
        refusal("profile_refusal", getattr(p, operation), *args, **CALL)
        assert source(p) == before and hooks == []


def test_already_retired_edges_stay_untouched(project):
    old = project.propose("Task", "old", **CALL)
    peer = project.propose("Task", "peer", **CALL)
    edge = project.link("relates-to", old["id"], peer["id"], **CALL)
    project.retire(peer["id"], "finished", **CALL)
    retired = stored_edge(project, edge["id"]).to_dict()
    new = project.supersede(old["id"], **CALL)
    assert stored_edge(project, edge["id"]).to_dict() == retired
    assert not any(
        e["type"] == "relates-to" for e in project.show(new["id"], **CALL)["edges"]
    )


@pytest.mark.parametrize("operation,args", [("instruct", (4,)), ("retire", (4,))])
def test_revision_text_arguments_are_strings(project, operation, args):
    old = project.propose("Task", "old", **CALL)
    before = source(project)
    refusal("schema", getattr(project, operation), old["id"], *args, **CALL)
    assert source(project) == before


def test_committed_revision_callbacks_have_canonical_candidates(tmp_path):
    calls, validations = [], []
    with profile_project(
        tmp_path,
        hooks=[lambda p, w, c: calls.append((deepcopy(w), c))],
        validators=[lambda p, w: validations.append(deepcopy(w))],
    ) as p:
        old = p.propose("Task", "old", **CALL)
        calls.clear()
        instruction = p.instruct(old["id"], "text", role="owner", **CALL)
        replacement = p.supersede(old["id"], instruction="next", role="owner", **CALL)
        retired = p.retire(replacement["id"], "abandoned", role="owner", **CALL)
        assert [w.op for w, _ in calls] == ["instruct", "supersede", "retire"]
        assert [w.op for w in validations[-3:]] == ["instruct", "supersede", "retire"]
        assert instruction in [n.to_dict() for n in calls[0][0].nodes]
        assert replacement in [n.to_dict() for n in calls[1][0].nodes]
        assert retired in [n.to_dict() for n in calls[2][0].nodes]
        assert all(w.role == "owner" and commit for w, commit in calls)


def test_revision_single_lock_hooks_no_commit_and_scoped_commit(tmp_path, monkeypatch):
    import researchgraph.project as module

    hooks = []
    with profile_project(
        tmp_path, hooks=[lambda p, w, c: hooks.append((w.op, c))]
    ) as p:
        task = p.propose("Task", "T", **CALL)
        current = task["id"]
        hooks.clear()
        for name, args in [
            ("instruct", (task["id"], "text")),
            ("supersede", (task["id"],)),
            ("retire", ()),
        ]:
            builder = getattr(module, "_" + name)

            def locked(project, write, nodes, edges, builder=builder):
                with (
                    (project.path / ".researchgraph/lock").open("a+b") as lock,
                    pytest.raises(BlockingIOError),
                ):
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return builder(project, write, nodes, edges)

            monkeypatch.setattr(module, "_" + name, locked)
            if name == "retire":
                args = (current, "why")
            head = git(tmp_path, "rev-parse", "HEAD")
            result = getattr(p, name)(*args, commit=False, **CALL)
            assert git(tmp_path, "rev-parse", "HEAD") == head and hooks == []
            if name == "supersede":
                current = result["id"]
        (tmp_path / "unrelated").write_text("user")
        git(tmp_path, "add", "unrelated")
        value = p.propose("Task", "commit target", **CALL)
        hooks.clear()
        p.retire(value["id"], "done", **CALL)
        assert [op for op, _ in hooks] == ["retire"]
        assert "unrelated" in git(tmp_path, "diff", "--cached", "--name-only")


@settings(max_examples=5, deadline=None)
@given(
    st.lists(
        st.sampled_from(["supersede", "instruct", "retire", "invalid"]),
        min_size=1,
        max_size=8,
    )
)
def test_revision_sequences_preserve_append_only_graph_and_cache(actions):
    with (
        TemporaryDirectory() as directory,
        Project.init(repository(Path(directory)), **CALL) as p,
    ):
        current = p.propose("Task", "T", **CALL)
        for action in actions:
            before_nodes, before_edges = p._before_state()
            before = source(p)
            if action == "invalid":
                refusal(
                    "schema", p.supersede, current["id"], title="bad\ntitle", **CALL
                )
                assert source(p) == before
            elif action == "retire":
                p.retire(current["id"], "reason", **CALL)
                current = p.propose("Task", "next", **CALL)
            elif action == "instruct":
                p.instruct(current["id"], "instruction", **CALL)
            else:
                current = p.supersede(current["id"], **CALL)
            nodes, edges = p._before_state()
            assert set(before_nodes) <= set(nodes) and set(before_edges) <= set(edges)
            for identifier, old in before_nodes.items():
                new = nodes[identifier].to_dict()
                assert_immutable(old.to_dict(), new)
                assert (
                    new["approvals"][: len(old.approvals)] == old.to_dict()["approvals"]
                )
            for identifier, old in before_edges.items():
                assert_immutable(
                    old.to_dict(), edges[identifier].to_dict(), EDGE_IMMUTABLE_KEYS
                )
                assert not old.retired or edges[identifier].retired
            validate_graph(p.profile, nodes, edges)
            kept = [
                p.digest(**CALL),
                p.show(current["id"], **CALL),
                p.history(current["id"], **CALL),
            ]
            p.index.close()
            (p.path / ".researchgraph/index.sqlite").unlink(missing_ok=True)
            assert [
                p.digest(**CALL),
                p.show(current["id"], **CALL),
                p.history(current["id"], **CALL),
            ] == kept
