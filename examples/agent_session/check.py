"""Audit an agent session from native YAML, artifact bytes and Git history.

This is a declared-identity audit, not authentication of a person or model. Keep
its seed manifest outside the agent's control; it is the trusted audit boundary.
No Project, index, MCP response or agent-provided outcome IDs are consulted.
"""

import argparse
import json
import subprocess
from hashlib import sha256
from pathlib import Path, PurePosixPath

import yaml

MANAGED = (
    "graph",
    "artifacts",
    ".researchgraph/profile.yaml",
    ".researchgraph/history.jsonl",
)


def require(condition, message):
    # Do not use assert: the standalone checker must also work under python -O.
    if not condition:
        raise AssertionError(message)


def git(root, *args):
    result = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, check=False
    )
    require(result.returncode == 0, f"Git audit failed: {' '.join(args)}")
    return result.stdout


def load_records(root, kind):
    records = {}
    for path in sorted((root / "graph" / kind).glob("*.yaml")):
        require(not path.is_symlink(), f"Symlink graph record: {path.name}")
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
        require(isinstance(value, dict), f"Invalid record: {path.name}")
        require(value.get("id") == path.stem, f"Record filename mismatch: {path.name}")
        require(value["id"] not in records, "Duplicate record ID")
        records[value["id"]] = value
    return records


def only(values, description):
    require(len(values) == 1, f"Expected exactly one {description}")
    return values[0]


def check(root, manifest):
    """Return a JSON-safe success summary, or raise AssertionError on audit failure."""
    try:
        return _check(Path(root).expanduser().resolve(), manifest)
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError) as error:
        raise AssertionError(f"Invalid session audit input: {error}") from error


def _check(root, manifest):
    require(manifest["version"] == 1, "Unsupported seed manifest")
    boundary = manifest["seed_head"]
    require(
        isinstance(boundary, str)
        and len(boundary) == 40
        and all(c in "0123456789abcdef" for c in boundary),
        "Invalid seed commit",
    )
    git(root, "merge-base", "--is-ancestor", boundary, "HEAD")
    baseline = manifest["baseline"]
    require(isinstance(baseline, dict) and baseline, "Missing seed baseline")
    seed_files = (
        git(
            root,
            "ls-tree",
            "-r",
            "--name-only",
            boundary,
            "--",
            "graph",
            ".researchgraph/profile.yaml",
        )
        .decode()
        .splitlines()
    )
    require(set(seed_files) == set(baseline), "Seed inventory mismatch")
    for relative, digest in baseline.items():
        path = root / relative
        require(not path.is_symlink(), f"Symlink seed file: {relative}")
        original = git(root, "show", f"{boundary}:{relative}")
        require(
            sha256(original).hexdigest() == digest, "Seed manifest does not match Git"
        )
        require(path.read_bytes() == original, f"Seed prerequisite changed: {relative}")
    profile = yaml.safe_load(
        (root / ".researchgraph/profile.yaml").read_text(encoding="utf-8")
    )
    require(profile == {"name": "research", "version": "1.0"}, "Wrong pinned profile")
    nodes, edges = load_records(root, "nodes"), load_records(root, "edges")
    seed_ids = {
        PurePosixPath(path).stem for path in baseline if path.startswith("graph/")
    }
    for key, type_name in (("hypothesis", "Hypothesis"), ("prediction", "Prediction")):
        node = nodes[manifest[key]]
        require(
            node["id"] in seed_ids
            and node["type"] == type_name
            and node["status"] == "active",
            f"Invalid seed {key}",
        )
        require(
            node["author"] == manifest["seed_actor"]
            and node["author"]["kind"] == "human",
            "Seed author must be human",
        )
        require(
            any(
                event["what"] == "approve" and event["actor"]["kind"] == "human"
                for event in node["approvals"]
            ),
            "Missing human prerequisite approval",
        )
    fresh = [node for node in nodes.values() if node["id"] not in seed_ids]
    tasks = [node for node in fresh if node["type"] == "Task"]
    require(len(tasks) == 2, "Expected exactly two session Tasks")
    done = only([node for node in tasks if node["status"] == "done"], "completed Task")
    failed = only([node for node in tasks if node["status"] == "failed"], "failed Task")
    result = only([node for node in fresh if node["type"] == "Result"], "Result")
    claim = only([node for node in fresh if node["type"] == "Claim"], "Claim")
    failure = only([node for node in fresh if node["type"] == "Failure"], "Failure")
    require(len(fresh) == 5, "Unexpected session nodes")
    actor = done["author"]
    require(
        actor["kind"] == "agent" and bool(actor["name"]),
        "Session author must be an agent",
    )
    for record in [
        *fresh,
        *(edge for edge in edges.values() if edge["id"] not in seed_ids),
    ]:
        require(
            record["author"] == actor,
            "Session records have inconsistent declared actors",
        )
    require(
        claim["status"] == "proposed" and not claim["approvals"],
        "Claim bypassed human gate",
    )
    require(
        result["status"] == "active" and failure["status"] == "active",
        "Unexpected evidence status",
    )
    for task, outcome in ((done, "complete"), (failed, "fail")):
        require(task["lease"] is None, "Terminal Task still leased")
        require(
            [event["what"] for event in task["approvals"]] == ["claim", outcome],
            "Incorrect Task execution history",
        )
    for node in fresh:
        for event in node["approvals"]:
            require(event["actor"] == actor, "Human event after seed")
        for artifact in node["artifacts"]:
            require(artifact["added_by"] == actor, "Human artifact after seed")
    require(
        all(
            isinstance(failure["fields"].get(key), str)
            and failure["fields"][key].strip()
            for key in ("step", "check")
        ),
        "Failure requires scalar step and check",
    )
    live = {
        (edge["type"], edge["from"], edge["to"])
        for edge in edges.values()
        if not edge["retired"]
    }
    expected = {
        ("tests", manifest["prediction"], manifest["hypothesis"]),
        ("addresses", done["id"], manifest["prediction"]),
        ("addresses", failed["id"], manifest["prediction"]),
        ("derived-from", result["id"], done["id"]),
        ("cites", claim["id"], result["id"]),
        ("supports", claim["id"], manifest["hypothesis"]),
        ("explains", failure["id"], failed["id"]),
    }
    require(
        live == expected and len(edges) == len(expected),
        "Incorrect session relationships",
    )
    for edge in edges.values():
        require(edge["from"] in nodes and edge["to"] in nodes, "Dangling edge")
    artifact_paths = set()
    for node in (done, result):
        require(bool(node["artifacts"]), "Missing recorded evidence")
        for artifact in node["artifacts"]:
            relative = artifact["path"]
            path_parts = PurePosixPath(relative)
            require(
                not path_parts.is_absolute()
                and ".." not in path_parts.parts
                and path_parts.parts[:2] == ("artifacts", node["id"]),
                "Artifact outside owner directory",
            )
            path = root / relative
            require(
                path.is_file()
                and not any(
                    parent.is_symlink()
                    for parent in (path, *path.parents)
                    if parent != root.parent
                ),
                "Missing or symlink artifact",
            )
            require(
                path.resolve().is_relative_to(root / "artifacts" / node["id"]),
                "Escaping artifact",
            )
            content = path.read_bytes()
            require(
                content
                and len(content) == artifact["bytes"]
                and sha256(content).hexdigest() == artifact["sha256"],
                "Artifact hash/size mismatch",
            )
            require(
                git(root, "show", f"HEAD:{relative}") == content,
                "Artifact not committed",
            )
            artifact_paths.add(relative)
    citation = only(
        [edge for edge in edges.values() if edge["type"] == "cites"], "citation"
    )
    if "artifact" in citation["fields"]:
        cited = only(
            [
                item
                for item in result["artifacts"]
                if item["path"] == citation["fields"]["artifact"]
            ],
            "cited artifact",
        )
        if "sha256" in citation["fields"]:
            require(
                citation["fields"]["sha256"] == cited["sha256"],
                "Incorrect citation hash",
            )

    # Walk every reachable post-seed managed commit, not just the final author or
    # net diff. A later agent restoring bytes cannot erase a human write.
    commits = (
        git(root, "rev-list", "--reverse", f"{boundary}..HEAD").decode().splitlines()
    )
    require(bool(commits), "No session history")
    expected_author = f"{actor['name']} <{actor['name']}@agents.researchgraph>"
    managed_commits = 0
    for commit in commits:
        parents = git(root, "show", "-s", "--format=%P", commit).decode().split()
        require(
            len(parents) == 1,
            "Merged session histories are not supported by this audit",
        )
        touched = (
            git(
                root,
                "diff-tree",
                "--no-commit-id",
                "--name-only",
                "-r",
                commit,
                "--",
                *MANAGED,
            )
            .decode()
            .splitlines()
        )
        if not touched:
            continue
        managed_commits += 1
        identity = (
            git(root, "show", "-s", "--format=%an <%ae>", commit).decode().strip()
        )
        require(
            identity == expected_author, f"Non-agent managed write after seed: {commit}"
        )
        for relative in touched:
            if relative.startswith("graph/nodes/"):
                historical = yaml.safe_load(git(root, "show", f"{commit}:{relative}"))
                require(
                    historical["author"] == actor,
                    "Changed seed node or non-agent record in history",
                )
                require(
                    all(event["actor"] == actor for event in historical["approvals"]),
                    "Non-agent event in session history",
                )
                if historical["type"] in {"Hypothesis", "Prediction", "Claim"}:
                    require(
                        historical["status"] == "proposed"
                        and not historical["approvals"],
                        "Human-only gate bypass in session history",
                    )
    require(managed_commits > 0, "No managed session commits")
    require(
        not git(root, "status", "--porcelain", "--", *MANAGED).strip(),
        "Uncommitted managed session changes",
    )
    return {
        "ok": True,
        "seed_head": boundary,
        "hypothesis": manifest["hypothesis"],
        "prediction": manifest["prediction"],
        "completed_task": done["id"],
        "failed_task": failed["id"],
        "result": result["id"],
        "claim": claim["id"],
        "failure": failure["id"],
        "artifacts": sorted(artifact_paths),
        "managed_commits": managed_commits,
        "audit": "declared actors and Git authors; not authenticated human/model identity",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        summary = check(args.project, manifest)
    except (AssertionError, OSError, ValueError) as error:
        print(json.dumps({"ok": False, "error": str(error)}))
        return 1
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
