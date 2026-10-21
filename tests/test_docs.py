"""T17: public call documentation and the exact quickstart as an executable recipe."""

import ast
import datetime as datetime_module
import hashlib
import json
import os
import re
import runpy
import shutil
import subprocess
import sys
import sysconfig
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

import pytest

from researchgraph import Project, __version__
from researchgraph.profiles import RESEARCH_PROFILE, TASKS_PROFILE
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


# These deliberately small conventions describe these two manuals, not Markdown
# in general. Presence/reference checks are not evidence of semantic correctness.
MANUALS = ("docs/user-manual.md", "docs/developer-manual.md")
DELIVERABLES = (*MANUALS, "docs/research-tutorial.ipynb")
REFERENCE_LABELS = (
    "Invocation",
    "Arguments",
    "Options and defaults",
    "Permissions / preconditions",
    "Effect / result",
    "Errors",
)
PROVISIONING = {"source-install", "wheel-install", "notebook-setup"}
WORKFLOWS = {"user-lifecycle", "integration"}
NOTEBOOK_MARKER = "<!-- rg-example: notebook-execution; part=1; notebook -->"


@dataclass(frozen=True)
class ManualBlock:
    scenario: str
    part: int
    kind: str
    language: str
    code: str


def manual_blocks(text):
    """Classify every fence; preserve its actual body for subprocess execution."""
    marker = re.compile(
        r"<!-- rg-example: ([a-z][a-z0-9-]*); part=([1-9][0-9]*); "
        r"(runnable|fragment|output|notebook) -->"
    )
    blocks = []
    lines = text.splitlines(keepends=True)
    pending = None
    index = 0
    while index < len(lines):
        line = lines[index].rstrip("\r\n")
        if "<!-- rg-example:" in line:
            assert pending is None, "marker without a fence"
            pending = marker.fullmatch(line)
            assert pending, "malformed example marker"
        elif line.startswith("```"):
            assert pending is not None, "unclassified fence"
            language = line[3:]
            scenario, part, kind = pending.groups()
            index += 1
            body = []
            while index < len(lines) and lines[index].rstrip("\r\n") != "```":
                body.append(lines[index])
                index += 1
            assert index < len(lines), "unclosed fence"
            assert body and "".join(body).strip(), "empty example"
            if kind in {"runnable", "notebook"}:
                assert language in {"sh", "python"}, "unsupported runnable language"
            if kind == "notebook":
                assert scenario == "notebook-execution" and part == "1", (
                    "only the notebook-execution scenario belongs to the notebook lane"
                )
                assert language == "python", "notebook recipe must be Python"
            else:
                assert scenario != "notebook-execution", "notebook recipe misclassified"
            blocks.append(
                ManualBlock(scenario, int(part), kind, language, "".join(body))
            )
            pending = None
        elif pending is not None:
            assert not line.strip(), "marker must immediately precede its fence"
        index += 1
    assert pending is None, "marker without a fence"
    keys = [(block.scenario, block.part) for block in blocks]
    assert len(keys) == len(set(keys)), "duplicate example part"
    for scenario in {block.scenario for block in blocks}:
        parts = [block.part for block in blocks if block.scenario == scenario]
        assert parts == list(range(1, len(parts) + 1)), (
            "unordered or missing example part"
        )
        assert (
            len({block.kind for block in blocks if block.scenario == scenario}) == 1
        ), "mixed example classifications"
    return blocks


def runnable_scenarios(blocks):
    """An explicit bounded dispatch ensures a newly added recipe cannot go untested."""
    scenarios = {block.scenario for block in blocks if block.kind == "runnable"}
    assert scenarios == PROVISIONING | WORKFLOWS, (
        "uncovered or missing runnable scenario"
    )
    return scenarios


def spec_commands(text):
    inventories = {
        category: set() for category in ("Writes", "Reads", "Streams", "Servers")
    }
    section = None
    for line in text.splitlines():
        if line.startswith("## "):
            section = line[3:] if line[3:] in inventories else None
        match = re.match(r"\| `([a-z][a-z-]*)(?:[ `])", line)
        if section and match:
            command = match[1]
            assert not any(command in names for names in inventories.values()), (
                "duplicate or ambiguous spec command"
            )
            inventories[section].add(command)
    assert not inventories["Writes"] & inventories["Reads"], "ambiguous spec command"
    return inventories


def help_commands(text):
    assert "\nCommands:\n" in text, "missing installed Commands section"
    section = text.split("\nCommands:\n", 1)[1]
    commands = re.findall(r"^  ([a-z][a-z-]*)(?:  +|$)", section, re.MULTILINE)
    assert commands and len(commands) == len(set(commands)), "invalid help inventory"
    return set(commands)


def help_options(text):
    """Ignore descriptions, including wrapped ones, but retain both boolean forms."""
    assert "\nOptions:\n" in text, "missing installed Options section"
    section = text.split("\nOptions:\n", 1)[1].split("\nCommands:\n", 1)[0]
    result = set()
    for line in section.splitlines():
        if re.match(r"^  -(?:-|[A-Za-z])", line):
            declaration = re.split(r"\s{2,}", line.strip(), maxsplit=1)[0]
            result.update(re.findall(r"--[a-z][a-z0-9-]*", declaration))
    assert result, "empty help options"
    return result


def manual_reference(text):
    assert text.count("\n## CLI reference\n") == 1, "missing CLI reference"
    reference = text.split("\n## CLI reference\n", 1)[1].split("\n## ", 1)[0]
    chunks = re.split(r"^### ([a-z][a-z-]*)\s*$", reference, flags=re.MULTILINE)
    entries = {}
    for command, body in zip(chunks[1::2], chunks[2::2], strict=True):
        assert command not in entries, "duplicate manual command"
        fields = {}
        labels = list(re.finditer(r"^\*\*([^\n*]+)\*\*:\s*", body, re.MULTILINE))
        assert [match[1] for match in labels] == list(REFERENCE_LABELS), (
            f"missing, duplicate or unordered required labels: {command}"
        )
        for index, match in enumerate(labels):
            end = labels[index + 1].start() if index + 1 < len(labels) else len(body)
            value = body[match.end() : end].strip()
            assert value, f"empty required label: {command} {match[1]}"
            fields[match[1]] = value
        entries[command] = fields
    assert entries, "empty manual reference"
    return entries


def option_tokens(text):
    return set(re.findall(r"--[a-z][a-z0-9-]*", text))


def check_reference(spec, installed, entries, top_options, command_options, common):
    expected = set().union(*spec.values())
    assert expected == installed, "spec/help command inventory disagreement"
    assert set(entries) == expected, "manual command inventory disagreement"
    assert common == top_options | {"--as-of"}, "common option inventory disagreement"
    for command in expected:
        local = option_tokens(entries[command]["Options and defaults"])
        actual = command_options[command]
        assert not local - actual, f"unknown option for {command}: {local - actual}"
        shared = common & actual
        if actual - local:
            assert (
                "[Common options](#common-options)"
                in entries[command]["Options and defaults"]
            ), f"missing common-options reference: {command}"
        assert actual == local | shared, (
            f"missing option for {command}: {actual - local - shared}"
        )


def _reference_fixture():
    return "\n## CLI reference\n\n### ready\n\n" + "\n\n".join(
        f"**{label}**: "
        + (
            "`--help` [Common options](#common-options)"
            if label == "Options and defaults"
            else "No arguments."
            if label == "Arguments"
            else "Documented value."
        )
        for label in REFERENCE_LABELS
    )


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda text: text.replace("### ready", "### unknown"), "manual command"),
        (
            lambda text: text.replace(
                "### ready",
                "### ready\n" + text.split("### ready\n")[1] + "\n### ready",
            ),
            "duplicate manual",
        ),
        (lambda text: text.replace("**Errors**:", "**Failure**:"), "required labels"),
        (
            lambda text: text.replace("**Errors**: Documented value.", "**Errors**:"),
            "empty required",
        ),
        (lambda text: text.replace("`--help`", "`--imaginary`"), "unknown option"),
        (lambda text: text.replace("`--help`", ""), "missing option"),
        (lambda text: text.replace("### ready", "## End\n### ready"), "empty manual"),
    ],
)
def test_manual_reference_rejects_incomplete_fixtures(change, message):
    with pytest.raises(AssertionError, match=message):
        entries = manual_reference(change(_reference_fixture()))
        check_reference(
            {"Writes": set(), "Reads": {"ready"}},
            {"ready"},
            entries,
            {"--json"},
            {"ready": {"--json", "--help"}},
            {"--json", "--as-of"},
        )


def test_help_parsers_handle_aliases_pairs_metavariables_and_wrapping():
    help_text = (
        "Usage: rg [OPTIONS] COMMAND\n\nOptions:\n"
        "  -p, --project PATH           Project path.\n"
        "  --commit / --no-commit       Default is commit;\n"
        "                               --not-an-option is descriptive text.\n"
        "  --as-agent NAME[:MODEL]      Declared identity.\n"
        "  -h, --help                  Show help.\n\nCommands:\n"
        "  init   Initialise.\n  ready  List ready tasks with a\n"
        "         wrapped description.\n"
    )
    assert help_commands(help_text) == {"init", "ready"}
    assert help_options(help_text) == {
        "--project",
        "--commit",
        "--no-commit",
        "--as-agent",
        "--help",
    }
    assert spec_commands(
        "## Writes\n| `init` | path |\n## Reads\n| `show <id>` | node |\n"
    ) == {"Writes": {"init"}, "Reads": {"show"}, "Streams": set(), "Servers": set()}
    with pytest.raises(AssertionError, match="spec/help"):
        check_reference(
            {"Writes": set(), "Reads": {"ready"}}, {"unknown"}, {}, set(), {}, set()
        )


@pytest.mark.parametrize("category", ["Writes", "Reads", "Streams", "Servers"])
def test_reference_inventory_requires_every_category_entry(category):
    spec = {name: set() for name in ("Writes", "Reads", "Streams", "Servers")}
    spec[category].add("ready")
    with pytest.raises(AssertionError, match="manual command"):
        check_reference(spec, {"ready"}, {}, set(), {}, set())
    with pytest.raises(AssertionError, match="spec/help"):
        check_reference(spec, set(), {}, set(), {}, set())


@pytest.mark.parametrize("category", ["Writes", "Reads", "Streams", "Servers"])
def test_spec_inventory_refuses_duplicate_category_assignments(category):
    text = f"## {category}\n| `ready` | description |\n## Reads\n| `ready` | description |\n"
    with pytest.raises(AssertionError, match="duplicate or ambiguous"):
        spec_commands(text)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("```python\npass\n```", "unclassified"),
        ("<!-- rg-example: x; part=1; runnable -->\n```ruby\n1\n```", "unsupported"),
        ("<!-- rg-example: x; part=0; runnable -->", "malformed"),
        ("<!-- rg-example: x; part=1; runnable -->", "without a fence"),
        ("<!-- rg-example: x; part=1; runnable -->\n```sh\ntrue", "unclosed"),
        ("<!-- rg-example: x; part=2; runnable -->\n```sh\ntrue\n```", "unordered"),
        (
            ("<!-- rg-example: x; part=1; runnable -->\n```sh\ntrue\n```\n") * 2,
            "duplicate",
        ),
        (NOTEBOOK_MARKER + "\n```sh\ntrue\n```", "must be Python"),
    ],
)
def test_manual_examples_reject_unclassified_or_invalid_fixtures(text, message):
    with pytest.raises(AssertionError, match=message):
        manual_blocks(text)


def test_manual_example_dispatch_rejects_new_uncovered_recipes():
    blocks = [
        ManualBlock(name, 1, "runnable", "sh", "true\n")
        for name in PROVISIONING | WORKFLOWS
    ]
    assert runnable_scenarios(blocks) == PROVISIONING | WORKFLOWS
    for bad in ([], blocks + [ManualBlock("untested", 1, "runnable", "sh", "true\n")]):
        with pytest.raises(AssertionError, match="uncovered or missing"):
            runnable_scenarios(bad)
    code = "raise AssertionError('the docs lane must not execute this')\n"
    (block,) = manual_blocks(NOTEBOOK_MARKER + "\n```python\n" + code + "```\n")
    assert block.kind == "notebook" and block.code == code


def published_manual_blocks():
    blocks = []
    for relative in MANUALS:
        blocks.extend(manual_blocks((ROOT / relative).read_text()))
    keys = [(block.scenario, block.part) for block in blocks]
    assert len(keys) == len(set(keys)), "example IDs must be unique across manuals"
    runnable_scenarios(blocks)
    notebook = [block for block in blocks if block.kind == "notebook"]
    assert len(notebook) == 1 and notebook[0].scenario == "notebook-execution"
    assert NOTEBOOK_MARKER in (ROOT / MANUALS[1]).read_text()
    return blocks


def test_manual_navigation_versions_and_example_inventory():
    readme = (ROOT / "README.md").read_text()
    for relative in DELIVERABLES:
        assert (ROOT / relative).is_file(), f"missing deliverable: {relative}"
        assert re.search(r"\]\(" + re.escape(relative) + r"(?:#[^)]+)?\)", readme), (
            f"missing README navigation: {relative}"
        )
    for relative in MANUALS:
        path = ROOT / relative
        text = path.read_text()
        for destination in re.findall(r"\[[^\]]*\]\(([^)]+)\)", text):
            if "://" not in destination and not destination.startswith("#"):
                assert (path.parent / destination.split("#", 1)[0]).is_file(), (
                    destination
                )
        # Provenance only: these checks do not claim the prose is self-contained.
        assert f"researchgraph {__version__}" in text
        assert "v0.1" in text
        for profile in (RESEARCH_PROFILE, TASKS_PROFILE):
            assert f"{profile.name} {profile.version}" in text
        assert "](research-tutorial.ipynb)" in text
    assert "](quickstart.md)" in (ROOT / MANUALS[0]).read_text()
    published_manual_blocks()


def docs_environment(tmp_path):
    assert not tmp_path.resolve().is_relative_to(ROOT), (
        "manual examples need external temp space"
    )
    environment = os.environ.copy()
    for name in (
        "PYTHONPATH",
        "PYTHONHOME",
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_CONFIG_COUNT",
        "GIT_CONFIG_PARAMETERS",
        "GIT_AUTHOR_DATE",
        "GIT_COMMITTER_DATE",
        "GIT_AUTHOR_NAME",
        "GIT_AUTHOR_EMAIL",
        "GIT_COMMITTER_NAME",
        "GIT_COMMITTER_EMAIL",
    ):
        environment.pop(name, None)
    temporary = tmp_path / "temporary"
    temporary.mkdir(exist_ok=True)
    environment.update(
        GIT_CONFIG_GLOBAL=str(tmp_path / "absent-global-gitconfig"),
        GIT_CONFIG_NOSYSTEM="1",
        TMPDIR=str(temporary),
        UV_PYTHON=sys.executable,
        PYTHONNOUSERSITE="1",
        COLUMNS="100",
        LC_ALL="C.UTF-8",
        RG=str((Path(sysconfig.get_path("scripts")) / "rg").resolve()),
        RESEARCHGRAPH_CHECKOUT_ROOT=str(ROOT),
    )
    assert Path(environment["RG"]).is_file(), (
        "install researchgraph's real console first"
    )
    return environment


def docs_process(arguments, *, cwd, environment, timeout=60):
    result = subprocess.run(
        arguments,
        cwd=cwd,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=timeout,
    )
    assert result.returncode == 0, (
        f"{arguments!r}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    return result


@pytest.mark.cli
def test_manual_reference_matches_spec_and_real_installed_help(tmp_path):
    environment = docs_environment(tmp_path)
    console = environment["RG"]

    def help_for(*arguments):
        return docs_process(
            [console, *arguments, "--help"], cwd=tmp_path, environment=environment
        ).stdout

    top = help_for()
    installed = help_commands(top)
    spec = spec_commands((ROOT / "spec/operations.md").read_text())
    assert len(spec["Writes"]) == 18
    assert len(spec["Reads"]) == 19 and "export" in spec["Reads"]
    assert len(installed) == 40
    assert spec["Streams"] == {"events"} and spec["Servers"] == {"mcp", "serve"}
    assert installed == set().union(*spec.values()), (
        "spec/help command inventory disagreement"
    )
    from researchgraph.registry import OPERATIONS

    assert {op["name"] for op in OPERATIONS} == spec["Writes"] | spec["Reads"]
    for op in OPERATIONS:
        assert op["name"] in spec["Reads" if op["kind"] == "read" else "Writes"]
    options = {command: help_options(help_for(command)) for command in installed}
    text = (ROOT / MANUALS[0]).read_text()
    assert text.count("\n## Servers\n") == 1
    servers = text.split("\n## Servers\n", 1)[1].split("\n## ", 1)[0]
    assert all(f"`rg {name}`" in servers for name in spec["Servers"])
    assert text.count("\n## Common options\n") == 1
    shared = text.split("\n## Common options\n", 1)[1].split("\n## ", 1)[0]
    check_reference(
        spec,
        installed,
        manual_reference(text),
        help_options(top),
        options,
        option_tokens(shared),
    )
    version = docs_process(
        [console, "--version"], cwd=tmp_path, environment=environment
    )
    assert version.stdout == f"rg, version {__version__}\n"


def copy_documentation_checkout(destination):
    source = copy_checkout(destination)
    # git ls-files intentionally excludes untracked deliverables. Never stage them.
    for relative in DELIVERABLES:
        target = source / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)
    return source


def execute_manual_parts(
    blocks, scenario, *, cwd, environment, python, before_part=None
):
    selected = [
        block
        for block in blocks
        if block.scenario == scenario and block.kind == "runnable"
    ]
    assert selected, f"missing runnable scenario: {scenario}"
    results = []
    for block in selected:
        if before_part is not None:
            before_part()
        script = cwd / f"{scenario}-{block.part}.{block.language}"
        script.write_text(block.code)
        arguments = (
            ["sh", "-eu", str(script)]
            if block.language == "sh"
            else [str(python), str(script)]
        )
        results.append(
            docs_process(
                arguments,
                cwd=cwd,
                environment=environment,
                timeout=300 if scenario in PROVISIONING else 90,
            )
        )
    return results


@pytest.mark.cli
@pytest.mark.parametrize("scenario", sorted(PROVISIONING))
def test_exact_manual_provisioning_recipes(tmp_path, scenario):
    assert shutil.which("uv"), "uv is required for the published provisioning recipes"
    blocks = published_manual_blocks()
    source = copy_documentation_checkout(tmp_path / "source checkout λ")
    environment = docs_environment(tmp_path)
    environment["RESEARCHGRAPH_CHECKOUT_ROOT"] = str(source)
    execute_manual_parts(
        blocks, scenario, cwd=source, environment=environment, python=sys.executable
    )
    # Each provisioning recipe leaves this isolated, interpreter-matched console.
    python = source / ".venv/bin/python"
    console = source / ".venv/bin/rg"
    assert python.is_file() and console.is_file()
    result = docs_process(
        [str(console), "--version"], cwd=tmp_path, environment=environment
    )
    assert result.stdout == f"rg, version {__version__}\n"
    probe = docs_process(
        [
            str(python),
            "-c",
            "import json,sys,researchgraph; print(json.dumps([sys.prefix,researchgraph.__file__]))",
        ],
        cwd=tmp_path,
        environment=environment,
    )
    prefix, package = json.loads(probe.stdout)
    assert Path(prefix).resolve() == (source / ".venv").resolve()
    assert Path(package).resolve().is_relative_to(source)
    if scenario == "wheel-install":
        assert list((source / "dist").glob("*.whl")), "build and install a local wheel"
        assert Path(package).resolve().is_relative_to((source / ".venv").resolve())
    if scenario == "notebook-setup":
        tooling = docs_process(
            [
                str(python),
                "-c",
                "import importlib.metadata,json; print(json.dumps({name:importlib.metadata.version(name) for name in ('nbclient','nbformat','ipykernel')}))",
            ],
            cwd=tmp_path,
            environment=environment,
        )
        assert set(json.loads(tooling.stdout)) == {"nbclient", "nbformat", "ipykernel"}


def clear_manual_caches(directory):
    for profile in directory.rglob(".researchgraph/profile.yaml"):
        for suffix in ("", "-wal", "-shm"):
            (profile.parent / ("index.sqlite" + suffix)).unlink(missing_ok=True)


def test_user_manual_workflow_uses_shell_commands():
    text = (ROOT / "docs/user-manual.md").read_text()
    blocks = [
        block for block in manual_blocks(text) if block.scenario == "user-lifecycle"
    ]
    assert len(blocks) == 9
    assert all(len(block.code.splitlines()) <= 25 for block in blocks)
    assert all(block.language == "sh" for block in blocks)
    assert "subprocess" not in text
    assert shutil.which("jq"), "jq is required for the published shell workflow"


def assert_manual_project(root):
    """Independently check durable graph/artifact semantics, not a printed success word."""
    assert root.is_dir()
    with Project.open(root) as project:
        nodes = project.get_nodes()
        tasks = [node for node in nodes if node.type == "Task"]
        assert tasks and any(node.status.value == "done" for node in tasks)
        artifacts = [artifact for node in nodes for artifact in node.artifacts]
        assert artifacts
        for artifact in artifacts:
            content = (root / artifact.path).read_bytes()
            assert artifact.bytes == len(content)
            assert artifact.sha256 == hashlib.sha256(content).hexdigest()
        assert all(node.created.microsecond == 0 for node in nodes)
        assert all(node.created.year == 2026 for node in nodes)
        assert all(node.created.tzinfo is not None for node in nodes)
        records = [record for node in nodes for record in node.approvals]
        assert records
        assert all(
            record.at.microsecond == 0 and record.at.year == 2026 for record in records
        )
        assert any(node.updated > node.created for node in tasks)
        return sorted((node.id, node.status.value) for node in nodes)


@pytest.mark.cli
@pytest.mark.parametrize("scenario", sorted(WORKFLOWS))
def test_exact_manual_workflows_and_documented_results(
    tmp_path, scenario, cli_cache_policy, index_mode
):
    blocks = published_manual_blocks()
    selected = [block for block in blocks if block.scenario == scenario]
    assert len(selected) >= 2, "separate setup from later stateful operations/readback"
    environment = docs_environment(tmp_path)
    environment["UV_OFFLINE"] = "1"
    results = execute_manual_parts(
        blocks,
        scenario,
        cwd=tmp_path,
        environment=environment,
        python=sys.executable,
        before_part=(lambda: clear_manual_caches(tmp_path))
        if index_mode == "deleted"
        else None,
    )
    receipt = json.loads(results[-1].stdout.splitlines()[-1])
    root = Path(receipt["project"]).resolve()
    assert root.is_relative_to(tmp_path) and root != tmp_path
    before = assert_manual_project(root)
    clear_manual_caches(tmp_path)
    assert assert_manual_project(root) == before
    if scenario == "user-lifecycle":
        with Project.open(root) as project:
            nodes = project.get_nodes()
            assert project.profile.name == "research"
            claims = [
                node
                for node in nodes
                if node.type == "Claim" and node.status.value == "active"
            ]
            assert claims
            for claim in claims:
                assert any(
                    record.what == "approve" and record.actor.kind == "human"
                    for record in claim.approvals
                )
                evidence = project.evidence(
                    claim.id, actor=ALICE, now=datetime(2026, 12, 31, tzinfo=UTC)
                )
                assert any(
                    item["type"] == "Result" and item["artifacts"]
                    for item in evidence["items"]
                )
            assert any(edge.type == "supersedes" for edge in project.get_edges()), (
                "demonstrate history through supersession"
            )
    else:
        imported = Path(receipt["imported"]).resolve()
        assert imported.is_relative_to(tmp_path) and imported != root
        assert_manual_project(imported)
        expected = {
            "agent-approval": (2, "human_only_approval"),
            "claim-evidence": (2, "claim_needs_evidence"),
            "read-only": (2, "read_only"),
            "missing-project": (3, "project_not_found"),
            "usage": (2, None),
        }
        errors = receipt["errors"]
        assert {item["case"] for item in errors} == set(expected)
        for error in errors:
            code, rule = expected[error["case"]]
            assert error["returncode"] == code and error["stdout"] == ""
            if rule is None:
                assert "Usage:" in error["stderr"] and "Error:" in error["stderr"]
                with pytest.raises(json.JSONDecodeError):
                    json.loads(error["stderr"])
            else:
                payload = json.loads(error["stderr"])
                assert set(payload) == {"rule", "message", "ids"}
                assert payload["rule"] == rule and payload["message"]
                assert isinstance(payload["ids"], list)
