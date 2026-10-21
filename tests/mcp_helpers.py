"""Real official SDK memory transport, not direct calls to server handlers."""

from contextlib import asynccontextmanager
from functools import partial

import anyio
from mcp import ClientSession
from mcp.shared.memory import create_client_server_memory_streams

from researchgraph.mcp import create_server
from tests.helpers import ALICE


@asynccontextmanager
async def memory_client(
    root, *, actor=ALICE, role=None, read_only=False, preset="full"
):
    """Own a real SDK memory connection; the caller negotiates its protocol."""
    server = create_server(
        root, actor=actor, role=role, read_only=read_only, preset=preset
    )
    async with (
        create_client_server_memory_streams() as (client_streams, server_streams),
        anyio.create_task_group() as group,
    ):
        group.start_soon(
            server.run, *server_streams, server.create_initialization_options()
        )
        try:
            async with ClientSession(*client_streams) as client:
                yield client
        finally:
            group.cancel_scope.cancel()


async def memory_session(
    root,
    requests=(),
    *,
    actor=ALICE,
    role=None,
    read_only=False,
    modern=True,
    preset="full",
):
    async with memory_client(
        root, actor=actor, role=role, read_only=read_only, preset=preset
    ) as client:
        initialized = await client.discover() if modern else await client.initialize()
        tools = await client.list_tools()
        results = [
            await client.call_tool(name, arguments) for name, arguments in requests
        ]
    return initialized, tools, results


def call_memory(root, requests=(), **context):
    return anyio.run(partial(memory_session, root, requests, **context))
