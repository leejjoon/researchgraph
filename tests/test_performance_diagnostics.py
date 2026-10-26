"""Diagnostic profiling must retain errors and never claim acceptance timing."""

import json

import pytest

from tests.profile_performance import main, profile_workload


def test_profile_preserves_real_workload_result(tmp_path, capsys):
    target = tmp_path / "result.txt"

    def workload():
        target.write_text("profiled bytes")
        return target.read_text()

    assert profile_workload("read-and-write", workload) == "profiled bytes"
    output = capsys.readouterr().out
    receipt = json.loads(output.splitlines()[0])
    assert receipt["completed"] is True
    assert receipt["profiling_not_acceptance"] is True
    assert "workload" in output and "function calls" in output


def test_profile_does_not_swallow_workload_failure(capsys):
    failure = RuntimeError("diagnostic workload failed")

    def workload():
        raise failure

    with pytest.raises(RuntimeError) as caught:
        profile_workload("failing-workload", workload)
    assert caught.value is failure
    receipt = json.loads(capsys.readouterr().out.splitlines()[0])
    assert receipt["completed"] is False
    assert receipt["profiling_not_acceptance"] is True


def test_profile_refuses_existing_workspace_without_changing_it(tmp_path):
    sentinel = tmp_path / "keep.txt"
    sentinel.write_text("keep these bytes")
    with pytest.raises(FileExistsError):
        main([str(tmp_path)])
    assert sentinel.read_text() == "keep these bytes"
    assert list(tmp_path.iterdir()) == [sentinel]
