"""Public execution lifecycle: leases, accounting, artifacts and transactions."""

import hashlib
import multiprocessing
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from researchgraph.model import NODE_IMMUTABLE_KEYS, Actor, RuleViolation
from researchgraph.profiles import Gate, NodeType, Profile
from researchgraph.project import Project
from researchgraph.queries import evaluation_time
from researchgraph.store.io import dump_node, load_node
from researchgraph.validation import validate_graph
from tests.helpers import ALICE, NOW, git, refusal, repository

BOT = Actor("agent", "agent1", model="original", version="1")
OTHER = Actor("agent", "agent2")
CALL = {"actor": ALICE, "now": NOW}


def snapshot(project):
    return (
        {
            p.relative_to(project.path).as_posix(): p.read_bytes()
            for p in (project.path / "graph").rglob("*.yaml")
        },
        git(project.path, "rev-parse", "HEAD"),
        git(project.path, "status", "--porcelain"),
    )


def cold(project, mode="deleted"):
    if mode == "deleted":
        project.index.close()
        (project.path / ".researchgraph/index.sqlite").unlink(missing_ok=True)


def task(project, title="T"):
    return project.propose("Task", title, **CALL)["id"]


def claim(project, id_, seconds=7200):
    return project.claim(id_, seconds, actor=BOT, now=NOW)


@pytest.mark.parametrize("seconds", [1, 3600, 7200])
def test_claim_canonical_reclaim_equality_and_lost_rights(project, index_mode, seconds):
    id_ = task(project)
    cold(project, index_mode)
    value = claim(project, id_, seconds)
    assert value == load_node(project.path / f"graph/nodes/{id_}.yaml")
    assert value["status"] == "running"
    assert value["lease"] == {
        "actor": BOT.to_dict(),
        "acquired_at": "2026-10-20T00:00:00Z",
        "expires_at": (NOW + timedelta(seconds=seconds))
        .isoformat()
        .replace("+00:00", "Z"),
    }
    assert value["approvals"][-1] == {
        "actor": BOT.to_dict(),
        "at": value["updated"],
        "what": "claim",
    }
    refusal("lease_held", project.claim, id_, 0, actor=OTHER, now=NOW)
    expiry = NOW + timedelta(seconds=seconds)
    assert id_ in {n["id"] for n in project.ready(actor=ALICE, now=expiry)["items"]}
    reclaimed = project.claim(id_, 10, actor=OTHER, now=expiry)
    assert reclaimed["lease"]["actor"] == OTHER.to_dict()
    assert reclaimed["lease"]["acquired_at"] == reclaimed["updated"]
    for op, args in [
        ("renew", ()),
        ("release", ()),
        ("complete", ("done",)),
        ("fail", ("bad",)),
    ]:
        refusal(
            "not_lease_holder", getattr(project, op), id_, *args, actor=BOT, now=expiry
        )
    assert id_ not in {n["id"] for n in project.ready(actor=ALICE, now=expiry)["items"]}


def test_claim_readiness_blockers_nonclaimable_and_budget(project, index_mode):
    a, b = task(project, "A"), task(project, "B")
    project.link("blocks", a, b, **CALL)
    cold(project, index_mode)
    refusal("not_ready", project.claim, b, 0, actor=BOT, now=NOW)
    for type_ in ["Instruction", "Failure", "Note", "Hypothesis"]:
        id_ = project.propose(type_, type_, **CALL)["id"]
        refusal("not_ready", project.claim, id_, actor=BOT, now=NOW)
    # T13's budget operation is not available yet; retain the prescribed given.
    path = project.path / f"graph/nodes/{a}.yaml"
    stored = load_node(path)
    stored["budget"] = {"tokens": 0}
    path.write_text(dump_node(stored), encoding="utf-8")
    refusal("not_ready", project.claim, a, actor=BOT, now=NOW)


def test_claim_lease_held_precedes_new_blocker_and_negative(project):
    id_, blocker = task(project), task(project, "blocker")
    claim(project, id_)
    project.link("blocks", blocker, id_, **CALL)
    before = snapshot(project)
    refusal("lease_held", project.claim, id_, 0, actor=BOT, now=NOW)
    assert snapshot(project) == before
    refusal(
        "not_ready",
        project.claim,
        id_,
        0,
        actor=OTHER,
        now=NOW + timedelta(seconds=7200),
    )


@pytest.mark.parametrize("seconds", [0, -1, True, 1.5, "10", None])
def test_bad_lease_durations_are_atomic(project, seconds):
    id_ = task(project)
    before = snapshot(project)
    refusal("negative", project.claim, id_, seconds, actor=BOT, now=NOW)
    assert snapshot(project) == before
    claim(project, id_)
    before = snapshot(project)
    refusal("negative", project.renew, id_, seconds, actor=BOT, now=NOW)
    assert snapshot(project) == before


def test_renew_never_shortens_preserves_holder_metadata_and_acquisition(
    project, index_mode
):
    id_ = task(project)
    original = claim(project, id_, 5000)
    changed_metadata = Actor("agent", BOT.name, model="new", version="2")
    cold(project, index_mode)
    renewed = project.renew(id_, 3600, actor=changed_metadata, role="owner", now=NOW)
    assert renewed["lease"] == original["lease"]
    assert renewed["approvals"][-1]["actor"] == changed_metadata.to_dict()
    assert renewed["approvals"][-1]["role"] == "owner"
    later = NOW + timedelta(seconds=6000)
    renewed = project.renew(id_, 3600, actor=changed_metadata, now=later)
    assert renewed["lease"]["acquired_at"] == original["lease"]["acquired_at"]
    assert renewed["lease"]["actor"] == BOT.to_dict()
    assert renewed["lease"]["expires_at"] == "2026-10-20T02:40:00Z"
    refusal(
        "not_lease_holder",
        project.renew,
        id_,
        actor=Actor("human", BOT.name),
        now=later,
    )


@pytest.mark.parametrize(
    "operation,args",
    [("release", ()), ("complete", ("done",)), ("fail", ("bad",)), ("renew", ())],
)
def test_execution_not_running_and_shared_priorities(project, operation, args):
    id_ = task(project)
    fn = getattr(project, operation)
    refusal("not_running", fn, id_, *args, actor=OTHER, now=NOW)
    refusal("wrong_role", fn, id_, *args, actor=BOT, role="unknown", now=NOW)
    refusal("not_found", fn, "ta-zzzzzzzz", *args, actor=BOT, role="unknown", now=NOW)
    project.read_only = True
    refusal("read_only", fn, id_, *args, actor=BOT, now=NOW - timedelta(seconds=1))
    project.read_only = False
    refusal("clock_skew", fn, id_, *args, actor=BOT, now=NOW - timedelta(seconds=1))


@pytest.mark.parametrize(
    "operation,args,status",
    [
        ("release", ("pause",), "active"),
        ("complete", ("done",), "done"),
        ("fail", ("bad",), "failed"),
    ],
)
def test_expired_holder_finish_usage_and_no_alias(
    project, index_mode, operation, args, status
):
    id_ = task(project)
    claim(project, id_, 1)
    first_usage = {"tokens": 100}
    project.release(id_, usage=first_usage, actor=BOT, now=NOW)
    claim(project, id_, 1)
    usage = {"tokens": 50, "compute_seconds": 0}
    original = deepcopy(usage)
    cold(project, index_mode)
    result = getattr(project, operation)(
        id_, *args, usage=usage, actor=BOT, now=NOW + timedelta(seconds=1)
    )
    assert (
        result["id"] == id_ and result["status"] == status and result["lease"] is None
    )
    assert result["usage"] == {"tokens": 150, "compute_seconds": 0}
    assert result["approvals"][-1]["what"] == operation
    assert result["approvals"][-1]["note"] == args[0]
    assert usage == original and first_usage == {"tokens": 100}
    usage["tokens"] = 999
    result["usage"]["tokens"] = 999
    assert project.get_node(id_).usage["tokens"] == 150
    refusal(
        "not_running",
        getattr(project, operation),
        id_,
        *args,
        actor=BOT,
        now=NOW + timedelta(seconds=1),
    )


@pytest.mark.parametrize(
    "usage,rule",
    [
        ({"tokens": -1}, "negative"),
        ({"compute_seconds": -1}, "negative"),
        ({"tokens": True}, "schema"),
        ({"tokens": 1.2}, "schema"),
        ({"tokens": "1"}, "schema"),
        ({"other": 1}, "schema"),
        ([], "schema"),
    ],
)
@pytest.mark.parametrize("operation", ["release", "complete", "fail"])
def test_usage_validation_atomic(project, usage, rule, operation):
    id_ = task(project)
    claim(project, id_)
    before = snapshot(project)
    args = () if operation == "release" else ("summary",)
    refusal(
        rule, getattr(project, operation), id_, *args, usage=usage, actor=BOT, now=NOW
    )
    assert snapshot(project) == before


def test_human_may_release_but_not_complete_or_fail(project):
    id_ = task(project)
    claim(project, id_)
    for op in ["complete", "fail"]:
        refusal(
            "not_lease_holder",
            getattr(project, op),
            id_,
            "summary",
            actor=ALICE,
            now=NOW,
        )
    refusal("not_lease_holder", project.release, id_, actor=OTHER, now=NOW)
    assert project.release(id_, actor=ALICE, now=NOW)["status"] == "active"
    assert id_ in {n["id"] for n in project.ready(**CALL)["items"]}


def test_execution_does_not_run_task_command(project):
    id_ = project.propose(
        "Task", "opaque task", fields={"command": "touch MUST_NOT_EXIST"}, **CALL
    )["id"]
    claim(project, id_)
    project.release(id_, actor=BOT, now=NOW)
    claim(project, id_)
    project.fail(id_, "reported failure", actor=BOT, now=NOW)
    assert not (project.path / "MUST_NOT_EXIST").exists()


def test_execution_usage_rolls_up_research_subtree(project, index_mode):
    hypothesis = project.propose("Hypothesis", "H", **CALL)["id"]
    prediction = project.propose("Prediction", "P", **CALL)["id"]
    id_ = task(project)
    project.link("tests", prediction, hypothesis, fields={"expected": "yes"}, **CALL)
    project.link("addresses", id_, prediction, **CALL)
    claim(project, id_)
    cold(project, index_mode)
    project.complete(
        id_, "done", usage={"tokens": 1200, "compute_seconds": 5}, actor=BOT, now=NOW
    )
    assert project.subtree(hypothesis, **CALL)["usage"] == {
        "tokens": 1200,
        "compute_seconds": 5,
    }


def artifact(project, id_, name="out.csv", content=b"hello"):
    relative = f"artifacts/{id_}/{name}"
    path = project.path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return relative


def test_complete_entire_artifact_stream_unicode_newline_scoped_commit(
    project, index_mode
):
    id_, blocked = task(project), task(project, "blocked")
    project.link("blocks", id_, blocked, **CALL)
    claim(project, id_)
    data = b"a" * (2 * 1024 * 1024) + b"late bytes\x00\xff"
    relative = artifact(project, id_, "결과\nfile.csv", data)
    other = artifact(project, id_, "not-named", b"keep")
    (project.path / "unrelated").write_text("staged")
    git(project.path, "add", "unrelated")
    cold(project, index_mode)
    result = project.complete(id_, "done", [relative], actor=BOT, now=NOW)
    assert result["artifacts"] == [
        {
            "path": relative,
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data),
            "added_at": "2026-10-20T00:00:00Z",
            "added_by": BOT.to_dict(),
        }
    ]
    paths = set(
        git(
            project.path,
            "-c",
            "core.quotePath=false",
            "diff-tree",
            "--no-commit-id",
            "--name-only",
            "-z",
            "-r",
            "HEAD",
        ).split("\x00")
    )
    assert paths - {""} == {relative, f"graph/nodes/{id_}.yaml"}
    assert "unrelated" in git(project.path, "diff", "--cached", "--name-only")
    assert (project.path / other).read_bytes() == b"keep"
    assert blocked in {n["id"] for n in project.ready(**CALL)["items"]}


@pytest.mark.parametrize(
    "bad",
    [
        "data/x",
        "../x",
        "/tmp/x",
        "artifacts/{id}/../x",
        "artifacts/{id}/./x",
        "artifacts/{id}//x",
        "artifacts/{id}/x\\y",
        "artifacts/{other}/x",
    ],
)
def test_artifact_path_precedes_all_missing_and_usage(project, bad):
    id_, other = task(project), task(project, "other")
    claim(project, id_)
    before = snapshot(project)
    refusal(
        "artifact_path",
        project.complete,
        id_,
        "done",
        [f"artifacts/{id_}/missing", bad.format(id=id_, other=other)],
        usage={"tokens": -1},
        actor=BOT,
        now=NOW,
    )
    assert snapshot(project) == before


@pytest.mark.parametrize(
    "kind,rule",
    [
        ("directory", "artifact_missing"),
        ("missing", "artifact_missing"),
        ("symlink", "artifact_path"),
        ("parent_symlink", "artifact_path"),
    ],
)
def test_artifact_nonfiles_and_symlinks(project, kind, rule):
    id_ = task(project)
    claim(project, id_)
    relative = f"artifacts/{id_}/out"
    path = project.path / relative
    path.parent.mkdir(parents=True)
    if kind == "directory":
        path.mkdir()
    elif kind == "symlink":
        path.symlink_to(project.path / "absent")
    elif kind == "parent_symlink":
        path.parent.rmdir()
        path.parent.symlink_to(project.path / "graph", target_is_directory=True)
    before = snapshot(project)
    refusal(
        rule,
        project.complete,
        id_,
        "done",
        [relative],
        usage={"tokens": -1},
        actor=BOT,
        now=NOW,
    )
    assert snapshot(project) == before


def test_artifact_duplicate_records_empty_usage_and_input_isolation(project):
    id_ = task(project)
    claim(project, id_)
    relative = artifact(project, id_, content=b"")
    paths = [relative, relative]
    value = project.complete(
        id_, "done", paths, usage={}, actor=BOT, now=NOW, commit=False
    )
    assert len(value["artifacts"]) == 2
    assert value["artifacts"][0] == value["artifacts"][1]
    assert value["artifacts"][0]["sha256"] == hashlib.sha256(b"").hexdigest()
    assert value["artifacts"][0]["bytes"] == 0
    assert value["usage"] is None
    paths.clear()
    value["artifacts"].clear()
    assert len(project.get_node(id_).artifacts) == 2


@pytest.mark.parametrize("paths", ["out.csv", {}, [None]])
def test_malformed_artifact_input_is_atomic(project, paths):
    id_ = task(project)
    claim(project, id_)
    before = snapshot(project)
    refusal(
        "artifact_path" if isinstance(paths, list) else "schema",
        project.complete,
        id_,
        "done",
        paths,
        actor=BOT,
        now=NOW,
    )
    assert snapshot(project) == before


def test_fail_generated_failure_and_blocked_queries(project, index_mode):
    id_, blocked = task(project, "T" * 200), task(project, "blocked")
    project.link("blocks", id_, blocked, **CALL)
    claim(project, id_)
    cold(project, index_mode)
    summary = "SNR too low\nexact body\n"
    result = project.fail(
        id_,
        summary,
        step="extract_1d",
        check="continuum_snr>=5",
        measured="min 3.1",
        actor=BOT,
        now=NOW,
    )
    assert result["id"] == id_ and result["status"] == "failed"
    explains = [e for e in project.get_edges() if e.type == "explains"]
    assert len(explains) == 1 and explains[0].to == id_
    failure = project.get_node(explains[0].from_).to_dict()
    assert failure["title"] == ("Failure: " + "T" * 200)[:200]
    assert failure["body"] == summary and failure["author"] == BOT.to_dict()
    assert failure["fields"] == {
        "step": "extract_1d",
        "check": "continuum_snr>=5",
        "measured": "min 3.1",
    }
    assert failure["status"] == "active" and failure["lease"] is None
    assert failure["id"] not in {n["id"] for n in project.ready(**CALL)["items"]}
    assert blocked in {n["id"] for n in project.blocked(**CALL)["items"]}
    failed = next(
        group
        for group in project.digest(**CALL)["items"]
        if group["kind"] == "failed_24h"
    )
    assert id_ in {n["id"] for n in failed["nodes"]}


def execution_profile(path, *, failure=True, gated=False, hooks=(), validators=()):
    types = [NodeType("Task", "ta", {}, claimable=True)]
    if failure:
        types.append(
            NodeType("Failure", "fl", {"properties": {"error": {"type": "string"}}})
        )
    Profile.register(
        Profile(
            "execution",
            "1.0",
            types,
            [],
            [Gate(node_type="Failure")] if gated else [],
            [],
            list(validators),
            event_hooks=list(hooks),
        )
    )
    return Project.init(repository(path), profile="execution", **CALL)


def test_failure_gate_and_missing_reserved_type(tmp_path):
    with execution_profile(tmp_path / "gated", gated=True) as project:
        id_ = task(project)
        claim(project, id_)
        project.fail(id_, "bad", error="error", actor=BOT, now=NOW)
        assert project.where(type="Failure", **CALL)[0]["status"] == "proposed"
    with execution_profile(tmp_path / "missing", failure=False) as project:
        id_ = task(project)
        before = snapshot(project)
        refusal("unsupported_by_profile", project.fail, id_, "bad", actor=BOT, now=NOW)
        assert snapshot(project) == before


def test_claim_waits_for_task_activate_gate(tmp_path):
    profile = Profile(
        "execution-gate",
        "1.0",
        [NodeType("Task", "ta", {}, claimable=True)],
        [],
        [Gate(node_type="Task")],
        [],
        [],
    )
    Profile.register(profile)
    with Project.init(repository(tmp_path), profile=profile.name, **CALL) as project:
        id_ = task(project)
        refusal("not_ready", project.claim, id_, actor=BOT, now=NOW)
        project.approve(id_, **CALL)
        assert claim(project, id_)["status"] == "running"


def test_callbacks_once_no_commit_visibility_and_fail_rollback(tmp_path, monkeypatch):
    from researchgraph.store import transaction

    validations, events = [], []
    with execution_profile(
        tmp_path,
        validators=[lambda p, w: validations.append(w.op)],
        hooks=[lambda p, w, c: events.append(w.op)],
    ) as project:
        id_ = task(project)
        validations.clear()
        events.clear()
        head = git(tmp_path, "rev-parse", "HEAD")
        for op, args in [
            ("claim", ()),
            ("renew", ()),
            ("release", ()),
            ("claim", ()),
            ("complete", ("done",)),
        ]:
            result = getattr(project, op)(id_, *args, actor=BOT, now=NOW, commit=False)
            assert result == load_node(tmp_path / f"graph/nodes/{id_}.yaml")
        assert validations == ["claim", "renew", "release", "claim", "complete"]
        assert events == [] and git(tmp_path, "rev-parse", "HEAD") == head
        id_ = task(project, "rollback")
        claim(project, id_)
        before = snapshot(project)
        replace = transaction.os.replace
        count = 0

        def broken(source, destination):
            nonlocal count
            count += 1
            if count == 2:
                raise OSError("injected rename failure")
            return replace(source, destination)

        monkeypatch.setattr(transaction.os, "replace", broken)
        refusal("write_failed", project.fail, id_, "bad", actor=BOT, now=NOW)
        assert snapshot(project) == before
        monkeypatch.setattr(transaction.os, "replace", replace)
        validations.clear()
        events.clear()
        project.fail(id_, "bad", actor=BOT, now=NOW)
        assert validations == ["fail"] and events == ["fail"]


@pytest.mark.parametrize("operation", ["claim", "renew", "release", "complete", "fail"])
@pytest.mark.parametrize("commit", [True, False])
def test_each_execution_callback_and_no_commit_contract(tmp_path, operation, commit):
    validations, events = [], []
    with execution_profile(
        tmp_path,
        validators=[lambda p, w: validations.append(deepcopy(w))],
        hooks=[lambda p, w, c: events.append((w.op, c))],
    ) as project:
        id_ = task(project)
        if operation != "claim":
            claim(project, id_)
        validations.clear()
        events.clear()
        before_head = git(tmp_path, "rev-parse", "HEAD")
        args = ("summary",) if operation in {"complete", "fail"} else ()
        result = getattr(project, operation)(
            id_, *args, actor=BOT, now=NOW, commit=commit
        )
        assert result == project.get_node(id_).to_dict()
        assert len(validations) == 1 and validations[0].op == operation
        assert validations[0].args["node"] == id_
        if commit:
            head = git(tmp_path, "rev-parse", "HEAD")
            assert head != before_head and events == [(operation, head)]
            assert git(tmp_path, "log", "-1", "--format=%s") == f"rg: {operation} {id_}"
            assert (
                git(tmp_path, "log", "-1", "--format=%an <%ae>")
                == "agent1 <agent1@agents.researchgraph>"
            )
        else:
            assert git(tmp_path, "rev-parse", "HEAD") == before_head and events == []
            assert git(tmp_path, "status", "--porcelain")
            kept = project.show(id_, **CALL)
            cold(project)
            assert project.show(id_, **CALL) == kept


def _contend(path, id_, start, output, name):
    try:
        with Project.open(path) as project:
            start.wait(20)
            try:
                project.claim(id_, actor=Actor("agent", name), now=NOW)
                output.put("success")
            except RuleViolation as exc:
                output.put(exc.rule)
    except BaseException as exc:
        output.put(repr(exc))
        raise


def test_two_process_claims_one_checkout(tmp_path, index_mode):
    with Project.init(repository(tmp_path), **CALL) as project:
        id_ = task(project)
        cold(project, index_mode)
    context = multiprocessing.get_context("spawn")
    start, output = context.Event(), context.Queue()
    workers = [
        context.Process(target=_contend, args=(tmp_path, id_, start, output, str(i)))
        for i in range(2)
    ]
    try:
        for worker in workers:
            worker.start()
        start.set()
        assert sorted(output.get(timeout=60) for _ in workers) == [
            "lease_held",
            "success",
        ]
        for worker in workers:
            worker.join(timeout=30)
            assert worker.exitcode == 0
    finally:
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
                worker.join()


def queries(project, ids, now, as_of=None):
    call = {"actor": ALICE, "now": now, "as_of": as_of}
    q, h, c = ids["q"], ids["h"], ids["c"]
    visible = project.where(**call)
    visible_ids = {record["id"] for record in visible}
    edges = project.get_edges()
    cutoff = evaluation_time(project, now, as_of)
    visible_ids.update(
        {
            edge.id
            for edge in edges
            if as_of is None
            or (
                edge.created <= cutoff
                and edge.from_ in visible_ids
                and edge.to in visible_ids
            )
        }
    )

    def required_query(operation, identifier):
        try:
            return operation(identifier, **call)
        except RuleViolation as exc:
            assert exc.rule == "not_found"
            assert identifier not in visible_ids
            return {"rule": exc.rule, "ids": exc.ids}

    return {
        "ready": project.ready(**call),
        "blocked": project.blocked(**call),
        "queue": project.queue(**call),
        "stale": project.stale(**call),
        "where": project.where(**call),
        "digest": project.digest(**call),
        "since": project.since(NOW - timedelta(seconds=1), **call),
        "show": [project.show(n["id"], **call) for n in visible],
        "edges": [required_query(project.show, e.id) for e in edges],
        "history": [project.history(n["id"], **call) for n in visible],
        "notes": required_query(project.notes, h),
        "subtree": required_query(project.subtree, h),
        "evidence": required_query(project.evidence, c),
        "weakest": required_query(project.weakest, c),
        "alternatives": required_query(project.alternatives, h),
        "predictions": required_query(project.predictions, h),
        "matrix": required_query(project.matrix, q),
    }


@settings(max_examples=8, deadline=None)
@given(
    st.lists(
        st.tuples(
            st.sampled_from(
                [
                    "claim",
                    "renew",
                    "release",
                    "complete",
                    "fail",
                    "propose",
                    "link",
                    "supersede",
                    "instruct",
                    "retire",
                    "approve",
                    "reject",
                ]
            ),
            st.booleans(),
            st.booleans(),
            st.integers(0, 3),
        ),
        min_size=5,
        max_size=14,
    )
)
def test_random_public_execution_sequences_i1_to_i8(actions):
    with (
        TemporaryDirectory() as directory,
        Project.init(repository(Path(directory)), **CALL) as project,
    ):
        ids = {
            type_[0].lower(): project.propose(type_, type_, **CALL)["id"]
            for type_ in ["Question", "Hypothesis", "Claim"]
        }
        result = project.propose("Result", "R", **CALL)["id"]
        project.link("answers", ids["h"], ids["q"], **CALL)
        project.link("cites", ids["c"], result, **CALL)
        target = task(project)
        for i, (operation, agent, commit, delta) in enumerate(actions):
            now = NOW + timedelta(seconds=(i + 1) * 10 + delta)
            before = snapshot(project)
            old_nodes = {n.id: n.to_dict() for n in project.get_nodes()}
            old_edges = {e.id: e.to_dict() for e in project.get_edges()}
            target_before = target
            success = False
            kwargs = {"actor": BOT if agent else ALICE, "now": now, "commit": commit}
            try:
                if operation == "claim":
                    project.claim(target, 1 if delta else 0, **kwargs)
                elif operation == "renew":
                    project.renew(target, 1 if delta else -1, **kwargs)
                elif operation == "release":
                    project.release(target, usage={"tokens": delta}, **kwargs)
                elif operation in {"complete", "fail"}:
                    getattr(project, operation)(
                        target, "summary", usage={"tokens": delta}, **kwargs
                    )
                elif operation == "propose":
                    target = project.propose("Task", "new", **kwargs)["id"]
                elif operation == "link":
                    project.link(
                        "blocks", target, target if delta else result, **kwargs
                    )
                elif operation == "supersede":
                    target = project.supersede(target, instruction="retry", **kwargs)[
                        "id"
                    ]
                elif operation == "instruct":
                    project.instruct(target, "guidance", **kwargs)
                elif operation == "retire":
                    project.retire(target, "stop", **kwargs)
                elif operation == "approve":
                    project.approve(ids["h"], **kwargs)
                else:
                    project.reject(ids["h"], "reject", **kwargs)
                success = True
            except RuleViolation as exc:
                assert exc.rule in {
                    "not_running",
                    "not_lease_holder",
                    "negative",
                    "not_ready",
                    "lease_held",
                    "cycle",
                    "running",
                    "terminal_status",
                    "already_ran",
                    "human_only_approval",
                    "not_proposed",
                    "already_approved",
                    "duplicate_edge",
                }
                assert snapshot(project) == before
            nodes = {n.id: n for n in project.get_nodes()}
            edges = {e.id: e for e in project.get_edges()}
            validate_graph(project.profile, nodes, edges)
            if success and operation in {
                "claim",
                "renew",
                "release",
                "complete",
                "fail",
            }:
                previous = old_nodes[target_before]
                current = nodes[target_before].to_dict()
                transitions = {
                    "claim": ({"active", "running"}, "running"),
                    "renew": ({"running"}, "running"),
                    "release": ({"running"}, "active"),
                    "complete": ({"running"}, "done"),
                    "fail": ({"running"}, "failed"),
                }
                assert previous["status"] in transitions[operation][0]
                assert current["status"] == transitions[operation][1]
                if operation != "claim":
                    holder = previous["lease"]["actor"]
                    assert (holder["kind"], holder["name"]) == (
                        kwargs["actor"].kind,
                        kwargs["actor"].name,
                    ) or (operation == "release" and not agent)
                assert len(current["approvals"]) == len(previous["approvals"]) + 1
                assert current["approvals"][-1]["actor"] == kwargs["actor"].to_dict()
            if success and operation in {"instruct", "retire", "reject"}:
                assert not agent
            assert old_nodes.keys() <= nodes.keys() and old_edges.keys() <= edges.keys()
            for id_, previous in old_nodes.items():
                current = nodes[id_].to_dict()
                assert {k: current[k] for k in NODE_IMMUTABLE_KEYS} == {
                    k: previous[k] for k in NODE_IMMUTABLE_KEYS
                }
                for key in ["approvals", "artifacts"]:
                    assert current[key][: len(previous[key])] == previous[key]
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
            kept = [
                queries(project, ids, now, as_of)
                for as_of in [None, NOW, now, git(project.path, "rev-parse", "HEAD")]
            ]
            cold(project)
            assert [
                queries(project, ids, now, as_of)
                for as_of in [None, NOW, now, git(project.path, "rev-parse", "HEAD")]
            ] == kept


@pytest.mark.parametrize("operation", ["complete", "attach"])
def test_low_level_execution_candidates_keep_artifact_ownership(project, operation):
    from hashlib import sha256

    from researchgraph.model import Approval, Artifact, Status, Write

    id_, other = task(project), task(project, "other")
    if operation == "complete":
        claim(project, id_)
    relative = artifact(project, other, content=b"foreign")
    candidate = deepcopy(project.get_node(id_))
    candidate.artifacts.append(
        Artifact(relative, sha256(b"foreign").hexdigest(), 7, NOW, BOT)
    )
    args = {"node": id_, "artifacts": [relative]}
    if operation == "complete":
        candidate.status, candidate.lease = Status.DONE, None
        candidate.approvals.append(Approval(BOT, NOW, "complete", note="done"))
        args["summary"] = "done"
    before = snapshot(project)
    refusal(
        "artifact_path",
        project.apply,
        Write(operation, BOT, None, NOW, args, [candidate], []),
        commit=False,
    )
    assert snapshot(project) == before
