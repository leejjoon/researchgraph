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


def _options(ctx, local_options):
    return {
        key: local_options[key] if local_options[key] is not None else value
        for key, value in ctx.obj.items()
    }


def _now(value):
    try:
        return (
            parse_timestamp(value)
            if value is not None
            else datetime.now(UTC).replace(microsecond=0)
        )
    except (TypeError, ValueError) as exc:
        raise RuleViolation("schema", f"Invalid --now: {exc}") from exc


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


def _error(ctx, error):
    click.echo(
        json.dumps(
            {"rule": error.rule, "message": error.message, "ids": error.ids},
            ensure_ascii=False,
        ),
        err=True,
    )
    ctx.exit(3 if error.rule in ("project_not_found", "invalid_project") else 2)


def _project_root(path):
    if path is not None:
        return path.expanduser().resolve()
    current = Path.cwd()
    for candidate in (current, *current.parents):
        if (candidate / ".researchgraph/profile.yaml").is_file():
            return candidate
    raise RuleViolation("project_not_found", f"Project not found from: {current}")


def _json_argument(value, option):
    if value is None:
        return None
    try:
        return json.loads(value)
    except (TypeError, ValueError) as exc:
        raise RuleViolation("schema", f"Invalid {option} JSON: {exc}") from exc


def _write_result(result, json_output):
    if json_output:
        click.echo(json.dumps(result, ensure_ascii=False))
    elif "from" in result:
        click.echo("ID\tTYPE\tFROM\tTO")
        click.echo(
            f"{result['id']}\t{result['type']}\t{result['from']}\t{result['to']}"
        )
    else:
        click.echo("ID\tTYPE\tSTATUS\tTITLE")
        click.echo(
            f"{result['id']}\t{result['type']}\t{result['status']}\t{result['title']}"
        )


def _run_write(ctx, local_options, operation):
    options = _options(ctx, local_options)
    try:
        if options["read_only"]:
            raise RuleViolation("read_only", "Project is read-only")
        root = _project_root(options["project"])
        now = _now(options["now"])
        with Project.open(root) as project:
            project._git.check_clock(now)
            context = {
                "actor": _actor(root, options["as_agent"]),
                "role": options["role"],
                "now": now,
                "commit": options["commit"] is not False,
            }
            result = operation(project, context)
        _write_result(result, options["json_output"])
    except RuleViolation as exc:
        _error(ctx, exc)


@main.command("init")
@click.argument("path", required=False, type=click.Path(path_type=Path))
@click.option("--profile", default="research", show_default=True)
@_common_options
@click.pass_context
def init_command(ctx, path, profile, **local_options):
    """Initialise PATH (or --project) with a pinned profile."""
    options = _options(ctx, local_options)
    root = (path or options["project"] or Path.cwd()).expanduser().resolve()
    try:
        if options["read_only"]:
            raise RuleViolation("read_only", "Project is read-only")
        now = _now(options["now"])
        actor = _actor(root, options["as_agent"])
        with Project.init(
            root,
            profile=profile,
            actor=actor,
            role=options["role"],
            now=now,
            commit=options["commit"] is not False,
            read_only=bool(options["read_only"]),
        ) as project:
            result = {
                "path": str(project.path),
                "profile": project.profile_name,
                "version": project.profile_version,
            }
        click.echo(
            json.dumps(result, ensure_ascii=False)
            if options["json_output"]
            else f"Initialised {project.path} ({project.profile_name} {project.profile_version})"
        )
    except RuleViolation as exc:
        _error(ctx, exc)


@main.command("propose")
@click.argument("type")
@click.argument("title")
@click.option("--body", default="")
@click.option("--fields", default=None)
@click.option("--edge", "edges", multiple=True)
@_common_options
@click.pass_context
def propose_command(ctx, type, title, body, fields, edges, **local_options):
    """Propose TYPE TITLE, with optional incident --edge JSON objects."""
    _run_write(
        ctx,
        local_options,
        lambda project, context: project.propose(
            type,
            title,
            body=body,
            fields=_json_argument(fields, "--fields"),
            edges=[_json_argument(edge, "--edge") for edge in edges],
            **context,
        ),
    )


@main.command("link")
@click.argument("type")
@click.argument("from_")
@click.argument("to")
@click.option("--fields", default=None)
@_common_options
@click.pass_context
def link_command(ctx, type, from_, to, fields, **local_options):
    """Link TYPE FROM TO between existing nodes."""
    _run_write(
        ctx,
        local_options,
        lambda project, context: project.link(
            type, from_, to, fields=_json_argument(fields, "--fields"), **context
        ),
    )


@main.command("approve")
@click.argument("node")
@click.option("--what", default="activate", show_default=True)
@click.option("--note", default=None)
@_common_options
@click.pass_context
def approve_command(ctx, node, what, note, **local_options):
    """Approve NODE for activation."""
    _run_write(
        ctx,
        local_options,
        lambda project, context: project.approve(node, what=what, note=note, **context),
    )


@main.command("reject")
@click.argument("node")
@click.argument("reason")
@_common_options
@click.pass_context
def reject_command(ctx, node, reason, **local_options):
    """Reject NODE with REASON, retiring its live incident edges."""
    _run_write(
        ctx,
        local_options,
        lambda project, context: project.reject(node, reason, **context),
    )


@main.command("supersede")
@click.argument("node")
@click.option("--title", default=None)
@click.option("--body", default=None)
@click.option("--fields", default=None)
@click.option("--inherit-edges/--no-inherit-edges", default=True, show_default=True)
@click.option("--instruction", default=None)
@_common_options
@click.pass_context
def supersede_command(
    ctx, node, title, body, fields, inherit_edges, instruction, **local_options
):
    """Replace NODE, transferring eligible edges unless --no-inherit-edges."""
    _run_write(
        ctx,
        local_options,
        lambda project, context: project.supersede(
            node,
            title=title,
            body=body,
            fields=_json_argument(fields, "--fields"),
            inherit_edges=inherit_edges,
            instruction=instruction,
            **context,
        ),
    )


@main.command("instruct")
@click.argument("node")
@click.argument("text")
@_common_options
@click.pass_context
def instruct_command(ctx, node, text, **local_options):
    """Record TEXT as an Instruction applying to NODE."""
    _run_write(
        ctx,
        local_options,
        lambda project, context: project.instruct(node, text, **context),
    )


@main.command("retire")
@click.argument("node")
@click.argument("reason")
@_common_options
@click.pass_context
def retire_command(ctx, node, reason, **local_options):
    """Retire NODE without replacement, recording REASON."""
    _run_write(
        ctx,
        local_options,
        lambda project, context: project.retire(node, reason, **context),
    )
