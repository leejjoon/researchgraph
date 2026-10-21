"""Encode existing CLI scenario arguments onto real SDK tool calls.

The status namespace adapts real SDK text/error channels to existing assertions;
it is not evidence of subprocess exit codes. Dedicated stdio tests prove framing.
"""

import inspect
import json
from types import SimpleNamespace

from click.core import ParameterSource

from researchgraph.cli import _actor, main
from researchgraph.model import RuleViolation, format_timestamp
from researchgraph.project import Project
from researchgraph.registry import OPERATIONS
from tests.helpers import NOW
from tests.mcp_helpers import call_memory
from tests.test_cli import prepare_console

_BY_NAME = {op["name"]: op for op in OPERATIONS}


def invoke_mcp(root, *arguments, agent=None, role=None, now=NOW):
    """Run the actual MCP client with logical values parsed from scenario argv."""
    name, *tail = arguments
    operation = _BY_NAME[name]
    with main.commands[name].make_context(name, tail) as context:
        values = context.params
        payload = {}
        for arg in operation["arguments"]:
            if (
                context.get_parameter_source(arg["cli_name"])
                != ParameterSource.COMMANDLINE
            ):
                continue
            value = values[arg["cli_name"]]
            if arg["repeatable"]:
                value = [
                    json.loads(item) if arg["encoding"] == "json" else item
                    for item in value
                ]
            elif arg["encoding"] == "json":
                value = json.loads(value)
            payload[arg["name"]] = value
        payload["now"] = (
            values["now"] if values["now"] is not None else format_timestamp(now)
        )
        if operation["kind"] == "write" and values["commit"] is not None:
            payload["commit"] = values["commit"]
        agent = values["as_agent"] if values["as_agent"] is not None else agent
        role = values["role"] if values["role"] is not None else role
        read_only = bool(values["read_only"])
    prepare_console(["--project", str(root)])
    _, _, results = call_memory(
        root,
        [(name, payload)],
        actor=_actor(root, agent),
        role=role,
        read_only=read_only,
    )
    result = results[0]
    assert len(result.content) == 1 and result.content[0].type == "text"
    text = result.content[0].text
    assert json.loads(text) == result.structured_content
    return SimpleNamespace(
        exit_code=2 if result.is_error else 0,
        stdout="" if result.is_error else text,
        stderr=text if result.is_error else "",
        output=text,
        native_result=result,
    )


def operation_mcp(project, name, *args, **kwargs):
    """Call a public-method scenario through the actual SDK and rethrow its error.

    This is explicit test-boundary injection, not a replacement Project. Native
    getters/bytes/cache oracles still use the original owner. Bind the public
    signature to preserve legal positional optional parameters, then encode only
    graph arguments; fixed authority is selected outside the tool payload.
    """
    method = getattr(project, name)
    signature = inspect.signature(method)
    bound = signature.bind(*args, **kwargs)
    logical_name = "profile" if name == "profile_info" else name
    operation = _BY_NAME[logical_name]
    parameters = [
        key
        for key in signature.parameters
        if key not in {"actor", "role", "now", "commit"}
    ]
    argument_names = {arg["name"] for arg in operation["arguments"]}
    names = {
        key: key
        if key in argument_names
        else next(
            arg["name"] for arg in operation["arguments"] if arg["cli_name"] == key
        )
        for key in parameters
    }
    values = dict(bound.arguments)
    actor, role = values.pop("actor"), values.pop("role", None)
    now = values.pop("now")
    commit = values.pop("commit", None)
    payload = {names[key]: value for key, value in values.items()}
    payload["now"] = format_timestamp(now)
    if commit is not None:
        payload["commit"] = commit
    _, _, results = call_memory(
        project.path,
        [(logical_name, payload)],
        actor=actor,
        role=role,
        read_only=project.read_only,
    )
    result = results[0]
    value = json.loads(result.content[0].text)
    assert value == result.structured_content
    if result.is_error:
        raise RuleViolation(value["rule"], value["message"], value["ids"])
    return value


def import_mcp(archive, path, *, actor, now, role=None, commit=True, read_only=False):
    """Import via the real SDK, then open an independent native assertion owner.

    The returned Project is only the test's source/index oracle, not a fake
    transport object or evidence that the remote call returned a Python owner.
    """
    _, _, results = call_memory(
        path,
        [
            (
                "import",
                {
                    "archive": str(archive),
                    "commit": commit,
                    "now": format_timestamp(now),
                },
            )
        ],
        actor=actor,
        role=role,
        read_only=read_only,
    )
    result = results[0]
    value = json.loads(result.content[0].text)
    assert value == result.structured_content
    if result.is_error:
        raise RuleViolation(value["rule"], value["message"], value["ids"])
    owner = Project.open(path)
    assert (
        str(owner.path) == value["path"]
        and owner.profile_name == value["profile"]
        and owner.profile_version == value["version"]
    )
    return owner
