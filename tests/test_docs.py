"""T17: public call documentation and the exact quickstart as an executable recipe."""

import ast
import datetime as datetime_module
import json
import os
import runpy
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

from researchgraph import Project
from tests.helpers import ALICE, NOW, copy_checkout, repository

ROOT = Path(__file__).resolve().parents[1]


def test_exact_readme_python_block_accepts_subsecond_wall_clock(
    tmp_path, monkeypatch, capsys
):
    document = (ROOT / "README.md").read_text()
    assert document.count("```python\n") == 1
    recipe = document.split("```python\n", 1)[1].split("\n```", 1)[0]
    with Project.init(repository(tmp_path / "demo"), actor=ALICE, now=NOW):
        pass
    monkeypatch.chdir(tmp_path)
    clock_calls = []
    wall_clock = datetime(2026, 10, 20, 1, 2, 3, 456789, tzinfo=UTC)

    class ControlledDatetime(datetime):
        @staticmethod
        def now(tz):
            clock_calls.append(tz)
            return wall_clock

    # Preserve the real module globals used by PyPy's timezone implementation.
    provider = ModuleType("datetime")
    provider.__dict__.update(vars(datetime_module))
    provider.datetime = ControlledDatetime
    monkeypatch.setitem(sys.modules, "datetime", provider)
    recipe_path = tmp_path / "readme_example.py"
    recipe_path.write_text(recipe)
    namespace = runpy.run_path(str(recipe_path))
    assert clock_calls == [UTC]
    assert type(wall_clock) is datetime and wall_clock.microsecond != 0
    assert namespace["ready"]["items"] == []
    captured = capsys.readouterr()
    assert captured.out == "[]\n"
    assert captured.err == ""


def declarations(statements, prefix=""):
    for statement in statements:
        if isinstance(
            statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ) and not statement.name.startswith("_"):
            name = prefix + statement.name
            yield name, statement
            if isinstance(statement, ast.ClassDef):
                yield from declarations(statement.body, name + ".")


def test_every_public_call_has_a_meaningful_source_docstring():
    missing = []
    for path in sorted((ROOT / "src/researchgraph").rglob("*.py")):
        tree = ast.parse(path.read_text())
        for name, node in declarations(tree.body):
            doc = ast.get_docstring(node)
            if not doc or len(doc.strip()) < 20 or "TODO" in doc:
                missing.append(f"{path.relative_to(ROOT)}:{node.lineno}:{name}")
    assert not missing, "Undocumented public calls:\n" + "\n".join(missing)


def test_quickstart_is_under_thirty_lines_and_one_complete_shell_recipe():
    text = (ROOT / "docs/quickstart.md").read_text()
    assert len(text.splitlines()) < 30
    assert text.count("```sh\n") == 1
    assert text.count("```") == 2
    recipe = text.split("```sh\n", 1)[1].split("\n```", 1)[0]
    assert "examples/minimal_runner.py" in recipe
    assert ".venv/bin/rg" in recipe


def test_exact_quickstart_reproduces_three_tasks_in_an_isolated_install(tmp_path):
    assert shutil.which("uv"), (
        "Install uv to execute the documented isolated installation"
    )
    source = copy_checkout(tmp_path / "source checkout λ")
    document = (ROOT / "docs/quickstart.md").read_text()
    recipe = document.split("```sh\n", 1)[1].split("\n```", 1)[0]
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment["UV_PYTHON"] = sys.executable
    environment["GIT_CONFIG_GLOBAL"] = str(tmp_path / "absent-global-gitconfig")
    environment["GIT_CONFIG_NOSYSTEM"] = "1"
    result = subprocess.run(
        ["sh", "-eu", "-c", recipe],
        cwd=source,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=240,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert isinstance(json.loads(result.stdout.splitlines()[-1]), dict)
    root = source / "demo"
    with Project.open(root) as project:
        tasks = [node for node in project.get_nodes() if node.type == "Task"]
        assert len(tasks) == 3
        assert {node.status.value for node in tasks} == {"done"}
        edges = [edge for edge in project.get_edges() if edge.type == "blocks"]
        assert len(edges) == 2
        for node in tasks:
            assert len(node.artifacts) == 1
            artifact = node.artifacts[0]
            assert (root / artifact.path).read_bytes() == node.title.removeprefix(
                "Step "
            ).encode()
            assert artifact.bytes == 1
    console = (source / ".venv/bin/rg").resolve()
    installed = subprocess.run(
        [str(console), "--project", str(root), "--json", "digest"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert installed.returncode == 0, installed.stdout + installed.stderr
    assert installed.stderr == ""
    assert isinstance(json.loads(installed.stdout), dict)
