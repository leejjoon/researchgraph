"""Additive citation targets preserve core edges and vocabulary-free scoring."""

import string
from itertools import count
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from researchgraph.profiles import EdgeType, NodeType, Profile
from researchgraph.project import Project
from tests.helpers import ALICE, NOW, refusal, repository
from tests.test_cli import invoke, refused, succeeds
from tests.test_operations import discard_index, snapshot


@pytest.fixture(name="invoke")
def scenario_invoke(door):
    if door == "mcp":
        from tests.door_helpers import invoke_mcp

        return invoke_mcp
    return invoke


def citation_profile(target="Literature"):
    return Profile(
        "citations-" + target.lower(),
        "1.0",
        [
            NodeType("Claim", "cl", {}),
            NodeType(
                target,
                "li" if target == "Literature" else "re",
                {"properties": {"runs": {}, "held_out": {}}},
            ),
        ],
        [
            EdgeType(
                "cites",
                [("Claim", "Result"), ("Claim", "Reference"), ("Claim", target)],
            )
        ],
        [],
        [],
        [],
    )


@pytest.mark.parametrize(
    "pairs",
    [
        [("Claim", "Literature")],
        [("Claim", "Result"), ("Claim", "Reference"), ("*", "Literature")],
        [("Claim", "Result"), ("Claim", "Reference"), ("Claim", "*")],
        [("Claim", "Result"), ("Claim", "Reference"), ("Literature", "Literature")],
    ],
)
def test_invalid_citation_endpoint_extensions(pairs):
    value = citation_profile()
    value.edge_types[0].allowed = pairs
    refusal("schema", Profile.register, value)


def test_additive_exception_does_not_change_other_core_edges():
    value = citation_profile()
    value.edge_types.append(EdgeType("pins", [("Claim", "Literature")]))
    refusal("schema", Profile.register, value)


@pytest.mark.parametrize(
    "fields",
    [
        {"runs": True},
        {"runs": "1"},
        {"runs": 1.5},
        {"runs": 1.0},
        {"held_out": 0},
        {"held_out": 1},
        {"held_out": "false"},
    ],
)
@pytest.mark.parametrize(
    "door", ["api", pytest.param("cli", marks=pytest.mark.cli), "mcp"]
)
def test_invalid_scoring_fields_refused_atomically(
    tmp_path, index_mode, door, fields, monkeypatch, invoke
):
    import json

    monkeypatch.setenv("RG_TEST_CLI_INDEX_MODE", index_mode)
    root = repository(tmp_path)
    with Project.init(root, profile="test-literature", actor=ALICE, now=NOW) as project:
        before = snapshot(project)
        if door == "api":
            discard_index(project, index_mode)
            refusal(
                "schema",
                project.propose,
                "Literature",
                "bad",
                fields=fields,
                actor=ALICE,
                now=NOW,
            )
        else:
            refused(
                invoke(
                    root, "propose", "Literature", "bad", "--fields", json.dumps(fields)
                ),
                "schema",
            )
        assert snapshot(project) == before


@pytest.mark.parametrize(
    "door", ["api", pytest.param("cli", marks=pytest.mark.cli), "mcp"]
)
def test_claim_literature_without_result_reference(
    tmp_path, index_mode, door, monkeypatch, invoke
):
    import json

    monkeypatch.setenv("RG_TEST_CLI_INDEX_MODE", index_mode)
    root = repository(tmp_path)
    with Project.init(root, profile="test-literature", actor=ALICE, now=NOW) as project:

        def call(op, *args, **kwargs):
            if door == "api":
                discard_index(project, index_mode)
                return getattr(project, op)(*args, actor=ALICE, now=NOW, **kwargs)
            arguments = list(args)
            for name, value in kwargs.items():
                if name == "edges":
                    for edge in value:
                        arguments.extend(["--edge", json.dumps(edge)])
                else:
                    arguments.extend(
                        [
                            "--" + name.replace("_", "-"),
                            json.dumps(value) if isinstance(value, dict) else value,
                        ]
                    )
            return succeeds(invoke(root, op, *arguments))

        missing = call("propose", "Literature", "no scores")
        assert missing["fields"] == {}
        scored = call(
            "propose", "Literature", "scored", fields={"runs": 1, "held_out": True}
        )
        tied = call("propose", "Literature", "tie")
        claim = call(
            "propose", "Claim", "claim", edges=[{"type": "cites", "to": scored["id"]}]
        )
        call("link", "cites", claim["id"], missing["id"])
        call("link", "cites", claim["id"], tied["id"])
        targets = {missing["id"], scored["id"], tied["id"]}
        assert {n["id"] for n in call("evidence", claim["id"])["items"]} == targets
        assert [n["id"] for n in call("weakest", claim["id"])] == [
            *sorted([missing["id"], tied["id"]]),
            scored["id"],
        ]
        replacement = call("supersede", scored["id"], title="new scored input")
        # cites outgoing inheritance leaves incoming Claim citations pointing at
        # the old immutable target; stale must report the replacement head.
        assert call("stale")["items"][0]["responsible"] == [replacement["id"]]
        assert call("weakest", claim["id"])[0]["id"] == scored["id"]
        assert call("show", scored["id"])["type"] == "Literature"
        before = snapshot(project)
        if door == "api":
            refusal("edge_endpoints", call, "link", "cites", missing["id"], tied["id"])
        else:
            refused(
                invoke(root, "link", "cites", missing["id"], tied["id"]),
                "edge_endpoints",
            )
        assert snapshot(project) == before


@settings(max_examples=12, deadline=None)
@given(
    scores=st.lists(st.tuples(st.integers(0, 4), st.booleans()), min_size=2, max_size=4)
)
def test_paired_target_profiles_preserve_membership_staleness_and_rank(scores):
    observations = []
    for target in ("Literature", "Result"):
        profile = citation_profile(target)
        Profile.register(profile)
        sequence = count()

        def identifier(prefix, occupied, sequence=sequence):
            # Fresh deterministic, order-preserving identifiers per fixture;
            # compare semantic positions, never differently typed records.
            return prefix + "-aaaaaaa" + string.ascii_lowercase[next(sequence)]

        with (
            TemporaryDirectory() as directory,
            patch("researchgraph.model.new_id", identifier),
            Project.init(
                repository(Path(directory)), profile=profile.name, actor=ALICE, now=NOW
            ) as project,
        ):
            claim = project.propose("Claim", "claim", actor=ALICE, now=NOW)
            targets = []
            for runs, held_out in scores:
                node = project.propose(
                    target,
                    "evidence",
                    fields={"runs": runs, "held_out": held_out},
                    actor=ALICE,
                    now=NOW,
                )
                targets.append(node["id"])
                project.link("cites", claim["id"], node["id"], actor=ALICE, now=NOW)
            project.supersede(targets[-1], title="replacement", actor=ALICE, now=NOW)
            correspondence = {id_: index for index, id_ in enumerate(targets)}
            rank = [
                correspondence[n["id"]]
                for n in project.weakest(claim["id"], actor=ALICE, now=NOW)
            ]
            evidence = {
                correspondence[n["id"]]
                for n in project.evidence(claim["id"], actor=ALICE, now=NOW)["items"]
            }
            stale = project.stale(actor=ALICE, now=NOW)["items"]
            observations.append(
                (rank, evidence, len(stale), len(stale[0]["responsible"]))
            )
            assert rank[0] == len(scores) - 1
    assert observations[0] == observations[1]
