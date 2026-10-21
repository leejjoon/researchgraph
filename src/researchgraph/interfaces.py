"""Shared thin transport dispatch with launch-fixed authority and project root."""

from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path

from researchgraph.model import RuleViolation, parse_timestamp
from researchgraph.project import Project
from researchgraph.registry import OPERATIONS

_BY_NAME = {operation["name"]: operation for operation in OPERATIONS}


def _prepare(name, arguments, *, actor, role=None, read_only=False):
    operation = _BY_NAME.get(name)
    if operation is None:
        raise RuleViolation("schema", f"Unknown operation: {name}")
    write = operation["kind"] == "write"
    if read_only and write:
        raise RuleViolation("read_only", "Project is read-only")
    if not isinstance(arguments, dict):
        raise RuleViolation("schema", "Operation arguments must be an object")
    values = dict(arguments)
    allowed = {arg["name"] for arg in operation["arguments"]} | {"now"}
    if write:
        allowed.add("commit")
    if values.keys() - allowed:
        raise RuleViolation("schema", "Unknown or authority-overriding arguments")
    if any(
        arg["required"] and arg["name"] not in values for arg in operation["arguments"]
    ):
        raise RuleViolation("schema", "Missing required operation argument")
    timestamp = values.pop("now", None)
    try:
        now = (
            parse_timestamp(timestamp)
            if timestamp is not None
            else datetime.now(UTC).replace(microsecond=0)
        )
    except (TypeError, ValueError) as exc:
        raise RuleViolation("schema", f"Invalid now: {exc}") from exc
    context = {"actor": actor, "role": role, "now": now}
    if write:
        commit = values.pop("commit", True)
        if type(commit) is not bool:
            raise RuleViolation("schema", "commit must be a boolean")
        context["commit"] = commit
    return operation, values, context


def _dispatch(root, operation, values, context, *, read_only=False, project=None):
    name = operation["name"]
    root = Path(root).expanduser().resolve()
    if name in {"init", "import"}:
        destination = values.pop("path", None)
        if destination is not None and Path(destination).expanduser().resolve() != root:
            raise RuleViolation(
                "schema", "Destination cannot override the selected project"
            )
        if name == "init":
            owner = Project.init(root, **values, **context)
        else:
            owner = Project.import_(values["archive"], root, **context)
        with owner:
            return {
                "path": str(owner.path),
                "profile": owner.profile_name,
                "version": owner.profile_version,
            }
    positional, keyword = [], {}
    for arg in operation["arguments"]:
        if arg["repeatable"] and arg["name"] not in values:
            values[arg["name"]] = []
        if arg["name"] in values:
            if arg["options"]:
                keyword[arg["name"]] = values[arg["name"]]
            else:
                positional.append(values[arg["name"]])
    with (
        nullcontext(project)
        if project is not None
        else Project.open(root, read_only=read_only)
    ) as owner:
        method = "profile_info" if name == "profile" else name
        return getattr(owner, method)(*positional, **keyword, **context)


def execute(root, name, arguments, *, actor, role=None, read_only=False):
    """Dispatch one registry operation and close its owned Project on every exit.

    Authority stays outside arguments. Only transport names, presence and clock
    encoding are checked here; public methods retain business validation and SQL.
    Omission is preserved except Click's empty repeated collectors. HTTP uses the
    same preparation/dispatch with its serialized lifespan-owned Project.
    """
    operation, values, context = _prepare(
        name, arguments, actor=actor, role=role, read_only=read_only
    )
    return _dispatch(root, operation, values, context, read_only=read_only)
