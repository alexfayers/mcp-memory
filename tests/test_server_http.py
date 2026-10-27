"""Tests that sync tools work through the real streamable-HTTP server and an MCP client."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Iterator
import contextlib
import socket
import threading
from typing import TYPE_CHECKING
import urllib.request

from mcp import Client
from mcp.types import CallToolResult, TextContent
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
    original, original_usage = server._db, server._usage
    server._db, server._usage = manager, None
    yield manager
    server._db, server._usage = original, original_usage


def _text(result: CallToolResult) -> str:
    block = result.content[0]
    assert isinstance(block, TextContent)
    return block.text


@contextlib.asynccontextmanager
async def _serving() -> AsyncGenerator[str, None]:
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

    async def scenario() -> tuple[CallToolResult, CallToolResult]:
        async with _serving() as url, Client(url) as client:
            created = await client.call_tool("create_entities", {"project": "p", "entities": [entity]})
            found = await client.call_tool("search_nodes", {"project": "p", "query": "hello"})
            return created, found

    created, found = asyncio.run(scenario())
    assert not created.is_error
    assert not found.is_error
    assert found.structured_content is not None
    assert found.structured_content["entities"]


@pytest.mark.usefixtures("server_db")
def test_failed_tool_call_is_an_mcp_error_with_the_message_text() -> None:
    async def scenario() -> CallToolResult:
        async with _serving() as url, Client(url) as client:
            return await client.call_tool("list_metadata", {"kind": "bogus"})

    result = asyncio.run(scenario())
    assert result.is_error
    assert _text(result) == "Invalid kind 'bogus'. Must be one of: projects, paths, groups."


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

    async def scenario() -> list[CallToolResult]:
        async with _serving() as url, Client(url) as client:
            calls = [
                client.call_tool("create_entities", {"project": "p", "entities": [entity(i)]}) for i in range(count)
            ] + [client.call_tool("search_nodes", {"project": "p", "query": "hello"}) for _ in range(count)]
            return list(await asyncio.gather(*calls))

    errors = [_text(r) for r in asyncio.run(scenario()) if r.is_error]
    assert not errors


@pytest.mark.usefixtures("server_db")
def test_telemetry_flush_route_reports_pending_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    reports: list[int | None] = []
    threads: list[int] = []

    def report(_self: server.telemetry.UsageTelemetry, up_to: int | None = None) -> None:
        reports.append(up_to)
        threads.append(threading.get_ident())

    monkeypatch.setattr(server.telemetry.UsageTelemetry, "report", report)

    async def scenario() -> int:
        async with _serving() as url:
            request = urllib.request.Request(url.removesuffix("/mcp") + "/api/telemetry/flush", method="POST")  # ruff: ignore[suspicious-url-open-usage]
            with await asyncio.to_thread(urllib.request.urlopen, request) as response:  # ruff: ignore[suspicious-url-open-usage]
                return int(response.status)

    assert asyncio.run(scenario()) == 204
    assert reports == [None]
    assert threads != [threading.get_ident()]


@pytest.mark.usefixtures("server_db")
def test_telemetry_loop_reports_off_the_event_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    threads: list[int] = []
    reported = threading.Event()

    def report_when_due(_self: server.telemetry.UsageTelemetry, _idle_seconds: float) -> None:
        threads.append(threading.get_ident())
        reported.set()

    async def no_ranking(_self: server.telemetry.EvalTelemetry, _idle_seconds: float) -> None:
        return None

    monkeypatch.setattr(server.telemetry, "POLL_SECONDS", 0)
    monkeypatch.setattr(server.telemetry, "tool_versions", dict)
    monkeypatch.setattr(server.telemetry.UsageTelemetry, "report_when_due", report_when_due)
    monkeypatch.setattr(server.telemetry.EvalTelemetry, "report_when_due", no_ranking)

    async def scenario() -> None:
        task = asyncio.create_task(server._telemetry_loop())
        await asyncio.to_thread(reported.wait)
        task.cancel()

    asyncio.run(scenario())
    assert threads[0] != threading.get_ident()
