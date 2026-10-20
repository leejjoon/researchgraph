"""Local git operations with literal, explicitly scoped commit paths."""

import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from researchgraph.model import Actor, RuleViolation, format_timestamp


class Git:
    """Run local Git commands at a caller-owned project directory.

    Commands use literal pathspecs, capture text output, and do not change the
    Python process's working directory. Construction does not create a repo.
    """

    def __init__(self, path: Path):
        """Retain the project path without resolving or validating it."""
        self.path = path

    def run(self, *arguments: str, env=None, check=True):
        """Return a captured text CompletedProcess for the supplied Git arguments.

        ``env`` overlays the process environment when supplied. By default a
        nonzero exit raises CalledProcessError carrying stdout and stderr;
        ``check=False`` leaves exit-status handling to the caller. Process
        startup errors propagate unchanged.
        """
        return subprocess.run(
            ["git", "--literal-pathspecs", "-C", str(self.path), *arguments],
            capture_output=True,
            text=True,
            check=check,
            env=None if env is None else {**os.environ, **env},
        )

    def exists(self) -> bool:
        """Return whether the path is a directory inside a Git worktree.

        A failed Git probe returns False; process startup errors still propagate.
        """
        if not self.path.is_dir():
            return False
        result = self.run("rev-parse", "--is-inside-work-tree", check=False)
        return result.returncode == 0 and result.stdout.strip() == "true"

    def init(self) -> None:
        """Run git init at the path only when no worktree is detected.

        The directory must already be usable by Git; command failures propagate.
        """
        if not self.exists():
            self.run("init")

    def head(self, *, before_wait=None) -> str | None:
        """Return the resolved HEAD identifier, or None for a confirmed unborn ref.

        Call ``before_wait`` without arguments after starting the HEAD probe and
        before collecting its output. A non-worktree raises invalid_project;
        other resolution failures raise OSError chained from CalledProcessError
        with the probe's stdout and stderr. A failed resolution alone is never
        treated as an unborn branch. Startup and callback errors propagate.
        """
        with subprocess.Popen(
            [
                "git",
                "--literal-pathspecs",
                "-C",
                str(self.path),
                "rev-parse",
                "--is-inside-work-tree",
                "--verify",
                "HEAD",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        ) as process:
            if before_wait is not None:
                before_wait()
            output, error = process.communicate()
        lines = output.splitlines()
        if not lines or lines[0] != "true":
            raise RuleViolation("invalid_project", "Project is not in a git worktree")
        if process.returncode == 0:
            return lines[1]
        # An unborn branch is valid, but a failed resolution alone does not
        # establish absence: unreadable or malformed refs can fail identically.
        if process.returncode == 128:
            symbolic = self.run("symbolic-ref", "--quiet", "HEAD", check=False)
            reference = symbolic.stdout.strip()
            if symbolic.returncode == 0 and reference:
                references = self.run(
                    "for-each-ref", "--format=%(refname)", "--", reference, check=False
                )
                if (
                    references.returncode == 0
                    and not references.stderr.strip()
                    and reference not in references.stdout.splitlines()
                ):
                    return None
        failure = subprocess.CalledProcessError(
            process.returncode, process.args, output=output, stderr=error
        )
        raise OSError(
            f"Git HEAD resolution failed (status {process.returncode}): {error.strip()}"
        ) from failure

    def check_clock(self, now: datetime) -> None:
        """Validate write-time precision and zone, then compare with HEAD's clock.

        Invalid timestamps raise a schema RuleViolation, even without commits.
        A time earlier than HEAD's committer time raises clock_skew. Missing
        worktrees and confirmed unborn branches have no HEAD clock to compare;
        Git resolution and command errors otherwise propagate.
        """
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
        """Build a human Actor from Git's effective user.name and user.email.

        Both stripped configuration values are required; missing or empty values
        raise a schema RuleViolation. Process startup errors propagate.
        """
        name = self.run("config", "user.name", check=False).stdout.strip()
        email = self.run("config", "user.email", check=False).stdout.strip()
        if not name or not email:
            raise RuleViolation("schema", "Git user.name and user.email are required")
        return Actor("human", name, email)

    def commit(
        self, paths: list[str], message: str, actor: Actor, now: datetime
    ) -> str:
        """Stage and commit the literal paths, returning the new HEAD identifier.

        Commit working-tree versions of only these paths, retaining unrelated
        staged changes. Human identity comes from Git config; other actors use
        their name and a generated agents.researchgraph email. Both author and
        committer use that identity and ``now``. Timestamp, identity, and Git
        errors propagate; staging can already have occurred when commit fails.
        """
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
