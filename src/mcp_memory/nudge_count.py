"""Count by kind of the memory reminders and nudges the hooks showed, kept for the usage telemetry."""

from __future__ import annotations

from collections.abc import Generator, Mapping
from contextlib import contextmanager
from enum import StrEnum
import fcntl
import json
from pathlib import Path

from .atomic_write import write_json_atomic
from .config import get_data_dir

_KINDS_KEY = "kinds"


class NudgeKind(StrEnum):
    """The fixed kinds of nudge the hooks show, so a count never carries free text."""

    COMPLETION = "completion"
    COMPACT = "compact"
    REVIEW = "review"
    REMINDER = "reminder"


def _state_path() -> Path:
    """Return the path to the count file, next to the memory database."""
    return get_data_dir() / "memory-nudges.json"


@contextmanager
def _locked() -> Generator[None, None, None]:
    """Hold an exclusive lock for a read-modify-write cycle, as the hooks and the server run in separate processes."""
    lock_path = _state_path().with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


def read() -> dict[str, int]:
    """Return the number of nudges shown and not yet reported, by kind."""
    try:
        counts = dict(json.loads(_state_path().read_text(encoding="utf-8"))).get(_KINDS_KEY, {})
        return {kind: counts[kind] for kind in NudgeKind if isinstance(counts.get(kind), int) and counts[kind] > 0}
    except (OSError, ValueError, TypeError, AttributeError):
        return {}


def _write(counts: Mapping[str, int]) -> None:
    write_json_atomic(_state_path(), {_KINDS_KEY: {kind: count for kind, count in counts.items() if count > 0}})


def record(kind: NudgeKind) -> None:
    """Count one nudge of the given kind shown."""
    with _locked():
        counts = read()
        _write({**counts, kind: counts.get(kind, 0) + 1})


def subtract(reported: Mapping[str, int]) -> None:
    """Remove nudges that have been reported, keeping any recorded since they were read."""
    if reported:
        with _locked():
            counts = read()
            _write({kind: count - reported.get(kind, 0) for kind, count in counts.items()})
