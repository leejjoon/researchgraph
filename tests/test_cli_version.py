"""Verify the installed console entry point rather than the host ripgrep command."""

import subprocess
import sysconfig
from importlib.metadata import distribution
from pathlib import Path

import pytest

from researchgraph import __version__


@pytest.mark.cli
def test_installed_cli_version():
    executable = (Path(sysconfig.get_path("scripts")) / "rgraph").resolve()
    result = subprocess.run(
        [str(executable), "--version"], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == f"rgraph, version {__version__}\n"
    assert result.stderr == ""


def test_distribution_exposes_only_rgraph_console():
    scripts = {
        entry.name: entry.value
        for entry in distribution("researchgraph").entry_points
        if entry.group == "console_scripts"
    }
    assert scripts == {"rgraph": "researchgraph.cli:main"}


@pytest.mark.cli
def test_installed_cli_help_uses_rgraph():
    executable = (Path(sysconfig.get_path("scripts")) / "rgraph").resolve()
    for arguments, usage in (
        (["--help"], "Usage: rgraph [OPTIONS] COMMAND [ARGS]..."),
        (["propose", "--help"], "Usage: rgraph propose [OPTIONS] TYPE TITLE"),
    ):
        result = subprocess.run(
            [str(executable), *arguments], capture_output=True, text=True, check=False
        )
        assert result.returncode == 0, result.stderr
        assert usage in result.stdout
        assert result.stderr == ""
