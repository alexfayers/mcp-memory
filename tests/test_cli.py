"""Tests for CLI service-spec rendering and install targets."""

from __future__ import annotations

import argparse
import json

import pytest

from mcp_memory import cli


class TestSetupServiceDispatch:
    def test_uses_memory_spec(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, cli._ServiceSpec] = {}
        monkeypatch.setattr(
            cli, "_setup_service_from_spec", lambda spec: captured.update(spec=spec)
        )
        cli._cmd_setup_service(argparse.Namespace(port="3000", db_path="/x/m.db"))
        assert captured["spec"].binary_name == "mcp-memory"
        assert captured["spec"].port == "3000"


class TestRegisterClaudeCodeServer:
    def test_adds_server_and_allow_rule(
        self, tmp_path: cli.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(cli.Path, "home", classmethod(lambda _cls: tmp_path))
        (tmp_path / ".claude").mkdir()
        calls: list[list[str]] = []

        def fake_run(cmd: list[str], **_kw: object) -> object:
            calls.append(cmd)
            return type("R", (), {"returncode": 1, "stdout": ""})()

        monkeypatch.setattr(cli.subprocess, "run", fake_run)
        cli._register_claude_code_server(
            "/usr/bin/claude", "memory-agent", "http://localhost:8100/mcp"
        )

        allow = json.loads((tmp_path / ".claude" / "settings.json").read_text())
        assert "mcp__memory-agent__*" in allow["permissions"]["allow"]
        assert any("add" in c for c in calls)


class TestMemorySpec:
    def test_carries_db_and_port_env(self) -> None:
        spec = cli._memory_spec("3000", cli.Path("/data/memory.db"))
        assert spec.label == "com.mcp-memory"
        assert spec.binary_name == "mcp-memory"
        assert spec.env["MCP_MEMORY_PORT"] == "3000"
        assert spec.env["MCP_MEMORY_DB_PATH"] == "/data/memory.db"
        assert spec.log_path.name == "mcp-memory.log"


class TestAgentSpec:
    def test_carries_agent_port(self) -> None:
        spec = cli._agent_spec("8100")
        assert spec.label == "com.memory-agent"
        assert spec.binary_name == "memory-agent"
        assert spec.env["MCP_AGENT_PORT"] == "8100"
        assert "MCP_MEMORY_DB_PATH" not in spec.env

    def test_bakes_path_so_spawned_claude_is_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PATH", "/custom/bin:/usr/bin")
        spec = cli._agent_spec("8100")
        assert spec.env["PATH"] == "/custom/bin:/usr/bin"

    def test_propagates_dream_env_into_service(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MCP_DREAM_ENABLED", "false")
        monkeypatch.setenv("MCP_DREAM_IDLE_SECONDS", "60")
        monkeypatch.setenv("MCP_MEMORY_DB_PATH", "/should/not/leak")
        spec = cli._agent_spec("8100")
        assert spec.env["MCP_DREAM_ENABLED"] == "false"
        assert spec.env["MCP_DREAM_IDLE_SECONDS"] == "60"
        assert "MCP_MEMORY_DB_PATH" not in spec.env


class TestRenderPlist:
    def test_includes_label_binary_and_every_env_key(self) -> None:
        spec = cli._agent_spec("8100")
        plist = cli._render_plist(spec, binary="/usr/bin/memory-agent")
        assert "<string>com.memory-agent</string>" in plist
        assert "<string>/usr/bin/memory-agent</string>" in plist
        assert "<key>MCP_AGENT_PORT</key>" in plist
        assert "<string>8100</string>" in plist

    def test_memory_plist_keeps_both_env_keys(self) -> None:
        spec = cli._memory_spec("3000", cli.Path("/data/memory.db"))
        plist = cli._render_plist(spec, binary="/usr/bin/mcp-memory")
        assert "<key>MCP_MEMORY_DB_PATH</key>" in plist
        assert "<key>MCP_MEMORY_PORT</key>" in plist


class TestRenderSystemd:
    def test_includes_exec_and_env_lines(self) -> None:
        spec = cli._agent_spec("8100")
        unit = cli._render_systemd(spec, binary="/usr/bin/memory-agent", user="alice")
        assert "ExecStart=/usr/bin/memory-agent" in unit
        assert "Environment=MCP_AGENT_PORT=8100" in unit
        assert "User=alice" in unit
