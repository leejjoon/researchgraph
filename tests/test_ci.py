"""Executable CI contracts; collection probes never execute the selected test."""

import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
import yaml

from researchgraph.project import Project
from tests.helpers import ALICE, NOW, git, repository
from tests.test_cli import invoke, succeeds

ROOT = Path(__file__).resolve().parents[1]
TARGET = "tests/test_ci.py::test_adapter_cache_parity"
PINS = {
    "actions/checkout": "3d3c42e5aac5ba805825da76410c181273ba90b1",
    "actions/setup-python": "5fda3b95a4ea91299a34e894583c3862153e4b97",
    "astral-sh/setup-uv": "1c37ad07a6a961277cf70c0d37d6f313000f5884",
    "actions/upload-artifact": "cf430e030ddbb5b0abf93d22962f4752f3646cd9",
}


def test_notebook_tooling_is_pinned_only_in_test_extra():
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text())
    expected = {"nbclient==0.11.0", "nbformat==5.11.1", "ipykernel==7.4.0"}
    assert expected <= set(metadata["project"]["optional-dependencies"]["test"])
    assert not any(
        item.split("=")[0] in {"nbclient", "nbformat", "ipykernel"}
        for item in metadata["project"]["dependencies"]
    )


def test_mcp_is_optional_and_primary_jobs_admit_its_required_tests():
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert metadata["project"]["optional-dependencies"]["mcp"] == ["mcp>=2.3,<3"]
    assert not any(
        item.split(">=")[0] in {"mcp", "anyio", "starlette", "uvicorn"}
        for item in metadata["project"]["dependencies"]
    )
    workflow = yaml.load(
        (ROOT / ".github/workflows/ci.yml").read_text(), Loader=yaml.BaseLoader
    )
    scripts = {
        name: "\n".join(step.get("run", "") for step in job["steps"])
        for name, job in workflow["jobs"].items()
    }
    for name in ("acceptance", "scale"):
        assert "-e '.[test,mcp,viewer]'" in scripts[name]
    compatibility = scripts["compatibility"]
    assert "-e '.[test]'" in compatibility and "mcp]" not in compatibility
    assert "--protocol-fixtures core" in compatibility
    assert "tests/test_mcp_packaging.py" in scripts["scale"]


def test_viewer_is_optional_and_primary_jobs_admit_required_http_tests():
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text())
    extras = metadata["project"]["optional-dependencies"]
    assert extras["viewer"] == ["starlette>=1.9,<2", "uvicorn>=0.54,<1"]
    assert "httpx>=0.28.1,<1" in extras["test"]
    assert "httpx2>=2.16,<3" in extras["test"]
    assert not any(
        re.split(r"[<>=!~\[]", item)[0]
        in {"starlette", "uvicorn", "httpx", "httpx2", "mcp"}
        for item in metadata["project"]["dependencies"]
    )
    workflow = yaml.load(
        (ROOT / ".github/workflows/ci.yml").read_text(), Loader=yaml.BaseLoader
    )
    scripts = {
        name: "\n".join(step.get("run", "") for step in job["steps"])
        for name, job in workflow["jobs"].items()
    }
    for name in ("acceptance", "scale"):
        assert "-e '.[test,mcp,viewer]'" in scripts[name]
        assert "not http" not in scripts[name]
    assert re.search(r"python -m pytest\s+tests\s", scripts["acceptance"])
    console_commands = [
        line
        for line in scripts["acceptance"].splitlines()
        if "python -m pytest tests -m cli " in line
    ]
    assert len(console_commands) == 1
    assert "--protocol-fixtures core --query-door cli" in console_commands[0]
    for target in (
        "tests/test_serve.py",
        "tests/test_serve_artifacts.py",
        "tests/test_serve_events.py",
        "tests/test_serve_packaging.py",
    ):
        assert target in scripts["scale"]
    compatibility = scripts["compatibility"]
    assert "-e '.[test]'" in compatibility
    assert "viewer]" not in compatibility and "mcp]" not in compatibility
    assert "--protocol-fixtures core" in compatibility
    assert "--query-door cli" in compatibility
    assert "tests/test_serve" not in compatibility


def test_notebook_ci_execution_targets_are_separate_from_compatibility():
    workflow = yaml.load(
        (ROOT / ".github/workflows/ci.yml").read_text(), Loader=yaml.BaseLoader
    )
    selections = {}
    for name, job in workflow["jobs"].items():
        commands = []
        for step in job["steps"]:
            script = step.get("run", "").replace("\\\n", " ")
            for line in script.splitlines():
                if "python -m pytest " in line:
                    commands.append(re.findall(r"\btests(?:/[\w./:]+)?\b", line))
                    assert "if" not in step
        selections[name] = commands
    assert any("tests" in command for command in selections["acceptance"])
    assert any("tests/test_notebook.py" in command for command in selections["scale"])
    compatibility = {
        item for command in selections["compatibility"] for item in command
    }
    assert "tests/test_docs.py" in compatibility
    assert "tests/test_notebook.py" not in compatibility
    assert "tests" not in compatibility


@pytest.mark.parametrize(
    ("selection", "expected_modes"),
    [
        (None, {"kept", "deleted"}),
        ("both", {"kept", "deleted"}),
        ("kept", {"kept"}),
        ("deleted", {"deleted"}),
    ],
)
def test_index_mode_collects_only_selected_cases(selection, expected_modes):
    command = [sys.executable, "-m", "pytest", "--collect-only", "-q", TARGET]
    if selection is not None:
        command += ["--index-mode", selection]
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    result = subprocess.run(
        command,
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    nodeids = [
        line for line in result.stdout.splitlines() if line.startswith(TARGET + "[")
    ]
    expected = {
        (adapter, mode) for adapter in ("api", "cli") for mode in expected_modes
    }
    observed = []
    for nodeid in nodeids:
        parameters = nodeid.removeprefix(TARGET + "[").removesuffix("]").split("-")
        assert len(parameters) == 2, nodeid
        adapter = next(value for value in parameters if value in {"api", "cli"})
        mode = next(value for value in parameters if value in {"kept", "deleted"})
        observed.append((adapter, mode))
    assert len(observed) == len(expected), result.stdout
    assert set(observed) == expected


@pytest.mark.parametrize("adapter", ["api", pytest.param("cli", marks=pytest.mark.cli)])
def test_adapter_cache_parity(
    tmp_path, adapter, index_mode, cli_cache_policy, record_property
):
    record_property("adapter", adapter)
    record_property("index_mode", index_mode)
    root = repository(tmp_path / "repository")
    with Project.init(root, profile="tasks", actor=ALICE, now=NOW) as project:
        assert project.ready(actor=ALICE, now=NOW)["items"] == []
    index = root / ".researchgraph" / "index.sqlite"
    assert index.is_file()
    assert index.read_bytes().startswith(b"SQLite format 3\x00")
    before = {
        path.name: path.read_bytes() for path in (root / "graph/nodes").glob("*.yaml")
    }

    # The named fixture controls the actual When step, not a global refresh hook.
    if index_mode == "deleted":
        index.unlink()
        assert not index.exists()
    else:
        assert index.exists()
    if adapter == "api":
        with Project.open(root) as project:
            project.propose("Task", "CI adapter parity", actor=ALICE, now=NOW)
            rows = project.ready(actor=ALICE, now=NOW)["items"]
            assert len(rows) == 1
            node = project.get_node(rows[0]["id"]).to_dict()
    else:
        node = succeeds(invoke(root, "propose", "Task", "CI adapter parity", now=NOW))

    assert index.is_file()
    assert index.read_bytes().startswith(b"SQLite format 3\x00")
    after = {
        path.name: path.read_bytes() for path in (root / "graph/nodes").glob("*.yaml")
    }
    assert before == {}
    assert set(after) == {node["id"] + ".yaml"}
    assert yaml.safe_load(after[node["id"] + ".yaml"]) == node
    assert git(root, "status", "--porcelain") == ""
    with Project.open(root) as project:
        api_node = project.get_node(node["id"]).to_dict()
        api_ready = project.ready(actor=ALICE, now=NOW)
    shown = succeeds(invoke(root, "show", node["id"], now=NOW))
    assert {key: shown[key] for key in api_node} == api_node == node
    assert succeeds(invoke(root, "ready", now=NOW)) == api_ready
    assert [row["id"] for row in api_ready["items"]] == [node["id"]]
    assert {
        path.name: path.read_bytes() for path in (root / "graph/nodes").glob("*.yaml")
    } == after


def test_workflow_contract():
    # BaseLoader deliberately preserves the YAML 1.1 boolean-looking `on` key.
    workflow = yaml.load(
        (ROOT / ".github/workflows/ci.yml").read_text(), Loader=yaml.BaseLoader
    )
    assert {"push", "pull_request"} <= set(workflow["on"])
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["env"]["PYTHONHASHSEED"] == "0"
    jobs = workflow["jobs"]
    primary = jobs["acceptance"]
    assert primary["runs-on"] == "ubuntu-24.04"
    assert primary["strategy"]["matrix"]["index-mode"] == ["kept", "deleted"]
    assert primary["strategy"]["fail-fast"] == "false"
    compat = jobs["compatibility"]["strategy"]["matrix"]["python"]
    assert set(compat) == {"3.11", "pypy3.11"}
    assert set(jobs) == {"acceptance", "scale", "compatibility"}

    scripts = {}
    for name, job in jobs.items():
        assert job["runs-on"] == "ubuntu-24.04"
        assert "continue-on-error" not in job
        assert "permissions" not in job
        assert "${{ runner.temp }}" in job["env"]["TMPDIR"]
        steps = job["steps"]
        scripts[name] = "\n".join(step.get("run", "") for step in steps)
        actions = {step["uses"].split("@")[0]: step for step in steps if "uses" in step}
        assert set(actions) == set(PINS)
        for action, pin in PINS.items():
            assert actions[action]["uses"] == action + "@" + pin
        assert actions["actions/checkout"]["with"]["persist-credentials"] == "false"
        assert actions["astral-sh/setup-uv"]["with"]["version"] == "0.11.8"
        if name != "compatibility":
            assert actions["actions/setup-python"]["with"]["python-version"] == "3.14.4"
        assert steps.index(actions["actions/setup-python"]) < steps.index(
            actions["astral-sh/setup-uv"]
        )
        upload = actions["actions/upload-artifact"]
        assert upload["if"] == "always()"
        assert upload["with"]["include-hidden-files"] == "true"
        assert upload["with"]["if-no-files-found"] == "error"
        assert "${{ runner.temp }}" in upload["with"]["path"]
        assert name in upload["with"]["name"]
        if name == "acceptance":
            assert "matrix.index-mode" in upload["with"]["name"]
        if name == "compatibility":
            assert "matrix.python" in upload["with"]["name"]
        script = scripts[name]
        assert "uv venv .venv" in script
        expected_extra = ".[test]" if name == "compatibility" else ".[test,mcp,viewer]"
        assert f"-e '{expected_extra}'" in script
        assert "-e tests/testprofiles" in script
        assert "rpds-py==0.25.1" in script
        assert "platform_python_implementation" in script and "PyPy" in script
        assert "python_version" in script and "3.11" in script
        assert "--hypothesis-seed=20261020" in script
        assert "--junitxml" in script and "--basetemp" in script
        assert "junit_family=legacy" in script
        assert "${{ runner.temp }}" in script
        assert "--durations" in script
        for step in steps:
            assert "continue-on-error" not in step
            run = step.get("run", "")
            assert not re.search(r"\|\|\s*(?:true|:|echo)|set\s+\+e|;\s*exit\s+0", run)
            if "python -m pytest" in run:
                assert "if" not in step
                assert "set -euo pipefail" in run
                assert re.search(r"--junitxml=['\"]\$\{\{ runner.temp \}\}/", run)
                assert re.search(r"--basetemp=['\"]\$\{\{ runner.temp \}\}/", run)
                assert "--hypothesis-seed=20261020" in run
                assert "junit_family=legacy" in run
                assert "--durations" in run
        # Runtime evidence must identify the executable, dependency closure, Git,
        # SQLite and runner as well as the commit under test.
        for evidence in (
            "sys.version",
            "sqlite3.sqlite_version",
            "SOABI",
            "rev-parse",
            "HEAD",
            "sysconfig.get_path",
            "minimal_runner.py",
            "uv pip freeze",
        ):
            assert evidence in script, (name, evidence)

    acceptance = scripts["acceptance"]
    assert "ruff check ." in acceptance
    assert "ruff format --check src tests examples setup.py" in acceptance
    assert re.search(r"python -m pytest\s+tests\s", acceptance)
    assert "--index-mode" in acceptance and "matrix.index-mode" in acceptance
    assert "-m cli" in acceptance
    scale = scripts["scale"]
    assert "tests/test_performance.py::test_public_temporal_scrub_budget" in scale
    assert (
        "tests/test_performance.py::test_fresh_twenty_thousand_node_rebuild_budget"
        in scale
    )
    assert "tests/test_packaging.py" in scale
    for target in (
        "tests/test_cli_version.py::test_installed_cli_version",
        "tests/test_runner.py",
        "tests/test_docs.py",
    ):
        assert target in scale
    for script in (scripts["compatibility"],):
        assert "tests/test_performance.py" not in script
        assert not re.search(r"python -m pytest\s+tests(?:\s|$)", script)
        assert " -k " not in script
        for target in (
            "tests/test_io.py",
            "tests/test_model.py",
            "tests/test_profiles.py",
            "tests/test_native.py",
            "tests/test_source_scanner.py",
            "tests/test_mapped_hashing.py",
            "tests/test_hashing.py",
            "tests/test_temporal.py",
            "tests/test_validation.py",
            "tests/test_profile_pins.py",
            "tests/test_queries.py",
            "tests/test_cli_queries.py::test_where_field_api_installed_console_parity",
            "tests/test_store.py::test_persistent_rename_failure_uses_direct_precall_restoration",
            "tests/test_cli_queries.py::test_all_query_json_matches_public_api_and_cache",
            "tests/test_cli_profiles.py",
            "tests/test_runner.py",
            "tests/test_docs.py",
            "tests/test_packaging.py",
            "tests/test_archives.py::test_canonical_members_metadata_long_unicode_and_current_artifact_bytes",
            "tests/test_archives.py::test_frozen_canonical_pax_header_bytes_without_payload",
            "tests/test_archives.py::test_export_as_of_selects_current_bytes_and_complete_history",
        ):
            assert target in script
    performance = (ROOT / "tests/test_performance.py").read_text()
    assert 'assert measurement["elapsed_seconds"] < 2.0' in performance
    assert "range(5000)" in performance and "range(200)" in performance
    assert "project.ready(actor=ALICE, now=NOW, as_of=commit)" in performance


def test_required_browser_jobs_install_chromium_and_admit_graph_acceptance():
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert (
        'playwright>=1.59,<2; implementation_name == "cpython"'
        in metadata["project"]["optional-dependencies"]["test"]
    )
    assert not any(
        item.startswith("playwright") for item in metadata["project"]["dependencies"]
    )
    jobs = yaml.load(
        (ROOT / ".github/workflows/ci.yml").read_text(), Loader=yaml.BaseLoader
    )["jobs"]
    for name in ("acceptance", "scale"):
        scripts = [step.get("run", "") for step in jobs[name]["steps"]]
        installers = [
            index
            for index, script in enumerate(scripts)
            if "python -m playwright install --with-deps chromium" in script
        ]
        assert len(installers) == 1
        assert installers[0] < next(
            index
            for index, script in enumerate(scripts)
            if "python -m pytest" in script
        )
        assert all("continue-on-error" not in step for step in jobs[name]["steps"])
    assert "tests/test_viewer_graph.py" in "\n".join(
        step.get("run", "") for step in jobs["scale"]["steps"]
    )
    compatibility = "\n".join(
        step.get("run", "") for step in jobs["compatibility"]["steps"]
    )
    assert (
        "playwright install" not in compatibility
        and "tests/test_viewer_graph.py" not in compatibility
    )
