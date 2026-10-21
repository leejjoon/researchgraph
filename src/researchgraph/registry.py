"""Data-only graph operation catalogue, cross-checked against the installed CLI.

Arguments describe logical JSON values, not Click's JSON-encoded strings.
Common CLI authority/output options are separate from operation arguments so
transport adapters cannot inadvertently offer them as identity overrides.
Absence of a default is meaningful: consumers must preserve omitted arguments,
especially tag.priority, where explicit null clears an existing priority.
"""

_MISSING = object()


def _arg(
    name,
    type_="string",
    *,
    required=False,
    default=_MISSING,
    repeatable=False,
    option=False,
    cli_name=None,
    encoding="value",
    nullable=False,
    options=None,
):
    schema = {"type": [type_, "null"] if nullable else type_}
    if repeatable:
        schema = {"type": "array", "items": schema}
    result = {
        "name": name,
        "cli_name": cli_name or name,
        "schema": schema,
        "required": required,
        "repeatable": repeatable,
        "encoding": encoding,
        "options": options
        if options is not None
        else ["--" + name.replace("_", "-")]
        if option
        else [],
    }
    if default is not _MISSING:
        result["default"] = default
    return result


def _op(name, kind, arguments, result):
    arguments = list(arguments)
    if kind == "read":
        arguments.append(_arg("as_of", option=True, default=None, nullable=True))
    return {"name": name, "kind": kind, "arguments": arguments, "result": result}


COMMON_OPTIONS = (
    _arg("project", option=True, default=None, nullable=True),
    _arg("json_output", "boolean", default=None, nullable=True, options=["--json"]),
    _arg("read_only", "boolean", option=True, default=None, nullable=True),
    _arg("as_agent", option=True, default=None, nullable=True),
    _arg("role", option=True, default=None, nullable=True),
    _arg("now", option=True, default=None, nullable=True),
    _arg(
        "commit",
        "boolean",
        default=None,
        nullable=True,
        options=["--commit", "--no-commit"],
    ),
)

OPERATIONS = (
    _op(
        "init",
        "write",
        [
            _arg("path", default=None, nullable=True),
            _arg("profile", option=True, default="research"),
        ],
        "Object with project path, pinned profile name and version.",
    ),
    _op(
        "propose",
        "write",
        [
            _arg("type", required=True),
            _arg("title", required=True),
            _arg("body", option=True, default=""),
            _arg(
                "fields",
                "object",
                option=True,
                default=None,
                nullable=True,
                encoding="json",
            ),
            _arg(
                "edges",
                "object",
                repeatable=True,
                default=[],
                options=["--edge"],
                encoding="json",
            ),
        ],
        "Created node object using canonical node key names.",
    ),
    _op(
        "link",
        "write",
        [
            _arg("type", required=True),
            _arg("from", required=True, cli_name="from_"),
            _arg("to", required=True),
            _arg(
                "fields",
                "object",
                option=True,
                default=None,
                nullable=True,
                encoding="json",
            ),
        ],
        "Created edge object using canonical edge key names.",
    ),
    _op(
        "approve",
        "write",
        [
            _arg("node", required=True),
            _arg("what", option=True, default="activate"),
            _arg("note", option=True, default=None, nullable=True),
        ],
        "Node object with appended approval and resulting activation status.",
    ),
    _op(
        "reject",
        "write",
        [_arg("node", required=True), _arg("reason", required=True)],
        "Retired node object; live incident edges are retired by the operation.",
    ),
    _op(
        "supersede",
        "write",
        [
            _arg("node", required=True),
            _arg("title", option=True, default=None, nullable=True),
            _arg("body", option=True, default=None, nullable=True),
            _arg(
                "fields",
                "object",
                option=True,
                default=None,
                nullable=True,
                encoding="json",
            ),
            _arg(
                "inherit_edges",
                "boolean",
                default=True,
                options=["--inherit-edges", "--no-inherit-edges"],
            ),
            _arg("instruction", option=True, default=None, nullable=True),
        ],
        "Replacement node object; old node retires, optional Instruction is separate.",
    ),
    _op(
        "instruct",
        "write",
        [_arg("node", required=True), _arg("text", required=True)],
        "Created Instruction node object.",
    ),
    _op(
        "claim",
        "write",
        [
            _arg("node", required=True),
            _arg("lease_seconds", "integer", option=True, default=7200),
        ],
        "Running node object with lease and claim approval record.",
    ),
    _op(
        "renew",
        "write",
        [
            _arg("node", required=True),
            _arg("lease_seconds", "integer", option=True, default=7200),
        ],
        "Running node object with extended lease and renew record.",
    ),
    _op(
        "release",
        "write",
        [
            _arg("node", required=True),
            _arg("note", option=True, default=None, nullable=True),
            _arg(
                "usage",
                "object",
                option=True,
                default=None,
                nullable=True,
                encoding="json",
            ),
        ],
        "Active node object with cleared lease and accumulated usage.",
    ),
    _op(
        "complete",
        "write",
        [
            _arg("node", required=True),
            _arg("summary", required=True),
            _arg("artifacts", repeatable=True, default=[], options=["--artifact"]),
            _arg(
                "usage",
                "object",
                option=True,
                default=None,
                nullable=True,
                encoding="json",
            ),
        ],
        "Done node object with cleared lease and appended artifact records.",
    ),
    _op(
        "fail",
        "write",
        [
            _arg("node", required=True),
            _arg("summary", required=True),
            _arg("step", option=True, default=None, nullable=True),
            _arg("check", option=True, default=None, nullable=True),
            _arg("measured", option=True, default=None, nullable=True),
            _arg("error", option=True, default=None, nullable=True),
            _arg(
                "usage",
                "object",
                option=True,
                default=None,
                nullable=True,
                encoding="json",
            ),
        ],
        "Failed node object; its explanatory Failure is created separately.",
    ),
    _op(
        "expand",
        "write",
        [
            _arg("nodes", repeatable=True, default=[], cli_name="targets"),
            _arg("hint", option=True, default=None, nullable=True),
        ],
        "Created Expansion Task object; no work is executed.",
    ),
    _op(
        "budget",
        "write",
        [
            _arg("node", required=True),
            _arg("tokens", "integer", option=True, default=None, nullable=True),
            _arg(
                "compute_seconds", "integer", option=True, default=None, nullable=True
            ),
        ],
        "Node object with replaced budget dimensions and appended budget record.",
    ),
    _op(
        "retire",
        "write",
        [_arg("node", required=True), _arg("reason", required=True)],
        "Retired node object without a replacement.",
    ),
    _op(
        "attach",
        "write",
        [
            _arg("node", required=True),
            _arg("artifacts", repeatable=True, default=[], options=["--artifact"]),
        ],
        "Node object with appended registered artifact records.",
    ),
    _op(
        "tag",
        "write",
        [
            _arg("node", required=True),
            _arg("add", option=True, repeatable=True, default=[]),
            _arg("remove", option=True, repeatable=True, default=[]),
            _arg("priority", "integer", option=True, nullable=True, encoding="json"),
        ],
        "Node object with tags/priority changed; omitted priority preserves, null clears.",
    ),
    _op(
        "import",
        "write",
        [
            _arg("archive", required=True),
            _arg("path", default=None, nullable=True),
        ],
        "Object with destination path, pinned profile name and version.",
    ),
    _op(
        "show",
        "read",
        [_arg("id", required=True)],
        "Edge object, or node object plus incident edges, instructions, pins, flags and warnings.",
    ),
    _op(
        "ready",
        "read",
        [],
        "Object with items: claimable ready nodes in priority/created/id order.",
    ),
    _op(
        "blocked",
        "read",
        [],
        "Object with items: blocked nodes, each with blocking ID array.",
    ),
    _op(
        "queue",
        "read",
        [],
        "Object with items: proposed nodes with matching gate metadata and approvals.",
    ),
    _op(
        "history",
        "read",
        [_arg("id", required=True)],
        "Array of interval/approval/artifact/supersedes events with introducing commits.",
    ),
    _op(
        "evidence",
        "read",
        [_arg("claim", required=True)],
        "Object with items: transitive non-retired cites/derived-from inputs, excluding Claim.",
    ),
    _op(
        "stale",
        "read",
        [],
        "Object with items: stale Claims, each with responsible replacement-head ID array.",
    ),
    _op(
        "weakest",
        "read",
        [_arg("claim", required=True)],
        "Array of direct citation targets ranked by superseded input, runs, held_out and ID.",
    ),
    _op(
        "predictions",
        "read",
        [_arg("hypothesis", required=True)],
        "Array of tests edge objects with source, expected, discriminating and status.",
    ),
    _op(
        "matrix",
        "read",
        [_arg("question", required=True)],
        "Object with rows and columns: source/cells/non_discriminating rows and hypotheses.",
    ),
    _op(
        "alternatives",
        "read",
        [_arg("hypothesis", required=True)],
        "Array of alternative Hypothesis objects with support/contradiction information.",
    ),
    _op(
        "since",
        "read",
        [_arg("time", required=True)],
        "Object with nodes and edges changed strictly after time.",
    ),
    _op(
        "where",
        "read",
        [
            _arg("type", option=True, default=None, nullable=True),
            _arg("status", option=True, default=None, nullable=True),
            _arg("author", option=True, default=None, nullable=True),
            _arg("field", option=True, repeatable=True, default=[]),
            _arg("tag", option=True, default=None, nullable=True),
            _arg("text", option=True, default=None, nullable=True),
            _arg("over_budget", "boolean", option=True, default=False),
        ],
        "Array of nodes satisfying every supplied filter, ordered by created/id.",
    ),
    _op(
        "notes",
        "read",
        [_arg("id", required=True)],
        "Array of incident Note nodes, newest first.",
    ),
    _op(
        "subtree",
        "read",
        [_arg("id", required=True)],
        "Object with nodes, status_counts and accumulated usage for distinct subtree members.",
    ),
    _op(
        "digest",
        "read",
        [],
        "Object with items: running/ready/blocked/awaiting/failed_24h/claims groups and counts.",
    ),
    _op(
        "export",
        "read",
        [
            _arg("out", required=True),
            _arg("include_artifacts", "boolean", option=True, default=False),
            _arg("since", option=True, default=None, nullable=True),
        ],
        "Object describing written canonical archive path and selected counts.",
    ),
)
