"""Cumulative memory-write tracker - nudges a memory-review once a threshold is reached.

Unlike hooks/tracker.py (which counts tool calls *without* a memory write and blocks
at a low threshold), this is a single global counter of memory *writes* that persists
across sessions and tasks. It is never cleared on task lifecycle events.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from mcp_memory.config import get_data_dir

logger = logging.getLogger("hooks")

_REVIEW_NUDGE_THRESHOLD = 40
_WRITES_KEY = "writes"


def _state_path() -> Path:
    """Return the path to the state file, next to the memory database."""
    return get_data_dir() / "memory-review-state.json"


def _read_count() -> int:
    try:
        data = dict(json.loads(_state_path().read_text()))
    except (FileNotFoundError, json.JSONDecodeError, TypeError, ValueError):
        return 0
    count = data.get(_WRITES_KEY, 0)
    return count if isinstance(count, int) else 0


def _write_count(count: int) -> None:
    _state_path().parent.mkdir(parents=True, exist_ok=True)
    _state_path().write_text(json.dumps({_WRITES_KEY: count}))


def record_write() -> int:
    """Increment the cumulative memory-write counter and return the new value."""
    count = _read_count() + 1
    _write_count(count)
    return count


def should_nudge(threshold: int = _REVIEW_NUDGE_THRESHOLD) -> bool:
    """Return True if the cumulative write count has reached the nudge threshold."""
    return _read_count() >= threshold


def reset() -> None:
    """Reset the cumulative memory-write counter to zero."""
    _write_count(0)
