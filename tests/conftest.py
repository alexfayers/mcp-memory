"""Fixtures shared across the whole test suite."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from starlette.testclient import TestClient

from mcp_memory import (
    api_client,
    eval as ranking_eval,
    server,
)
from mcp_memory.storage import open_writable

if TYPE_CHECKING:
    from pathlib import Path

    from mcp_memory.storage import Storage


@pytest.fixture(autouse=True)
def _isolate_db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the config-resolved database and its on-disk markers at a per-test directory."""
    monkeypatch.setenv("MCP_MEMORY_DB_PATH", str(tmp_path / "memory.db"))


class FakeApi:
    """Stands in for api_client.call: records (op, args) and answers from ``results``; unlisted ops are unreachable."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any] | None]] = []
        self.results: dict[str, Any] = {}

    def __call__(self, op: str, args: dict[str, Any] | None = None, *, timeout: float = 0) -> Any:
        self.calls.append((op, args))
        if op not in self.results:
            raise api_client.ApiError("mcp-memory server is not reachable at test")
        result = self.results[op]
        if isinstance(result, Exception):
            raise result
        return result(args) if callable(result) else result


@pytest.fixture(autouse=True)
def api(monkeypatch: pytest.MonkeyPatch) -> FakeApi:
    """Replace the server call in every test, so none reaches a real server."""
    fake = FakeApi()
    monkeypatch.setattr(api_client, "call", fake)
    return fake


@pytest.fixture
def live_server(monkeypatch: pytest.MonkeyPatch) -> None:
    """Send server calls to the real /api handlers, over the per-test database."""
    monkeypatch.setattr(server, "_db", None)
    ranking_eval.clear_cache()
    client = TestClient(server.mcp.streamable_http_app(stateless_http=True, json_response=True))

    def call(op: str, args: dict[str, Any] | None = None, *, timeout: float = 0) -> Any:
        response = client.post(f"/api/call/{op}", json=args or {})
        if response.is_error:
            raise api_client.ApiError(response.json()["error"])
        return response.json()

    monkeypatch.setattr(api_client, "call", call)


@pytest.fixture(autouse=True)
def _opt_out_of_telemetry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep tests from sending real telemetry."""
    monkeypatch.setenv("LLM_PROMPTS_NO_TELEMETRY", "1")


@pytest.fixture
def store(tmp_path: Path) -> Storage:
    """Create a fresh store-backed database for each test."""
    return open_writable(tmp_path / "test.db")
