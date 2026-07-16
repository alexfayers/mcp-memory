"""Tests for configuration resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

from mcp_memory import config


class TestMemoryUrl:
    def test_explicit_url_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MCP_MEMORY_URL", "http://example:9/mcp")
        assert config.get_memory_url() == "http://example:9/mcp"

    def test_uses_memory_port_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MCP_MEMORY_URL", raising=False)
        monkeypatch.setenv("MCP_MEMORY_PORT", "7777")
        monkeypatch.setattr(config, "detect_service_port", lambda: None)
        assert config.get_memory_url() == "http://localhost:7777/mcp"

    def test_detects_running_service_port(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MCP_MEMORY_URL", raising=False)
        monkeypatch.delenv("MCP_MEMORY_PORT", raising=False)
        monkeypatch.setattr(config, "detect_service_port", lambda: "3000")
        assert config.get_memory_url() == "http://localhost:3000/mcp"

    def test_falls_back_to_default_port(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MCP_MEMORY_URL", raising=False)
        monkeypatch.delenv("MCP_MEMORY_PORT", raising=False)
        monkeypatch.setattr(config, "detect_service_port", lambda: None)
        assert config.get_memory_url() == "http://localhost:8000/mcp"


class TestRecallConfig:
    def test_max_turns_default_and_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MCP_RECALL_MAX_TURNS", raising=False)
        assert config.get_recall_max_turns() == 12
        monkeypatch.setenv("MCP_RECALL_MAX_TURNS", "6")
        assert config.get_recall_max_turns() == 6


class TestDreamConfig:
    def test_disabled_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MCP_DREAM_ENABLED", raising=False)
        assert config.get_dream_enabled() is False

    @pytest.mark.parametrize("value", ["true", "1", "yes", "on", "TRUE"])
    def test_enabled_by_truthy_env(self, value: str, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MCP_DREAM_ENABLED", value)
        assert config.get_dream_enabled() is True

    @pytest.mark.parametrize("value", ["false", "0", "no", "off", "nonsense"])
    def test_disabled_by_non_truthy_env(self, value: str, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MCP_DREAM_ENABLED", value)
        assert config.get_dream_enabled() is False

    def test_idle_seconds_default_and_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MCP_DREAM_IDLE_SECONDS", raising=False)
        assert config.get_dream_idle_seconds() == 7200.0
        monkeypatch.setenv("MCP_DREAM_IDLE_SECONDS", "60")
        assert config.get_dream_idle_seconds() == 60.0

    def test_poll_seconds_default_and_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MCP_DREAM_POLL_SECONDS", raising=False)
        assert config.get_dream_poll_seconds() == 1800.0
        monkeypatch.setenv("MCP_DREAM_POLL_SECONDS", "5")
        assert config.get_dream_poll_seconds() == 5.0

    def test_model_defaults_to_recall_model(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MCP_DREAM_MODEL", raising=False)
        assert config.get_dream_model() == config.get_recall_model()
        monkeypatch.setenv("MCP_DREAM_MODEL", "some-model")
        assert config.get_dream_model() == "some-model"

    def test_timeout_default_and_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MCP_DREAM_TIMEOUT", raising=False)
        assert config.get_dream_timeout() == 300.0
        monkeypatch.setenv("MCP_DREAM_TIMEOUT", "10")
        assert config.get_dream_timeout() == 10.0

    def test_max_votes_default_and_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MCP_DREAM_MAX_VOTES", raising=False)
        assert config.get_dream_max_votes() == 15
        monkeypatch.setenv("MCP_DREAM_MAX_VOTES", "3")
        assert config.get_dream_max_votes() == 3


class TestDreamHeavyConfig:
    def test_disabled_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MCP_DREAM_HEAVY_ENABLED", raising=False)
        assert config.get_dream_heavy_enabled() is False

    @pytest.mark.parametrize("value", ["true", "1", "yes", "on", "TRUE"])
    def test_enabled_by_truthy_env(self, value: str, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MCP_DREAM_HEAVY_ENABLED", value)
        assert config.get_dream_heavy_enabled() is True

    @pytest.mark.parametrize("value", ["false", "0", "no", "off", "nonsense"])
    def test_disabled_by_non_truthy_env(self, value: str, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MCP_DREAM_HEAVY_ENABLED", value)
        assert config.get_dream_heavy_enabled() is False

    def test_idle_seconds_default_and_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MCP_DREAM_HEAVY_IDLE_SECONDS", raising=False)
        assert config.get_dream_heavy_idle_seconds() == 7200.0
        monkeypatch.setenv("MCP_DREAM_HEAVY_IDLE_SECONDS", "60")
        assert config.get_dream_heavy_idle_seconds() == 60.0

    def test_interval_seconds_default_and_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MCP_DREAM_HEAVY_INTERVAL_SECONDS", raising=False)
        assert config.get_dream_heavy_interval_seconds() == 86400.0
        monkeypatch.setenv("MCP_DREAM_HEAVY_INTERVAL_SECONDS", "120")
        assert config.get_dream_heavy_interval_seconds() == 120.0

    def test_poll_seconds_default_and_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MCP_DREAM_HEAVY_POLL_SECONDS", raising=False)
        assert config.get_dream_heavy_poll_seconds() == 3600.0
        monkeypatch.setenv("MCP_DREAM_HEAVY_POLL_SECONDS", "5")
        assert config.get_dream_heavy_poll_seconds() == 5.0

    def test_model_defaults_to_dream_model(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MCP_DREAM_HEAVY_MODEL", raising=False)
        assert config.get_dream_heavy_model() == config.get_dream_model()
        monkeypatch.setenv("MCP_DREAM_HEAVY_MODEL", "some-heavy-model")
        assert config.get_dream_heavy_model() == "some-heavy-model"

    def test_timeout_default_and_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MCP_DREAM_HEAVY_TIMEOUT", raising=False)
        assert config.get_dream_heavy_timeout() == 600.0
        monkeypatch.setenv("MCP_DREAM_HEAVY_TIMEOUT", "30")
        assert config.get_dream_heavy_timeout() == 30.0

    def test_max_ops_default_and_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MCP_DREAM_HEAVY_MAX_OPS", raising=False)
        assert config.get_dream_heavy_max_ops() == 10
        monkeypatch.setenv("MCP_DREAM_HEAVY_MAX_OPS", "2")
        assert config.get_dream_heavy_max_ops() == 2


class TestPurgeConfig:
    def test_disabled_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MCP_MEMORY_PURGE_ENABLED", raising=False)
        assert config.get_purge_enabled() is False

    @pytest.mark.parametrize("value", ["true", "1", "yes", "on", "TRUE"])
    def test_enabled_by_truthy_env(self, value: str, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MCP_MEMORY_PURGE_ENABLED", value)
        assert config.get_purge_enabled() is True

    def test_grace_days_default_and_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MCP_MEMORY_PURGE_GRACE_DAYS", raising=False)
        assert config.get_purge_grace_days() == 30
        monkeypatch.setenv("MCP_MEMORY_PURGE_GRACE_DAYS", "7")
        assert config.get_purge_grace_days() == 7


class TestGcConfig:
    def test_disabled_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MCP_MEMORY_GC_ENABLED", raising=False)
        assert config.get_gc_enabled() is False

    @pytest.mark.parametrize("value", ["true", "1", "yes", "on", "TRUE"])
    def test_enabled_by_truthy_env(self, value: str, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MCP_MEMORY_GC_ENABLED", value)
        assert config.get_gc_enabled() is True

    @pytest.mark.parametrize("value", ["false", "0", "no", "off", "nonsense"])
    def test_disabled_by_non_truthy_env(self, value: str, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MCP_MEMORY_GC_ENABLED", value)
        assert config.get_gc_enabled() is False


class TestDetectServicePort:
    def test_reads_launchd_plist(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        plist = tmp_path / "com.mcp-memory.plist"
        plist.write_text("<key>MCP_MEMORY_PORT</key>\n<string>3000</string>", encoding="utf-8")
        monkeypatch.setattr(config, "_LAUNCHD_PLIST", plist)
        monkeypatch.setattr(config, "_SYSTEMD_UNIT", tmp_path / "absent.service")
        assert config.detect_service_port() == "3000"

    def test_reads_systemd_unit(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        unit = tmp_path / "mcp-memory.service"
        unit.write_text("Environment=MCP_MEMORY_PORT=4200\n", encoding="utf-8")
        monkeypatch.setattr(config, "_LAUNCHD_PLIST", tmp_path / "absent.plist")
        monkeypatch.setattr(config, "_SYSTEMD_UNIT", unit)
        assert config.detect_service_port() == "4200"

    def test_none_when_no_service(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(config, "_LAUNCHD_PLIST", tmp_path / "absent.plist")
        monkeypatch.setattr(config, "_SYSTEMD_UNIT", tmp_path / "absent.service")
        assert config.detect_service_port() is None
