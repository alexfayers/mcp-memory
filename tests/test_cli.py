"""Tests for CLI service-spec rendering and install targets."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import functools
import io
import json
import os
from pathlib import Path
import socket
from types import SimpleNamespace

import pytest

from mcp_memory import backup, cli, config, dream
from mcp_memory.storage import open_writable


def _eval_since_help() -> str:
    """Return the rendered help text of `mcp-memory utils eval --since`."""
    utils = cli._build_parser()._subparsers._group_actions[0].choices["utils"]  # type: ignore[union-attr]
    action = next(
        a for a in utils._subparsers._group_actions[0].choices["eval"]._actions if "--since" in a.option_strings
    )
    return str(action.help)


class TestUtilsGroup:
    @pytest.mark.parametrize("name", ["migrate-db", "audit", "eval", "metrics"])
    def test_maintenance_commands_live_under_utils_only(self, name: str) -> None:
        parser = cli._build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args([name])
        assert parser.parse_args(["utils", name, *(["--all-projects"] if name == "audit" else [])]).command == "utils"


class TestEvalSinceHelp:
    def test_describes_unlimited_retention_without_a_negative_day_count(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MCP_MEMORY_SURFACED_RETENTION_DAYS", "-1")
        assert "-1 days" not in _eval_since_help()

    def test_names_the_window_when_retention_is_finite(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MCP_MEMORY_SURFACED_RETENTION_DAYS", "30")
        assert "~30 days" in _eval_since_help()


class TestSetupServiceDispatch:
    def test_uses_memory_spec(self, tmp_path: cli.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(cli, "_LAUNCHD_PLIST", tmp_path / "absent.plist")
        monkeypatch.setattr(cli, "_SYSTEMD_UNIT", tmp_path / "absent.service")
        captured: dict[str, cli._ServiceSpec] = {}
        monkeypatch.setattr(cli, "_setup_service_from_spec", lambda spec: captured.update(spec=spec))
        monkeypatch.setattr(cli, "_remove_legacy_agent_service", lambda: None)
        cli._cmd_setup_service(argparse.Namespace(port="3000", db_path="/x/m.db"))
        assert captured["spec"].binary_name == "mcp-memory"
        assert captured["spec"].port == "3000"

    def test_keeps_installed_service_port_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MCP_MEMORY_PORT", raising=False)
        monkeypatch.setattr(config, "detect_service_port", lambda: "3000")
        assert cli._build_parser().parse_args(["setup-service"]).port == "3000"


class TestSetupServiceKeepsInstalledDreamEnv:
    @pytest.fixture(autouse=True)
    def _isolate(self, tmp_path: cli.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        for name in [k for k in os.environ if k.startswith("MCP_DREAM_")]:
            monkeypatch.delenv(name)
        monkeypatch.setattr(cli, "_LAUNCHD_PLIST", tmp_path / "com.mcp-memory.plist")
        monkeypatch.setattr(cli, "_SYSTEMD_UNIT", tmp_path / "mcp-memory.service")
        monkeypatch.setattr(cli, "_remove_legacy_agent_service", lambda: None)

    def _setup(
        self, monkeypatch: pytest.MonkeyPatch, system: str, installed: dict[str, str] | None = None
    ) -> cli._ServiceSpec:
        """Run setup-service against an installed main service holding `installed` env; return the spec it builds."""
        monkeypatch.setattr(cli.platform, "system", lambda: system)
        if installed is not None:
            darwin = system == "Darwin"
            path = cli._LAUNCHD_PLIST if darwin else cli._SYSTEMD_UNIT
            render = cli._render_plist if darwin else functools.partial(cli._render_systemd, user="u")
            path.write_text(render(_agent_spec(installed), binary="/bin/mcp-memory"))
        captured: dict[str, cli._ServiceSpec] = {}
        monkeypatch.setattr(cli, "_setup_service_from_spec", lambda spec: captured.update(spec=spec))
        cli._cmd_setup_service(argparse.Namespace(port="3000", db_path="/x/m.db"))
        return captured["spec"]

    @pytest.mark.parametrize("system", ["Darwin", "Linux"])
    def test_keeps_installed_values_unless_the_shell_sets_them_and_is_idempotent(
        self, monkeypatch: pytest.MonkeyPatch, system: str
    ) -> None:
        installed = {"MCP_DREAM_ENABLED": "true", "MCP_DREAM_IDLE_SECONDS": "60", "MCP_AGENT_PORT": "1"}
        monkeypatch.setenv("MCP_DREAM_IDLE_SECONDS", "900")

        spec = self._setup(monkeypatch, system, installed)

        dream_env = {k: v for k, v in spec.env.items() if k.startswith("MCP_DREAM_")}
        assert dream_env == {"MCP_DREAM_ENABLED": "true", "MCP_DREAM_IDLE_SECONDS": "900"}
        assert "MCP_AGENT_PORT" not in spec.env

        render = cli._render_plist if system == "Darwin" else functools.partial(cli._render_systemd, user="u")
        (cli._LAUNCHD_PLIST if system == "Darwin" else cli._SYSTEMD_UNIT).write_text(render(spec, binary="/bin/m"))
        assert self._setup(monkeypatch, system).env == spec.env

    @pytest.mark.parametrize("system", ["Darwin", "Linux"])
    @pytest.mark.parametrize("installed", [None, {}, {"MCP_AGENT_PORT": "1"}], ids=["absent", "no-env", "non-dream"])
    def test_adds_no_dream_env_where_none_was_installed_or_set(
        self, monkeypatch: pytest.MonkeyPatch, system: str, installed: dict[str, str] | None
    ) -> None:
        spec = self._setup(monkeypatch, system, installed)

        assert not [k for k in spec.env if k.startswith("MCP_DREAM_")]


def _agent_spec(env: dict[str, str]) -> cli._ServiceSpec:
    """Service spec shaped like the retired memory-agent service."""
    return cli._ServiceSpec(
        name="memory-agent",
        binary_name="memory-agent",
        label="com.memory-agent",
        description="memory-agent recall server",
        port="8001",
        env=env,
        log_path=cli.Path("/tmp/memory-agent.log"),
        plist_path=cli.Path("/tmp/com.memory-agent.plist"),
        systemd_unit=cli.Path("/tmp/memory-agent.service"),
    )


class TestRemoveLegacyAgentService:
    def test_launchd_job_is_booted_out_and_plist_deleted(
        self, tmp_path: cli.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        plist = tmp_path / "com.memory-agent.plist"
        plist.write_text("<plist/>")
        monkeypatch.setattr(cli, "_LEGACY_AGENT_PLIST", plist)
        monkeypatch.setattr(cli.platform, "system", lambda: "Darwin")
        calls: list[list[str]] = []
        monkeypatch.setattr(cli.subprocess, "run", lambda cmd, **_kw: calls.append(cmd))

        cli._remove_legacy_agent_service()

        assert not plist.exists()
        assert any(c[:2] == ["launchctl", "bootout"] and c[-1] == str(plist) for c in calls)

    def test_systemd_unit_is_disabled_and_deleted(self, tmp_path: cli.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        unit = tmp_path / "memory-agent.service"
        unit.write_text("[Unit]")
        monkeypatch.setattr(cli, "_LEGACY_AGENT_UNIT", unit)
        monkeypatch.setattr(cli.platform, "system", lambda: "Linux")
        calls: list[list[str]] = []
        monkeypatch.setattr(cli.subprocess, "run", lambda cmd, **_kw: calls.append(cmd))

        cli._remove_legacy_agent_service()

        assert ["sudo", "systemctl", "disable", "--now", "memory-agent.service"] in calls
        assert ["sudo", "rm", str(unit)] in calls

    def test_does_nothing_when_no_legacy_service_is_installed(
        self, tmp_path: cli.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(cli, "_LEGACY_AGENT_PLIST", tmp_path / "missing.plist")
        monkeypatch.setattr(cli, "_LEGACY_AGENT_UNIT", tmp_path / "missing.service")
        calls: list[list[str]] = []
        monkeypatch.setattr(cli.subprocess, "run", lambda cmd, **_kw: calls.append(cmd))

        cli._remove_legacy_agent_service()

        assert calls == []


class TestRetiredAgentDreamEnvMigration:
    def _legacy_plist(self, tmp_path: cli.Path, monkeypatch: pytest.MonkeyPatch, env: dict[str, str]) -> cli.Path:
        plist = tmp_path / "com.memory-agent.plist"
        plist.write_text(cli._render_plist(_agent_spec(env), binary="/bin/memory-agent"))
        monkeypatch.setattr(cli, "_LEGACY_AGENT_PLIST", plist)
        return plist

    def _main_plist(self, tmp_path: cli.Path, monkeypatch: pytest.MonkeyPatch) -> cli.Path:
        plist = tmp_path / "com.mcp-memory.plist"
        plist.write_text(cli._render_plist(cli._memory_spec("3000", tmp_path / "m.db"), binary="/bin/mcp-memory"))
        monkeypatch.setattr(cli, "_LAUNCHD_PLIST", plist)
        return plist

    def test_launchd_dream_env_moves_to_main_service_without_overwriting_and_reloads_once(
        self, tmp_path: cli.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        legacy = self._legacy_plist(
            tmp_path, monkeypatch, {"MCP_DREAM_ENABLED": "true", "MCP_DREAM_IDLE_SECONDS": "60", "MCP_AGENT_PORT": "1"}
        )
        monkeypatch.setenv("MCP_DREAM_IDLE_SECONDS", "900")
        main = self._main_plist(tmp_path, monkeypatch)
        monkeypatch.setattr(cli.platform, "system", lambda: "Darwin")
        calls: list[list[str]] = []
        monkeypatch.setattr(cli.subprocess, "run", lambda cmd, **_kw: calls.append(cmd))

        cli._remove_legacy_agent_service()

        text = main.read_text()
        assert "<key>MCP_DREAM_ENABLED</key>\n                <string>true</string>" in text
        assert "<string>900</string>" in text
        assert "<string>60</string>" not in text
        assert "MCP_AGENT_PORT" not in text
        assert not legacy.exists()
        assert [c[1] for c in calls if str(main) in c or c[-1].endswith("com.mcp-memory")] == [
            "bootout",
            "bootstrap",
            "kickstart",
        ]

        legacy.write_text(cli._render_plist(_agent_spec({"MCP_DREAM_ENABLED": "true"}), binary="/bin/memory-agent"))
        calls.clear()
        cli._remove_legacy_agent_service()
        assert main.read_text() == text
        assert not any("com.mcp-memory" in " ".join(c) for c in calls)

    def test_systemd_dream_env_moves_to_main_unit_and_restarts(
        self, tmp_path: cli.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        legacy = tmp_path / "memory-agent.service"
        legacy.write_text(
            cli._render_systemd(_agent_spec({"MCP_DREAM_HEAVY_ENABLED": "true"}), binary="/bin/a", user="u")
        )
        main = tmp_path / "mcp-memory.service"
        main.write_text(cli._render_systemd(cli._memory_spec("3000", tmp_path / "m.db"), binary="/bin/m", user="u"))
        monkeypatch.setattr(cli, "_LEGACY_AGENT_UNIT", legacy)
        monkeypatch.setattr(cli, "_SYSTEMD_UNIT", main)
        monkeypatch.setattr(cli.platform, "system", lambda: "Linux")
        calls: list[list[str]] = []

        def fake_run(cmd: list[str], **kw: bytes) -> None:
            calls.append(cmd)
            if cmd[:2] == ["sudo", "tee"]:
                main.write_bytes(kw["input"])

        monkeypatch.setattr(cli.subprocess, "run", fake_run)

        cli._remove_legacy_agent_service()

        assert "Environment=MCP_DREAM_HEAVY_ENABLED=true" in main.read_text()
        assert ["sudo", "systemctl", "restart", "mcp-memory.service"] in calls

    def test_warns_instead_of_losing_dream_env_when_main_service_is_not_installed(
        self, tmp_path: cli.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        self._legacy_plist(tmp_path, monkeypatch, {"MCP_DREAM_ENABLED": "true"})
        monkeypatch.setattr(cli, "_LAUNCHD_PLIST", tmp_path / "absent.plist")
        monkeypatch.setattr(cli.platform, "system", lambda: "Darwin")
        monkeypatch.setattr(cli.subprocess, "run", lambda *_a, **_kw: None)

        cli._remove_legacy_agent_service()

        assert "MCP_DREAM_ENABLED" in capsys.readouterr().err

    @pytest.mark.parametrize("system", ["Darwin", "Linux"])
    @pytest.mark.parametrize("legacy_env", [{}, {"MCP_AGENT_PORT": "8001"}], ids=["no-env", "non-dream-env"])
    def test_retired_service_without_dream_env_leaves_dreaming_off(
        self, tmp_path: cli.Path, monkeypatch: pytest.MonkeyPatch, system: str, legacy_env: dict[str, str]
    ) -> None:
        for name in [k for k in os.environ if k.startswith("MCP_DREAM_")]:
            monkeypatch.delenv(name)
        darwin = system == "Darwin"
        legacy = tmp_path / ("com.memory-agent.plist" if darwin else "memory-agent.service")
        main = tmp_path / ("com.mcp-memory.plist" if darwin else "mcp-memory.service")
        render = cli._render_plist if darwin else functools.partial(cli._render_systemd, user="u")
        legacy.write_text(render(_agent_spec(legacy_env), binary="/bin/memory-agent"))
        main.write_text(render(cli._memory_spec("3000", tmp_path / "m.db"), binary="/bin/mcp-memory"))
        before = main.read_bytes()
        monkeypatch.setattr(cli, "_LEGACY_AGENT_PLIST" if darwin else "_LEGACY_AGENT_UNIT", legacy)
        monkeypatch.setattr(cli, "_LAUNCHD_PLIST" if darwin else "_SYSTEMD_UNIT", main)
        monkeypatch.setattr(cli.platform, "system", lambda: system)
        calls: list[list[str]] = []
        monkeypatch.setattr(cli.subprocess, "run", lambda cmd, **_kw: calls.append(cmd))

        cli._remove_legacy_agent_service()

        assert main.read_bytes() == before
        assert not any({"bootstrap", "kickstart", "tee", "restart"} & set(c) for c in calls)
        assert not any(str(main) in c or "com.mcp-memory" in " ".join(c) or "mcp-memory.service" in c for c in calls)
        assert not config.get_dream_enabled()
        assert not config.get_dream_heavy_enabled()
        assert dream._enabled_tiers() == []


class TestRegisterClaudeCodeServer:
    def test_adds_server_and_allow_rule(self, tmp_path: cli.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(cli.Path, "home", classmethod(lambda _cls: tmp_path))
        (tmp_path / ".claude").mkdir()
        calls: list[list[str]] = []

        def fake_run(cmd: list[str], **_kw: object) -> object:
            calls.append(cmd)
            return type("R", (), {"returncode": 1, "stdout": ""})()

        monkeypatch.setattr(cli.subprocess, "run", fake_run)
        cli._register_claude_code_server("/usr/bin/claude", "memory", "http://localhost:8000/mcp")

        allow = json.loads((tmp_path / ".claude" / "settings.json").read_text())
        assert "mcp__memory__*" in allow["permissions"]["allow"]
        assert any("add" in c for c in calls)


class TestRegisterCodexServer:
    def test_adds_server_when_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[list[str]] = []

        def fake_run(cmd: list[str], **_kw: object) -> object:
            calls.append(cmd)
            return type("R", (), {"returncode": 1, "stdout": ""})()

        monkeypatch.setattr(cli.subprocess, "run", fake_run)
        cli._register_codex_server("/usr/bin/codex", "memory", "http://127.0.0.1:8000/mcp")

        assert any("add" in c for c in calls)

    def test_skips_add_when_already_registered(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[list[str]] = []

        def fake_run(cmd: list[str], **_kw: object) -> object:
            calls.append(cmd)
            return type("R", (), {"returncode": 0, "stdout": ""})()

        monkeypatch.setattr(cli.subprocess, "run", fake_run)
        cli._register_codex_server("/usr/bin/codex", "memory", "http://127.0.0.1:8000/mcp")

        assert not any("add" in c for c in calls)


class TestRegisterPiServer:
    def test_adds_url_only_servers(self, tmp_path: cli.Path) -> None:
        mcp_path = tmp_path / "agent" / "mcp.json"
        cli._register_pi_server(mcp_path, "memory", "http://localhost:8000/mcp")

        data = json.loads(mcp_path.read_text(encoding="utf-8"))
        assert data["mcpServers"]["memory"] == {"url": "http://localhost:8000/mcp"}

    def test_migration_drops_only_adapter_keys(self, tmp_path: cli.Path) -> None:
        mcp_path = tmp_path / "mcp.json"
        legacy = {
            "url": "http://custom/mcp",
            "headers": {"X": "1"},
            "directTools": True,
            "toolPrefix": "mcp",
            "extra": 1,
        }
        mcp_path.write_text(json.dumps({"mcpServers": {"memory": legacy}}), encoding="utf-8")

        cli._register_pi_server(mcp_path, "memory", "http://localhost:8000/mcp")
        data = json.loads(mcp_path.read_text(encoding="utf-8"))

        assert data["mcpServers"]["memory"] == {"url": "http://custom/mcp", "headers": {"X": "1"}, "extra": 1}

    def test_keeps_existing_servers_and_settings(self, tmp_path: cli.Path) -> None:
        mcp_path = tmp_path / "mcp.json"
        existing = {"settings": {"toolPrefix": "server"}, "mcpServers": {"memory": {"url": "http://custom/mcp"}}}
        mcp_path.write_text(json.dumps(existing), encoding="utf-8")

        cli._register_pi_server(mcp_path, "memory", "http://localhost:8000/mcp")
        data = json.loads(mcp_path.read_text(encoding="utf-8"))

        assert data["settings"] == existing["settings"]
        assert data["mcpServers"]["memory"] == {"url": "http://custom/mcp"}

    def test_config_path_honours_agent_dir_override(self, tmp_path: cli.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PI_CODING_AGENT_DIR", str(tmp_path / "pi"))
        assert cli._pi_mcp_config_path() == tmp_path / "pi" / "mcp.json"


class TestInstallPiRemovesRetiredAgent:
    def test_drops_only_memory_agent_servers_when_memory_exists(
        self, tmp_path: cli.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PI_CODING_AGENT_DIR", str(tmp_path))
        monkeypatch.setattr(cli, "resolve_port", lambda: "8000")
        servers = {
            "memory": {"url": "http://custom/mcp"},
            "memory-agent": {"url": "http://localhost:8001/mcp"},
            "memory_agent": {"url": "http://localhost:8001/mcp"},
            "other": {"url": "http://other/mcp"},
        }
        mcp_path = tmp_path / "mcp.json"
        mcp_path.write_text(json.dumps({"settings": {"a": 1}, "mcpServers": servers}), encoding="utf-8")

        cli._cmd_install_pi()
        data = json.loads(mcp_path.read_text(encoding="utf-8"))

        assert data["settings"] == {"a": 1}
        assert data["mcpServers"] == {"memory": servers["memory"], "other": servers["other"]}

    def test_second_run_leaves_the_file_untouched(self, tmp_path: cli.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PI_CODING_AGENT_DIR", str(tmp_path))
        monkeypatch.setattr(cli, "resolve_port", lambda: "8000")
        mcp_path = tmp_path / "mcp.json"
        mcp_path.write_text('{"mcpServers": {"memory": {"url": "u"}, "memory_agent": {"url": "x"}}}', encoding="utf-8")

        cli._cmd_install_pi()
        after_first = mcp_path.read_text(encoding="utf-8")
        cli._cmd_install_pi()

        assert mcp_path.read_text(encoding="utf-8") == after_first


class TestInstallRemovesRetiredAgentService:
    def test_install_deletes_the_service_even_when_memory_is_already_registered(
        self, tmp_path: cli.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        plist = tmp_path / "com.memory-agent.plist"
        plist.write_text("<plist/>")
        monkeypatch.setattr(cli, "_LEGACY_AGENT_PLIST", plist)
        monkeypatch.setattr(cli.platform, "system", lambda: "Darwin")
        monkeypatch.setattr(cli.subprocess, "run", lambda *_a, **_kw: None)
        monkeypatch.setenv("PI_CODING_AGENT_DIR", str(tmp_path))
        monkeypatch.setattr(cli, "resolve_port", lambda: "8000")
        (tmp_path / "mcp.json").write_text('{"mcpServers": {"memory": {"url": "u"}}}', encoding="utf-8")

        cli._cmd_install(cli._build_parser().parse_args(["install", "pi"]), cli._build_parser())

        assert not plist.exists()


class TestInstallClaudeCodeRemovesRetiredAgent:
    def _run(self, tmp_path: cli.Path, monkeypatch: pytest.MonkeyPatch, registered: set[str]) -> list[list[str]]:
        monkeypatch.setattr(cli.Path, "home", classmethod(lambda _cls: tmp_path))
        (tmp_path / ".claude").mkdir(exist_ok=True)
        monkeypatch.setattr(cli.shutil, "which", lambda _cmd: "/usr/bin/claude")
        monkeypatch.setattr(cli, "resolve_port", lambda: "8000")
        calls: list[list[str]] = []

        def fake_run(cmd: list[str], **_kw: object) -> object:
            calls.append(cmd)
            known = cmd[1:3] == ["mcp", "get"] and cmd[3] in registered
            return type("R", (), {"returncode": 0 if known else 1, "stdout": ""})()

        monkeypatch.setattr(cli.subprocess, "run", fake_run)
        cli._cmd_install_claude_code()
        return calls

    def test_removes_registered_servers_and_allow_rules(
        self, tmp_path: cli.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / ".claude").mkdir()
        settings_path = tmp_path / ".claude" / "settings.json"
        allow = ["mcp__memory-agent__*", "mcp__memory_agent__*", "mcp__memory__*", "Bash(ls)"]
        settings_path.write_text(json.dumps({"model": "x", "permissions": {"allow": allow}}), encoding="utf-8")

        calls = self._run(tmp_path, monkeypatch, {"memory-agent"})

        settings = json.loads(settings_path.read_text(encoding="utf-8"))
        assert settings["permissions"]["allow"] == ["mcp__memory__*", "Bash(ls)"]
        assert settings["model"] == "x"
        assert ["/usr/bin/claude", "mcp", "remove", "--scope", "user", "memory-agent"] in calls
        assert not any(c[:3] == ["/usr/bin/claude", "mcp", "remove"] and c[-1] == "memory_agent" for c in calls)

    def test_does_nothing_when_nothing_is_stale(self, tmp_path: cli.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = self._run(tmp_path, monkeypatch, {"memory"})

        assert not any("remove" in c for c in calls)


class TestInstallCodexRemovesRetiredAgent:
    def _run(self, monkeypatch: pytest.MonkeyPatch, registered: set[str]) -> list[list[str]]:
        monkeypatch.setattr(cli.shutil, "which", lambda _cmd: "/usr/bin/codex")
        monkeypatch.setattr(cli, "resolve_port", lambda: "8000")
        calls: list[list[str]] = []

        def fake_run(cmd: list[str], **_kw: object) -> object:
            calls.append(cmd)
            known = cmd[1:3] == ["mcp", "get"] and cmd[3] in registered
            return type("R", (), {"returncode": 0 if known else 1, "stdout": ""})()

        monkeypatch.setattr(cli.subprocess, "run", fake_run)
        cli._cmd_install_codex()
        return calls

    def test_removes_registered_servers(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = self._run(monkeypatch, {"memory", "memory_agent"})

        assert ["/usr/bin/codex", "mcp", "remove", "memory_agent"] in calls
        assert not any(c[:3] == ["/usr/bin/codex", "mcp", "remove"] and c[-1] != "memory_agent" for c in calls)

    def test_does_nothing_when_nothing_is_stale(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assert not any("remove" in c for c in self._run(monkeypatch, {"memory"}))


class TestInstallAntigravityRemovesRetiredAgent:
    def test_drops_only_memory_agent_servers(self, tmp_path: cli.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(cli.Path, "home", classmethod(lambda _cls: tmp_path))
        monkeypatch.setattr(cli.shutil, "which", lambda _cmd: None)
        monkeypatch.setattr(cli, "resolve_port", lambda: "8000")
        mcp_path = tmp_path / ".gemini" / "config" / "mcp_config.json"
        mcp_path.parent.mkdir(parents=True)
        servers = {"memory-agent": {"serverUrl": "a"}, "memory_agent": {"serverUrl": "b"}, "other": {"serverUrl": "c"}}
        mcp_path.write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")

        cli._cmd_install_antigravity()

        assert sorted(json.loads(mcp_path.read_text(encoding="utf-8"))["mcpServers"]) == ["memory", "other"]


class TestInstallKiroRemovesRetiredAgent:
    def test_drops_only_memory_agent_entries(self, tmp_path: cli.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(cli.Path, "home", classmethod(lambda _cls: tmp_path))
        agent_path = tmp_path / "agent.json"
        agent = {
            "name": "a",
            "mcpServers": {"memory": {"url": "u"}, "memory-agent": {"url": "x"}, "memory_agent": {"url": "y"}},
            "allowedTools": ["@memory", "@memory-agent", "@memory_agent", "fs_read"],
            "tools": ["@memory", "@memory-agent", "fs_read"],
        }
        agent_path.write_text(json.dumps(agent), encoding="utf-8")

        cli._cmd_install_kiro(argparse.Namespace(port="8000", agent_config=str(agent_path)))
        data = json.loads(agent_path.read_text(encoding="utf-8"))

        assert data["mcpServers"] == {"memory": {"url": "u"}}
        assert data["allowedTools"] == ["@memory", "fs_read"]
        assert data["tools"] == ["@memory", "fs_read"]
        assert data["name"] == "a"


class TestUninstallCopilot:
    def test_removes_memory_servers_and_keeps_the_rest_of_the_jsonc_config(self, tmp_path: cli.Path) -> None:
        mcp_path = tmp_path / "mcp.json"
        servers = {
            "memory": {"type": "http", "url": "u"},
            "memory-agent": {"url": "x"},
            "memory_agent": {"url": "y"},
            "github": {"type": "http", "url": "g"},
        }
        mcp_path.write_text("// c\n" + json.dumps({"inputs": [1], "servers": servers}), encoding="utf-8")

        cli._cmd_uninstall_copilot(argparse.Namespace(mcp_config=str(mcp_path)))
        data = json.loads(mcp_path.read_text(encoding="utf-8"))

        assert data["servers"] == {"github": servers["github"]}
        assert data["inputs"] == [1]

    def test_missing_config_is_not_an_error(self, tmp_path: cli.Path) -> None:
        cli._cmd_uninstall_copilot(argparse.Namespace(mcp_config=str(tmp_path / "mcp.json")))


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

    def test_prefers_xdg_config_home_on_linux(self, tmp_path: cli.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("mcp_memory.cli.platform.system", lambda: "Linux")
        monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)
        monkeypatch.setattr(cli.Path, "home", classmethod(lambda _cls: tmp_path / "home"))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
        xdg = tmp_path / "xdg" / "Code" / "User"
        xdg.mkdir(parents=True)
        (xdg / "mcp.json").write_text("{}\n", encoding="utf-8")

        resolved = cli._default_copilot_mcp_config_path()
        assert str(resolved) == str(xdg / "mcp.json")

    def test_uses_macos_user_path(self, tmp_path: cli.Path, monkeypatch: pytest.MonkeyPatch) -> None:
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

    def test_bakes_path_so_spawned_claude_is_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PATH", "/custom/bin:/usr/bin")
        spec = cli._memory_spec("3000", cli.Path("/data/memory.db"))
        assert spec.env["PATH"] == "/custom/bin:/usr/bin"

    def test_baked_path_leaves_out_venv_directories(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        venv_bin = _make_bin_dir(tmp_path, "venv", venv=True)
        user_bin = _make_bin_dir(tmp_path, "user", venv=False)
        monkeypatch.setenv("PATH", f"{venv_bin}:{user_bin}")
        spec = cli._memory_spec("3000", cli.Path("/data/memory.db"))
        assert spec.env["PATH"] == str(user_bin)

    def test_propagates_backup_env_into_service(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MCP_MEMORY_BACKUP_ENABLED", "false")
        monkeypatch.setenv("MCP_MEMORY_BACKUP_KEEP_DAILY", "3")
        spec = cli._memory_spec("3000", cli.Path("/data/memory.db"))
        assert spec.env["MCP_MEMORY_BACKUP_ENABLED"] == "false"
        assert spec.env["MCP_MEMORY_BACKUP_KEEP_DAILY"] == "3"

    def test_propagates_dream_env_into_service(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MCP_DREAM_ENABLED", "false")
        monkeypatch.setenv("MCP_DREAM_IDLE_SECONDS", "60")
        spec = cli._memory_spec("3000", cli.Path("/data/memory.db"))
        assert spec.env["MCP_DREAM_ENABLED"] == "false"
        assert spec.env["MCP_DREAM_IDLE_SECONDS"] == "60"

    def test_propagates_dream_heavy_env_into_service(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MCP_DREAM_HEAVY_ENABLED", "true")
        monkeypatch.setenv("MCP_DREAM_HEAVY_INTERVAL_SECONDS", "90")
        spec = cli._memory_spec("3000", cli.Path("/data/memory.db"))
        assert spec.env["MCP_DREAM_HEAVY_ENABLED"] == "true"
        assert spec.env["MCP_DREAM_HEAVY_INTERVAL_SECONDS"] == "90"


def _make_bin_dir(parent: Path, name: str, *, venv: bool) -> Path:
    """Create `<parent>/<name>/bin/tool`, with a `pyvenv.cfg` beside `bin` where venv is set."""
    bin_dir = parent / name / "bin"
    bin_dir.mkdir(parents=True)
    tool = bin_dir / "tool"
    tool.write_text("#!/bin/sh\n")
    tool.chmod(0o755)
    if venv:
        (parent / name / "pyvenv.cfg").write_text("")
    return bin_dir


class TestFindBinary:
    def test_returns_the_user_wide_binary_not_venv_one(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        venv_bin = _make_bin_dir(tmp_path, "venv", venv=True)
        user_bin = _make_bin_dir(tmp_path, "user", venv=False)
        monkeypatch.setenv("PATH", f"{venv_bin}:{user_bin}")
        assert cli._find_binary("tool") == str(user_bin / "tool")

    def test_exits_when_only_a_venv_has_the_binary(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PATH", str(_make_bin_dir(tmp_path, "venv", venv=True)))
        with pytest.raises(SystemExit):
            cli._find_binary("tool")


class TestRenderPlist:
    def test_includes_label_binary_and_every_env_key(self) -> None:
        spec = cli._memory_spec("3000", cli.Path("/data/memory.db"))
        plist = cli._render_plist(spec, binary="/usr/bin/mcp-memory")
        assert "<string>com.mcp-memory</string>" in plist
        assert "<string>/usr/bin/mcp-memory</string>" in plist
        assert "<key>MCP_MEMORY_DB_PATH</key>" in plist
        assert "<key>MCP_MEMORY_PORT</key>" in plist


class TestRenderSystemd:
    def test_includes_exec_and_env_lines(self) -> None:
        spec = cli._memory_spec("3000", cli.Path("/data/memory.db"))
        unit = cli._render_systemd(spec, binary="/usr/bin/mcp-memory", user="alice")
        assert "ExecStart=/usr/bin/mcp-memory" in unit
        assert "Environment=MCP_MEMORY_PORT=3000" in unit
        assert "User=alice" in unit


class TestSetupLaunchd:
    def test_refuses_when_label_is_loaded_from_another_plist(
        self, tmp_path: cli.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        plist = tmp_path / "com.mcp-memory.plist"
        monkeypatch.setattr(cli, "_LAUNCHD_PLIST", plist)
        calls: list[list[str]] = []

        def fake_run(cmd: list[str], **_kw: object) -> object:
            calls.append(cmd)
            stdout = "\tstdout path = /x.log\n\tpath = /other/com.mcp-memory.plist\n"
            return type("R", (), {"returncode": 0, "stdout": stdout})()

        monkeypatch.setattr(cli.subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            cli._setup_launchd(cli._memory_spec("3000", tmp_path / "m.db"), "/bin/mcp-memory")

        assert exc_info.value.code == 1
        assert [cmd[1] for cmd in calls] == ["print"]
        assert not plist.exists()
        assert "/other/com.mcp-memory.plist" in capsys.readouterr().err


class TestSetupSystemd:
    def test_refuses_to_replace_unit_when_home_is_redirected(
        self, tmp_path: cli.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        unit = tmp_path / "mcp-memory.service"
        unit.write_text("", encoding="utf-8")
        monkeypatch.setattr(cli, "_SYSTEMD_UNIT", unit)
        monkeypatch.setenv("HOME", str(tmp_path / "redirected"))
        calls: list[list[str]] = []
        monkeypatch.setattr(cli.subprocess, "run", lambda cmd, **_kw: calls.append(cmd))

        with pytest.raises(SystemExit) as exc_info:
            cli._setup_systemd(cli._memory_spec("3000", tmp_path / "m.db"), "/bin/mcp-memory")

        assert exc_info.value.code == 1
        assert calls == []
        assert str(unit) in capsys.readouterr().err


class TestCmdDream:
    @staticmethod
    def _fake_urlopen(monkeypatch: pytest.MonkeyPatch, *responses: object) -> list[cli.urllib.request.Request]:
        """Serve the responses in order (an exception is raised), recording each request."""
        requests: list[cli.urllib.request.Request] = []
        queue = list(responses)

        def fake_urlopen(request: cli.urllib.request.Request, timeout: float) -> object:
            requests.append(request)
            response = queue.pop(0)
            if isinstance(response, Exception):
                raise response
            return io.BytesIO(json.dumps(response).encode())

        monkeypatch.setattr(cli.urllib.request, "urlopen", fake_urlopen)
        return requests

    @staticmethod
    def _conflict(reason: str) -> cli.urllib.error.HTTPError:
        body = io.BytesIO(json.dumps({"started": False, "reason": reason}).encode())
        return cli.urllib.error.HTTPError("http://x", 409, "Conflict", None, body)  # type: ignore[arg-type]

    def test_posts_the_tier_and_reports_started(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("MCP_MEMORY_URL", "http://example.test:4321/mcp")
        monkeypatch.setenv("MCP_MEMORY_PORT", "9999")
        requests = self._fake_urlopen(monkeypatch, {"started": True})

        cli._cmd_dream(argparse.Namespace(tier="heavy", wait=False))

        assert requests[0].full_url == "http://example.test:4321/api/dream/trigger"
        assert requests[0].get_method() == "POST"
        assert json.loads(requests[0].data) == {"tier": "heavy"}  # type: ignore[arg-type]
        assert "heavy" in capsys.readouterr().out

    def test_refusal_prints_the_reason_and_exits_nonzero(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        self._fake_urlopen(monkeypatch, self._conflict("already running"))

        with pytest.raises(SystemExit) as exit_info:
            cli._cmd_dream(argparse.Namespace(tier="light", wait=False))

        assert exit_info.value.code == 1
        assert "already running" in capsys.readouterr().err

    def test_server_down_exits_nonzero(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        self._fake_urlopen(monkeypatch, cli.urllib.error.URLError("refused"))

        with pytest.raises(SystemExit) as exit_info:
            cli._cmd_dream(argparse.Namespace(tier="light", wait=False))

        assert exit_info.value.code == 1
        assert "not running" in capsys.readouterr().err

    def test_wait_polls_until_the_pass_finishes_then_prints_it(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(cli, "time", SimpleNamespace(sleep=lambda _: None))
        requests = self._fake_urlopen(
            monkeypatch,
            {"started": True},
            {"running": {"tier": "light"}, "last_pass": None},
            {"running": None, "last_pass": {"tier": "light", "summary": "LAST-PASS-MARK"}},
        )

        cli._cmd_dream(argparse.Namespace(tier="light", wait=True))

        assert [r.full_url.rsplit("/api/", 1)[1] for r in requests] == ["dream/trigger", "dream", "dream"]
        assert "LAST-PASS-MARK" in capsys.readouterr().out

    def test_wait_prints_a_usage_summary(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(cli, "time", SimpleNamespace(sleep=lambda _: None))
        metrics = {
            "input_tokens": 123_000,
            "output_tokens": 4_000,
            "cache_read_tokens": 98_000,
            "num_turns": 37,
            "cost_usd": 1.234,
            "duration_ms": 361_000,
        }
        self._fake_urlopen(
            monkeypatch, {"started": True}, {"running": None, "last_pass": {"tier": "light", "metrics": metrics}}
        )

        cli._cmd_dream(argparse.Namespace(tier="light", wait=True))

        assert "tokens: 123k in / 4k out / 98k cache read, 37 turns, $1.23, 361s" in capsys.readouterr().out

    def test_wait_prints_a_count_per_action(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(cli, "time", SimpleNamespace(sleep=lambda _: None))
        operations = [{"action": "merge"}, {"action": "merge"}, {"action": "trim"}]
        self._fake_urlopen(
            monkeypatch, {"started": True}, {"running": None, "last_pass": {"tier": "heavy", "operations": operations}}
        )

        cli._cmd_dream(argparse.Namespace(tier="heavy", wait=True))

        assert "actions: merge 2, trim 1" in capsys.readouterr().out

    def test_wait_prints_each_new_progress_line_once(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(cli, "time", SimpleNamespace(sleep=lambda _: None))

        def progress(*calls: str) -> dict[str, object]:
            return {"reads": 2, "calls": list(calls)}

        self._fake_urlopen(
            monkeypatch,
            {"started": True},
            {"running": "light", "last_pass": None, "progress": progress("vote p a -1")},
            {"running": "light", "last_pass": None, "progress": progress("vote p a -1", "rename_entity p b c")},
            {
                "running": None,
                "last_pass": {"tier": "light"},
                "progress": progress("vote p a -1", "rename_entity p b c", "vote p d -1"),
            },
        )

        cli._cmd_dream(argparse.Namespace(tier="light", wait=True))

        out = capsys.readouterr().out
        assert out.count("vote p a -1") == 1
        assert out.count("rename_entity p b c") == 1
        assert out.index("vote p d -1") < out.index('"tier": "light"')


class TestCmdRestore:
    @pytest.fixture(autouse=True)
    def _server_stopped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            monkeypatch.setenv("MCP_MEMORY_PORT", str(probe.getsockname()[1]))

    @staticmethod
    def _restore(backup_file: Path) -> None:
        cli._cmd_restore(argparse.Namespace(backup_file=str(backup_file)))

    def test_refuses_while_the_server_is_running(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        snapshot = tmp_path / "snapshot.db"
        snapshot.touch()
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            monkeypatch.setenv("MCP_MEMORY_PORT", str(listener.getsockname()[1]))
            with pytest.raises(SystemExit):
                self._restore(snapshot)
        assert "running" in capsys.readouterr().err

    def test_reports_a_current_database_that_cannot_be_preserved_without_blaming_the_backup(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        source = tmp_path / "source.db"
        open_writable(source).connection.close()
        snapshot = backup.create_backup(source, tmp_path / "backups", datetime(2026, 1, 2, tzinfo=UTC))
        config.get_db_path().write_bytes(b"damaged")
        blocker = tmp_path / "blocker"
        blocker.touch()
        monkeypatch.setenv("MCP_MEMORY_BACKUP_DIR", str(blocker))
        with pytest.raises(SystemExit):
            self._restore(snapshot)
        err = capsys.readouterr().err
        assert "restore failed" in err
        assert "not a valid" not in err
        assert config.get_db_path().read_bytes() == b"damaged"


class TestCmdRestart:
    def test_restarts_via_launchd_when_plist_installed(
        self, tmp_path: cli.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        plist = tmp_path / "com.mcp-memory.plist"
        plist.write_text("", encoding="utf-8")
        monkeypatch.setattr(cli, "_LAUNCHD_PLIST", plist)
        monkeypatch.setattr(cli.platform, "system", lambda: "Darwin")
        calls: list[list[str]] = []

        def fake_run(cmd: list[str], **_kw: object) -> object:
            calls.append(cmd)
            return type("R", (), {"returncode": 0})()

        monkeypatch.setattr(cli.subprocess, "run", fake_run)
        cli._cmd_restart()

        assert calls[0][:2] == ["launchctl", "kickstart"]
        assert "-k" in calls[0]

    def test_restarts_via_systemd_when_unit_installed(
        self, tmp_path: cli.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        unit = tmp_path / "mcp-memory.service"
        unit.write_text("", encoding="utf-8")
        monkeypatch.setattr(cli, "_LAUNCHD_PLIST", tmp_path / "absent.plist")
        monkeypatch.setattr(cli, "_SYSTEMD_UNIT", unit)
        monkeypatch.setattr(cli.platform, "system", lambda: "Linux")
        calls: list[list[str]] = []

        def fake_run(cmd: list[str], **_kw: object) -> object:
            calls.append(cmd)
            return type("R", (), {"returncode": 0})()

        monkeypatch.setattr(cli.subprocess, "run", fake_run)
        cli._cmd_restart()

        assert calls[0] == ["sudo", "systemctl", "restart", unit.name]

    def test_reports_when_no_service_installed(
        self,
        tmp_path: cli.Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setattr(cli, "_LAUNCHD_PLIST", tmp_path / "absent.plist")
        monkeypatch.setattr(cli, "_SYSTEMD_UNIT", tmp_path / "absent.service")
        monkeypatch.setattr(cli.platform, "system", lambda: "Darwin")

        cli._cmd_restart()

        assert "No mcp-memory service installed" in capsys.readouterr().out

    def test_exits_nonzero_when_restart_command_fails(
        self, tmp_path: cli.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        plist = tmp_path / "com.mcp-memory.plist"
        plist.write_text("", encoding="utf-8")
        monkeypatch.setattr(cli, "_LAUNCHD_PLIST", plist)
        monkeypatch.setattr(cli.platform, "system", lambda: "Darwin")
        monkeypatch.setattr(cli.subprocess, "run", lambda *_a, **_kw: type("R", (), {"returncode": 1})())

        with pytest.raises(SystemExit) as exc_info:
            cli._cmd_restart()
        assert exc_info.value.code == 1


class TestInstallAntigravity:
    def test_adds_server_when_not_registered(
        self, tmp_path: cli.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(cli.Path, "home", classmethod(lambda _cls: tmp_path))
        calls: list[list[str]] = []

        def fake_run(cmd: list[str], *args: object, **kwargs: object) -> object:
            calls.append(cmd)
            if "list" in cmd:
                return type("R", (), {"returncode": 0, "stdout": "other-server"})()
            return type("R", (), {"returncode": 0, "stdout": ""})()

        monkeypatch.setattr(cli.shutil, "which", lambda cmd: "/usr/bin/agy" if cmd == "agy" else None)
        monkeypatch.setattr(cli.subprocess, "run", fake_run)
        monkeypatch.setattr(cli, "resolve_port", lambda: "8000")

        cli._cmd_install_antigravity()

        assert any("add" in cmd and "memory" in cmd for cmd in calls)
        assert "Added memory MCP server to Antigravity" in capsys.readouterr().out

    def test_skips_when_already_configured(
        self, tmp_path: cli.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(cli.Path, "home", classmethod(lambda _cls: tmp_path))
        calls: list[list[str]] = []

        def fake_run(cmd: list[str], *args: object, **kwargs: object) -> object:
            calls.append(cmd)
            return type("R", (), {"returncode": 0, "stdout": "memory  http  enabled"})()

        monkeypatch.setattr(cli.shutil, "which", lambda cmd: "/usr/bin/agy" if cmd == "agy" else None)
        monkeypatch.setattr(cli.subprocess, "run", fake_run)

        cli._cmd_install_antigravity()

        assert not any("add" in cmd for cmd in calls)
        assert "already configured in Antigravity" in capsys.readouterr().out
