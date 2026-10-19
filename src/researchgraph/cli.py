"""Command-line interface for researchgraph."""

import json
from datetime import UTC, datetime
from pathlib import Path

import click

from researchgraph import __version__
from researchgraph.model import Actor, RuleViolation, parse_timestamp
from researchgraph.project import Project
from researchgraph.store.git import Git


def _common_options(function):
    for option in reversed(
        [
            click.option("--project", type=click.Path(path_type=Path), default=None),
            click.option("--json", "json_output", is_flag=True, default=None),
            click.option("--read-only", is_flag=True, default=None),
            click.option("--as-agent", default=None),
            click.option("--role", default=None),
            click.option("--now", default=None),
            click.option("--commit/--no-commit", default=None),
        ]
    ):
        function = option(function)
    return function


@click.group()
@click.version_option(__version__, prog_name="rg")
@_common_options
@click.pass_context
def main(ctx, **options):
    """Store and inspect a research graph in a git repository."""
    ctx.obj = options


def _actor(path, agent):
    if agent is not None:
        parts = agent.split(":")
        if len(parts) > 3 or not parts[0]:
            raise RuleViolation("schema", "Agent must be NAME[:MODEL[:VERSION]]")
        parts += [None] * (3 - len(parts))
        return Actor("agent", parts[0], model=parts[1], version=parts[2])
    # New projects need identity before their directory/repository exists.
    identity_path = path
    while not identity_path.is_dir() and identity_path != identity_path.parent:
        identity_path = identity_path.parent
    return Git(identity_path).human_actor()


@main.command("init")
@click.argument("path", required=False, type=click.Path(path_type=Path))
@click.option("--profile", default="research", show_default=True)
@_common_options
@click.pass_context
def init_command(ctx, path, profile, **local_options):
    """Initialise PATH (or --project) with a pinned profile."""
    options = {
        key: local_options[key] if local_options[key] is not None else value
        for key, value in ctx.obj.items()
    }
    root = (path or options["project"] or Path.cwd()).expanduser().resolve()
    try:
        try:
            now = (
                parse_timestamp(options["now"])
                if options["now"]
                else datetime.now(UTC).replace(microsecond=0)
            )
        except (TypeError, ValueError) as exc:
            raise RuleViolation("schema", f"Invalid --now: {exc}") from exc
        actor = None if options["read_only"] else _actor(root, options["as_agent"])
        project = Project.init(
            root,
            profile=profile,
            actor=actor,
            role=options["role"],
            now=now,
            commit=options["commit"] is not False,
            read_only=bool(options["read_only"]),
        )
        result = {
            "path": str(project.path),
            "profile": project.profile_name,
            "version": project.profile_version,
        }
        click.echo(
            json.dumps(result)
            if options["json_output"]
            else f"Initialised {project.path} ({project.profile_name} {project.profile_version})"
        )
    except RuleViolation as exc:
        click.echo(
            json.dumps({"rule": exc.rule, "message": exc.message, "ids": exc.ids}),
            err=True,
        )
        ctx.exit(3 if exc.rule in ("project_not_found", "invalid_project") else 2)
