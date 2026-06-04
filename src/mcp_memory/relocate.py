"""Relocate the memory database between filesystem locations safely."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

_PLIST_DB_RE = re.compile(
    r"<key>MCP_MEMORY_DB_PATH</key>\s*<string>([^<]+)</string>",
)
_SYSTEMD_DB_RE = re.compile(r"^Environment=MCP_MEMORY_DB_PATH=(.+)$", re.MULTILINE)

_SIDECAR_SUFFIXES = ("-wal", "-shm")


def parse_db_path_from_plist(content: str) -> str | None:
    """Extract the configured DB path from a launchd plist, or None."""
    match = _PLIST_DB_RE.search(content)
    return match.group(1) if match else None


def parse_db_path_from_systemd(content: str) -> str | None:
    """Extract the configured DB path from a systemd unit, or None."""
    match = _SYSTEMD_DB_RE.search(content)
    return match.group(1) if match else None


def _entity_count(path: Path) -> int:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return int(conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0])
    except sqlite3.Error:
        return 0
    finally:
        conn.close()


def relocate_db(source: Path, target: Path) -> int:
    """Move a memory database from source to target, returning entities moved.

    Checkpoints the WAL into the main file first, refuses to overwrite a target that
    already contains entities, and removes leftover source sidecar files. A no-op
    (returns 0) when source and target resolve to the same path.
    """
    source = Path(source).expanduser().resolve()
    target = Path(target).expanduser().resolve()

    if not source.exists():
        raise FileNotFoundError(f"Source database not found: {source}")
    if source == target:
        return 0

    if target.exists() and _entity_count(target) > 0:
        raise ValueError(f"Target '{target}' already contains data; refusing to overwrite")

    # Best-effort: collapse the WAL into the main file. If the DB is locked (a reader or a
    # not-fully-stopped service still holds it), skip this - the sidecars are moved alongside
    # the main file below so no committed data is lost either way.
    conn = sqlite3.connect(str(source))
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute("PRAGMA journal_mode=DELETE")
    except sqlite3.OperationalError:
        pass
    finally:
        conn.close()

    moved = _entity_count(source)

    target.parent.mkdir(parents=True, exist_ok=True)
    for suffix in _SIDECAR_SUFFIXES:
        stale = Path(f"{target}{suffix}")
        if stale.exists():
            stale.unlink()
    if target.exists():
        target.unlink()
    source.replace(target)

    # Move any remaining WAL sidecars alongside the main file so un-checkpointed commits survive.
    for suffix in _SIDECAR_SUFFIXES:
        leftover = Path(f"{source}{suffix}")
        if leftover.exists():
            leftover.replace(Path(f"{target}{suffix}"))

    return moved
