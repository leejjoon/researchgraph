# Quickstart
From this source checkout, install Python 3.11+, Git, uv, and a C compiler. Run only trusted shell commands; the example is not a sandbox.
The PyPy 3.11 constraint selects a compatible version of jsonschema's existing dependency, avoiding observed rpds-py 2026.9.1 startup diagnostics.
```sh
set -eu
test ! -e demo
git init -q demo
git -C demo config user.name You
git -C demo config user.email you@example.org
uv venv .venv
uv pip install --python .venv/bin/python -e . "rpds-py==0.25.1; platform_python_implementation == 'PyPy' and python_version == '3.11'"
.venv/bin/python - <<'PY'
from datetime import UTC, datetime
from itertools import pairwise
from researchgraph import Project
from researchgraph.model import Actor
context = {"actor": Actor("human", "You", "you@example.org"), "now": datetime.now(UTC).replace(microsecond=0)}
with Project.init("demo", profile="tasks", **context) as project:
    tasks = []
    for i in range(1, 4):
        task = project.propose("Task", f"Step {i}", fields={"command": f'printf {i} > "$RESEARCHGRAPH_ARTIFACT_DIR/result.txt"'}, **context)
        tasks.append(task["id"])
    for source, target in pairwise(tasks):
        project.link("blocks", source, target, **context)
PY
.venv/bin/python examples/minimal_runner.py demo
.venv/bin/rg --project demo --json digest
```
The three Tasks are done, each with a committed `artifacts/<id>/result.txt`. The example supplies `RESEARCHGRAPH_NODE_ID` and `RESEARCHGRAPH_ARTIFACT_DIR`; it skips commandless Tasks and records nonzero exits as Failures without retrying.
