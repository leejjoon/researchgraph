"""Git-derived intervals, archive union and record provenance."""

import json
from datetime import timedelta
from hashlib import sha256

import pytest

from researchgraph.model import (
    Approval,
    Artifact,
    Lease,
    RuleViolation,
    Status,
    format_timestamp,
)
from researchgraph.project import Project
from researchgraph.store.history import INTERVAL_KEYS, intervals_as_dicts
from tests.helpers import ALICE, NOW, node, repository, write


def rows(project):
    project.get_nodes()
    return intervals_as_dicts(project.index.connection)


@pytest.mark.parametrize(
    "directory", ["projects/example", 'projects/연구-\n"/graphstore']
)
def test_nested_project_committed_history_and_provenance(tmp_path, directory):
    repository(tmp_path)
    with Project.init(tmp_path / directory, actor=ALICE, now=NOW) as nested:
        value = node()
        first = nested.apply(write(value))
        value.budget = {"tokens": 10}
        value.approvals.append(Approval(ALICE, NOW, "budget", note="tokens=10"))
        budget_commit = nested.apply(
            write(value, op="budget", args={"node": value.id, "tokens": 10})
        )
        value.status = Status.RUNNING
        value.updated = NOW + timedelta(seconds=1)
        value.lease = Lease(ALICE, value.updated, value.updated + timedelta(seconds=30))
        value.approvals.append(Approval(ALICE, value.updated, "claim"))
        nested.apply(
            write(
                value,
                op="claim",
                args={"node": value.id, "lease_seconds": 30},
                now=value.updated,
            ),
            commit=False,
        )
        value.status, value.lease = Status.DONE, None
        value.updated = NOW + timedelta(seconds=2)
        value.approvals.append(
            Approval(ALICE, value.updated, "complete", note="nested")
        )
        second = nested.apply(
            write(
                value,
                op="complete",
                args={"node": value.id, "summary": "nested"},
                now=value.updated,
            )
        )
        expected = [
            dict(
                zip(
                    INTERVAL_KEYS,
                    [
                        value.id,
                        "active",
                        first,
                        format_timestamp(NOW),
                        second,
                        format_timestamp(value.updated),
                    ],
                )
            ),
            dict(
                zip(
                    INTERVAL_KEYS,
                    [
                        value.id,
                        "done",
                        second,
                        format_timestamp(value.updated),
                        None,
                        None,
                    ],
                )
            ),
        ]
        assert rows(nested) == expected
        assert (
            nested.index.connection.execute(
                "select from_commit from approvals where ordinal=0"
            ).fetchone()[0]
            == budget_commit
        )
        (nested.path / ".researchgraph/index.sqlite").unlink()
        with Project.open(nested.path) as reopened:
            assert rows(reopened) == expected
            assert (
                reopened.index.connection.execute(
                    "select from_commit from approvals where ordinal=0"
                ).fetchone()[0]
                == budget_commit
            )


def test_commit_chain_pending_replacement_and_provenance(project):
    value = node()
    first = project.apply(write(value))
    value.tags = ["metadata-only"]
    value.updated = NOW + timedelta(seconds=1)
    project.apply(write(value, op="tag", args={"node": value.id}, now=value.updated))
    assert len(rows(project)) == 1
    value.status = Status.RUNNING
    value.updated = NOW + timedelta(seconds=2)
    value.lease = Lease(ALICE, value.updated, value.updated + timedelta(seconds=30))
    value.approvals.append(Approval(ALICE, value.updated, "claim"))
    project.apply(
        write(
            value,
            op="claim",
            args={"node": value.id, "lease_seconds": 30},
            now=value.updated,
        ),
        commit=False,
    )
    value.status, value.lease = Status.DONE, None
    value.updated = NOW + timedelta(seconds=3)
    value.approvals.append(Approval(ALICE, value.updated, "complete", note="finished"))
    path = project.path / f"artifacts/{value.id}/result"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"x")
    value.artifacts = [
        Artifact(
            str(path.relative_to(project.path)),
            sha256(b"x").hexdigest(),
            1,
            value.updated,
            ALICE,
        )
    ]
    project.apply(
        write(
            value,
            op="complete",
            args={
                "node": value.id,
                "summary": "finished",
                "artifacts": [str(path.relative_to(project.path))],
            },
            now=value.updated,
        ),
        commit=False,
    )
    pending = rows(project)
    assert [r["status"] for r in pending] == ["active", "done"]
    assert pending[0]["from_commit"] == first
    assert pending[0]["to_commit"] is None
    assert pending[0]["to_time"] == format_timestamp(value.updated)
    assert (
        project.index.connection.execute(
            "select from_commit from approvals"
        ).fetchone()[0]
        is None
    )
    value.tags = ["committed", "metadata-only"]
    final = project.apply(
        write(value, op="tag", args={"node": value.id}, now=value.updated)
    )
    committed = rows(project)
    assert committed[0]["to_commit"] == final
    assert committed[1]["from_commit"] == final
    assert (
        project.index.connection.execute(
            "select from_commit from approvals"
        ).fetchone()[0]
        == final
    )
    assert (
        project.index.connection.execute(
            "select from_commit from artifacts"
        ).fetchone()[0]
        == final
    )
    (project.path / ".researchgraph/index.sqlite").unlink()
    assert rows(project) == committed


def test_archive_union_import_suppression_and_close(project):
    value = node()
    old = dict(
        zip(
            INTERVAL_KEYS,
            [value.id, "active", "old-commit", format_timestamp(NOW), None, None],
        )
    )
    archive = project.path / ".researchgraph/history.jsonl"
    archive.write_text(json.dumps(old) + "\n" + json.dumps(old) + "\n")
    project.apply(write(value, op="import"))
    assert rows(project) == [old]
    value.status = Status.RUNNING
    value.lease = Lease(ALICE, NOW, NOW + timedelta(seconds=30))
    value.approvals.append(Approval(ALICE, NOW, "claim"))
    project.apply(
        write(value, op="claim", args={"node": value.id, "lease_seconds": 30}),
        commit=False,
    )
    value.status, value.lease = Status.DONE, None
    value.updated = NOW + timedelta(seconds=1)
    value.approvals.append(Approval(ALICE, value.updated, "complete", note="restored"))
    project.apply(
        write(
            value,
            op="complete",
            args={"node": value.id, "summary": "restored"},
            now=value.updated,
        ),
        commit=False,
    )
    result = rows(project)
    assert len(result) == 2
    assert result[0]["from_commit"] == "old-commit"
    assert result[0]["to_time"] == format_timestamp(value.updated)
    assert sum(r["to_time"] is None for r in result) == 1


@pytest.mark.parametrize("text", ["not-json\n", "{}\n", '{"node_id":"x"}\n'])
def test_bad_history_is_not_treated_as_bad_cache(project, text):
    (project.path / ".researchgraph/history.jsonl").write_text(text)
    with pytest.raises(RuleViolation, match="history"):
        project.get_nodes()


@pytest.mark.parametrize("directory", ["plain", 'projects/연구-\n"/edges'])
def test_edge_introduction_provenance_survives_retirement(tmp_path, directory):
    from researchgraph.model import Edge

    repository(tmp_path)
    with Project.init(
        tmp_path / directory, profile="tasks", actor=ALICE, now=NOW
    ) as project:
        first, second = node(), node("ta-bbbbbbbb")
        project.apply(write(first, second))
        edge = Edge("ed-aaaaaaaa", "relates-to", first.id, second.id, ALICE, NOW)
        project.apply(write(edges=[edge], op="link"), commit=False)
        assert (
            project.index.connection.execute(
                "SELECT from_commit FROM edges"
            ).fetchone()[0]
            is None
        )
        introduced = project.apply(write(edges=[edge], op="link"))
        first.status = Status.RETIRED
        first.updated = NOW + timedelta(seconds=1)
        first.approvals.append(
            Approval(ALICE, first.updated, "retire", note="obsolete")
        )
        edge.retired, edge.retired_at = True, first.updated
        retired = project.apply(
            write(
                first,
                edges=[edge],
                op="retire",
                now=first.updated,
                args={"node": first.id, "reason": "obsolete"},
            )
        )
        assert introduced != retired
        assert (
            project.index.connection.execute(
                "SELECT from_commit FROM edges"
            ).fetchone()[0]
            == introduced
        )
        cache = project.path / ".researchgraph/index.sqlite"
        for remove_cache in (False, True):
            if remove_cache:
                cache.unlink()
            with Project.open(project.path) as reopened:
                assert reopened.get_edges()[0].retired
                assert (
                    reopened.index.connection.execute(
                        "SELECT from_commit FROM edges"
                    ).fetchone()[0]
                    == introduced
                )
