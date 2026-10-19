"""Git-derived intervals, archive union and record provenance."""

import json
from datetime import timedelta

import pytest

from researchgraph.model import (
    Approval,
    Artifact,
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
    nested = Project.init(tmp_path / directory, actor=ALICE, now=NOW)
    value = node()
    value.approvals = [Approval(ALICE, NOW, "activate")]
    first = nested.apply(write(value))
    value.status = Status.DONE
    value.updated = NOW + timedelta(seconds=1)
    second = nested.apply(write(value, now=value.updated))
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
                [value.id, "done", second, format_timestamp(value.updated), None, None],
            )
        ),
    ]
    assert rows(nested) == expected
    assert (
        nested.index.connection.execute("select from_commit from approvals").fetchone()[
            0
        ]
        == first
    )
    (nested.path / ".researchgraph/index.sqlite").unlink()
    reopened = Project.open(nested.path)
    assert rows(reopened) == expected
    assert (
        reopened.index.connection.execute(
            "select from_commit from approvals"
        ).fetchone()[0]
        == first
    )


def test_commit_chain_pending_replacement_and_provenance(project):
    value = node()
    first = project.apply(write(value))
    value.tags = ["metadata-only"]
    value.updated = NOW + timedelta(seconds=1)
    project.apply(write(value, now=value.updated))
    assert len(rows(project)) == 1
    value.status = Status.RUNNING
    from researchgraph.model import Lease

    value.lease = Lease(ALICE, value.updated, value.updated + timedelta(seconds=30))
    value.updated = NOW + timedelta(seconds=2)
    project.apply(write(value, now=value.updated), commit=False)
    value.status, value.lease = Status.DONE, None
    value.updated = NOW + timedelta(seconds=3)
    value.approvals = [Approval(ALICE, value.updated, "activate")]
    path = project.path / f"artifacts/{value.id}/result"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"x")
    value.artifacts = [
        Artifact(str(path.relative_to(project.path)), "a" * 64, 1, value.updated, ALICE)
    ]
    project.apply(write(value, now=value.updated), commit=False)
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
    final = project.apply(write(value, now=value.updated))
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
    value.status = Status.DONE
    value.updated = NOW + timedelta(seconds=1)
    project.apply(write(value, now=value.updated), commit=False)
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
