"""Deterministic SDK replay, not an unassisted agent-product recording.

The example checker is an independent native-files/Git audit. Its public contract
is check(root, manifest) -> dict on success and AssertionError on a failed audit.
Seed returns a JSON-serialisable manifest; it never approves session Claims.
"""

import json
from datetime import timedelta
from functools import partial
from hashlib import sha256
from pathlib import Path

import anyio
import pytest
import yaml
from examples.agent_session.check import check
from examples.agent_session.seed import seed

from researchgraph.model import Actor, format_timestamp
from tests.helpers import ALICE, NOW, git, repository
from tests.mcp_helpers import memory_client

pytestmark = pytest.mark.mcp

AGENT = Actor("agent", "session-replay", model="deterministic-script", version="1")
ARTIFACT = b"sample,value\ncontrol,2\ntreatment,4\n"
FAILURE_FIELDS = {
    "step": "validate second sample",
    "check": "sample count must be at least 2",
    "measured": "1",
    "error": "insufficient samples",
}


def native_graph(root):
    """Do not use the MCP responses, Project queries or SQLite as the oracle."""
    return tuple(
        {
            record["id"]: record
            for path in sorted((root / "graph" / kind).glob("*.yaml"))
            for record in [yaml.safe_load(path.read_text(encoding="utf-8"))]
        }
        for kind in ("nodes", "edges")
    )


def graph_bytes(root):
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted((root / "graph").rglob("*.yaml"))
    }


async def replay(root, manifest, *, preset, index_mode):
    """The ten prompt steps, through actual discovery and ClientSession RPCs."""
    tick = 0
    async with memory_client(root, actor=AGENT, preset=preset) as client:
        await client.discover()

        async def rpc(name, **arguments):
            nonlocal tick
            tick += 1
            if index_mode == "deleted":
                (root / ".researchgraph" / "index.sqlite").unlink(missing_ok=True)
            result = await client.call_tool(
                name,
                {
                    **arguments,
                    "now": format_timestamp(NOW + timedelta(minutes=tick)),
                },
            )
            assert not result.is_error, result
            assert len(result.content) == 1
            assert result.content[0].type == "text"
            value = json.loads(result.content[0].text)
            assert value == result.structured_content
            return value

        # 1. Inspect the human-approved Hypothesis.
        hypothesis = await rpc("show", id=manifest["hypothesis"])
        assert hypothesis["type"] == "Hypothesis"
        assert hypothesis["status"] == "active"

        # 2. Propose an ungated Task addressing the approved Prediction.
        task = await rpc(
            "propose",
            type="Task",
            title="Measure the deterministic sample",
            edges=[{"type": "addresses", "to": manifest["prediction"]}],
        )
        assert task["status"] == "active"

        # 3–5. Discover readiness, acquire the lease, write real bytes, complete.
        ready = await rpc("ready")
        assert task["id"] in {node["id"] for node in ready["items"]}
        running = await rpc("claim", node=task["id"])
        assert running["lease"]["actor"] == AGENT.to_dict()
        task_path = f"artifacts/{task['id']}/measurement.csv"
        target = root / task_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(ARTIFACT)
        completed = await rpc(
            "complete",
            node=task["id"],
            summary="Recorded deterministic measurements",
            artifacts=[task_path],
        )
        assert completed["status"] == "done" and completed["lease"] is None

        # 6. Result evidence must belong to the Result, not the Task's directory.
        result = await rpc(
            "propose",
            type="Result",
            title="Treatment is twice the control",
            fields={"runs": 1, "held_out": False},
            edges=[{"type": "derived-from", "to": task["id"]}],
        )
        result_path = f"artifacts/{result['id']}/measurement.csv"
        target = root / result_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(ARTIFACT)
        attached = await rpc("attach", node=result["id"], artifacts=[result_path])
        assert attached["artifacts"][0]["sha256"] == sha256(ARTIFACT).hexdigest()

        # 7. Proposing the gated Claim is the request for human approval.
        claim = await rpc(
            "propose",
            type="Claim",
            title="The sample supports the hypothesis",
            edges=[
                {
                    "type": "cites",
                    "to": result["id"],
                    "fields": {
                        "artifact": result_path,
                        "sha256": sha256(ARTIFACT).hexdigest(),
                    },
                },
                {
                    "type": "supports",
                    "to": manifest["hypothesis"],
                    "fields": {"strength": "weak"},
                },
            ],
        )
        assert claim["status"] == "proposed" and claim["approvals"] == []

        # 8. Confirm the approval request remains queued; do not call approve.
        queue = await rpc("queue")
        assert [node["id"] for node in queue["items"]] == [claim["id"]]

        # 9. A separate Task fails with scalar step/check fields, not a fake run.
        second = await rpc(
            "propose",
            type="Task",
            title="Validate a deliberately short sample",
            edges=[{"type": "addresses", "to": manifest["prediction"]}],
        )
        await rpc("claim", node=second["id"])
        failed = await rpc(
            "fail",
            node=second["id"],
            summary="Sample validation failed",
            **FAILURE_FIELDS,
        )
        assert failed["status"] == "failed" and failed["lease"] is None

        # 10. The digest reflects both outcomes and the pending human decision.
        digest = await rpc("digest")
        groups = {item["kind"]: item for item in digest["items"]}
        assert groups["running"]["count"] == 0
        assert groups["awaiting"]["count"] == 1
        assert groups["failed_24h"]["count"] == 1
        assert groups["claims"]["count"] == 1
        assert {node["id"] for node in groups["awaiting"]["nodes"]} == {claim["id"]}
        assert {node["id"] for node in groups["failed_24h"]["nodes"]} == {second["id"]}
    return {
        "task": task["id"],
        "second_task": second["id"],
        "result": result["id"],
        "claim": claim["id"],
        "task_artifact": task_path,
        "result_artifact": result_path,
    }


def assert_native_outcome(root, manifest, baseline, ids):
    nodes, edges = native_graph(root)
    for relative, content in baseline.items():
        assert (root / relative).read_bytes() == content
    live = {
        (edge["type"], edge["from"], edge["to"])
        for edge in edges.values()
        if not edge["retired"]
    }
    assert ("tests", manifest["prediction"], manifest["hypothesis"]) in live
    assert ("addresses", ids["task"], manifest["prediction"]) in live
    assert ("addresses", ids["second_task"], manifest["prediction"]) in live
    assert ("derived-from", ids["result"], ids["task"]) in live
    assert ("cites", ids["claim"], ids["result"]) in live
    assert ("supports", ids["claim"], manifest["hypothesis"]) in live
    assert nodes[ids["task"]]["status"] == "done"
    assert nodes[ids["second_task"]]["status"] == "failed"
    for key, outcome in (("task", "complete"), ("second_task", "fail")):
        node = nodes[ids[key]]
        assert node["lease"] is None
        assert [event["what"] for event in node["approvals"]] == ["claim", outcome]
        assert all(event["actor"] == AGENT.to_dict() for event in node["approvals"])
    claim = nodes[ids["claim"]]
    assert claim["status"] == "proposed" and claim["approvals"] == []
    failures = [node for node in nodes.values() if node["type"] == "Failure"]
    assert len(failures) == 1 and failures[0]["fields"] == FAILURE_FIELDS
    assert ("explains", failures[0]["id"], ids["second_task"]) in live
    for key in ("task", "result"):
        node = nodes[ids[key]]
        assert len(node["artifacts"]) == 1
        artifact = node["artifacts"][0]
        assert artifact["path"] == ids[f"{key}_artifact"]
        assert (root / artifact["path"]).read_bytes() == ARTIFACT
        assert artifact["sha256"] == sha256(ARTIFACT).hexdigest()
        assert artifact["bytes"] == len(ARTIFACT)
        assert artifact["added_by"] == AGENT.to_dict()
        assert (
            git(root, "show", f"HEAD:{artifact['path']}") == ARTIFACT.decode().strip()
        )
    baseline_ids = {Path(path).stem for path in baseline}
    for record in [*nodes.values(), *edges.values()]:
        if record["id"] not in baseline_ids:
            assert record["author"] == AGENT.to_dict()
    authors = git(root, "log", "--format=%an <%ae>", f"{manifest['seed_head']}..HEAD")
    assert authors.splitlines()
    assert set(authors.splitlines()) == {
        f"{AGENT.name} <{AGENT.name}@agents.researchgraph>"
    }
    assert not git(
        root,
        "status",
        "--porcelain",
        "--",
        "graph",
        "artifacts",
        ".researchgraph/profile.yaml",
    )


@pytest.fixture
def finished_session(tmp_path, preset, index_mode):
    root = repository(tmp_path / "project")
    manifest = seed(root, actor=ALICE, now=NOW)
    assert json.loads(json.dumps(manifest)) == manifest
    assert {"seed_head", "hypothesis", "prediction"} <= manifest.keys()
    assert manifest["seed_head"] == git(root, "rev-parse", "HEAD")
    nodes, _ = native_graph(root)
    for key, type_name in (("hypothesis", "Hypothesis"), ("prediction", "Prediction")):
        node = nodes[manifest[key]]
        assert node["type"] == type_name and node["status"] == "active"
        assert node["author"]["kind"] == "human"
        assert any(
            event["what"] == "approve" and event["actor"]["kind"] == "human"
            for event in node["approvals"]
        )
    baseline = graph_bytes(root)
    ids = anyio.run(
        partial(replay, root, manifest, preset=preset, index_mode=index_mode)
    )
    assert_native_outcome(root, manifest, baseline, ids)
    before = graph_bytes(root), git(root, "rev-parse", "HEAD")
    summary = check(root, manifest)
    assert isinstance(summary, dict) and summary["ok"] is True
    assert (graph_bytes(root), git(root, "rev-parse", "HEAD")) == before
    return root, manifest, ids


@pytest.mark.parametrize("preset", ["full", "essential"])
def test_ten_step_real_mcp_replay(finished_session):
    root, manifest, _ = finished_session
    # Checker must reconstruct from native sources after cache removal too.
    (root / ".researchgraph" / "index.sqlite").unlink(missing_ok=True)
    assert check(root, manifest)["ok"] is True


def rewrite(path, change):
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    change(value)
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")


@pytest.mark.parametrize("preset", ["full", "essential"])
@pytest.mark.parametrize(
    "damage",
    [
        "late_human_write",
        "bypassed_gate",
        "missing_citation",
        "wrong_citation",
        "missing_artifact",
        "corrupt_artifact",
        "missing_artifact_record",
        "wrong_derivation",
        "missing_failure_check",
    ],
)
def test_checker_rejects_adversarial_session(finished_session, damage):
    root, manifest, ids = finished_session
    nodes, edges = native_graph(root)
    if damage == "late_human_write":
        # Same graph, but a human-authored managed write after the seed boundary.
        path = root / "graph" / "nodes" / f"{ids['task']}.yaml"
        original = path.read_bytes()
        rewrite(path, lambda value: value["tags"].append("late-human"))
        git(root, "add", str(path.relative_to(root)))
        git(root, "commit", "-m", "human changed a managed node")
        path.write_bytes(original)
        git(root, "add", str(path.relative_to(root)))
        git(
            root,
            "-c",
            f"user.name={AGENT.name}",
            "-c",
            f"user.email={AGENT.name}@agents.researchgraph",
            "commit",
            "-m",
            "agent restored the original bytes",
        )
    elif damage == "bypassed_gate":
        path = root / "graph" / "nodes" / f"{ids['claim']}.yaml"
        rewrite(path, lambda value: value.update(status="active"))
    elif damage in {"missing_citation", "wrong_citation", "wrong_derivation"}:
        edge_type = "derived-from" if damage == "wrong_derivation" else "cites"
        edge = next(edge for edge in edges.values() if edge["type"] == edge_type)
        path = root / "graph" / "edges" / f"{edge['id']}.yaml"
        if damage == "missing_citation":
            path.unlink()
        else:
            target = ids["second_task"] if damage == "wrong_derivation" else ids["task"]
            rewrite(path, lambda value: value.update(to=target))
    elif damage in {"missing_artifact", "corrupt_artifact"}:
        path = root / ids["result_artifact"]
        if damage == "missing_artifact":
            path.unlink()
        else:
            path.write_bytes(b"fabricated evidence\n")
    elif damage == "missing_artifact_record":
        path = root / "graph" / "nodes" / f"{ids['result']}.yaml"
        rewrite(path, lambda value: value.update(artifacts=[]))
    else:
        failure = next(node for node in nodes.values() if node["type"] == "Failure")
        path = root / "graph" / "nodes" / f"{failure['id']}.yaml"
        rewrite(path, lambda value: value["fields"].pop("check"))
    with pytest.raises(AssertionError):
        check(root, manifest)
