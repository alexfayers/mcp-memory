"""Resolve a filesystem path to its associated project via registered mappings."""

from __future__ import annotations

import sqlite3
import sys
from collections.abc import Iterable
from pathlib import Path

from .config import get_db_path

_CASE_INSENSITIVE_PLATFORMS = {"darwin", "win32"}


def _case_normalize(path: str) -> str:
    """Casefold the path on case-insensitive platforms, leave it untouched otherwise."""
    if sys.platform in _CASE_INSENSITIVE_PLATFORMS:
        return path.casefold()
    return path


def normalize_path(path: str) -> str:
    """Return an absolute, user-expanded, case-normalised path for comparison."""
    return _case_normalize(str(Path(path).expanduser().resolve()))


def match_project_for_path(target: str, mappings: Iterable[tuple[str, str]]) -> str | None:
    """Return the project owning the longest registered path that contains the target.

    Args:
        target: The path to resolve (normalised internally).
        mappings: Pairs of (project_name, registered_path) where registered_path is
            already normalised via normalize_path.

    Returns:
        The project name of the longest matching registered path, or None.
    """
    target_path = Path(normalize_path(target))
    best_name: str | None = None
    best_depth = -1
    for name, registered in mappings:
        registered_path = Path(registered)
        if target_path.is_relative_to(registered_path):
            depth = len(registered_path.parts)
            if depth > best_depth:
                best_name, best_depth = name, depth
    return best_name


def resolve_project_for_path(path: str, db_path: Path | None = None) -> str | None:
    """Resolve a path to its project via a read-only database lookup.

    Returns None on any database error (missing file, missing table, corruption) so
    callers can fall back to other detection without surfacing failures.
    """
    resolved_db = db_path or get_db_path()
    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(f"file:{resolved_db}?mode=ro", uri=True)
        rows = conn.execute(
            "SELECT p.name, pp.path FROM project_paths pp JOIN projects p ON pp.project_id = p.id"
        ).fetchall()
    except (sqlite3.Error, OSError):
        return None
    finally:
        if conn is not None:
            conn.close()
    return match_project_for_path(path, [(name, registered) for name, registered in rows])
