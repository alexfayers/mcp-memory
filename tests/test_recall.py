"""Tests for the recall-trigger normaliser and matcher."""

from __future__ import annotations

import pytest

pytest.importorskip("cline_hooks")

from mcp_memory.hooks.recall import Facts, facts, matches


def _row(kind: str, value: str, content_hash: str = "h1", tool: str = "") -> dict[str, object]:
    return {"kind": kind, "value": value, "tool": tool, "content": "fact", "content_hash": content_hash}


class TestFacts:
    def test_a_file_edit_names_its_path_and_tool(self) -> None:
        event = {"tool_name": "replace_in_file", "parameters": {"path": "/repo/src/app.py", "diff": "a"}}
        found = facts(event)
        assert found.tool == "replace_in_file"
        assert found.paths == ("/repo/src/app.py",)
        assert '"diff": "a"' in found.input_json

    def test_an_mcp_call_is_named_by_its_mcp_tool(self) -> None:
        event = {"tool_name": "use_mcp_tool", "parameters": {"server_name": "memory", "tool_name": "search_nodes"}}
        assert facts(event).tool == "search_nodes"

    def test_a_shell_call_names_no_path(self) -> None:
        assert facts({"tool_name": "execute_command", "parameters": {"command": "ls"}}).paths == ()


class TestMatches:
    def test_a_pattern_matches_the_input_json_for_its_tool_only(self) -> None:
        call = Facts(tool="execute_command", input_json='{"command": "git push --force"}')
        rows = [_row("pattern", r"push --force", tool="execute_command"), _row("pattern", "push", "h2", tool="x")]
        assert [row["content_hash"] for row in matches(rows, call, "pattern")] == ["h1"]

    def test_a_path_glob_matches_a_repo_relative_suffix(self) -> None:
        read = Facts(tool="read_file", paths=("/home/me/repo/src/mcp_memory/api.py",))
        rows = [_row("path", "src/*/api.py"), _row("path", "docs/*.md", "h2")]
        assert [row["content_hash"] for row in matches(rows, read, "path")] == ["h1"]

    def test_a_mention_matches_the_turn_text(self) -> None:
        turn = Facts(text="I will deploy now")
        assert matches([_row("mention", r"\bdeploy\b")], turn, "mention")

    def test_an_observation_matched_twice_is_listed_once_and_a_bad_regex_is_skipped(self) -> None:
        call = Facts(tool="execute_command", input_json='{"command": "rm -rf build"}')
        rows = [_row("pattern", "("), _row("pattern", "rm"), _row("pattern", "build")]
        assert len(matches(rows, call, "pattern")) == 1
