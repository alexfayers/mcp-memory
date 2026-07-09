"""Shared configuration for the mcp-memory package."""

from __future__ import annotations

import os
import re
from pathlib import Path

_DEFAULT_DB_PATH = "~/.local/share/mcp-memory/memory.db"
_DEFAULT_PORT = "8000"
_DEFAULT_AGENT_PORT = "8100"
_DEFAULT_RECALL_MODEL = "global.anthropic.claude-haiku-4-5-20251001-v1:0"

_LAUNCHD_PLIST = Path.home() / "Library" / "LaunchAgents" / "com.mcp-memory.plist"
_SYSTEMD_UNIT = Path("/etc/systemd/system/mcp-memory.service")
_PLIST_PORT_RE = re.compile(r"<key>MCP_MEMORY_PORT</key>\s*<string>(\d+)</string>")


def detect_service_port() -> str | None:
    """Return the port of an installed mcp-memory service, or None if none is installed."""
    if _LAUNCHD_PLIST.exists():
        match = _PLIST_PORT_RE.search(_LAUNCHD_PLIST.read_text(encoding="utf-8"))
        if match:
            return match.group(1)
    if _SYSTEMD_UNIT.exists():
        for line in _SYSTEMD_UNIT.read_text(encoding="utf-8").splitlines():
            if line.startswith("Environment=MCP_MEMORY_PORT="):
                return line.split("=", 2)[2]
    return None


def get_default_db_path() -> Path:
    """Return the built-in default database path, ignoring any env override."""
    return Path(_DEFAULT_DB_PATH).expanduser()


def get_db_path() -> Path:
    """Return the resolved database file path."""
    return Path(os.environ.get("MCP_MEMORY_DB_PATH", _DEFAULT_DB_PATH)).expanduser()


def get_data_dir() -> Path:
    """Return the directory containing the database file."""
    return get_db_path().parent


def get_agent_port() -> int:
    """Return the HTTP port for the memory-agent server."""
    return int(os.environ.get("MCP_AGENT_PORT", _DEFAULT_AGENT_PORT))


def get_memory_url() -> str:
    """Return the URL of the mcp-memory server that recall queries.

    Prefers an explicit MCP_MEMORY_URL, then MCP_MEMORY_PORT, then the port of an
    installed service, and finally the default port.
    """
    explicit = os.environ.get("MCP_MEMORY_URL")
    if explicit:
        return explicit
    port = os.environ.get("MCP_MEMORY_PORT") or detect_service_port() or _DEFAULT_PORT
    return f"http://localhost:{port}/mcp"


def get_recall_model() -> str:
    """Return the fully-qualified model id used for recall spawns."""
    return os.environ.get("MCP_RECALL_MODEL", _DEFAULT_RECALL_MODEL)


def get_preflight_command() -> str | None:
    """Return the optional pre-flight command run before spawning a recall agent."""
    return os.environ.get("MCP_AGENT_PREFLIGHT_COMMAND") or None
