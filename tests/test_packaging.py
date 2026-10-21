"""Build real distributions and consume the sdist-built wheel outside its source."""

import json
import os
import subprocess
import sys
import sysconfig
import tarfile
import zipfile
from pathlib import Path

from researchgraph import __version__
from tests.helpers import copy_checkout

PYPY_CONSTRAINT = "rpds-py==0.25.1; platform_python_implementation == 'PyPy' and python_version == '3.11'"


def execute(arguments, *, cwd, environment, timeout=180):
    result = subprocess.run(
        arguments,
        cwd=cwd,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result


def test_sdist_native_sources_and_real_installed_wheel(tmp_path):
    source = copy_checkout(tmp_path / "build source λ")
    private = source / ".gjc"
    private.mkdir()
    (private / "not-for-release.txt").write_text("private workflow evidence")
    (source / "src/researchgraph/_native_clone.stale.so").write_bytes(
        b"deliberately invalid stale build fixture"
    )
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment["UV_PYTHON"] = sys.executable
    distributions = tmp_path / "distributions"
    execute(
        [
            "uv",
            "build",
            "--python",
            sys.executable,
            "--sdist",
            "--wheel",
            "--out-dir",
            str(distributions),
        ],
        cwd=source,
        environment=environment,
    )
    sdists = list(distributions.glob("*.tar.gz"))
    assert len(sdists) == 1
    with tarfile.open(sdists[0]) as archive:
        members = archive.getnames()
    assert any(name.endswith("/src/researchgraph/_native_clone.c") for name in members)
    assert any(
        name.endswith("/src/researchgraph/store/_native_scan.c") for name in members
    )
    assert not any(name.endswith((".so", ".pyd")) for name in members)
    assert not any(
        {".gjc", "build", "dist"} & set(Path(name).parts[1:]) for name in members
    )
    rebuilt = tmp_path / "from-sdist"
    execute(
        [
            "uv",
            "build",
            "--python",
            sys.executable,
            "--wheel",
            "--out-dir",
            str(rebuilt),
            str(sdists[0]),
        ],
        cwd=tmp_path,
        environment=environment,
    )
    wheels = list(rebuilt.glob("*.whl"))
    assert len(wheels) == 1
    with zipfile.ZipFile(wheels[0]) as archive:
        members = archive.namelist()
    assert {
        f"researchgraph/viewer/static/{name}"
        for name in ("index.html", "bootstrap.js", "style.css", "research-graph.js")
    } <= set(members)
    assert not any(name.startswith("researchgraph/web/") for name in members)
    native = [name for name in members if name.endswith(".so")]
    assert len(native) == 2
    suffix = sysconfig.get_config_var("EXT_SUFFIX")
    assert {name.removesuffix(suffix) for name in native} == {
        "researchgraph/_native_clone",
        "researchgraph/store/_native_scan",
    }
    assert not any(".gjc" in Path(name).parts or "stale" in name for name in members)
    venv = tmp_path / "consumer environment λ"
    execute(
        ["uv", "venv", "--python", sys.executable, str(venv)],
        cwd=tmp_path,
        environment=environment,
    )
    python = venv / "bin/python"
    execute(
        [
            "uv",
            "pip",
            "install",
            "--python",
            str(python),
            str(wheels[0]),
            PYPY_CONSTRAINT,
        ],
        cwd=tmp_path,
        environment=environment,
    )
    script = """
import hashlib, json, subprocess, sys
from datetime import UTC, datetime
from pathlib import Path
from researchgraph import Project, __version__
from researchgraph.model import Actor
import researchgraph._native_clone as clone
import researchgraph.store._native_scan as scan
venv=Path(sys.argv[1]).resolve()
assert all(Path(module.__file__).resolve().is_relative_to(venv) for module in (clone,scan))
root=Path('installed-project').resolve()
root.mkdir()
for args in (('init','-q'),('config','user.name','Wheel consumer'),('config','user.email','wheel@example.test')):
    subprocess.run(['git','-C',str(root),*args],check=True,capture_output=True)
now=datetime.now(UTC).replace(microsecond=0)
human=Actor('human','Wheel consumer','wheel@example.test')
worker=Actor('agent','wheel-consumer')
with Project.init(root,profile='tasks',actor=human,now=now) as project:
    node=project.propose('Task','installed native lifecycle',actor=human,now=now)
    assert [item['id'] for item in project.ready(actor=worker,now=now)['items']]==[node['id']]
    project.claim(node['id'],actor=worker,now=now)
    relative='artifacts/'+node['id']+'/payload.bin'
    artifact=root/relative
    artifact.parent.mkdir()
    artifact.write_bytes(bytes([0,255,42]))
    done=project.complete(node['id'],'real wheel consumer',artifacts=[relative],actor=worker,now=now)
    assert done['status']=='done' and done['lease'] is None
    assert done['artifacts'][0]['bytes']==3
    assert done['artifacts'][0]['sha256']==hashlib.sha256(bytes([0,255,42])).hexdigest()
    assert project.ready(actor=worker,now=now)=={'items':[]}
print(json.dumps({'version':__version__,'root':str(root),'node':node['id']}))
"""
    consumed = execute(
        [str(python), "-c", script, str(venv)], cwd=tmp_path, environment=environment
    )
    assert consumed.stderr == ""
    receipt = json.loads(consumed.stdout)
    assert receipt["version"] == __version__
    console = (venv / "bin/rg").resolve()
    version = execute(
        [str(console), "--version"], cwd=tmp_path, environment=environment
    )
    assert version.stdout == f"rg, version {__version__}\n" and version.stderr == ""
    ready = execute(
        [str(console), "--project", receipt["root"], "--json", "ready"],
        cwd=tmp_path,
        environment=environment,
    )
    assert json.loads(ready.stdout) == {"items": []} and ready.stderr == ""
