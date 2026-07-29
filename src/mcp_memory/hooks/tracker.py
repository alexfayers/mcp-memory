"""Memory tracker - counts tool calls per session and blocks when threshold is reached."""

from __future__ import annotations

import fcntl
import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from mcp_memory.config import get_data_dir

logger = logging.getLogger("hooks")

_MEMORY_BLOCK_THRESHOLD = 10


def _state_path() -> Path:
    """Return the path to the state file, next to the memory database."""
    return get_data_dir() / "memory-tracker-state.json"


def _lock_path() -> Path:
    """Return the path to the lock file guarding the state file."""
    return _state_path().with_suffix(".lock")


_StateData = dict[str, float | list[str]]


@contextmanager
def _locked() -> Iterator[None]:
    """Hold an exclusive lock for the duration of a state read-modify-write cycle."""
    lock_path = _lock_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


def _read() -> _StateData:
    try:
        return dict(json.loads(_state_path().read_text()))
    except (FileNotFoundError, json.JSONDecodeError, TypeError, ValueError):
        return {}


def _write(data: _StateData) -> None:
    _state_path().parent.mkdir(parents=True, exist_ok=True)
    _state_path().write_text(json.dumps(data))


def increment(task_id: str, amount: float = 1.0) -> float:
    """Increment the tool-call counter for a task and return the new value."""
    with _locked():
        data = _read()
        count = data.get(task_id, 0)
        new_count = (count if isinstance(count, (int, float)) else 0) + amount
        data[task_id] = new_count
        _write(data)
        return new_count


def reset(task_id: str) -> None:
    """Reset the tool-call counter to zero after a memory write."""
    with _locked():
        data = _read()
        data[task_id] = 0
        _write(data)


def should_block(task_id: str, threshold: int = _MEMORY_BLOCK_THRESHOLD) -> bool:
    """Return True if the counter has reached the blocking threshold."""
    with _locked():
        count = _read().get(task_id, 0)
    return isinstance(count, (int, float)) and count >= threshold


def clear(task_id: str) -> None:
    """Remove the counter entry for a completed task."""
    with _locked():
        data = _read()
        if task_id in data:
            del data[task_id]
            _write(data)


def has_scope_blocked(task_id: str, project: str) -> bool:
    """Return True if a scope mismatch block has already fired for this project."""
    with _locked():
        blocked = _read().get(f"{task_id}:scope_blocked", [])
    return isinstance(blocked, list) and project in blocked


def mark_scope_blocked(task_id: str, project: str) -> None:
    """Record that a scope mismatch block has fired for a project."""
    with _locked():
        data = _read()
        blocked = data.get(f"{task_id}:scope_blocked", [])
        if not isinstance(blocked, list):
            blocked = []
        if project not in blocked:
            blocked.append(project)
        data[f"{task_id}:scope_blocked"] = blocked
        _write(data)
