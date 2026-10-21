"""Execute the published research tutorial and independently inspect its evidence."""

import copy
import hashlib
import json
import math
import os
import random
import re
import statistics
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import nbformat
import pytest
from jupyter_client import KernelManager
from jupyter_client.kernelspec import KernelSpecManager
from nbclient import NotebookClient
from nbclient.exceptions import CellExecutionError

from researchgraph import Actor, Project
from researchgraph.model import Edge, Node, Status
from researchgraph.profiles import Profile

ROOT = Path(__file__).resolve().parents[1]
NOTEBOOK = ROOT / "docs/research-tutorial.ipynb"
RECIPE_MARKER = "<!-- rg-example: notebook-execution; part=1; notebook -->"
SEED = 20261020
TYPES = {"Question", "Hypothesis", "Prediction", "Task", "Result", "Claim"}


def assert_portable(notebook):
    """Reject saved runtime state while allowing portable notebook metadata."""
    nbformat.validate(notebook)
    assert set(notebook.metadata) <= {"kernelspec", "language_info"}
    assert set(notebook.metadata.get("kernelspec", {})) <= {
        "name",
        "display_name",
        "language",
    }
    assert set(notebook.metadata.get("language_info", {})) <= {
        "name",
        "file_extension",
        "mimetype",
        "pygments_lexer",
        "codemirror_mode",
    }
    for value in notebook.metadata.get("kernelspec", {}).values():
        assert isinstance(value, str) and "/" not in value and "\\" not in value
    assert notebook.cells, "The tutorial must contain cells"
    assert any(cell.cell_type == "markdown" for cell in notebook.cells)
    assert any(cell.cell_type == "code" for cell in notebook.cells)
    for cell in notebook.cells:
        assert cell.cell_type in {"markdown", "code"}
        assert not cell.metadata, "Strip cell execution/trusted/timing metadata"
        if cell.cell_type == "code":
            assert cell.outputs == [], "Strip saved cell outputs"
            assert cell.execution_count is None, "Strip execution counts"


def isolated_environment(base):
    """Build a fresh external working directory and sanitized child environment."""
    base = base.resolve()
    assert not base.is_relative_to(ROOT), "pytest basetemp must be outside checkout"
    base.mkdir(parents=True, exist_ok=True)
    cwd = base / "cwd"
    temporary = base / "tmp"
    home = base / "home"
    for directory in (cwd, temporary, home):
        directory.mkdir()
    environment = {
        key: value
        for key, value in os.environ.items()
        if key != "PYTHONPATH" and not key.startswith(("GIT_", "JUPYTER_", "IPYTHON"))
    }
    environment.update(
        HOME=str(home),
        TMPDIR=str(temporary),
        TMP=str(temporary),
        TEMP=str(temporary),
        GIT_CONFIG_GLOBAL=str(base / "absent-gitconfig"),
        GIT_CONFIG_NOSYSTEM="1",
        JUPYTER_CONFIG_DIR=str(home / "jupyter"),
        IPYTHONDIR=str(home / "ipython"),
        RESEARCHGRAPH_CHECKOUT_ROOT=str(ROOT),
        PYTHONHASHSEED="0",
    )
    return cwd, temporary, environment


def execute_notebook(notebook, base):
    """Use an explicit temporary manager, never an installed user kernelspec."""
    cwd, temporary, environment = isolated_environment(base)
    kernel_root = base / "kernels"
    kernel_name = "researchgraph-test"
    spec_directory = kernel_root / kernel_name
    spec_directory.mkdir(parents=True)
    argv = [sys.executable, "-m", "ipykernel_launcher", "-f", "{connection_file}"]
    (spec_directory / "kernel.json").write_text(
        json.dumps({"argv": argv, "display_name": kernel_name, "language": "python"}),
        encoding="utf-8",
    )
    specs = KernelSpecManager(kernel_dirs=[str(kernel_root)])
    assert specs.get_kernel_spec(kernel_name).argv == argv
    manager = KernelManager(kernel_spec_manager=specs, kernel_name=kernel_name)
    executed = copy.deepcopy(notebook)
    executed.cells.insert(
        0,
        nbformat.v4.new_code_cell(
            "import os, sys\n"
            "from pathlib import Path\n"
            f"assert sys.executable == {sys.executable!r}\n"
            "assert 'PYTHONPATH' not in os.environ\n"
            f"assert Path.cwd() == Path({str(cwd)!r})\n"
            f"assert os.environ['TMPDIR'] == {str(temporary)!r}\n"
            f"assert os.environ['RESEARCHGRAPH_CHECKOUT_ROOT'] == {str(ROOT)!r}\n"
            "assert os.environ['GIT_CONFIG_NOSYSTEM'] == '1'\n"
            "assert not Path(os.environ['GIT_CONFIG_GLOBAL']).exists()\n"
        ),
    )
    client = NotebookClient(
        executed,
        km=manager,
        kernel_name=kernel_name,
        allow_errors=False,
        timeout=90,
        startup_timeout=60,
        record_timing=False,
    )
    try:
        client.execute(cwd=str(cwd), env=environment, cleanup_kc=True)
    finally:
        # execute cleans channels and kernel on both success and cell failure.
        # Also cover startup failures before nbclient's context is established.
        if manager.has_kernel:
            manager.shutdown_kernel(now=True)
        manager.cleanup_resources()
        nbformat.write(executed, base / "executed-diagnostics.ipynb")
    return temporary


def notebook_recipe(document):
    """Extract only the exact published notebook recipe, without docs-test imports."""
    assert document.count(RECIPE_MARKER) == 1, "Require one notebook-execution recipe"
    suffix = document.split(RECIPE_MARKER, 1)[1]
    match = re.match(r"\s*```python\n(.*?)\n```(?:\n|$)", suffix, re.DOTALL)
    assert match, "Notebook marker must introduce a Python fence"
    assert match[1].strip(), "Notebook recipe must not be empty"
    return match[1]


def discover_demo(temporary):
    parents = sorted(temporary.glob("researchgraph-tutorial-*"))
    assert len(parents) == 1, f"Expected one retained tutorial parent, got {parents}"
    parent = parents[0].resolve()
    assert parent.is_dir() and not parent.is_relative_to(ROOT)
    assert parent.parent == temporary.resolve()
    demo = parent / "demo"
    assert demo.is_dir() and not demo.is_symlink()
    return demo


def assert_science(payload):
    """Recompute the constructed paired experiment from bytes, not cell assertions."""
    assert payload["seed"] == SEED
    pairs = payload["pairs"]
    assert len(pairs) >= 8
    assert payload["sample_size"] == len(pairs)
    assert payload["expected_delta"] == 2.0
    generator = random.Random(SEED)
    deltas = []
    for pair in pairs:
        baseline, treated = pair["baseline"], pair["treated"]
        assert math.isfinite(baseline) and math.isfinite(treated)
        assert baseline == pytest.approx(generator.uniform(0, 10))
        assert treated - baseline == pytest.approx(2.0)
        deltas.append(treated - baseline)
    assert payload["mean_delta"] == pytest.approx(statistics.mean(deltas))
    assert payload["mean_delta"] > 0


def assert_graph(nodes, edges, evidence, demo):
    """Independent graph oracle shared only with small adversarial fixtures."""
    assert len(nodes) == 6 and {node.type for node in nodes} == TYPES
    by_type = {node.type: node for node in nodes}
    question, hypothesis, prediction, task, result, claim = (
        by_type[name]
        for name in ("Question", "Hypothesis", "Prediction", "Task", "Result", "Claim")
    )
    for node in (question, hypothesis, prediction, result, claim):
        assert node.status == Status.ACTIVE
    assert task.status == Status.DONE and task.lease is None
    required = {
        ("answers", hypothesis.id, question.id),
        ("tests", prediction.id, hypothesis.id),
        ("addresses", task.id, prediction.id),
        ("derived-from", result.id, task.id),
        ("cites", claim.id, result.id),
        ("supports", claim.id, hypothesis.id),
    }
    assert {
        (edge.type, edge.from_, edge.to) for edge in edges if not edge.retired
    } == required
    tests_edge = next(
        edge for edge in edges if edge.type == "tests" and not edge.retired
    )
    assert tests_edge.fields.get("expected"), (
        "Prediction tests edge must declare its criterion"
    )
    approvals = []
    for node in (hypothesis, prediction, claim):
        events = [event for event in node.approvals if event.what == "approve"]
        assert len(events) == 1
        approvals.append(events[0])
    human = approvals[0].actor
    assert human.kind == "human"
    assert all(event.actor == human for event in approvals)
    events = [event for event in task.approvals if event.what in {"claim", "complete"}]
    assert [event.what for event in events] == ["claim", "complete"]
    agent = events[0].actor
    assert agent.kind == "agent" and agent.name != human.name
    assert all(event.actor == agent for event in events)
    assert events[0].at < events[1].at
    cites = next(edge for edge in edges if edge.type == "cites" and not edge.retired)
    assert cites.created < approvals[-1].at, "Link evidence before Claim approval"
    timestamps = [node.created for node in nodes] + [edge.created for edge in edges]
    timestamps += [event.at for node in nodes for event in node.approvals]
    assert all(
        value.utcoffset() == timedelta(0) and value.microsecond == 0
        for value in timestamps
    )
    assert len({value.date() for value in timestamps}) == 1
    assert result.artifacts, "Saved evidence must be owned by the Result"
    scientific = []
    for artifact in result.artifacts:
        relative = Path(artifact.path)
        assert relative.parts[:2] == ("artifacts", result.id)
        path = (demo / relative).resolve()
        assert path.is_relative_to((demo / "artifacts" / result.id).resolve())
        data = path.read_bytes()
        assert data and artifact.bytes == len(data)
        assert artifact.sha256 == hashlib.sha256(data).hexdigest()
        assert result.created < artifact.added_at < approvals[-1].at
        if path.suffix == ".json":
            scientific.append(json.loads(data))
    assert len(scientific) == 1, "One Result JSON records the synthetic experiment"
    assert_science(scientific[0])
    evidence_by_id = {item["id"]: item for item in evidence["items"]}
    assert result.id in evidence_by_id and claim.id not in evidence_by_id
    assert evidence_by_id[result.id]["artifacts"] == [
        a.to_dict() for a in result.artifacts
    ]


def verify_demo(temporary, index_mode):
    demo = discover_demo(temporary)
    if index_mode == "deleted":
        (demo / ".researchgraph/index.sqlite").unlink(missing_ok=True)
    with Project.open(demo) as project:
        assert project.profile_name == "research"
        assert project.profile_version == Profile.load("research").version
        nodes, edges = list(project.get_nodes()), list(project.get_edges())
        claim = next(node for node in nodes if node.type == "Claim")
        latest = max(node.updated for node in nodes)
        evidence = project.evidence(
            claim.id, actor=claim.author, now=latest + timedelta(seconds=1)
        )
        assert_graph(nodes, edges, evidence, demo)
    for key in ("user.name", "user.email"):
        configured = subprocess.run(
            ["git", "-C", str(demo), "config", "--local", "--get", key],
            capture_output=True,
            text=True,
            check=True,
            timeout=15,
        )
        assert configured.stdout.strip()
    return demo


def test_tutorial_stored_form():
    assert list((ROOT / "docs").glob("*.ipynb")) == [NOTEBOOK]
    assert_portable(nbformat.read(NOTEBOOK, as_version=4))


def test_real_tutorial_execution(tmp_path, index_mode):
    notebook = nbformat.read(NOTEBOOK, as_version=4)
    assert_portable(notebook)
    original = NOTEBOOK.read_bytes()
    temporary = execute_notebook(notebook, tmp_path / "direct")
    verify_demo(temporary, index_mode)
    assert NOTEBOOK.read_bytes() == original


def test_published_headless_recipe(tmp_path, index_mode):
    recipe = notebook_recipe(
        (ROOT / "docs/developer-manual.md").read_text(encoding="utf-8")
    )
    cwd, temporary, environment = isolated_environment(tmp_path / "published")
    original = NOTEBOOK.read_bytes()
    result = subprocess.run(
        [sys.executable, "-c", recipe],
        cwd=cwd,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    demo = verify_demo(temporary, index_mode)
    assert str(demo) in result.stdout, (
        "Recipe must print the retained absolute demo path"
    )
    assert NOTEBOOK.read_bytes() == original


@pytest.mark.parametrize("startup_location", ["ipython-directory", "home-directory"])
def test_published_recipe_ignores_inherited_startup(
    tmp_path, index_mode, startup_location
):
    recipe = notebook_recipe(
        (ROOT / "docs/developer-manual.md").read_text(encoding="utf-8")
    )
    cwd, temporary, environment = isolated_environment(tmp_path / "hostile-published")
    inherited_home = tmp_path / "inherited-home"
    inherited_home.mkdir()
    profile_root = inherited_home / ".ipython"
    startup = profile_root / "profile_default" / "startup"
    startup.mkdir(parents=True)
    sentinel = tmp_path / "inherited-startup-executed"
    startup_file = startup / "00-inherited.py"
    startup_file.write_text(
        f"from pathlib import Path\nPath({str(sentinel)!r}).write_text('executed')\n"
    )
    # Inject hostile user settings AFTER the normal test helper sanitizes them.
    environment["HOME"] = str(inherited_home)
    if startup_location == "ipython-directory":
        environment["IPYTHONDIR"] = str(profile_root)
    else:
        environment.pop("IPYTHONDIR", None)
    environment["PYTHONSTARTUP"] = str(startup_file)
    for variable in ("JUPYTER_CONFIG_DIR", "JUPYTER_DATA_DIR", "JUPYTER_RUNTIME_DIR"):
        destination = inherited_home / variable.lower()
        destination.mkdir()
        environment[variable] = str(destination)
    original = NOTEBOOK.read_bytes()
    result = subprocess.run(
        [sys.executable, "-c", recipe],
        cwd=cwd,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    demo = verify_demo(temporary, index_mode)
    assert str(demo) in result.stdout
    assert not sentinel.exists(), (
        "Published recipe executed inherited user startup code"
    )
    assert NOTEBOOK.read_bytes() == original


def portable_fixture():
    return nbformat.v4.new_notebook(
        cells=[
            nbformat.v4.new_markdown_cell(
                "A tiny harness fixture, not a second tutorial."
            ),
            nbformat.v4.new_code_cell("assert True"),
        ]
    )


@pytest.mark.parametrize(
    "mutation", ["outputs", "count", "trusted", "timing", "widgets", "path"]
)
def test_stored_form_rejects_runtime_state(mutation):
    notebook = portable_fixture()
    if mutation == "outputs":
        notebook.cells[1].outputs = [
            nbformat.v4.new_output("stream", name="stdout", text="stale")
        ]
    elif mutation == "count":
        notebook.cells[1].execution_count = 1
    elif mutation in {"trusted", "timing"}:
        notebook.cells[1].metadata[mutation] = True
    elif mutation == "widgets":
        notebook.metadata["widgets"] = {}
    else:
        notebook.metadata["kernelspec"] = {
            "name": "/host/python",
            "display_name": "Python",
            "language": "python",
        }
    with pytest.raises(AssertionError):
        assert_portable(notebook)


def test_portable_fixture_is_accepted():
    assert_portable(portable_fixture())


def test_actual_cell_error_propagates(tmp_path):
    notebook = portable_fixture()
    notebook.cells[1].source = "raise RuntimeError('notebook-error-sentinel')"
    with pytest.raises(CellExecutionError, match="notebook-error-sentinel"):
        execute_notebook(notebook, tmp_path / "failing")


@pytest.mark.parametrize(
    "document",
    [
        "",
        RECIPE_MARKER + "\n```sh\ntrue\n```\n",
        (RECIPE_MARKER + "\n```python\npass\n```\n") * 2,
    ],
)
def test_recipe_extraction_rejects_missing_malformed_duplicate(document):
    with pytest.raises(AssertionError):
        notebook_recipe(document)


def test_recipe_extraction_preserves_exact_bytes():
    source = "# exact published bytes\nprint('hello')"
    assert (
        notebook_recipe(RECIPE_MARKER + "\n```python\n" + source + "\n```\n") == source
    )


def test_discovery_rejects_missing_or_multiple_parents(tmp_path):
    with pytest.raises(AssertionError):
        discover_demo(tmp_path)
    for suffix in ("a", "b"):
        (tmp_path / ("researchgraph-tutorial-" + suffix) / "demo").mkdir(parents=True)
    with pytest.raises(AssertionError):
        discover_demo(tmp_path)


def graph_fixture(demo):
    """Construct only oracle input records; do not simulate successful execution."""
    from researchgraph.model import Approval, Artifact

    human, agent = Actor("human", "simulated-reviewer"), Actor("agent", "experiment")
    now = datetime(2026, 10, 20, tzinfo=UTC)
    nodes = [
        Node(
            f"n-{index}",
            kind,
            kind,
            "",
            human,
            now + timedelta(seconds=index),
            status=Status.ACTIVE,
        )
        for index, kind in enumerate(
            ("Question", "Hypothesis", "Prediction", "Task", "Result", "Claim")
        )
    ]
    q, h, p, t, r, c = nodes
    for index, node in enumerate((h, p, c)):
        node.approvals = [
            Approval(human, now + timedelta(seconds=20 + index * 10), "approve")
        ]
    t.status = Status.DONE
    t.approvals = [
        Approval(agent, now + timedelta(seconds=31), "claim"),
        Approval(agent, now + timedelta(seconds=32), "complete"),
    ]
    relations = [
        ("answers", h, q),
        ("tests", p, h),
        ("addresses", t, p),
        ("derived-from", r, t),
        ("cites", c, r),
        ("supports", c, h),
    ]
    edges = [
        Edge(
            f"e-{index}",
            kind,
            source.id,
            target.id,
            human,
            now + timedelta(seconds=33 + index),
        )
        for index, (kind, source, target) in enumerate(relations)
    ]
    edges[1].fields["expected"] = "The constructed mean paired delta is 2.0"
    generator = random.Random(SEED)
    baselines = [generator.uniform(0, 10) for _ in range(8)]
    payload = {
        "seed": SEED,
        "pairs": [{"baseline": value, "treated": value + 2.0} for value in baselines],
        "sample_size": 8,
        "expected_delta": 2.0,
        "mean_delta": 2.0,
    }
    relative = Path("artifacts") / r.id / "experiment.json"
    path = demo / relative
    path.parent.mkdir(parents=True)
    data = json.dumps(payload).encode()
    path.write_bytes(data)
    r.artifacts = [
        Artifact(
            relative.as_posix(),
            hashlib.sha256(data).hexdigest(),
            len(data),
            now + timedelta(seconds=39),
            agent,
        )
    ]
    evidence = {"items": [r.to_dict()]}
    return nodes, edges, evidence


def test_graph_oracle_accepts_valid_fixture(tmp_path):
    assert_graph(*graph_fixture(tmp_path), tmp_path)


@pytest.mark.parametrize(
    "mutation",
    [
        "direction",
        "expected",
        "agent_approval",
        "different_human",
        "task_actor",
        "task_status",
        "late_cites",
        "artifact_owner",
        "artifact_bytes",
        "evidence",
        "self_evidence",
    ],
)
def test_graph_oracle_rejects_invalid_outcomes(tmp_path, mutation):
    nodes, edges, evidence = graph_fixture(tmp_path)
    _, hypothesis, _, task, result, claim = nodes
    if mutation == "direction":
        edges[0].from_, edges[0].to = edges[0].to, edges[0].from_
    elif mutation == "expected":
        edges[1].fields.clear()
    elif mutation == "agent_approval":
        hypothesis.approvals[0].actor = Actor("agent", "not-human")
    elif mutation == "different_human":
        claim.approvals[0].actor = Actor("human", "other-human")
    elif mutation == "task_actor":
        task.approvals[-1].actor = hypothesis.approvals[0].actor
    elif mutation == "task_status":
        task.status = Status.ACTIVE
    elif mutation == "late_cites":
        edges[4].created = claim.approvals[0].at + timedelta(seconds=1)
    elif mutation == "artifact_owner":
        result.artifacts[0].path = "artifacts/other/experiment.json"
    elif mutation == "artifact_bytes":
        (tmp_path / result.artifacts[0].path).write_bytes(b"tampered")
    elif mutation == "evidence":
        evidence["items"].clear()
    else:
        evidence["items"].append(claim.to_dict())
    with pytest.raises(AssertionError):
        assert_graph(nodes, edges, evidence, tmp_path)


@pytest.mark.parametrize("mutation", ["seed", "mean", "empty", "constructed"])
def test_scientific_oracle_rejects_false_summary(tmp_path, mutation):
    nodes, _, _ = graph_fixture(tmp_path)
    payload = json.loads((tmp_path / nodes[4].artifacts[0].path).read_bytes())
    if mutation == "seed":
        payload["seed"] += 1
    elif mutation == "mean":
        payload["mean_delta"] = -2.0
    elif mutation == "empty":
        payload["pairs"] = []
    else:
        payload["pairs"][0]["treated"] -= 4.0
    with pytest.raises(AssertionError):
        assert_science(payload)
