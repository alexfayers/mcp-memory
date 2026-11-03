"""Memory tracker - counts tool calls per session and blocks when threshold is reached."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
import fcntl
import json
import logging
from pathlib import Path
import uuid

from mcp_memory.config import get_data_dir

logger = logging.getLogger("hooks")

_MEMORY_BLOCK_THRESHOLD = 10
_MEMORY_WARN_RATIO = 0.8
_MEMORY_WARN_THRESHOLD = _MEMORY_BLOCK_THRESHOLD * _MEMORY_WARN_RATIO


def _state_path() -> Path:
    """Return the path to the state file, next to the memory database."""
    return get_data_dir() / "memory-tracker-state.json"


def _lock_path() -> Path:
    """Return the path to the lock file guarding the state file."""
    return _state_path().with_suffix(".lock")


_StateData = dict[str, float | str | list[str]]


@contextmanager
def _locked() -> Generator[None, None, None]:
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


def should_block(task_id: str, threshold: float = _MEMORY_BLOCK_THRESHOLD) -> bool:
    """Return True if the counter has reached the blocking threshold."""
    with _locked():
        count = _read().get(task_id, 0)
    return isinstance(count, (int, float)) and count >= threshold


def should_warn(task_id: str, threshold: float = _MEMORY_WARN_THRESHOLD) -> bool:
    """Return True if the counter has reached the soft reminder threshold."""
    return should_block(task_id, threshold)


def clear(task_id: str) -> None:
    """Remove the counter and any scoped entries for a completed task."""
    with _locked():
        data = _read()
        prefix = f"{task_id}:"
        keys_to_remove = [key for key in data if key == task_id or key.startswith(prefix)]
        if keys_to_remove:
            for key in keys_to_remove:
                del data[key]
            _write(data)


def _drop(task_id: str, names: tuple[str, ...]) -> None:
    """Remove the task's entries whose key starts with one of these names."""
    prefixes = tuple(f"{task_id}:{name}" for name in names)
    with _locked():
        data = _read()
        for key in [key for key in data if key.startswith(prefixes)]:
            del data[key]
        _write(data)


def reset_work(task_id: str) -> None:
    """Forget what the task's current piece of work has edited, written and been reminded of."""
    _drop(task_id, ("wrote:", "edited", "edit_path", "once:", "wide_edit"))


def reset_context_window(task_id: str) -> None:
    """Start a new context window for the task, as after a compaction."""
    _drop(task_id, ("window", "turn"))


def tick_turn(task_id: str, amount: int = 1) -> int:
    """Add to the task's context-window turn counter and return it."""
    return int(increment(f"{task_id}:turn", amount))


def get_window(task_id: str) -> str:
    """Return the task's context window id, minting it on first use."""
    with _locked():
        data = _read()
        window_id = data.setdefault(f"{task_id}:window", uuid.uuid4().hex)
        _write(data)
        return str(window_id)


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


def get_scope(task_id: str) -> str | None:
    """Return the project scope recorded for a task, if any."""
    with _locked():
        scope = _read().get(f"{task_id}:scope")
    return scope if isinstance(scope, str) else None


def set_scope(task_id: str, project: str) -> None:
    """Record the project scope for a task."""
    with _locked():
        data = _read()
        data[f"{task_id}:scope"] = project
        _write(data)


def _is_flagged(key: str) -> bool:
    """Return True if the flag stored under this key is set."""
    with _locked():
        return _read().get(key) is True


def _flag(key: str) -> None:
    """Set the flag stored under this key."""
    with _locked():
        data = _read()
        data[key] = True
        _write(data)


def has_edited(task_id: str) -> bool:
    """Return True if the task has made a file edit."""
    return _is_flagged(f"{task_id}:edited")


def mark_edited(task_id: str) -> None:
    """Record that the task has made a file edit."""
    _flag(f"{task_id}:edited")


def has_wide_edit(task_id: str) -> bool:
    """Return True if the task's work went past one existing file."""
    return _is_flagged(f"{task_id}:wide_edit")


def mark_wide_edit(task_id: str) -> None:
    """Record that the task's work went past one existing file."""
    _flag(f"{task_id}:wide_edit")


def mark_failed(task_id: str) -> None:
    """Record that a shell command failed and has not yet been followed by a success."""
    _flag(f"{task_id}:failed")


def pop_failed(task_id: str) -> bool:
    """Return True and clear the flag if a shell command failed since the last call."""
    with _locked():
        data = _read()
        failed = data.pop(f"{task_id}:failed", False) is True
        if failed:
            _write(data)
        return failed


def claim_once(task_id: str, name: str) -> bool:
    """Return True the first time this one-off is claimed for the task, False afterwards."""
    with _locked():
        data = _read()
        key = f"{task_id}:once:{name}"
        if data.get(key) is True:
            return False
        data[key] = True
        _write(data)
        return True


def has_written(task_id: str, kind: str) -> bool:
    """Return True if the task has written to an entity of this kind (e.g. `feature`, `task`)."""
    return _is_flagged(f"{task_id}:wrote:{kind}")


def mark_written(task_id: str, kind: str) -> None:
    """Record that the task has written to an entity of this kind."""
    _flag(f"{task_id}:wrote:{kind}")


def claim_edit_path(task_id: str, path: str) -> bool:
    """Return True if this path is the task's first edited file or was already claimed as it."""
    with _locked():
        data = _read()
        stored = data.setdefault(f"{task_id}:edit_path", path)
        if stored == path:
            _write(data)
        return stored == path


def get_reminder_chance(task_id: str, default: float) -> float:
    """Return the memory-reminder probability recorded for a task, or the default."""
    with _locked():
        chance = _read().get(f"{task_id}:reminder_chance")
    return float(chance) if isinstance(chance, (int, float)) else default


def set_reminder_chance(task_id: str, chance: float) -> None:
    """Record the memory-reminder probability for a task."""
    with _locked():
        data = _read()
        data[f"{task_id}:reminder_chance"] = chance
        _write(data)
