"""Shared thin transport dispatch with launch-fixed authority and project root."""

from datetime import UTC, datetime
from pathlib import Path

from researchgraph.model import RuleViolation, parse_timestamp
from researchgraph.project import Project
from researchgraph.registry import OPERATIONS

_BY_NAME = {operation["name"]: operation for operation in OPERATIONS}


def execute(root, name, arguments, *, actor, role=None, read_only=False):
    """Dispatch one registry operation; return its ordinary public JSON data.

    Fix root/actor/role/read_only outside arguments. Check transport argument
    names and missing parameters only; core methods retain business validation,
    permission/error precedence and all source/index handling. Each call closes
    its Project, including failures. Preserve omitted values, except Click's
    empty repeated collectors, which map to empty arrays.
    """
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
    with Project.open(root, read_only=read_only) as project:
        method = "profile_info" if name == "profile" else name
        return getattr(project, method)(*positional, **keyword, **context)
