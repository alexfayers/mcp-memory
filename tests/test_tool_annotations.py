"""Tests for MCP ToolAnnotations on every registered mcp-memory tool."""

from __future__ import annotations

import asyncio

from mcp_memory import server, tool_names


class TestToolAnnotations:
    def test_every_tool_has_annotations_matching_the_mutation_partition(self) -> None:
        tools = {tool.name: tool for tool in asyncio.run(server.mcp.list_tools())}
        for tool in tools.values():
            assert tool.annotations is not None
        for name in tool_names.READ_ONLY_TOOLS:
            assert tools[name].annotations.readOnlyHint is True
        for name in tool_names.MUTATING_TOOLS:
            assert tools[name].annotations.readOnlyHint is False
