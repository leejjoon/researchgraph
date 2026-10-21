"""Evidence-kit contracts preserve real-session and owner-review requirements."""

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

import pytest

from researchgraph.project import Project
from tests.helpers import ALICE, repository

ROOT = Path(__file__).resolve().parents[1]


def test_agent_protocol_requires_real_products_consent_and_independent_audit():
    text = (ROOT / "docs/evaluation/agent-session.md").read_text()
    for value in (
        "Status: awaiting product-owner review",
        "Claude Code",
        "Codex CLI",
        "consent",
        "product_version",
        "model_version",
        "configuration",
        "assistance_interventions",
        "checker_exit_code",
        "transcript",
        "2026-07-28",
        "examples/agent_session/PROMPT.md",
        "check.py",
    ):
        assert value in text
    assert "recorded_product_count: 0" in text
    assert "not a substitute" in text


def test_usability_protocol_records_each_real_timed_task_and_achieved_count():
    text = (ROOT / "docs/evaluation/usability-session.md").read_text()
    for value in (
        "Status: awaiting product-owner review",
        "outside the team",
        "consent",
        "inspect",
        "re-run",
        "expand",
        "task_elapsed_seconds",
        "participant_count_target: 3",
        "participant_count_achieved: 0",
        "600",
        "findings",
    ):
        assert value in text


def test_release_evidence_distinguishes_committed_proof_and_open_external_cells():
    text = (ROOT / "docs/evaluation/release-evidence.md").read_text()
    for value in (
        "Status: release blocked",
        "3a0477d70311081c3e3c95516ac236c32d9c7128",
        "96 passed",
        "protected",
        "447bb9a",
        "aec49a3",
        "0c0ff07",
        "pending",
        "Claude Code",
        "Codex CLI",
    ):
        assert value in text


@pytest.mark.cli
def test_exact_usability_preparation_creates_reviewable_failed_seed(tmp_path):
    root = repository(tmp_path / "viewer-project")
    manifest = tmp_path / "trusted-seed.json"
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "examples/agent_session/seed.py"),
            str(root),
            "--manifest",
            str(manifest),
        ],
        cwd=tmp_path,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    text = (ROOT / "docs/evaluation/usability-session.md").read_text()
    code = text.split("python - <<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
    code = code.replace("/tmp/researchgraph-usability-p01", str(root)).replace(
        "/absolute/operator-only/evidence/usability-p01-seed.json", str(manifest)
    )
    script = tmp_path / "prepare.py"
    script.write_text(code)
    result = subprocess.run(
        [sys.executable, str(script)],
        cwd=tmp_path,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    receipt = json.loads(result.stdout)
    with Project.open(root, read_only=True) as project:
        now = datetime.now(UTC).replace(microsecond=0)
        task = project.show(receipt["failed_task"], actor=ALICE, now=now)
        assert task["status"] == "failed" and task["lease"] is None
        assert (
            task["artifacts"][0]["sha256"]
            == sha256((root / receipt["artifact"]).read_bytes()).hexdigest()
        )
        graph = project.graph(actor=ALICE, now=now)
        (failure,) = [node for node in graph["nodes"] if node["type"] == "Failure"]
        assert failure["fields"]["step"] == "validate second sample"
        assert failure["fields"]["check"] == "sample count must be at least 2"
        assert any(
            edge["type"] == "explains" and edge["to"] == task["id"]
            for edge in graph["edges"]
        )
        assert (
            project.show(receipt["hypothesis"], actor=ALICE, now=now)["status"]
            == "active"
        )
