"""Verify the installed console entry point rather than the host ripgrep command."""

import subprocess
import sysconfig
from pathlib import Path

import pytest

from researchgraph import __version__


@pytest.mark.cli
def test_installed_cli_version():
    executable = (Path(sysconfig.get_path("scripts")) / "rg").resolve()
    result = subprocess.run(
        [str(executable), "--version"], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == f"rg, version {__version__}\n"
    assert result.stderr == ""
