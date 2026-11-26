"""Memory tracker - counts tool calls per session and blocks when threshold is reached."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
import fcntl
import json
import logging
from pathlib import Path
import uuid

from cline_hooks.core.state import PluginStateStore

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


_CONTEXT_FILE = "memory-tracker-context.json"


@dataclass
class _ContextState:
    """What a task has done in its current context."""

    count: float = 0.0
    turn: int = 0
    window: str = ""
    flags: list[str] = field(default_factory=list)
    scope_blocked: list[str] = field(default_factory=list)
    seen: dict[str, list[str]] = field(default_factory=dict)


def _context_store() -> PluginStateStore[_ContextState]:
    """Return the store of state that follows the task's context, next to the memory database."""
    return PluginStateStore(_CONTEXT_FILE, _ContextState, get_data_dir() / _CONTEXT_FILE)


_StateData = dict[str, str | bool]


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

    def add(state: _ContextState) -> float:
        state.count += amount
        return state.count

    return _context_store().update(task_id, add)


def reset(task_id: str) -> None:
    """Reset the tool-call counter to zero after a memory write."""

    def zero(state: _ContextState) -> None:
        state.count = 0.0

    _context_store().update(task_id, zero)


def should_block(task_id: str, threshold: float = _MEMORY_BLOCK_THRESHOLD) -> bool:
    """Return True if the counter has reached the blocking threshold."""
    return _context_store().get(task_id).count >= threshold


def should_warn(task_id: str, threshold: float = _MEMORY_WARN_THRESHOLD) -> bool:
    """Return True if the counter has reached the soft reminder threshold."""
    return should_block(task_id, threshold)


def clear(task_id: str) -> None:
    """Remove the counter and any scoped entries for a completed task."""
    _context_store().reset(task_id)
    with _locked():
        data = _read()
        prefix = f"{task_id}:"
        keys_to_remove = [key for key in data if key == task_id or key.startswith(prefix)]
        if keys_to_remove:
            for key in keys_to_remove:
                del data[key]
            _write(data)


def tick_turn(task_id: str, amount: int = 1) -> int:
    """Add to the task's context-window turn counter and return it."""

    def add(state: _ContextState) -> int:
        state.turn += amount
        return state.turn

    return _context_store().update(task_id, add)


def get_window(task_id: str) -> str:
    """Return the task's context window id, minting it on first use."""

    def mint(state: _ContextState) -> str:
        state.window = state.window or uuid.uuid4().hex
        return state.window

    return _context_store().update(task_id, mint)


def has_scope_blocked(task_id: str, project: str) -> bool:
    """Return True if a scope mismatch block has already fired for this project."""
    return project in _context_store().get(task_id).scope_blocked


def mark_scope_blocked(task_id: str, project: str) -> None:
    """Record that a scope mismatch block has fired for a project."""

    def mark(state: _ContextState) -> None:
        if project not in state.scope_blocked:
            state.scope_blocked.append(project)

    _context_store().update(task_id, mark)


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


def has_edited(task_id: str) -> bool:
    """Return True if the task has made a file edit."""
    return has_flag(task_id, "edited")


def mark_edited(task_id: str) -> None:
    """Record that the task has made a file edit."""
    mark_flag(task_id, "edited", per_session=True)


def has_flag(task_id: str, name: str) -> bool:
    """Return True if the task's flag with this name is set."""
    if name in _context_store().get(task_id).flags:
        return True
    with _locked():
        return _read().get(f"{task_id}:{name}") is True


def mark_flag(task_id: str, name: str, *, per_session: bool = False) -> None:
    """Set the task's flag with this name, for the whole session or else for the current context."""
    if per_session:
        with _locked():
            data = _read()
            data[f"{task_id}:{name}"] = True
            _write(data)
        return

    def mark(state: _ContextState) -> None:
        if name not in state.flags:
            state.flags.append(name)

    _context_store().update(task_id, mark)


def claim_edit_path(task_id: str, path: str) -> bool:
    """Return True if this path is the task's first edited file or was already claimed as it."""

    key = f"{task_id}:edit_path"
    with _locked():
        data = _read()
        claimed = data.setdefault(key, path)
        _write(data)
    return claimed == path


def reset_work(task_id: str, names: tuple[str, ...]) -> None:
    """Forget the task's current piece of work: its edited path and these flags."""

    def forget(state: _ContextState) -> None:
        state.flags = [flag for flag in state.flags if flag not in names]

    _context_store().update(task_id, forget)
    with _locked():
        data = _read()
        keys = [key for name in (*names, "edit_path") if (key := f"{task_id}:{name}") in data]
        for key in keys:
            del data[key]
        if keys:
            _write(data)


def take_unseen(task_id: str, name: str, items: list[str], limit: int) -> list[str]:
    """Return up to limit items the task has not yet taken under this name, and record them as taken."""

    def take(state: _ContextState) -> list[str]:
        seen = state.seen.get(name, [])
        taken = [item for item in items if item not in seen][:limit]
        if taken:
            state.seen[name] = [*seen, *taken]
        return taken

    return _context_store().update(task_id, take)
