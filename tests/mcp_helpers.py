"""Real official SDK memory transport, not direct calls to server handlers."""

from functools import partial

import anyio
from mcp import ClientSession
from mcp.shared.memory import create_client_server_memory_streams

from researchgraph.mcp import create_server
from tests.helpers import ALICE


async def memory_session(
    root, requests=(), *, actor=ALICE, role=None, read_only=False, modern=True
):
    server = create_server(root, actor=actor, role=role, read_only=read_only)
    async with (
        create_client_server_memory_streams() as (client_streams, server_streams),
        anyio.create_task_group() as group,
    ):
        group.start_soon(
            server.run, *server_streams, server.create_initialization_options()
        )
        async with ClientSession(*client_streams) as client:
            initialized = (
                await client.discover() if modern else await client.initialize()
            )
            tools = await client.list_tools()
            results = [
                await client.call_tool(name, arguments) for name, arguments in requests
            ]
        group.cancel_scope.cancel()
    return initialized, tools, results


def call_memory(root, requests=(), **context):
    return anyio.run(partial(memory_session, root, requests, **context))
