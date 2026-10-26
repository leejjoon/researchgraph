"""Executable CI contracts; collection probes never execute the selected test."""

import json
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


def test_feedback_workflow_is_bounded_and_keeps_core_assertions():
    workflow = yaml.load(
        (ROOT / ".github/workflows/checks.yml").read_text(), Loader=yaml.BaseLoader
    )
    assert workflow["on"]["push"] == {"branches": ["main"]}
    assert "pull_request" in workflow["on"]
    assert workflow["concurrency"]["cancel-in-progress"] == "true"
    assert workflow["permissions"] == {"contents": "read"}
    steps = workflow["jobs"]["checks"]["steps"]
    for step in steps:
        assert "continue-on-error" not in step
        if "uses" in step:
            action, pin = step["uses"].split("@")
            assert pin == PINS[action]
    scripts = "\n".join(step.get("run", "") for step in steps)
    assert "ruff check ." in scripts
    assert "ruff format --check src tests examples setup.py" in scripts
    assert "--protocol-fixtures core --query-door cli --index-mode kept" in scripts
    assert "--hypothesis-seed=20261020" in scripts
    assert "--durations" in scripts and "--junitxml" in scripts
    for target in (
        "tests/test_model.py",
        "tests/test_profiles.py",
        "tests/test_validation.py",
        "tests/test_registry.py",
        "tests/test_operations.py",
        "tests/test_cli_version.py",
        "tests/test_ci.py",
        "tests/test_docs.py",
    ):
        assert target in scripts
    assert "not exact_" in scripts
    assert "playwright install" not in scripts


def test_exhaustive_lanes_partition_the_full_collection():
    """Prove functional cache lanes and performance retain every node ID once."""
    probe = """
import json, pytest, sys
class Inventory:
    def pytest_collection_finish(self, session):
        print(json.dumps(sorted(item.nodeid for item in session.items)))
raise SystemExit(pytest.main(sys.argv[1:], plugins=[Inventory()]))
"""
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    selections = {}
    for lane, marker, mode, indexed_only in (
        ("full", None, "both", False),
        ("kept", "not performance", "kept", False),
        ("deleted", "not performance", "deleted", True),
        ("performance", "performance", "both", False),
    ):
        arguments = [
            sys.executable,
            "-c",
            probe,
            "tests",
            "--collect-only",
            "-p",
            "no:terminal",
            "--protocol-fixtures",
            "all",
            "--query-door",
            "all",
            "--index-mode",
            mode,
        ]
        if marker:
            arguments += ["-m", marker]
        if indexed_only:
            arguments += ["--index-fixtures-only"]
        result = subprocess.run(
            arguments,
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        selections[lane] = set(json.loads(result.stdout))
    assert selections["performance"]
    assert not selections["kept"] & selections["deleted"]
    functional = selections["kept"] | selections["deleted"]
    assert not selections["performance"] & functional
    assert selections["full"] == selections["performance"] | functional
    assert any("test_model" in nodeid for nodeid in selections["kept"])
    assert all("test_model" not in nodeid for nodeid in selections["deleted"])
    for target in (
        "test_public_temporal_scrub_budget",
        "test_fresh_twenty_thousand_node_rebuild_budget",
        "test_fifty_running_twenty_real_cli_writes_reach_sse_and_dom_under_three_seconds",
    ):
        assert any(target in nodeid for nodeid in selections["performance"])


@pytest.mark.parametrize("workflow_name", ["ci", "checks"])
def test_ci_artifacts_upload_reports_not_fixture_trees(workflow_name):
    workflow = yaml.load(
        (ROOT / f".github/workflows/{workflow_name}.yml").read_text(),
        Loader=yaml.BaseLoader,
    )
    for job in workflow["jobs"].values():
        upload = next(
            step
            for step in job["steps"]
            if step.get("uses", "").startswith("actions/upload-artifact@")
        )
        paths = upload["with"]["path"].splitlines()
        assert len(paths) == 3
        assert {path.rsplit("/", 1)[-1] for path in paths} == {
            "*.txt",
            "*.xml",
            "*.json",
        }
        assert all("**" not in path for path in paths)
        assert upload["if"] == "always()"
        assert upload["with"]["if-no-files-found"] == "error"


def test_all_doors_live_latency_and_replay_are_explicit_ci_admissions():
    workflow = yaml.load(
        (ROOT / ".github/workflows/ci.yml").read_text(), Loader=yaml.BaseLoader
    )
    scripts = {
        name: "\n".join(step.get("run", "") for step in job["steps"])
        for name, job in workflow["jobs"].items()
    }
    full = [
        line
        for line in scripts["acceptance"].splitlines()
        if re.search(r"python -m pytest tests --", line)
    ]
    assert len(full) == 1
    assert "--protocol-fixtures all --query-door all" in full[0]
    assert '-m "not performance"' in full[0]
    for target in (
        "tests/test_viewer_live.py",
        "tests/test_agent_session.py",
        "tests/test_serve_docs.py",
        "tests/test_event_consumers.py",
    ):
        assert target not in scripts["compatibility"], target
    assert "python -m pytest tests -m performance " in scripts["scale"]
    for name in ("acceptance", "scale"):
        assert "curl --version" in scripts[name]
    performance = (ROOT / "tests/test_performance.py").read_text()
    assert "installed_graph" in performance


def test_scale_failure_profile_is_separate_from_acceptance():
    workflow = yaml.load(
        (ROOT / ".github/workflows/ci.yml").read_text(), Loader=yaml.BaseLoader
    )
    steps = workflow["jobs"]["scale"]["steps"]
    gate = next(step for step in steps if step.get("id") == "performance")
    diagnostic = next(step for step in steps if step.get("id") == "storage_profile")
    assert "python -m pytest tests -m performance" in gate["run"]
    assert "cProfile" not in gate["run"]
    assert (
        diagnostic["if"]
        == "${{ failure() && steps.performance.conclusion == 'failure' }}"
    )
    assert "python -m tests.profile_performance" in diagnostic["run"]
    assert "scale/profile.txt" in diagnostic["run"]
    assert "set -euo pipefail" in diagnostic["run"]
    assert "continue-on-error" not in diagnostic


@pytest.mark.browser
def test_required_browser_absence_fails_instead_of_skipping(tmp_path):
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment["PLAYWRIGHT_BROWSERS_PATH"] = str(tmp_path / "missing-browsers")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/test_viewer_graph.py::test_layout_i1_only_invalid_cycle_fallback_and_non_mutation",
            "--index-mode",
            "kept",
            "-q",
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 1, output
    assert "Executable doesn't exist" in output
    assert "1 error" in output and "skipped" not in output


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
    assert re.search(r"python -m pytest tests --", scripts["acceptance"])


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
    assert not console_commands, "the full four-door suite already includes CLI cases"
    assert "python -m pytest tests -m performance " in scripts["scale"]
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
    assert selections["scale"] == [["tests"]]
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


@pytest.mark.parametrize(
    ("job_name", "matrix_value"),
    [
        ("acceptance", "kept"),
        ("acceptance", "deleted"),
        ("scale", ""),
        ("compatibility", "3.11"),
        ("compatibility", "pypy3.11"),
    ],
)
def test_runtime_temp_directory_is_created_and_exported(
    tmp_path, job_name, matrix_value
):
    workflow = yaml.load(
        (ROOT / ".github/workflows/ci.yml").read_text(), Loader=yaml.BaseLoader
    )
    job = workflow["jobs"][job_name]
    # GitHub rejects runner context at job-level env before starting any jobs.
    assert "runner." not in str(job.get("env", {}))
    setup = next(
        step
        for step in job["steps"]
        if step.get("name") == "Install and disclose runtime"
    )
    prefix, separator, _ = setup["run"].partition("uv venv")
    assert separator, "exercise setup before installing dependencies"
    runner_temp = tmp_path / "runner temp"
    prefix = prefix.replace("${{ runner.temp }}", str(runner_temp))
    prefix = prefix.replace("${{ matrix.index-mode }}", matrix_value)
    prefix = prefix.replace("${{ matrix.python }}", matrix_value)
    assert "${{" not in prefix
    environment_file = tmp_path / "github-env"
    environment = dict(os.environ, GITHUB_ENV=str(environment_file))
    environment.pop("TMPDIR", None)
    result = subprocess.run(
        ["bash", "-c", prefix + '\nprintf "%s\\n" "$TMPDIR"\ntest -d "$TMPDIR"\n'],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    lane = f"{job_name}-{matrix_value}" if matrix_value else job_name
    expected = runner_temp / lane / "system-tmp"
    assert result.stdout.strip() == str(expected)
    assert expected.is_dir()
    assert environment_file.read_text() == f"TMPDIR={expected}\n"


def test_workflow_contract():
    # BaseLoader deliberately preserves the YAML 1.1 boolean-looking `on` key.
    workflow = yaml.load(
        (ROOT / ".github/workflows/ci.yml").read_text(), Loader=yaml.BaseLoader
    )
    assert set(workflow["on"]) == {"push", "schedule", "workflow_dispatch"}
    assert workflow["on"]["push"] == {"tags": ["v*"]}
    assert workflow["on"]["schedule"]
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["env"]["PYTHONHASHSEED"] == "0"
    jobs = workflow["jobs"]
    primary = jobs["acceptance"]
    assert primary["runs-on"] == "ubuntu-24.04"
    assert primary["strategy"]["matrix"]["index-mode"] == ["kept", "deleted"]
    assert primary["strategy"]["matrix"]["include"] == [
        {"index-mode": "kept", "cache-selection": ""},
        {"index-mode": "deleted", "cache-selection": "--index-fixtures-only"},
    ]
    assert primary["strategy"]["fail-fast"] == "false"
    compat = jobs["compatibility"]["strategy"]["matrix"]["python"]
    assert set(compat) == {"3.11", "pypy3.11"}
    assert set(jobs) == {"acceptance", "scale", "compatibility"}

    scripts = {}
    for name, job in jobs.items():
        assert job["runs-on"] == "ubuntu-24.04"
        assert "continue-on-error" not in job
        assert "permissions" not in job
        assert "runner." not in str(job.get("env", {}))
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
    assert "${{ matrix.cache-selection }}" in acceptance
    assert '-m "not performance"' in acceptance
    assert "-m cli" not in acceptance
    scale = scripts["scale"]
    assert "python -m pytest tests -m performance " in scale
    assert scale.count("python -m pytest") == 1
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
    assert "python -m pytest tests -m performance " in "\n".join(
        step.get("run", "") for step in jobs["scale"]["steps"]
    )
    compatibility = "\n".join(
        step.get("run", "") for step in jobs["compatibility"]["steps"]
    )
    assert (
        "playwright install" not in compatibility
        and "tests/test_viewer_graph.py" not in compatibility
    )
