"""Task-owned sdist/wheel consumers; a candidate copy is not release-clone proof."""

import json
import subprocess
import sys
from functools import partial

import anyio

from researchgraph.model import format_timestamp
from researchgraph.project import Project
from tests.helpers import ALICE, NOW, repository
from tests.test_agent_session import replay
from tests.test_packaging import execute


def test_core_only_help_and_optional_mcp_from_sdist_built_wheel(
    tmp_path, candidate_distribution
):
    source, wheel, environment = candidate_distribution
    root = repository(tmp_path / "project")
    with Project.init(root, "tasks", actor=ALICE, now=NOW):
        pass
    for extra in (False, True):
        venv = tmp_path / ("optional SDK consumer" if extra else "core consumer")
        execute(
            ["uv", "venv", "--python", sys.executable, str(venv)],
            cwd=tmp_path,
            environment=environment,
        )
        python, console = venv / "bin/python", venv / "bin/rg"
        requirement = f"researchgraph[mcp] @ {wheel.as_uri()}" if extra else str(wheel)
        execute(
            ["uv", "pip", "install", "--python", str(python), requirement],
            cwd=tmp_path,
            environment=environment,
        )
        imported = execute(
            [
                str(python),
                "-c",
                "import importlib.util,json,researchgraph; print(json.dumps({'module':researchgraph.__file__,'mcp':importlib.util.find_spec('mcp') is not None}))",
            ],
            cwd=tmp_path,
            environment=environment,
        )
        observed = json.loads(imported.stdout)
        assert observed["mcp"] is extra
        assert observed["module"].startswith(str(venv))
        help_result = execute(
            [str(console), "mcp", "--help"], cwd=tmp_path, environment=environment
        )
        assert "--as-human" in help_result.stdout and not help_result.stderr
        if not extra:
            missing = subprocess.run(
                [str(console), "--project", str(root), "--as-agent", "consumer", "mcp"],
                cwd=tmp_path,
                env=environment,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            assert missing.returncode == 2 and missing.stdout == ""
            error = json.loads(missing.stderr)
            assert (
                error["rule"] == "schema" and "researchgraph[mcp]" in error["message"]
            )
            execute(
                [
                    "uv",
                    "pip",
                    "install",
                    "--python",
                    str(python),
                    f"researchgraph[test] @ {wheel.as_uri()}",
                ],
                cwd=tmp_path,
                environment=environment,
            )
            execute(
                [
                    str(python),
                    "-c",
                    "import importlib.util; assert importlib.util.find_spec('mcp') is None",
                ],
                cwd=tmp_path,
                environment=environment,
            )
            # Actual compatibility nodes execute without an SDK, not a skip or
            # collection-only claim. They use this installed core wheel.
            compatible = execute(
                [
                    str(python),
                    "-m",
                    "pytest",
                    "tests/test_cli_queries.py::test_all_query_json_matches_public_api_and_cache",
                    "tests/test_cli_queries.py::test_where_field_api_installed_console_parity",
                    "tests/test_archives.py::test_canonical_members_metadata_long_unicode_and_current_artifact_bytes",
                    "tests/test_archives.py::test_frozen_canonical_pax_header_bytes_without_payload",
                    "tests/test_archives.py::test_export_as_of_selects_current_bytes_and_complete_history",
                    "--protocol-fixtures",
                    "core",
                    "--query-door",
                    "cli",
                    "--index-mode",
                    "both",
                    "--hypothesis-seed=20261020",
                    "-q",
                ],
                cwd=source,
                environment=environment,
            )
            assert "passed" in compatible.stdout
            assert "skipped" not in compatible.stdout
            print(
                {
                    "core_compatibility": compatible.stdout.strip(),
                    "mcp_installed": False,
                }
            )
            seeded_root = repository(tmp_path / "core-seeded")
            manifest_path = tmp_path / "trusted-seed.json"
            seeded = execute(
                [
                    str(python),
                    str(source / "examples/agent_session/seed.py"),
                    str(seeded_root),
                    "--manifest",
                    str(manifest_path),
                    "--now",
                    format_timestamp(NOW),
                ],
                cwd=tmp_path,
                environment=environment,
            )
            manifest = json.loads(seeded.stdout)
            assert json.loads(manifest_path.read_text()) == manifest
            # A real SDK replay writes the graph; a different, SDK-free wheel
            # interpreter then audits it from outside the source checkout.
            ids = anyio.run(
                partial(
                    replay,
                    seeded_root,
                    manifest,
                    preset="essential",
                    index_mode="deleted",
                )
            )
            checker = [
                str(python),
                "-O",
                str(source / "examples/agent_session/check.py"),
                str(seeded_root),
                "--manifest",
                str(manifest_path),
            ]
            checked = execute(checker, cwd=tmp_path, environment=environment)
            assert json.loads(checked.stdout)["ok"] is True
            (seeded_root / ids["result_artifact"]).write_bytes(b"corrupted evidence\n")
            rejected = subprocess.run(
                checker,
                cwd=tmp_path,
                env=environment,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            assert rejected.returncode == 1 and rejected.stderr == ""
            assert json.loads(rejected.stdout)["ok"] is False
            print({"core_only_seed_replay_checker": "passed, including -O rejection"})
        else:
            script = """
import anyio,json,sys
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters,stdio_client
async def main():
    parameters=StdioServerParameters(command=sys.argv[1],args=['--project',sys.argv[2],'--as-agent','wheel-agent','mcp','--read-only'])
    async with stdio_client(parameters) as streams:
        async with ClientSession(*streams) as client:
            await client.discover()
            listed=await client.list_tools()
            assert len(listed.tools)==19 and all(t.annotations.read_only_hint for t in listed.tools)
            result=await client.call_tool('graph',{})
            assert not result.is_error and result.structured_content==json.loads(result.content[0].text)=={'nodes':[],'edges':[]}
            refused=await client.call_tool('init',{'now':'invalid'})
            assert refused.is_error and refused.structured_content['rule']=='read_only'
    print(json.dumps({'installed_stdio':'passed','read_only_tools':19}))
anyio.run(main)
"""
            result = execute(
                [str(python), "-c", script, str(console), str(root)],
                cwd=tmp_path,
                environment=environment,
            )
            assert json.loads(result.stdout) == {
                "installed_stdio": "passed",
                "read_only_tools": 19,
            }
            assert result.stderr == ""
