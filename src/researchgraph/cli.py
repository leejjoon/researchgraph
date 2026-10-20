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


@main.command("export")
@click.argument("out", type=click.Path(path_type=Path))
@click.option("--include-artifacts", is_flag=True)
@click.option("--since", default=None)
@click.option("--as-of", default=None)
@_common_options
@click.pass_context
def export_command(ctx, out, include_artifacts, since, as_of, **local_options):
    """Export the graph to OUT, optionally including artifact files."""
    options = _options(ctx, local_options)
    try:
        root = _project_root(options["project"])
        now = _now(options["now"])
        with Project.open(root, read_only=bool(options["read_only"])) as project:
            result = project.export(
                out,
                include_artifacts=include_artifacts,
                since=since,
                actor=_actor(root, options["as_agent"]),
                role=options["role"],
                now=now,
                as_of=as_of,
            )
        click.echo(
            json.dumps(result, ensure_ascii=False)
            if options["json_output"]
            else result["path"]
        )
    except RuleViolation as exc:
        _error(ctx, exc)


@main.command("import")
@click.argument("archive", type=click.Path(path_type=Path))
@click.argument("path", required=False, type=click.Path(path_type=Path))
@_common_options
@click.pass_context
def import_command(ctx, archive, path, **local_options):
    """Import ARCHIVE into PATH (or --project)."""
    options = _options(ctx, local_options)
    root = (path or options["project"] or Path.cwd()).expanduser().resolve()
    try:
        if options["read_only"]:
            raise RuleViolation("read_only", "Project is read-only")
        now = _now(options["now"])
        actor = _actor(root, options["as_agent"])
        with Project.import_(
            archive,
            root,
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
            else f"Imported {result['path']} ({result['profile']} {result['version']})"
        )
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


@main.command("claim")
@click.argument("node")
@click.option("--lease-seconds", type=int, default=7200, show_default=True)
@_common_options
@click.pass_context
def claim_command(ctx, node, lease_seconds, **local_options):
    """Claim ready NODE for a bounded lease."""
    _run_write(
        ctx,
        local_options,
        lambda project, context: project.claim(
            node, lease_seconds=lease_seconds, **context
        ),
    )


@main.command("renew")
@click.argument("node")
@click.option("--lease-seconds", type=int, default=7200, show_default=True)
@_common_options
@click.pass_context
def renew_command(ctx, node, lease_seconds, **local_options):
    """Extend NODE's lease without shortening its remaining duration."""
    _run_write(
        ctx,
        local_options,
        lambda project, context: project.renew(
            node, lease_seconds=lease_seconds, **context
        ),
    )


@main.command("release")
@click.argument("node")
@click.option("--note", default=None)
@click.option("--usage", default=None)
@_common_options
@click.pass_context
def release_command(ctx, node, note, usage, **local_options):
    """Return running NODE to active, adding any metered usage."""
    _run_write(
        ctx,
        local_options,
        lambda project, context: project.release(
            node, note=note, usage=_json_argument(usage, "--usage"), **context
        ),
    )


@main.command("complete")
@click.argument("node")
@click.argument("summary")
@click.option("--artifact", "artifacts", multiple=True)
@click.option("--usage", default=None)
@_common_options
@click.pass_context
def complete_command(ctx, node, summary, artifacts, usage, **local_options):
    """Complete running NODE, recording SUMMARY and optional artifact files."""
    _run_write(
        ctx,
        local_options,
        lambda project, context: project.complete(
            node,
            summary,
            artifacts=list(artifacts),
            usage=_json_argument(usage, "--usage"),
            **context,
        ),
    )


@main.command("fail")
@click.argument("node")
@click.argument("summary")
@click.option("--step", default=None)
@click.option("--check", default=None)
@click.option("--measured", default=None)
@click.option("--error", default=None)
@click.option("--usage", default=None)
@_common_options
@click.pass_context
def fail_command(
    ctx, node, summary, step, check, measured, error, usage, **local_options
):
    """Fail running NODE and create a Failure explaining SUMMARY."""
    _run_write(
        ctx,
        local_options,
        lambda project, context: project.fail(
            node,
            summary,
            step=step,
            check=check,
            measured=measured,
            error=error,
            usage=_json_argument(usage, "--usage"),
            **context,
        ),
    )


@main.command("expand")
@click.argument("targets", nargs=-1)
@click.option("--hint", default=None)
@_common_options
@click.pass_context
def expand_command(ctx, targets, hint, **local_options):
    """Create an Expansion task for TARGETS without executing it."""
    _run_write(
        ctx,
        local_options,
        lambda project, context: project.expand(list(targets), hint=hint, **context),
    )


@main.command("budget")
@click.argument("node")
@click.option("--tokens", type=int, default=None)
@click.option("--compute-seconds", type=int, default=None)
@_common_options
@click.pass_context
def budget_command(ctx, node, tokens, compute_seconds, **local_options):
    """Set NODE's budget to the supplied dimensions."""
    _run_write(
        ctx,
        local_options,
        lambda project, context: project.budget(
            node, tokens=tokens, compute_seconds=compute_seconds, **context
        ),
    )


@main.command("tag")
@click.argument("node")
@click.option("--add", multiple=True)
@click.option("--remove", multiple=True)
@click.option("--priority", default=None)
@_common_options
@click.pass_context
def tag_command(ctx, node, add, remove, priority, **local_options):
    """Edit NODE's tags and optionally set or clear its priority."""
    _run_write(
        ctx,
        local_options,
        lambda project, context: project.tag(
            node,
            add=list(add),
            remove=list(remove),
            **(
                {"priority": _json_argument(priority, "--priority")}
                if priority is not None
                else {}
            ),
            **context,
        ),
    )


@main.command("attach")
@click.argument("node")
@click.option("--artifact", "artifacts", multiple=True)
@_common_options
@click.pass_context
def attach_command(ctx, node, artifacts, **local_options):
    """Append artifact files to NODE."""
    _run_write(
        ctx,
        local_options,
        lambda project, context: project.attach(node, list(artifacts), **context),
    )


def _table_value(value):
    if isinstance(value, str):
        return value.replace("\t", "\\t").replace("\r", "\\r").replace("\n", "\\n")
    return json.dumps(value, ensure_ascii=False)


def _read_table(rows):
    columns = list(dict.fromkeys(key for row in rows for key in row))
    if not columns:
        click.echo("RESULT\tCOUNT")
        click.echo("items\t0")
        return
    click.echo("\t".join(key.upper() for key in columns))
    for row in rows:
        click.echo(
            "\t".join(_table_value(row[key]) if key in row else "" for key in columns)
        )


def _read_result(result, json_output):
    if json_output:
        click.echo(json.dumps(result, ensure_ascii=False))
    elif isinstance(result, list):
        _read_table(result)
    elif set(result) == {"items"}:
        _read_table(result["items"])
    elif set(result) == {"rows", "columns"}:
        columns = result["columns"]
        click.echo(
            "\t".join(
                ["SOURCE", *(column["id"] for column in columns), "NON_DISCRIMINATING"]
            )
        )
        for row in result["rows"]:
            click.echo(
                "\t".join(
                    [
                        row["source"]["id"],
                        *(
                            _table_value(row["cells"][column["id"]])
                            for column in columns
                        ),
                        _table_value(row["non_discriminating"]),
                    ]
                )
            )
    elif set(result) == {"nodes", "status_counts", "usage"}:
        _read_table(result["nodes"])
        _read_table(
            [
                {"status": key, "count": value}
                for key, value in result["status_counts"].items()
            ]
        )
        _read_table([result["usage"]])
    else:
        _read_table([{"field": key, "value": value} for key, value in result.items()])


def _run_read(ctx, local_options, as_of, operation):
    options = _options(ctx, local_options)
    try:
        root = _project_root(options["project"])
        now = _now(options["now"])
        with Project.open(root, read_only=bool(options["read_only"])) as project:
            result = operation(
                project,
                {
                    "actor": _actor(root, options["as_agent"]),
                    "role": options["role"],
                    "now": now,
                    "as_of": as_of,
                },
            )
        _read_result(result, options["json_output"])
    except RuleViolation as exc:
        _error(ctx, exc)


@main.command("ready")
@click.option("--as-of", default=None)
@_common_options
@click.pass_context
def ready_command(ctx, as_of, **local_options):
    """List claimable, unblocked nodes in priority order."""
    _run_read(
        ctx, local_options, as_of, lambda project, context: project.ready(**context)
    )


@main.command("blocked")
@click.option("--as-of", default=None)
@_common_options
@click.pass_context
def blocked_command(ctx, as_of, **local_options):
    """List blocked nodes with their blocker IDs."""
    _run_read(
        ctx, local_options, as_of, lambda project, context: project.blocked(**context)
    )


@main.command("queue")
@click.option("--as-of", default=None)
@_common_options
@click.pass_context
def queue_command(ctx, as_of, **local_options):
    """List proposed nodes awaiting approval, oldest first."""
    _run_read(
        ctx, local_options, as_of, lambda project, context: project.queue(**context)
    )


@main.command("stale")
@click.option("--as-of", default=None)
@_common_options
@click.pass_context
def stale_command(ctx, as_of, **local_options):
    """List stale Claims with their replacement-head IDs."""
    _run_read(
        ctx, local_options, as_of, lambda project, context: project.stale(**context)
    )


@main.command("digest")
@click.option("--as-of", default=None)
@_common_options
@click.pass_context
def digest_command(ctx, as_of, **local_options):
    """Summarize running, ready, blocked, awaiting, failed and Claim groups."""
    _run_read(
        ctx, local_options, as_of, lambda project, context: project.digest(**context)
    )


@main.command("show")
@click.argument("id")
@click.option("--as-of", default=None)
@_common_options
@click.pass_context
def show_command(ctx, id, as_of, **local_options):
    """Show node or edge ID, including relations and derived flags."""
    _run_read(
        ctx, local_options, as_of, lambda project, context: project.show(id, **context)
    )


@main.command("history")
@click.argument("id")
@click.option("--as-of", default=None)
@_common_options
@click.pass_context
def history_command(ctx, id, as_of, **local_options):
    """List events and commits across ID's supersession family."""
    _run_read(
        ctx,
        local_options,
        as_of,
        lambda project, context: project.history(id, **context),
    )


@main.command("evidence")
@click.argument("claim")
@click.option("--as-of", default=None)
@_common_options
@click.pass_context
def evidence_command(ctx, claim, as_of, **local_options):
    """List CLAIM's transitive evidence, excluding CLAIM itself."""
    _run_read(
        ctx,
        local_options,
        as_of,
        lambda project, context: project.evidence(claim, **context),
    )


@main.command("weakest")
@click.argument("claim")
@click.option("--as-of", default=None)
@_common_options
@click.pass_context
def weakest_command(ctx, claim, as_of, **local_options):
    """Rank CLAIM's cited evidence by supersession and evidence quality."""
    _run_read(
        ctx,
        local_options,
        as_of,
        lambda project, context: project.weakest(claim, **context),
    )


@main.command("alternatives")
@click.argument("hypothesis")
@click.option("--as-of", default=None)
@_common_options
@click.pass_context
def alternatives_command(ctx, hypothesis, as_of, **local_options):
    """List other hypotheses answering HYPOTHESIS's questions."""
    _run_read(
        ctx,
        local_options,
        as_of,
        lambda project, context: project.alternatives(hypothesis, **context),
    )


@main.command("predictions")
@click.argument("hypothesis")
@click.option("--as-of", default=None)
@_common_options
@click.pass_context
def predictions_command(ctx, hypothesis, as_of, **local_options):
    """List live tests of HYPOTHESIS with their expected outcomes."""
    _run_read(
        ctx,
        local_options,
        as_of,
        lambda project, context: project.predictions(hypothesis, **context),
    )


@main.command("matrix")
@click.argument("question")
@click.option("--as-of", default=None)
@_common_options
@click.pass_context
def matrix_command(ctx, question, as_of, **local_options):
    """Show predictions against QUESTION's hypothesis group."""
    _run_read(
        ctx,
        local_options,
        as_of,
        lambda project, context: project.matrix(question, **context),
    )


@main.command("notes")
@click.argument("id")
@click.option("--as-of", default=None)
@_common_options
@click.pass_context
def notes_command(ctx, id, as_of, **local_options):
    """List Notes about ID, newest first."""
    _run_read(
        ctx, local_options, as_of, lambda project, context: project.notes(id, **context)
    )


@main.command("subtree")
@click.argument("id")
@click.option("--as-of", default=None)
@_common_options
@click.pass_context
def subtree_command(ctx, id, as_of, **local_options):
    """Show ID's live subtree, status counts and summed usage."""
    _run_read(
        ctx,
        local_options,
        as_of,
        lambda project, context: project.subtree(id, **context),
    )


@main.command("since")
@click.argument("time")
@click.option("--as-of", default=None)
@_common_options
@click.pass_context
def since_command(ctx, time, as_of, **local_options):
    """List nodes and edges changed strictly after TIME."""
    _run_read(
        ctx,
        local_options,
        as_of,
        lambda project, context: project.since(time, **context),
    )


@main.command("where")
@click.option("--type", default=None)
@click.option("--status", default=None)
@click.option("--author", default=None)
@click.option(
    "--field",
    multiple=True,
    help="Repeat K=V, splitting at the first =; identical repeats are allowed, conflicts invalid.",
)
@click.option("--tag", default=None)
@click.option("--text", default=None)
@click.option("--over-budget", is_flag=True)
@click.option("--as-of", default=None)
@_common_options
@click.pass_context
def where_command(
    ctx, type, status, author, field, tag, text, over_budget, as_of, **local_options
):
    """List nodes matching every supplied filter; repeat --field K=V."""

    def query(project, context):
        return project.where(
            type=type,
            status=status,
            author=author,
            field=field,
            tag=tag,
            text=text,
            over_budget=over_budget,
            **context,
        )

    _run_read(ctx, local_options, as_of, query)
