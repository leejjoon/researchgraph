"""Optional official MCP SDK adapter; no framing or graph semantics live here."""

import json
from copy import deepcopy
from functools import partial
from pathlib import Path

import anyio
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from researchgraph import __version__
from researchgraph.interfaces import execute
from researchgraph.model import RuleViolation
from researchgraph.project import Project
from researchgraph.registry import OPERATIONS


def create_server(root, *, actor, role=None, read_only=False):
    """Build a full-preset server with fixed root and declared actor authority.

    Instructions contain only the existing profile's guidance, or nothing for
    an uninitialized destination. Domain calls go through shared public methods
    in worker threads; cancellation waits for owned resources to close.
    """
    Project._actor(actor)
    root = Path(root).expanduser().resolve()
    guidance = ""
    if (root / ".researchgraph").exists() or (root / "graph").exists():
        with Project.open(root, read_only=True) as project:
            guidance = project.profile.agent_guidance
    tools = []
    for operation in OPERATIONS:
        if read_only and operation["kind"] == "write":
            continue
        properties = {
            arg["name"]: deepcopy(arg["schema"]) for arg in operation["arguments"]
        }
        properties["now"] = {"type": ["string", "null"]}
        if operation["kind"] == "write":
            properties["commit"] = {"type": "boolean", "default": True}
        tools.append(
            types.Tool(
                name=operation["name"],
                description=operation["result"],
                input_schema={
                    "type": "object",
                    "properties": properties,
                    "required": [
                        arg["name"] for arg in operation["arguments"] if arg["required"]
                    ],
                    "additionalProperties": False,
                },
                annotations=types.ToolAnnotations(
                    read_only_hint=operation["kind"] == "read"
                ),
            )
        )

    async def list_tools(ctx, params):
        return types.ListToolsResult(tools=tools)

    async def call_tool(ctx, params):
        try:
            if ctx.protocol_version != types.LATEST_PROTOCOL_VERSION:
                raise RuleViolation(
                    "schema",
                    f"MCP {types.LATEST_PROTOCOL_VERSION} discovery negotiation is required; legacy structuredContent cannot preserve array results",
                )
            value = await anyio.to_thread.run_sync(
                partial(
                    execute,
                    root,
                    params.name,
                    params.arguments if params.arguments is not None else {},
                    actor=actor,
                    role=role,
                    read_only=read_only,
                )
            )
            error = False
        except RuleViolation as exc:
            value = {"rule": exc.rule, "message": exc.message, "ids": exc.ids}
            error = True
        return types.CallToolResult(
            content=[
                types.TextContent(
                    type="text", text=json.dumps(value, ensure_ascii=False)
                )
            ],
            structured_content=value,
            is_error=error,
        )

    return Server(
        "researchgraph",
        version=__version__,
        instructions=guidance,
        on_list_tools=list_tools,
        on_call_tool=call_tool,
    )


async def _stdio(root, *, actor, role, read_only):
    server = create_server(root, actor=actor, role=role, read_only=read_only)
    async with stdio_server() as (incoming, outgoing):
        await server.run(incoming, outgoing, server.create_initialization_options())


def serve(root, *, actor, role=None, read_only=False):
    """Run the official stdio transport until disconnect, closing request owners."""
    anyio.run(partial(_stdio, root, actor=actor, role=role, read_only=read_only))
