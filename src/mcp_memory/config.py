"""Shared configuration for the mcp-memory package."""

from __future__ import annotations

import os
import re
from pathlib import Path

_DEFAULT_DB_PATH = "~/.local/share/mcp-memory/memory.db"
_DEFAULT_PORT = "8000"
_DEFAULT_AGENT_PORT = "8100"
_DEFAULT_RECALL_MODEL = "global.anthropic.claude-haiku-4-5-20251001-v1:0"
_DEFAULT_RECALL_MAX_TURNS = "12"
_DEFAULT_AUTO_VOTE_WINDOW_SECONDS = "1800"
_DEFAULT_AUTO_VOTE_MAX_PER_DAY = "3"
_DEFAULT_DREAM_IDLE_SECONDS = "7200"
_DEFAULT_DREAM_POLL_SECONDS = "1800"
_DEFAULT_DREAM_TIMEOUT = "300"
_DEFAULT_DREAM_MAX_VOTES = "15"
_DEFAULT_PURGE_GRACE_DAYS = "30"
_TRUTHY = frozenset({"1", "true", "yes", "on"})

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


def get_recall_max_turns() -> int:
    """Return the cap on tool-calling turns for a recall spawn.

    Bounds recall wall-time and cost below the calling client's timeout, since the
    sequential multi-turn tool loop - not the model - dominates recall latency.
    """
    return int(os.environ.get("MCP_RECALL_MAX_TURNS", _DEFAULT_RECALL_MAX_TURNS))


def get_auto_vote_window_seconds() -> float:
    """Return how long after a search an edit still counts as an implicit-usefulness vote.

    The deterministic auto-vote fires only when a surfaced entity is edited within this
    window; longer windows attribute more edits to a search, shorter ones are stricter.
    """
    return float(os.environ.get("MCP_AUTO_VOTE_WINDOW_SECONDS", _DEFAULT_AUTO_VOTE_WINDOW_SECONDS))


def get_auto_vote_max_per_day() -> int:
    """Return the per-entity daily cap on deterministic auto-votes, bounding score growth."""
    return int(os.environ.get("MCP_AUTO_VOTE_MAX_PER_DAY", _DEFAULT_AUTO_VOTE_MAX_PER_DAY))


def get_preflight_command() -> str | None:
    """Return the optional pre-flight command run before spawning a recall agent."""
    return os.environ.get("MCP_AGENT_PREFLIGHT_COMMAND") or None


def get_dream_enabled() -> bool:
    """Return whether the autonomous dream curation pass runs (opt-in, off by default)."""
    return os.environ.get("MCP_DREAM_ENABLED", "false").strip().lower() in _TRUTHY


def get_dream_idle_seconds() -> float:
    """Return the memory-inactivity window before a dream pass may run."""
    return float(os.environ.get("MCP_DREAM_IDLE_SECONDS", _DEFAULT_DREAM_IDLE_SECONDS))


def get_dream_poll_seconds() -> float:
    """Return how often the idle-watcher checks whether a dream pass is due."""
    return float(os.environ.get("MCP_DREAM_POLL_SECONDS", _DEFAULT_DREAM_POLL_SECONDS))


def get_dream_model() -> str:
    """Return the model id used for dream spawns, defaulting to the recall model."""
    return os.environ.get("MCP_DREAM_MODEL") or get_recall_model()


def get_dream_timeout() -> float:
    """Return the overall timeout for a single dream spawn."""
    return float(os.environ.get("MCP_DREAM_TIMEOUT", _DEFAULT_DREAM_TIMEOUT))


def get_dream_max_votes() -> int:
    """Return the advisory cap on how many entities a single dream pass may demote."""
    return int(os.environ.get("MCP_DREAM_MAX_VOTES", _DEFAULT_DREAM_MAX_VOTES))


def get_purge_enabled() -> bool:
    """Return whether soft-deleted entities are hard-purged past the grace window.

    Off by default so a normal boot (and every test database) never silently
    hard-deletes a soft-deleted entity.
    """
    return os.environ.get("MCP_MEMORY_PURGE_ENABLED", "false").strip().lower() in _TRUTHY


def get_purge_grace_days() -> int:
    """Return how long a soft-deleted entity is retained before it may be purged."""
    return int(os.environ.get("MCP_MEMORY_PURGE_GRACE_DAYS", _DEFAULT_PURGE_GRACE_DAYS))
