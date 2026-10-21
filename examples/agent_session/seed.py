"""Seed human-approved prerequisites before launching an agent session."""

import argparse
import json
import subprocess
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from researchgraph import Project
from researchgraph.model import Actor, format_timestamp, parse_timestamp


def git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def seed(root, *, actor, now):
    """Create a research project and return its durable, JSON-safe audit boundary.

    All seed operations use the supplied clock. No session node is created here.
    The caller must retain the manifest independently of the agent's checkout.
    """
    root = Path(root).expanduser().resolve()
    if actor.kind != "human":
        raise ValueError("The seed requires a declared human actor")
    with Project.init(root, "research", actor=actor, now=now) as project:
        hypothesis = project.propose(
            "Hypothesis",
            "Treatment doubles the control measurement",
            body="A small deterministic sample illustrates the evidence workflow, not scientific validation.",
            actor=actor,
            now=now,
        )
        project.approve(hypothesis["id"], actor=actor, now=now)
        prediction = project.propose(
            "Prediction",
            "Treatment 4 and control 2 in the example sample",
            edges=[
                {
                    "type": "tests",
                    "to": hypothesis["id"],
                    "fields": {"expected": "Treatment / control = 2"},
                }
            ],
            actor=actor,
            now=now,
        )
        project.approve(prediction["id"], actor=actor, now=now)
    baseline = {
        path.relative_to(root).as_posix(): sha256(path.read_bytes()).hexdigest()
        for path in sorted((root / "graph").rglob("*.yaml"))
    }
    baseline[".researchgraph/profile.yaml"] = sha256(
        (root / ".researchgraph/profile.yaml").read_bytes()
    ).hexdigest()
    return {
        "version": 1,
        "seed_head": git(root, "rev-parse", "HEAD"),
        "hypothesis": hypothesis["id"],
        "prediction": prediction["id"],
        "seed_actor": actor.to_dict(),
        "seed_time": format_timestamp(now),
        "baseline": baseline,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--now", type=parse_timestamp)
    args = parser.parse_args()
    root = args.project.expanduser().resolve()
    manifest_path = args.manifest.expanduser().resolve()
    if manifest_path.is_relative_to(root / "graph") or manifest_path.is_relative_to(
        root / "artifacts"
    ):
        parser.error("The manifest must be outside graph/ and artifacts/")
    if manifest_path.exists():
        parser.error("Refusing to overwrite an existing manifest")
    name = git(root, "config", "user.name")
    email = git(root, "config", "user.email")
    if not name or not email:
        parser.error("Configure Git user.name and user.email before seeding")
    manifest = seed(
        root,
        actor=Actor("human", name, email),
        now=args.now or datetime.now(UTC).replace(microsecond=0),
    )
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with manifest_path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest))


if __name__ == "__main__":
    main()
