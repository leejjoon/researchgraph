"""Local git operations with literal, explicitly scoped commit paths."""

import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from researchgraph.model import Actor, RuleViolation, format_timestamp


class Git:
    def __init__(self, path: Path):
        self.path = path

    def run(self, *arguments: str, env=None, check=True):
        return subprocess.run(
            ["git", "--literal-pathspecs", "-C", str(self.path), *arguments],
            capture_output=True,
            text=True,
            check=check,
            env=None if env is None else {**os.environ, **env},
        )

    def exists(self) -> bool:
        if not self.path.is_dir():
            return False
        result = self.run("rev-parse", "--is-inside-work-tree", check=False)
        return result.returncode == 0 and result.stdout.strip() == "true"

    def init(self) -> None:
        if not self.exists():
            self.run("init")

    def head(self) -> str | None:
        result = self.run("rev-parse", "--verify", "HEAD", check=False)
        if result.returncode:
            return None
        return result.stdout.strip()

    def check_clock(self, now: datetime) -> None:
        # Validate precision/zone even for an unborn branch.
        try:
            format_timestamp(now)
        except (TypeError, ValueError, AttributeError) as exc:
            raise RuleViolation("schema", f"Invalid write time: {exc}") from exc
        if self.exists() and self.head() is not None:
            timestamp = self.run("show", "-s", "--format=%ct", "HEAD").stdout.strip()
            if now < datetime.fromtimestamp(int(timestamp), UTC):
                raise RuleViolation("clock_skew", "Write time is earlier than HEAD")

    def human_actor(self) -> Actor:
        name = self.run("config", "user.name", check=False).stdout.strip()
        email = self.run("config", "user.email", check=False).stdout.strip()
        if not name or not email:
            raise RuleViolation("schema", "Git user.name and user.email are required")
        return Actor("human", name, email)

    def commit(
        self, paths: list[str], message: str, actor: Actor, now: datetime
    ) -> str:
        identity = (
            self.human_actor()
            if actor.kind == "human"
            else Actor("agent", actor.name, f"{actor.name}@agents.researchgraph")
        )
        date = format_timestamp(now)
        environment = {
            "GIT_AUTHOR_NAME": identity.name,
            "GIT_AUTHOR_EMAIL": identity.email,
            "GIT_COMMITTER_NAME": identity.name,
            "GIT_COMMITTER_EMAIL": identity.email,
            "GIT_AUTHOR_DATE": date,
            "GIT_COMMITTER_DATE": date,
        }
        # --only builds this commit from working-tree versions of precisely these
        # paths, retaining unrelated staged additions, edits, and deletions.
        self.run("add", "--", *paths)
        self.run("commit", "--only", "-m", message, "--", *paths, env=environment)
        return self.run("rev-parse", "HEAD").stdout.strip()
