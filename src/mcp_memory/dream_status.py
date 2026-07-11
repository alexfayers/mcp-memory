"""Persisted status of the autonomous dream curation pass, for the visualiser.

The dream runs inside the memory-agent process; the visualiser is served by the
separate mcp-memory process. The two share a data directory, so the agent writes
the latest pass and its config to a small JSON marker here (next to the database),
and mcp-memory reads it back to expose the dream's state over ``/api/dream``.

Only the most recent pass is kept - the current demoted state of the graph is the
ground truth for what remains demoted, so no rolling history is needed.
"""

from __future__ import annotations

import json
import re
import time
from typing import TYPE_CHECKING, TypedDict

from .config import get_data_dir

if TYPE_CHECKING:
    from pathlib import Path

_SCHEMA = 1
_DEMOTION_RE = re.compile(r"\[([^/\]]+)/([^\]]+)\]")

_config: DreamConfig | None = None
_last_pass: PassRecord | None = None


class Demotion(TypedDict):
    """One entity the dream reported demoting, parsed from its audit line."""

    project: str
    name: str
    reason: str


class PassRecord(TypedDict):
    """The outcome of a single dream curation pass."""

    ts: float
    ok: bool
    audit_text: str
    demotions: list[Demotion]


class DreamConfig(TypedDict):
    """The dream watcher's configuration, snapshotted at startup."""

    enabled: bool
    idle_threshold_seconds: float
    poll_seconds: float


class DreamStatus(TypedDict):
    """The persisted dream state: config plus the most recent pass."""

    schema: int
    config: DreamConfig | None
    last_pass: PassRecord | None


def parse_demotions(audit_text: str) -> list[Demotion]:
    """Best-effort extract ``[project/entity] - reason`` demotion lines from an audit.

    Lines without a bracketed slug are ignored, so narration around the demotions
    does not produce spurious entries. The entity name keeps its type prefix.
    """
    demotions: list[Demotion] = []
    for line in audit_text.splitlines():
        match = _DEMOTION_RE.search(line)
        if match is None:
            continue
        reason = line[match.end() :].lstrip(" :-\t").strip()
        demotions.append({"project": match.group(1), "name": match.group(2), "reason": reason})
    return demotions


def record_startup(*, enabled: bool, idle_threshold_seconds: float, poll_seconds: float) -> None:
    """Snapshot the watcher config to the marker, preserving any prior pass on disk."""
    global _config, _last_pass  # noqa: PLW0603
    _config = {
        "enabled": enabled,
        "idle_threshold_seconds": idle_threshold_seconds,
        "poll_seconds": poll_seconds,
    }
    if _last_pass is None:
        _last_pass = _read_last_pass()
    _write()


def record_pass(audit_text: str, *, ok: bool) -> None:
    """Record the most recent dream pass (raw audit plus parsed demotions)."""
    global _last_pass  # noqa: PLW0603
    _last_pass = {
        "ts": time.time(),
        "ok": ok,
        "audit_text": audit_text,
        "demotions": parse_demotions(audit_text),
    }
    _write()


def read_status() -> DreamStatus | None:
    """Read the persisted dream status, or None if the marker is absent or corrupt."""
    return _read_file()


def clear() -> None:
    """Reset the in-memory config and pass (for test isolation)."""
    global _config, _last_pass  # noqa: PLW0603
    _config = None
    _last_pass = None


def _status_path() -> Path:
    """Return the path of the persisted dream-status marker file."""
    return get_data_dir() / "dream-status.json"


def _write() -> None:
    """Persist the current config and last pass to the marker file."""
    status: DreamStatus = {"schema": _SCHEMA, "config": _config, "last_pass": _last_pass}
    try:
        path = _status_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(status), encoding="utf-8")
    except OSError:
        pass


def _read_file() -> DreamStatus | None:
    """Read and validate the marker file, tolerating a missing or malformed file."""
    try:
        raw = json.loads(_status_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    return {
        "schema": raw.get("schema", _SCHEMA),
        "config": raw.get("config"),
        "last_pass": raw.get("last_pass"),
    }


def _read_last_pass() -> PassRecord | None:
    """Return the last pass persisted on disk, or None if unavailable."""
    status = _read_file()
    return status["last_pass"] if status else None
