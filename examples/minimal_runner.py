"""Unsupported tasks-profile consumer (decision 002); runs trusted shell commands.

Commands run in the project root with RESEARCHGRAPH_NODE_ID and
RESEARCHGRAPH_ARTIFACT_DIR set. Every file under that artifact directory is recorded
on success. Failures retain their exit code and are never automatically retried.
"""

import argparse
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from researchgraph import Project
from researchgraph.model import Actor


def clock():
    """Return the current UTC operation time at stored timestamp precision."""
    return datetime.now(UTC).replace(microsecond=0)


def main():
    """Run available command-bearing Tasks; return 1 if any command fails."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", nargs="?", type=Path, default=Path("."))
    args = parser.parse_args()
    actor = Actor("agent", "minimal-runner")
    failed = False
    with Project.open(args.project) as project:
        if project.profile.name != "tasks":
            parser.error("the reference runner requires the tasks profile")
        while True:
            ready = project.ready(actor=actor, now=clock())["items"]
            task = next((node for node in ready if node["fields"].get("command")), None)
            if task is None:
                return int(failed)
            id = task["id"]
            project.claim(id, actor=actor, now=clock())
            directory = project.path / "artifacts" / id
            directory.mkdir(parents=True, exist_ok=True)
            environment = os.environ.copy()
            environment["RESEARCHGRAPH_NODE_ID"] = id
            environment["RESEARCHGRAPH_ARTIFACT_DIR"] = str(directory)
            result = subprocess.run(
                task["fields"]["command"],
                shell=True,
                cwd=project.path,
                env=environment,
                check=False,
            )
            if result.returncode:
                failed = True
                project.fail(
                    id,
                    f"Command exited with status {result.returncode}",
                    error=str(result.returncode),
                    actor=actor,
                    now=clock(),
                )
            else:
                paths = sorted(
                    path.relative_to(project.path).as_posix()
                    for path in directory.rglob("*")
                    if path.is_file()
                )
                project.complete(
                    id,
                    "Command completed",
                    artifacts=paths,
                    actor=actor,
                    now=clock(),
                )


if __name__ == "__main__":
    sys.exit(main())
