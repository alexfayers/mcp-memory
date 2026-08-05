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


class TestRegisterCodexServer:
    def test_adds_server_when_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[list[str]] = []

        def fake_run(cmd: list[str], **_kw: object) -> object:
            calls.append(cmd)
            return type("R", (), {"returncode": 1, "stdout": ""})()

        monkeypatch.setattr(cli.subprocess, "run", fake_run)
        cli._register_codex_server("/usr/bin/codex", "memory-agent", "http://127.0.0.1:8100/mcp")

        assert any("add" in c for c in calls)

    def test_skips_add_when_already_registered(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[list[str]] = []

        def fake_run(cmd: list[str], **_kw: object) -> object:
            calls.append(cmd)
            return type("R", (), {"returncode": 0, "stdout": ""})()

        monkeypatch.setattr(cli.subprocess, "run", fake_run)
        cli._register_codex_server("/usr/bin/codex", "memory-agent", "http://127.0.0.1:8100/mcp")

        assert not any("add" in c for c in calls)


class TestRegisterCopilotServer:
    def test_adds_memory_servers_to_empty_config(self, tmp_path: cli.Path) -> None:
        mcp_path = tmp_path / "mcp.json"
        cli._register_copilot_server(mcp_path, "memory", "http://localhost:8000/mcp")
        cli._register_copilot_server(mcp_path, "memory-agent", "http://localhost:8100/mcp")

        data = json.loads(mcp_path.read_text(encoding="utf-8"))
        assert data["servers"]["memory"]["url"] == "http://localhost:8000/mcp"
        assert data["servers"]["memory-agent"]["url"] == "http://localhost:8100/mcp"

    def test_keeps_existing_jsonc_config(self, tmp_path: cli.Path) -> None:
        mcp_path = tmp_path / "mcp.json"
        mcp_path.write_text(
            """
{
    // existing server
    "servers": {
        "github": {
            "type": "http",
            "url": "https://api.githubcopilot.com/mcp/",
        },
    },
}
""".strip()
            + "\n",
            encoding="utf-8",
        )

        cli._register_copilot_server(mcp_path, "memory", "http://localhost:8000/mcp")
        data = json.loads(mcp_path.read_text(encoding="utf-8"))

        assert "github" in data["servers"]
        assert data["servers"]["memory"]["url"] == "http://localhost:8000/mcp"


class TestCopilotPathSelection:
    def test_prefers_local_vscode_path_before_wsl_fallback(
        self, tmp_path: cli.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("mcp_memory.cli.platform.system", lambda: "Linux")
        monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu")
        monkeypatch.setenv("USERNAME", "alex")
        monkeypatch.setattr(cli.Path, "home", classmethod(lambda _cls: tmp_path))
        remote = tmp_path / ".vscode-server" / "data" / "User"
        remote.mkdir(parents=True)
        (remote / "mcp.json").write_text("{}\n", encoding="utf-8")
        local = tmp_path / ".config" / "Code" / "User"
        local.mkdir(parents=True)
        (local / "mcp.json").write_text("{}\n", encoding="utf-8")
        resolved = cli._default_copilot_mcp_config_path()
        assert str(resolved) == str(remote / "mcp.json")

    def test_prefers_xdg_config_home_on_linux(
        self, tmp_path: cli.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("mcp_memory.cli.platform.system", lambda: "Linux")
        monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)
        monkeypatch.setattr(cli.Path, "home", classmethod(lambda _cls: tmp_path / "home"))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
        xdg = tmp_path / "xdg" / "Code" / "User"
        xdg.mkdir(parents=True)
        (xdg / "mcp.json").write_text("{}\n", encoding="utf-8")

        resolved = cli._default_copilot_mcp_config_path()
        assert str(resolved) == str(xdg / "mcp.json")

    def test_uses_macos_user_path(
        self, tmp_path: cli.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(cli.Path, "home", classmethod(lambda _cls: tmp_path))
        monkeypatch.setattr("mcp_memory.cli.platform.system", lambda: "Darwin")
        mac = tmp_path / "Library" / "Application Support" / "Code" / "User"
        mac.mkdir(parents=True)
        (mac / "mcp.json").write_text("{}\n", encoding="utf-8")

        resolved = cli._default_copilot_mcp_config_path()
        assert str(resolved) == str(mac / "mcp.json")


class TestMemorySpec:
    def test_carries_db_and_port_env(self) -> None:
        spec = cli._memory_spec("3000", cli.Path("/data/memory.db"))
        assert spec.label == "com.mcp-memory"
        assert spec.binary_name == "mcp-memory"
        assert spec.env["MCP_MEMORY_PORT"] == "3000"
        assert spec.env["MCP_MEMORY_DB_PATH"] == "/data/memory.db"
        assert spec.log_path.name == "mcp-memory.log"

    def test_propagates_gc_env_into_service(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MCP_MEMORY_GC_ENABLED", "true")
        spec = cli._memory_spec("3000", cli.Path("/data/memory.db"))
        assert spec.env["MCP_MEMORY_GC_ENABLED"] == "true"

    def test_propagates_agent_locator_env_into_service(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MCP_AGENT_PORT", "9100")
        spec = cli._memory_spec("3000", cli.Path("/data/memory.db"))
        assert spec.env["MCP_AGENT_PORT"] == "9100"


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

    def test_propagates_dream_heavy_env_into_service(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MCP_DREAM_HEAVY_ENABLED", "true")
        monkeypatch.setenv("MCP_DREAM_HEAVY_INTERVAL_SECONDS", "90")
        spec = cli._agent_spec("8100")
        assert spec.env["MCP_DREAM_HEAVY_ENABLED"] == "true"
        assert spec.env["MCP_DREAM_HEAVY_INTERVAL_SECONDS"] == "90"


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
