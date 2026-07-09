"""Tests for the memory-agent recall server."""

from __future__ import annotations

import asyncio
import json

import pytest

from mcp_memory import agent

_MODEL = "global.anthropic.claude-haiku-4-5-20251001-v1:0"


def _recall_payload(result: str, *, model: str = _MODEL, is_error: bool = False) -> str:
    return json.dumps(
        {
            "type": "result",
            "is_error": is_error,
            "result": result,
            "modelUsage": {model: {"inputTokens": 100, "outputTokens": 50}},
        }
    )


class TestBuildMcpConfig:
    def test_points_only_at_memory_server(self) -> None:
        config = agent.build_mcp_config("http://localhost:3000/mcp")
        assert config == {
            "mcpServers": {"memory": {"type": "http", "url": "http://localhost:3000/mcp"}}
        }


class TestBuildRecallCommand:
    def test_denies_every_mutating_and_builtin_tool(self) -> None:
        command = agent.build_recall_command(
            "who owns X",
            claude_bin="/usr/bin/claude",
            model=_MODEL,
            mcp_config_path="/tmp/cfg.json",
        )
        deny_index = command.index("--disallowedTools")
        denied = set(command[deny_index + 1 :])
        assert "mcp__memory__create_entities" in denied
        assert "mcp__memory__vote_entity" in denied
        assert "Bash" in denied

    def test_allows_memory_read_tools_by_not_denying_them(self) -> None:
        command = agent.build_recall_command(
            "q", claude_bin="claude", model=_MODEL, mcp_config_path="/tmp/cfg.json"
        )
        assert "mcp__memory__search_nodes" not in command
        assert "mcp__memory__get_entity_with_relations" not in command

    def test_denies_filesystem_and_web_read_builtins(self) -> None:
        command = agent.build_recall_command(
            "q", claude_bin="claude", model=_MODEL, mcp_config_path="/tmp/cfg.json"
        )
        deny_index = command.index("--disallowedTools")
        denied = set(command[deny_index + 1 :])
        for tool in ("Read", "Grep", "Glob", "WebFetch", "WebSearch"):
            assert tool in denied

    def test_isolates_and_pins_the_spawn(self) -> None:
        command = agent.build_recall_command(
            "q", claude_bin="claude", model=_MODEL, mcp_config_path="/tmp/cfg.json"
        )
        assert "--strict-mcp-config" in command
        assert command[command.index("--model") + 1] == _MODEL
        assert command[command.index("--mcp-config") + 1] == "/tmp/cfg.json"

    def test_embeds_query_and_slug_ritual_in_prompt(self) -> None:
        command = agent.build_recall_command(
            "who owns billing", claude_bin="claude", model=_MODEL, mcp_config_path="/c.json"
        )
        prompt = command[command.index("-p") + 1]
        assert "who owns billing" in prompt
        assert "[project/entity-name]" in prompt


class TestSpawnEnv:
    def test_isolates_config_dir_from_user_hooks(self) -> None:
        env = agent.build_spawn_env("/tmp/iso", base_env={"PATH": "/bin", "AWS_PROFILE": "x"})
        assert env["CLAUDE_CONFIG_DIR"] == "/tmp/iso"
        assert env["MCP_TIMEOUT"] == "30000"
        assert env["PATH"] == "/bin"
        assert env["AWS_PROFILE"] == "x"

    def test_isolated_settings_preapprove_memory_without_hooks(self) -> None:
        settings = agent.AGENT_SETTINGS
        assert "mcp__memory__*" in settings["permissions"]["allow"]
        assert "hooks" not in settings


class TestParseRecallResult:
    def test_returns_distilled_findings(self) -> None:
        stdout = _recall_payload("- Billing owned by team [bre/feature-billing]")
        result = agent.parse_recall_result(stdout, expected_model=_MODEL)
        assert result == "- Billing owned by team [bre/feature-billing]"

    def test_rejects_unexpected_model(self) -> None:
        stdout = _recall_payload("- finding", model="global.anthropic.claude-opus-4-8-v1:0")
        with pytest.raises(RuntimeError, match="unexpected model"):
            agent.parse_recall_result(stdout, expected_model=_MODEL)

    def test_rejects_error_payload(self) -> None:
        stdout = _recall_payload("boom", is_error=True)
        with pytest.raises(RuntimeError, match="error"):
            agent.parse_recall_result(stdout, expected_model=_MODEL)

    def test_rejects_malformed_output(self) -> None:
        with pytest.raises(RuntimeError, match="malformed"):
            agent.parse_recall_result("not json", expected_model=_MODEL)

    def test_rejects_empty_result(self) -> None:
        with pytest.raises(RuntimeError, match="no findings"):
            agent.parse_recall_result(_recall_payload("   "), expected_model=_MODEL)


class TestRunPreflight:
    def test_noop_when_unconfigured(self) -> None:
        asyncio.run(agent.run_preflight(None))

    def test_passes_on_exit_zero(self) -> None:
        asyncio.run(agent.run_preflight("true"))

    def test_raises_generic_error_on_failure(self) -> None:
        with pytest.raises(RuntimeError, match="pre-flight check failed"):
            asyncio.run(agent.run_preflight("false"))

    def test_raises_on_timeout(self) -> None:
        with pytest.raises(RuntimeError, match="pre-flight check failed"):
            asyncio.run(agent.run_preflight("sleep 5", timeout=0.1))


class TestRecallTool:
    def test_returns_distilled_findings_end_to_end(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(agent.shutil, "which", lambda _: "/usr/bin/claude")
        monkeypatch.setattr(agent, "get_recall_model", lambda: _MODEL)
        monkeypatch.setattr(agent, "get_preflight_command", lambda: None)

        captured: dict[str, object] = {}

        async def fake_spawn(
            command: list[str],
            *,
            env: dict[str, str] | None = None,
            timeout: float = 0,
        ) -> str:
            captured["env"] = env
            return _recall_payload("- Owned by team [bre/x]")

        monkeypatch.setattr(agent, "_spawn_recall", fake_spawn)
        result = asyncio.run(agent.recall("who owns x"))
        assert result == "- Owned by team [bre/x]"
        spawn_env = captured["env"]
        assert isinstance(spawn_env, dict)
        assert "CLAUDE_CONFIG_DIR" in spawn_env

    def test_reports_missing_claude_cli(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(agent.shutil, "which", lambda _: None)
        result = asyncio.run(agent.recall("q"))
        assert "claude CLI not found" in result

    def test_preflight_failure_blocks_spawn(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(agent.shutil, "which", lambda _: "/usr/bin/claude")
        monkeypatch.setattr(agent, "get_preflight_command", lambda: "false")

        async def fail_if_called(
            command: list[str],
            *,
            env: dict[str, str] | None = None,
            timeout: float = 0,
        ) -> str:
            msg = "spawn must not run when pre-flight fails"
            raise AssertionError(msg)

        monkeypatch.setattr(agent, "_spawn_recall", fail_if_called)
        result = asyncio.run(agent.recall("q"))
        assert "pre-flight check failed" in result
