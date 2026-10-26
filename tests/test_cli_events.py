"""Real installed stream lifecycle and twenty external claim latency samples."""

import json
import os
import queue
import signal
import subprocess
import sysconfig
import threading
from pathlib import Path
from time import perf_counter

import pytest

from researchgraph.model import format_timestamp
from researchgraph.project import Project
from researchgraph.store.transaction import write_lock
from tests.helpers import ALICE, NOW, repository
from tests.test_cli import invoke, prepare_console, run_console, succeeds

pytestmark = pytest.mark.cli


def follow(root, *, poll="0.05"):
    arguments = [
        "--project",
        str(root),
        "--json",
        "--now",
        format_timestamp(NOW),
        "--read-only",
        "events",
        "--follow",
        "--poll-interval",
        poll,
    ]
    prepare_console(arguments)
    process = subprocess.Popen(
        [str(Path(sysconfig.get_path("scripts")) / "rgraph"), *arguments],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        env={key: value for key, value in os.environ.items() if key != "PYTHONPATH"},
    )
    messages = queue.Queue()

    def consume():
        try:
            for line in process.stdout:
                messages.put(json.loads(line))
        finally:
            messages.put(None)

    thread = threading.Thread(target=consume, daemon=True)
    thread.start()
    return process, messages, thread


def next_event(messages, *, timeout=5):
    event = messages.get(timeout=timeout)
    assert event is not None, "event stream unexpectedly ended"
    return event


def close_stream(process, thread):
    if process.stdin is not None and not process.stdin.closed:
        process.stdin.close()
    try:
        code = process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        pytest.fail("stream did not release its owner after disconnect")
    thread.join(timeout=5)
    assert not thread.is_alive()
    errors = process.stderr.read()
    process.stdout.close()
    process.stderr.close()
    assert code == 0 and not errors, (code, errors)


def test_one_shot_human_json_and_restart_cursor_cases(
    tmp_path, index_mode, monkeypatch
):
    monkeypatch.setenv("RG_TEST_CLI_INDEX_MODE", index_mode)
    with Project.init(repository(tmp_path), "tasks", actor=ALICE, now=NOW):
        pass
    first = invoke(tmp_path, "events")
    assert first.exit_code == 0 and first.stderr == ""
    events = [json.loads(line) for line in first.stdout.splitlines()]
    assert len(events) == 1 and events[0]["kind"] == "index"
    for cursor in ("broken", events[0]["cursor"]):
        reset = invoke(tmp_path, "events", "--since", cursor)
        assert reset.exit_code == 0 and reset.stderr == ""
        replay = [json.loads(line) for line in reset.stdout.splitlines()]
        assert len(replay) == 1 and replay[0]["reset"] is True
        assert replay[0]["generation"] != events[0]["generation"]
    human = run_console(["--project", str(tmp_path), "events"])
    assert human.exit_code == 0 and human.stderr == ""
    assert len(human.stdout.splitlines()) == 1 and "index" in human.stdout
    assert not human.stdout.startswith("{")


@pytest.mark.parametrize("shutdown", ["stdin", "sigint"])
def test_follow_exits_zero_and_releases_owner(
    tmp_path, index_mode, monkeypatch, shutdown
):
    monkeypatch.setenv("RG_TEST_CLI_INDEX_MODE", index_mode)
    with Project.init(repository(tmp_path), "tasks", actor=ALICE, now=NOW):
        pass
    process, messages, thread = follow(tmp_path)
    try:
        assert next_event(messages)["kind"] == "index"
        if shutdown == "sigint":
            process.send_signal(signal.SIGINT)
    finally:
        close_stream(process, thread)
    with Project.open(tmp_path) as project:
        assert project.propose("Task", "owner released", actor=ALICE, now=NOW)


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf"])
def test_invalid_poll_intervals_fail_without_output(tmp_path, value):
    result = invoke(tmp_path, "events", "--poll-interval", value)
    assert result.exit_code == 2 and result.stdout == ""
    assert "poll-interval" in result.stderr


def test_events_read_completes_while_another_owner_holds_writer_lock(
    tmp_path, index_mode, monkeypatch
):
    monkeypatch.setenv("RG_TEST_CLI_INDEX_MODE", index_mode)
    with Project.init(repository(tmp_path), "tasks", actor=ALICE, now=NOW):
        pass
    arguments = ["--project", str(tmp_path), "--json", "--read-only", "events"]
    prepare_console(arguments)
    with write_lock(tmp_path):
        result = subprocess.run(
            [str(Path(sysconfig.get_path("scripts")) / "rgraph"), *arguments],
            env={
                key: value for key, value in os.environ.items() if key != "PYTHONPATH"
            },
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    assert result.returncode == 0 and result.stderr == ""
    assert json.loads(result.stdout)["kind"] == "index"


@pytest.mark.performance
def test_twenty_actual_claims_under_fifty_running_load(
    tmp_path, index_mode, monkeypatch, record_property
):
    monkeypatch.setenv("RG_TEST_CLI_INDEX_MODE", index_mode)
    with Project.init(repository(tmp_path), "tasks", actor=ALICE, now=NOW) as project:
        for i in range(50):
            task = project.propose("Task", f"background {i}", actor=ALICE, now=NOW)
            project.claim(task["id"], actor=ALICE, now=NOW)
        targets = [
            project.propose("Task", f"claim target {i}", actor=ALICE, now=NOW)["id"]
            for i in range(20)
        ]
        assert len(project.where(status="running", actor=ALICE, now=NOW)) == 50
    # Required one-second poll; each write is a real installed claim, not tag,
    # synthetic notifications, an in-process callback or a replay template.
    process, messages, thread = follow(tmp_path, poll="1")
    samples = []
    try:
        assert next_event(messages)["counts"]["nodes"] == 70
        for identifier in targets:
            path = tmp_path / f"graph/nodes/{identifier}.yaml"
            before = path.stat().st_size
            started = perf_counter()
            claimed = succeeds(
                invoke(tmp_path, "claim", identifier, agent="claim-writer")
            )
            while True:
                event = next_event(messages, timeout=3)
                if event["kind"] == "node" and event["id"] == identifier:
                    break
            latency = perf_counter() - started
            assert event["node"] == claimed and claimed["status"] == "running"
            assert event["previous_status"] == "active"
            assert claimed["approvals"][-1]["what"] == "claim"
            sample = {
                "id": identifier,
                "latency_seconds": latency,
                "before_bytes": before,
                "after_bytes": path.stat().st_size,
            }
            samples.append(sample)
            record_property(f"claim_{len(samples):02}", json.dumps(sample))
            assert sample["after_bytes"] > before
            assert latency < 3.0, sample
    finally:
        close_stream(process, thread)
    report = {
        "initial_running": 50,
        "claims": 20,
        "poll_seconds": 1,
        "index_mode": index_mode,
        "samples": samples,
    }
    print(report)
    assert len(samples) == 20
