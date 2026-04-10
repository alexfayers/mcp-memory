"""Memory tracker - counts tool calls per session and blocks when threshold is reached."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from mcp_memory.config import get_data_dir

logger = logging.getLogger("hooks")

_MEMORY_BLOCK_THRESHOLD = 10


def _state_path() -> Path:
    """Return the path to the state file, next to the memory database."""
    return get_data_dir() / "memory-tracker-state.json"


def _read() -> dict[str, int]:
    try:
        return dict(json.loads(_state_path().read_text()))
    except (FileNotFoundError, json.JSONDecodeError, TypeError, ValueError):
        return {}


def _write(data: dict[str, int]) -> None:
    _state_path().parent.mkdir(parents=True, exist_ok=True)
    _state_path().write_text(json.dumps(data))


def increment(task_id: str) -> int:
    """Increment the tool-call counter for a task and return the new value."""
    data = _read()
    data[task_id] = data.get(task_id, 0) + 1
    _write(data)
    return data[task_id]


def reset(task_id: str) -> None:
    """Reset the tool-call counter to zero after a memory write."""
    data = _read()
    data[task_id] = 0
    _write(data)


def should_block(task_id: str, threshold: int = _MEMORY_BLOCK_THRESHOLD) -> bool:
    """Return True if the counter has reached the blocking threshold."""
    return _read().get(task_id, 0) >= threshold


def clear(task_id: str) -> None:
    """Remove the counter entry for a completed task."""
    data = _read()
    if task_id in data:
        del data[task_id]
        _write(data)
