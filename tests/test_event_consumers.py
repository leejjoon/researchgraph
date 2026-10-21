"""Recovery by one bounded-stream consumer must not evict another consumer."""

from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from hypothesis import example, given, settings
from hypothesis import strategies as st

from researchgraph.events import ChangeSource
from researchgraph.project import Project
from tests.helpers import ALICE, NOW, repository
from tests.test_operations import discard_index


@settings(max_examples=12, deadline=None)
@example(index_mode="kept", recoveries=["expired"])
@example(index_mode="deleted", recoveries=["expired"])
@given(
    index_mode=st.sampled_from(["kept", "deleted"]),
    recoveries=st.lists(
        st.sampled_from(["expired", "malformed", "future", "other-generation"]),
        min_size=1,
        max_size=20,
    ),
)
def test_independent_recovery_never_advances_or_evicts_current_cursor(
    index_mode, recoveries
):
    with (
        TemporaryDirectory() as directory,
        Project.init(
            repository(Path(directory)), "tasks", actor=ALICE, now=NOW
        ) as project,
    ):
        source = ChangeSource(project, retention=1)
        expired = source.snapshot(actor=ALICE, now=NOW)["cursor"]
        project.propose("Task", "New observation", actor=ALICE, now=NOW)
        current = source.snapshot(actor=ALICE, now=NOW)
        retained = deepcopy(list(source._events))
        for recovery in recoveries:
            discard_index(project, index_mode)
            cursor = {
                "expired": expired,
                "malformed": "not-a-cursor",
                "future": f"v1:{source._generation}:999999",
                "other-generation": "v1:" + "0" * 32 + ":1",
            }[recovery]
            result = source.changes(
                actor=ALICE, now=NOW + timedelta(seconds=30), since=cursor
            )
            assert result["cursor"] == current["cursor"]
            assert len(result["events"]) == 1
            reset = result["events"][0]
            assert reset == dict(retained[-1], reset=True)
            assert list(source._events) == retained
            assert (
                source.changes(
                    actor=ALICE,
                    now=NOW + timedelta(seconds=30),
                    since=current["cursor"],
                )["events"]
                == []
            )
            reset["counts"]["nodes"] = 999
            assert list(source._events) == retained
        source.close()
