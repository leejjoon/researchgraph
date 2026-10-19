"""Real git repositories and graph records shared by storage-layer tests."""

import subprocess
from datetime import UTC, datetime

import pytest

from researchgraph.model import Actor, Node, RuleViolation, Status, Write

NOW = datetime(2026, 10, 20, tzinfo=UTC)
ALICE = Actor("human", "alice", "alice@example.test")


def git(path, *args):
    return subprocess.run(
        ["git", "-C", str(path), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def repository(path):
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init")
    git(path, "config", "user.name", "Alice Git")
    git(path, "config", "user.email", "alice@git.test")
    git(path, "config", "commit.gpgsign", "false")
    return path


def node(id="ta-aaaaaaaa", **kwargs):
    return Node(id, "Task", "A task", "", ALICE, NOW, status=Status.ACTIVE, **kwargs)


def write(*nodes, edges=(), now=NOW, op="propose", args=None):
    return Write(op, ALICE, None, now, args or {}, list(nodes), list(edges))


def refusal(rule, fn, *args, **kwargs):
    with pytest.raises(RuleViolation) as error:
        fn(*args, **kwargs)
    assert error.value.rule == rule
    return error.value
