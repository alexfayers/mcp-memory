"""Tests for the pruned tool input schemas advertised by tools/list."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

from mcp.server.fastmcp.exceptions import ToolError
import pytest

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


def _schema_nodes(node: object) -> Iterator[dict[str, Any]]:
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _schema_nodes(value)
    elif isinstance(node, list):
        for item in node:
            yield from _schema_nodes(item)


def _input_schemas() -> list[dict[str, Any]]:
    return [tool.inputSchema for tool in asyncio.run(server.mcp.list_tools())]


class TestPrunedInputSchemas:
    def test_no_string_titles(self) -> None:
        for schema in _input_schemas():
            assert not [n for n in _schema_nodes(schema) if isinstance(n.get("title"), str)]

    def test_no_null_branches(self) -> None:
        for schema in _input_schemas():
            assert not [n for n in _schema_nodes(schema) if n == {"type": "null"}]

    def test_no_null_defaults(self) -> None:
        for schema in _input_schemas():
            assert not [n for n in _schema_nodes(schema) if "default" in n and n["default"] is None]

    def test_optional_argument_keeps_its_type(self) -> None:
        tools = {tool.name: tool for tool in asyncio.run(server.mcp.list_tools())}
        assert tools["read_graph"].inputSchema["properties"]["max_observation_chars"] == {"type": "integer"}

    def test_tool_titles_are_kept(self) -> None:
        tools = {tool.name: tool for tool in asyncio.run(server.mcp.list_tools())}
        assert tools["read_graph"].title == "Read graph"


@pytest.mark.usefixtures("server_db")
class TestValidationUnchanged:
    def test_omitted_optional_argument_succeeds(self) -> None:
        asyncio.run(server.mcp.call_tool("read_graph", {"project": "p"}))

    def test_explicit_null_optional_argument_succeeds(self) -> None:
        asyncio.run(server.mcp.call_tool("read_graph", {"project": "p", "max_observation_chars": None}))

    def test_wrong_type_argument_is_rejected(self) -> None:
        with pytest.raises(ToolError):
            asyncio.run(server.mcp.call_tool("read_graph", {"project": "p", "compact": "notabool"}))
