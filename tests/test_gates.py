"""Conditional gates: load validation, every constructor and cross-door parity."""

import json
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from researchgraph.model import Actor, RuleViolation
from researchgraph.profiles import Gate, NodeType, Profile
from researchgraph.project import Project
from tests.helpers import ALICE, NOW, git, refusal, repository
from tests.strategies import conditional_gate_cases
from tests.test_cli import invoke, refused, succeeds
from tests.test_operations import discard_index, snapshot


@pytest.fixture(name="invoke")
def scenario_invoke(door):
    if door == "mcp":
        from tests.door_helpers import invoke_mcp

        return invoke_mcp
    return invoke


def declaration(*gates):
    return Profile(
        "gate-test",
        "1.0",
        [NodeType("Task", "ta", {"properties": {"review": {}, "mode": {}}}, True)],
        [],
        list(gates),
        ["owner", "reviewer"],
        [],
    )


@pytest.mark.parametrize(
    "when,node_type",
    [
        ({"unknown": True}, "Task"),
        ({"review.nested": True}, "Task"),
        ({"review": True}, "*"),
        ({}, "*"),
        ([], "Task"),
        (False, "Task"),
        ("review", "Task"),
        ({"review": None}, "Task"),
        ({"review": []}, "Task"),
        ({"review": {}}, "Task"),
    ],
)
@pytest.mark.parametrize("door", ["registry", "entrypoint"])
def test_condition_load_refusals(when, node_type, door, monkeypatch):
    value = declaration(Gate(node_type=node_type, when_fields=when))
    if door == "registry":
        refusal("schema", Profile.register, value)
    else:

        class Entry:
            name = value.name

            def load(self):
                return value

        monkeypatch.setattr(Profile, "_registry", {})
        monkeypatch.setattr(
            "researchgraph.profiles.base.metadata.entry_points", lambda **_: [Entry()]
        )
        refusal("schema", Profile.load, value.name)


def test_type_only_lookup_and_exact_scalar_conjunction():
    profile = Profile._validated_copy(
        declaration(
            Gate(node_type="Task", when_fields={"review": True, "mode": "fit"}),
            Gate(node_type="Task", when_fields={"review": 1}),
            Gate(node_type="Task", when_fields={}),
            Gate(),
        )
    )
    assert len(profile.gates_for("Task")) == 4
    assert profile.gates_for("Task", {}) == profile.gates[2:]
    assert profile.gates_for("Task", {"review": True, "mode": "fit"}) == [
        profile.gates[0],
        *profile.gates[2:],
    ]
    assert profile.gates_for("Task", {"review": 1}) == profile.gates[1:]
    assert profile.gates_for("Task", {"review": "1"}) == profile.gates[2:]
    refusal("unknown_type", profile.gates_for, "Missing", {})


@pytest.mark.parametrize(
    "door", ["api", pytest.param("cli", marks=pytest.mark.cli), "mcp"]
)
def test_all_constructors_defaults_roles_and_claim(
    tmp_path, index_mode, door, monkeypatch, invoke
):
    monkeypatch.setenv("RG_TEST_CLI_INDEX_MODE", index_mode)
    root = repository(tmp_path / "project")
    with Project.init(
        root, profile="test-conditional", actor=ALICE, now=NOW
    ) as project:

        def call(op, *args, role=None, agent=None, **kwargs):
            if door == "api":
                discard_index(project, index_mode)
                return getattr(project, op)(
                    *args,
                    actor=Actor("agent", agent) if agent else ALICE,
                    role=role,
                    now=NOW,
                    **kwargs,
                )
            options = list(args)
            for key, value in kwargs.items():
                options.extend(
                    [
                        "--" + key.replace("_", "-"),
                        json.dumps(value) if isinstance(value, dict) else value,
                    ]
                )
            return succeeds(invoke(root, op, *options, role=role, agent=agent))

        task = call("propose", "Task", "default gate", fields={"review": None})
        assert task["fields"]["review"] is True
        assert task["status"] == "proposed"
        queue = call("queue")["items"]
        assert [n["id"] for n in queue] == [task["id"]]
        assert queue[0]["gate"]["roles"] == ["owner", "reviewer"]
        before = snapshot(project)
        if door == "api":
            refusal("not_ready", call, "claim", task["id"])
            refusal(
                "human_only_approval",
                call,
                "approve",
                task["id"],
                agent="bot",
                role="owner",
            )
            refusal("wrong_role", call, "approve", task["id"])
        else:
            for op, agent, role, rule in [
                ("claim", None, None, "not_ready"),
                ("approve", "bot", "owner", "human_only_approval"),
                ("approve", None, None, "wrong_role"),
            ]:
                refused(invoke(root, op, task["id"], agent=agent, role=role), rule)
        assert snapshot(project) == before
        partial = call("approve", task["id"], role="owner")
        assert partial["status"] == "proposed"
        assert len(call("queue")["items"]) == 1
        if door == "api":
            discard_index(project, index_mode)
            approved = project.approve(
                task["id"], actor=Actor("human", "bob"), role="reviewer", now=NOW
            )
        else:
            # Human identity derives from Git; change it only within this fixture.
            git(root, "config", "user.name", "Bob Git")
            approved = call("approve", task["id"], role="reviewer")
        assert approved["status"] == "active"
        assert call("queue")["items"] == []
        replacement = call("supersede", task["id"], fields={"review": False})
        assert replacement["status"] == "active"
        assert replacement["approvals"] == []
        replacement2 = call(
            "supersede",
            replacement["id"],
            fields={"review": True},
            instruction="review again",
        )
        assert replacement2["status"] == "proposed"
        assert replacement2["approvals"] == []
        shown = call("show", replacement2["id"])
        assert shown["instructions"][0]["status"] == "proposed"
        instruction = call("instruct", replacement2["id"], "check input")
        assert instruction["status"] == "proposed"
        if door == "api":
            expanded = call("expand", [replacement2["id"]])
        else:
            expanded = call("expand", replacement2["id"])
        assert expanded["status"] == "proposed"
        ungated = call("propose", "Task", "no review", fields={"review": False})
        assert ungated["status"] == "active"
        call("claim", ungated["id"])
        failed = call("fail", ungated["id"], "bad", step="validation")
        assert failed["status"] == "failed"
        # Failure is separately gated from the Task it explains.
        failures = [n for n in call("queue")["items"] if n["type"] == "Failure"]
        assert len(failures) == 1 and failures[0]["fields"]["step"] == "validation"


@settings(max_examples=25, deadline=None)
@given(
    case=conditional_gate_cases(),
    actor_kind=st.sampled_from(["human", "agent"]),
    role=st.sampled_from([None, "owner", "reviewer"]),
)
def test_random_materialised_gates_and_approval_permission(case, actor_kind, role):
    value, fields = case
    allowed = value.gates[0].agents_allowed
    original = deepcopy(value)
    with TemporaryDirectory() as directory:
        repository(Path(directory))
        Profile.register(value)
        with Project.init(
            directory, profile=value.name, actor=ALICE, now=NOW
        ) as project:
            node = project.propose(
                "Task", "generated", fields=fields, actor=ALICE, now=NOW
            )
            materialised = fields.get("review")
            if materialised is None:
                materialised = value.node_types[0].schema["properties"]["review"][
                    "default"
                ]
            matches = materialised is value.gates[0].when_fields["review"]
            assert node["status"] == ("proposed" if matches else "active")
            before = snapshot(project)
            actor = Actor(actor_kind, "approver")
            try:
                result = project.approve(node["id"], actor=actor, role=role, now=NOW)
            except RuleViolation as exc:
                assert snapshot(project) == before
                assert (
                    not matches
                    or role != "owner"
                    or (actor_kind == "agent" and not allowed)
                )
                assert exc.rule in {"not_proposed", "wrong_role", "human_only_approval"}
            else:
                assert (
                    matches and role == "owner" and (actor_kind == "human" or allowed)
                )
                assert result["status"] == "active"
            project.index.close()
            (project.path / ".researchgraph/index.sqlite").unlink(missing_ok=True)
            current = project.show(node["id"], actor=ALICE, now=NOW)
            assert current["fields"] == node["fields"]
    assert value == original


@pytest.mark.parametrize("review", [False, True])
@pytest.mark.parametrize("door", ["api", "mcp"])
def test_generated_default_matching_and_nonmatching(
    tmp_path, index_mode, review, door, invoke, monkeypatch
):
    monkeypatch.setenv("RG_TEST_CLI_INDEX_MODE", index_mode)
    value = Profile.load("test-conditional")
    value.name = "generated-default-gates"
    for type_ in value.node_types:
        if type_.name == "Instruction":
            type_.schema["properties"]["review"]["default"] = review
        elif type_.name == "Failure":
            type_.schema["properties"]["step"]["default"] = (
                "validation" if review else "other"
            )
    Profile.register(value)
    with Project.init(
        repository(tmp_path), profile=value.name, actor=ALICE, now=NOW
    ) as project:
        task = project.propose(
            "Task", "input", fields={"review": False}, actor=ALICE, now=NOW
        )
        discard_index(project, index_mode)
        instruction = (
            project.instruct(task["id"], "do this", actor=ALICE, now=NOW)
            if door == "api"
            else succeeds(invoke(project.path, "instruct", task["id"], "do this"))
        )
        assert instruction["status"] == ("proposed" if review else "active")
        if door == "api":
            project.claim(task["id"], actor=ALICE, now=NOW)
            project.fail(task["id"], "failed", actor=ALICE, now=NOW)
            failures = project.where(type="Failure", actor=ALICE, now=NOW)
        else:
            succeeds(invoke(project.path, "claim", task["id"]))
            succeeds(invoke(project.path, "fail", task["id"], "failed"))
            failures = succeeds(invoke(project.path, "where", "--type", "Failure"))
        assert failures[0]["status"] == ("proposed" if review else "active")


@pytest.mark.parametrize("door", ["api", "mcp"])
def test_nonmatching_gates_never_grant_or_remove_agent_authority(
    tmp_path, door, invoke
):
    value = declaration(
        Gate(node_type="Task", roles=["owner"], when_fields={"review": True}),
        Gate(
            node_type="Task",
            roles=["owner"],
            agents_allowed=True,
            when_fields={"review": False},
        ),
    )
    Profile.register(value)
    with Project.init(
        repository(tmp_path), profile=value.name, actor=ALICE, now=NOW
    ) as project:
        human_only = project.propose(
            "Task", "human", fields={"review": True}, actor=ALICE, now=NOW
        )
        agent_ok = project.propose(
            "Task", "agent", fields={"review": False}, actor=ALICE, now=NOW
        )
        bot = Actor("agent", "reviewer")
        if door == "api":
            refusal(
                "human_only_approval",
                project.approve,
                human_only["id"],
                actor=bot,
                role="owner",
                now=NOW,
            )
            approved = project.approve(agent_ok["id"], actor=bot, role="owner", now=NOW)
        else:
            refused(
                invoke(
                    project.path,
                    "approve",
                    human_only["id"],
                    agent="reviewer",
                    role="owner",
                ),
                "human_only_approval",
            )
            approved = succeeds(
                invoke(
                    project.path,
                    "approve",
                    agent_ok["id"],
                    agent="reviewer",
                    role="owner",
                )
            )
        assert approved["status"] == "active"
