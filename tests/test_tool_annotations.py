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
            assert tools[name].annotations.read_only_hint is True
        for name in tool_names.MUTATING_TOOLS:
            assert tools[name].annotations.read_only_hint is False

    def test_only_the_hot_tools_are_marked_always_load(self) -> None:
        hot_tools = {
            "add_observations",
            "read_graph",
            "search_nodes",
            "create_entities",
            "set_entity_status",
            "trim_observations_to_outcome",
        }
        tools = asyncio.run(server.mcp.list_tools())
        always_loaded = {tool.name for tool in tools if (tool.meta or {}).get("anthropic/alwaysLoad") is True}
        assert always_loaded == hot_tools
