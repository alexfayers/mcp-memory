"""Tests that sync tools work through the real streamable-HTTP server and an MCP client."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
import contextlib
import socket
from typing import TYPE_CHECKING

from mcp import Client
import pytest
import uvicorn

from mcp_memory import server
from mcp_memory.storage import open_writable

if TYPE_CHECKING:
    from pathlib import Path

    from mcp_memory.storage import Storage


@pytest.fixture
def server_db(tmp_path: Path) -> Iterator[Storage]:
    """Point the server's module-level db singleton at a fresh database."""
    manager = open_writable(tmp_path / "server.db")
    original = server._db
    server._db = manager
    yield manager
    server._db = original


@contextlib.asynccontextmanager
async def _serving() -> AsyncIterator[str]:
    """Serve the MCP app over HTTP on a free port exactly as production does; yield its URL."""
    app = server.mcp.streamable_http_app(stateless_http=True, json_response=True)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    uvicorn_server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
    started = asyncio.Event()
    startup = uvicorn_server.startup

    async def startup_then_signal(sockets: list[socket.socket] | None = None) -> None:
        await startup(sockets)
        started.set()

    uvicorn_server.startup = startup_then_signal  # type: ignore[method-assign]
    task = asyncio.create_task(uvicorn_server.serve(sockets=[sock]))
    await started.wait()
    try:
        yield f"http://127.0.0.1:{sock.getsockname()[1]}/mcp"
    finally:
        uvicorn_server.should_exit = True
        await task


@pytest.mark.usefixtures("server_db")
def test_sync_tools_touching_sqlite_work_over_streamable_http() -> None:
    entity = {
        "name": "knowledge/e",
        "entityType": "knowledge",
        "observations": ["hello world"],
        "relations": [{"target": "project/p", "relation_type": "part-of"}],
    }

    async def scenario() -> tuple[dict[str, object], dict[str, object]]:
        async with _serving() as url, Client(url) as client:
            created = await client.call_tool("create_entities", {"project": "p", "entities": [entity]})
            found = await client.call_tool("search_nodes", {"project": "p", "query": "hello"})
            return created.structured_content, found.structured_content

    created, found = asyncio.run(scenario())
    assert "error" not in created
    assert "error" not in found
    assert found["entities"]


@pytest.mark.usefixtures("server_db")
def test_concurrent_sync_tools_work_over_streamable_http() -> None:
    count = 30

    def entity(i: int) -> dict[str, object]:
        return {
            "name": f"knowledge/e{i}",
            "entityType": "knowledge",
            "observations": [f"hello world {i}"],
            "relations": [{"target": "project/p", "relation_type": "part-of"}],
        }

    async def scenario() -> list[dict[str, object]]:
        async with _serving() as url, Client(url) as client:
            calls = [
                client.call_tool("create_entities", {"project": "p", "entities": [entity(i)]}) for i in range(count)
            ] + [client.call_tool("search_nodes", {"project": "p", "query": "hello"}) for _ in range(count)]
            return [r.structured_content for r in await asyncio.gather(*calls)]

    results = asyncio.run(scenario())
    assert all("error" not in r for r in results)
