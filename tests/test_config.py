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
