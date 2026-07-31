"""Persisted status of the memory-agent recall tool, for the visualiser.

Recall runs inside the memory-agent process; the visualiser is served by the
separate mcp-memory process. The two share a data directory, so the agent writes
a live in-flight count and a bounded rolling history of recent recalls to a small
JSON marker here (next to the database), and mcp-memory reads it back to expose
recall activity over ``/api/recall``.

Unlike the dream (a single idle-triggered loop, so a latest-only marker suffices),
recall is a pull tool spawned per caller-request and runs concurrently, and it
leaves no trace on the graph - so a rolling history is the only record of what it
did. Concurrent writers plus a cross-process reader mean the write must be atomic.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from collections import deque
from pathlib import Path
from typing import TypedDict

from .config import get_data_dir

__all__ = ["time"]

_SCHEMA = 1
_MAX_RECENT = 20
_MAX_QUERY_CHARS = 80

_active = 0
_recent: deque[RecallRecord] = deque(maxlen=_MAX_RECENT)


class RecallRecord(TypedDict):
    """One finished recall: its query and the claude-agent metrics it reported."""

    ts: float
    query: str
    ok: bool
    duration_ms: int | None
    num_turns: int | None
    cost_usd: float | None


class RecallStatus(TypedDict):
    """The persisted recall state: live in-flight count plus recent history."""

    schema: int
    active: int
    recent: list[RecallRecord]


def record_start() -> None:
    """Mark a recall as in flight."""
    global _active  # noqa: PLW0603
    _active += 1
    _write()


def record_finish(
    query: str,
    *,
    ok: bool,
    duration_ms: int | None,
    num_turns: int | None,
    cost_usd: float | None,
) -> None:
    """Record a finished recall (truncating the query) and clear its in-flight slot."""
    global _active  # noqa: PLW0603
    _active = max(0, _active - 1)
    _recent.append(
        {
            "ts": time.time(),
            "query": query[:_MAX_QUERY_CHARS],
            "ok": ok,
            "duration_ms": duration_ms,
            "num_turns": num_turns,
            "cost_usd": cost_usd,
        }
    )
    _write()


def record_startup() -> None:
    """Seed history from disk and reset the in-flight count on agent boot.

    Resetting ``active`` bounds a stale count a crash may have left mid-recall - on
    a fresh process nothing is truly in flight.
    """
    global _active  # noqa: PLW0603
    if not _recent:
        _recent.extend(_read_recent())
    _active = 0
    _write()


def read_status() -> RecallStatus | None:
    """Read the persisted recall status, or None if the marker is absent or corrupt."""
    return _read_file()


def clear() -> None:
    """Reset the in-memory count and history (for test isolation)."""
    global _active  # noqa: PLW0603
    _active = 0
    _recent.clear()


def _status_path() -> Path:
    """Return the path of the persisted recall-status marker file."""
    return get_data_dir() / "recall-status.json"


def _write() -> None:
    """Persist the current count and history atomically (temp file plus replace).

    Concurrent ``recall`` coroutines write this and a separate process reads it, so
    a plain write could expose a torn file; ``os.replace`` swaps it in atomically.
    """
    status: RecallStatus = {"schema": _SCHEMA, "active": _active, "recent": list(_recent)}
    try:
        path = _status_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        tmp_path = Path(tmp)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(status, handle)
            tmp_path.replace(path)
        except OSError:
            tmp_path.unlink()
            raise
    except OSError:
        pass


def _read_file() -> RecallStatus | None:
    """Read and validate the marker file, tolerating a missing or malformed file."""
    try:
        raw = json.loads(_status_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    return {
        "schema": raw.get("schema", _SCHEMA),
        "active": raw.get("active", 0),
        "recent": raw.get("recent", []),
    }


def _read_recent() -> list[RecallRecord]:
    """Return the recent history persisted on disk, or an empty list if unavailable."""
    status = _read_file()
    return status["recent"] if status else []
