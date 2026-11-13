"""Shared configuration for the mcp-memory package."""

from __future__ import annotations

import importlib
import os
from pathlib import Path
import re

_DEFAULT_DB_PATH = "~/.local/share/mcp-memory/memory.db"
_DEFAULT_PORT = "8000"
_DEFAULT_DREAM_MODEL = "claude-sonnet-5-5"
_DEFAULT_DREAM_HEAVY_MODEL = "claude-opus-5-5"
_DEFAULT_AUTO_VOTE_WINDOW_SECONDS = "1800"
_DEFAULT_AUTO_VOTE_MAX_PER_DAY = "3"
_DEFAULT_DREAM_IDLE_SECONDS = "1800"
_DEFAULT_DREAM_POLL_SECONDS = "300"
_DEFAULT_DREAM_TIMEOUT = "900"
_DEFAULT_DREAM_MAX_VOTES = "50"
_DEFAULT_DREAM_HEAVY_IDLE_SECONDS = "5400"
_DEFAULT_DREAM_HEAVY_POLL_SECONDS = "900"
_DEFAULT_DREAM_HEAVY_TIMEOUT = "3600"
_DEFAULT_DREAM_HEAVY_MAX_OPS = "100"
_DEFAULT_PURGE_GRACE_DAYS = "30"
_DEFAULT_SURFACED_RETENTION_DAYS = "-1"
_DEFAULT_CALL_METRICS_RETENTION_DAYS = "90"
_DEFAULT_SWEEP_INTERVAL_SECONDS = "43200"
_DEFAULT_EVAL_CACHE_TTL_SECONDS = "300"
_DEFAULT_BACKUP_INTERVAL_SECONDS = "21600"
_DEFAULT_BACKUP_KEEP_DAILY = "7"
_DEFAULT_BACKUP_KEEP_WEEKLY = "4"
_DEFAULT_BACKUP_KEEP_MONTHLY = "6"
_DEFAULT_MAX_OBSERVATION_CHARS = "2000"
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


def get_workspace_markers() -> tuple[str, ...]:
    """Return the directory names that mark a multi-package workspace root.

    Read from MCP_MEMORY_WORKSPACE_MARKERS (comma-separated), empty by default so
    the feature stays opt-in and build-system-agnostic. When a marker directory is
    found above a package's own repository root, that outer directory is treated as
    the workspace root so sibling packages share one project scope.
    """
    raw = os.environ.get("MCP_MEMORY_WORKSPACE_MARKERS", "")
    return tuple(marker.strip() for marker in raw.split(",") if marker.strip())


def resolve_port() -> str:
    """Return MCP_MEMORY_PORT, then the port of an installed service, then the default port."""
    return os.environ.get("MCP_MEMORY_PORT") or detect_service_port() or _DEFAULT_PORT


def get_memory_url() -> str:
    """Return the mcp-memory server URL that dream spawns, the hooks and the CLI connect to."""
    explicit = os.environ.get("MCP_MEMORY_URL")
    if explicit:
        return explicit
    return f"http://localhost:{resolve_port()}/mcp"


def get_auto_vote_window_seconds() -> float:
    """Return how long after a search an edit still counts as an implicit-usefulness vote.

    The deterministic auto-vote fires only when a surfaced entity is edited within this
    window; longer windows attribute more edits to a search, shorter ones are stricter.
    """
    return float(os.environ.get("MCP_AUTO_VOTE_WINDOW_SECONDS", _DEFAULT_AUTO_VOTE_WINDOW_SECONDS))


def get_auto_vote_max_per_day() -> int:
    """Return the per-entity daily cap on deterministic auto-votes, bounding score growth."""
    return int(os.environ.get("MCP_AUTO_VOTE_MAX_PER_DAY", _DEFAULT_AUTO_VOTE_MAX_PER_DAY))


def get_dream_enabled() -> bool:
    """Return whether the autonomous dream curation pass runs (opt-in, off by default)."""
    return os.environ.get("MCP_DREAM_ENABLED", "false").strip().lower() in _TRUTHY


def get_dream_idle_seconds() -> float:
    """Return the genuine-idle window before the light dream pass fires (once per session)."""
    return float(os.environ.get("MCP_DREAM_IDLE_SECONDS", _DEFAULT_DREAM_IDLE_SECONDS))


def get_dream_poll_seconds() -> float:
    """Return how often the idle-watcher checks whether a dream pass is due."""
    return float(os.environ.get("MCP_DREAM_POLL_SECONDS", _DEFAULT_DREAM_POLL_SECONDS))


def _resolve_model(name: str) -> str:
    """Return the id this Claude Code install requests for a model name, else the name."""
    try:
        claude_models = importlib.import_module("llm_prompts.claude_models")
    except ImportError:
        return name
    return str(claude_models.resolve_model(name))


def get_dream_model() -> str:
    """Return the model id used for dream spawns."""
    return os.environ.get("MCP_DREAM_MODEL") or _resolve_model(_DEFAULT_DREAM_MODEL)


def get_dream_timeout() -> float:
    """Return the overall timeout for a single dream spawn."""
    return float(os.environ.get("MCP_DREAM_TIMEOUT", _DEFAULT_DREAM_TIMEOUT))


def get_dream_max_votes() -> int:
    """Return the advisory cap on how many entities a single dream pass may demote."""
    return int(os.environ.get("MCP_DREAM_MAX_VOTES", _DEFAULT_DREAM_MAX_VOTES))


def get_dream_heavy_enabled() -> bool:
    """Return whether the heavy structural dream tier runs (opt-in, off by default).

    Independent of the light tier's MCP_DREAM_ENABLED - the heavy tier merges
    duplicates and downvotes, so it is enabled separately and defaults off.
    """
    return os.environ.get("MCP_DREAM_HEAVY_ENABLED", "false").strip().lower() in _TRUTHY


def get_dream_heavy_idle_seconds() -> float:
    """Return the genuine-idle window before the heavy dream pass fires (once per session).

    Measured from the true start of the idle session; the heavy tier fires once
    when the session's genuine idle reaches this threshold. Larger than the light
    threshold so the heavy pass covers the whole quiet period.
    """
    return float(os.environ.get("MCP_DREAM_HEAVY_IDLE_SECONDS", _DEFAULT_DREAM_HEAVY_IDLE_SECONDS))


def get_dream_heavy_poll_seconds() -> float:
    """Return how often the heavy-tier watcher checks whether a pass is due."""
    return float(os.environ.get("MCP_DREAM_HEAVY_POLL_SECONDS", _DEFAULT_DREAM_HEAVY_POLL_SECONDS))


def get_dream_heavy_model() -> str:
    """Return the model id for heavy dream spawns, defaulting to Opus.

    Structural curation (merges) benefits from a stronger model than the light pass.
    MCP_DREAM_HEAVY_MODEL overrides it with a fully-qualified id; a bare alias
    silently remaps to Opus.
    """
    return os.environ.get("MCP_DREAM_HEAVY_MODEL") or _resolve_model(_DEFAULT_DREAM_HEAVY_MODEL)


def get_dream_heavy_timeout() -> float:
    """Return the overall timeout for a single heavy dream spawn."""
    return float(os.environ.get("MCP_DREAM_HEAVY_TIMEOUT", _DEFAULT_DREAM_HEAVY_TIMEOUT))


def get_dream_heavy_max_ops() -> int:
    """Return the advisory cap on how many entities a single heavy pass may merge or demote."""
    return int(os.environ.get("MCP_DREAM_HEAVY_MAX_OPS", _DEFAULT_DREAM_HEAVY_MAX_OPS))


def get_purge_enabled() -> bool:
    """Return whether soft-deleted entities are hard-purged past the grace window.

    Off by default so a normal boot (and every test database) never silently
    hard-deletes a soft-deleted entity.
    """
    return os.environ.get("MCP_MEMORY_PURGE_ENABLED", "false").strip().lower() in _TRUTHY


def get_purge_grace_days() -> int:
    """Return how long a soft-deleted entity is retained before it may be purged."""
    return int(os.environ.get("MCP_MEMORY_PURGE_GRACE_DAYS", _DEFAULT_PURGE_GRACE_DAYS))


def get_surfaced_retention_days() -> int:
    """Return how long surfaced_entities retrieval telemetry is retained before pruning.

    Negative values mean unlimited (the default): the used_at labels are the ranking eval's
    only ground truth and cannot be regenerated once deleted.
    """
    return int(os.environ.get("MCP_MEMORY_SURFACED_RETENTION_DAYS", _DEFAULT_SURFACED_RETENTION_DAYS))


def get_max_observation_chars() -> int:
    """Return the per-entity cumulative observation-content character budget for reads.

    Negative values mean unlimited (full detail). Zero means a literal zero-char budget
    (only the single highest-voted observation is kept). Applied best-first (highest-voted
    observations kept) so large entities stay findable without overflowing the client's context.
    """
    return int(os.environ.get("MCP_MEMORY_MAX_OBSERVATION_CHARS", _DEFAULT_MAX_OBSERVATION_CHARS))


def get_dedupe_turns() -> int:
    """Return how many tool calls a sent observation stays deduplicated in a context window; 0 disables."""
    return int(os.environ.get("MCP_MEMORY_DEDUPE_TURNS", "40"))


def get_call_metrics_enabled() -> bool:
    """Return whether per-call usage metrics (byte sizes + option usage) are recorded."""
    return os.environ.get("MCP_MEMORY_CALL_METRICS_ENABLED", "true").strip().lower() in _TRUTHY


def get_call_metrics_retention_days() -> int:
    """Return how long tool_calls usage telemetry is retained before pruning."""
    return int(os.environ.get("MCP_MEMORY_CALL_METRICS_RETENTION_DAYS", _DEFAULT_CALL_METRICS_RETENTION_DAYS))


def get_sweep_interval_seconds() -> float:
    """Return how often the server's background loop runs the maintenance sweeps."""
    return float(os.environ.get("MCP_MEMORY_SWEEP_INTERVAL_SECONDS", _DEFAULT_SWEEP_INTERVAL_SECONDS))


def get_backup_enabled() -> bool:
    """Return whether the server backs up the database periodically and before each heavy dream pass."""
    return os.environ.get("MCP_MEMORY_BACKUP_ENABLED", "true").strip().lower() in _TRUTHY


def get_backup_dir() -> Path:
    """Return the directory holding database backups, defaulting to ``backups`` beside the database."""
    configured = os.environ.get("MCP_MEMORY_BACKUP_DIR")
    return Path(configured).expanduser() if configured else get_data_dir() / "backups"


def get_backup_interval_seconds() -> float:
    """Return how often the server's background loop backs up the database."""
    return float(os.environ.get("MCP_MEMORY_BACKUP_INTERVAL_SECONDS", _DEFAULT_BACKUP_INTERVAL_SECONDS))


def get_backup_keep_daily() -> int:
    """Return how many days keep their newest backup once past the last 24 hours."""
    return int(os.environ.get("MCP_MEMORY_BACKUP_KEEP_DAILY", _DEFAULT_BACKUP_KEEP_DAILY))


def get_backup_keep_weekly() -> int:
    """Return how many ISO weeks keep their newest backup."""
    return int(os.environ.get("MCP_MEMORY_BACKUP_KEEP_WEEKLY", _DEFAULT_BACKUP_KEEP_WEEKLY))


def get_backup_keep_monthly() -> int:
    """Return how many months keep their newest backup."""
    return int(os.environ.get("MCP_MEMORY_BACKUP_KEEP_MONTHLY", _DEFAULT_BACKUP_KEEP_MONTHLY))


def get_eval_cache_ttl_seconds() -> int:
    """Return how long a computed retrieval-quality EvalReport stays cached."""
    return int(os.environ.get("MCP_MEMORY_EVAL_CACHE_TTL_SECONDS", _DEFAULT_EVAL_CACHE_TTL_SECONDS))


def get_archive_enabled() -> bool:
    """Return whether stale resolved entities are auto-archived on startup.

    On by default, unlike the GC/purge sweeps below: leaving it off means nobody would
    ever see the feature. See the archive sweep in the storage maintenance module for the criteria.
    """
    return os.environ.get("MCP_MEMORY_ARCHIVE_ENABLED", "true").strip().lower() in _TRUTHY


def get_gc_enabled() -> bool:
    """Return whether downvoted orphan entities are garbage-collected on startup.

    Off by default so a normal boot (and every test database) never reaps an
    entity. Reversible: the GC only soft-deletes, so the grace-window purge stays
    the sole path to permanent removal.
    """
    return os.environ.get("MCP_MEMORY_GC_ENABLED", "false").strip().lower() in _TRUTHY


def get_strict_policy_enabled() -> bool:
    """Return whether stricter write-time memory policy checks are enforced.

    Off by default so existing local databases continue to work unless the user
    explicitly opts into tighter scope and relation constraints.
    """
    return os.environ.get("MCP_MEMORY_STRICT_POLICY", "false").strip().lower() in _TRUTHY
