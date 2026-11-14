"""Timestamped SQLite backups of the memory database."""

from __future__ import annotations

from collections.abc import Callable, Hashable
from datetime import UTC, datetime, timedelta
from pathlib import Path
import re
import shutil
import sqlite3

from .config import (
    get_backup_dir,
    get_backup_keep_daily,
    get_backup_keep_monthly,
    get_backup_keep_weekly,
    get_db_path,
)

_STAMP_FORMAT = "%Y%m%dT%H%M%S%fZ"
_KEEP_ALL_WINDOW = timedelta(hours=24)
_MONTHS_PER_YEAR = 12
_SIDECAR_SUFFIXES = ("-wal", "-shm")


def _copy_snapshot(db_path: Path, destination_path: Path) -> None:
    """Copy a consistent snapshot of the database at ``db_path`` to ``destination_path``."""
    source = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    destination = sqlite3.connect(destination_path)
    try:
        source.backup(destination)
        destination.execute("PRAGMA journal_mode=DELETE")
    finally:
        destination.close()
        source.close()


def create_backup(db_path: Path, backup_dir: Path, now: datetime) -> Path:
    """Copy a consistent snapshot of the database into ``backup_dir`` and return the new file's path."""
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / f"{db_path.stem}-{now.strftime(_STAMP_FORMAT)}{db_path.suffix}"
    partial = target.with_name(target.name + ".partial")
    try:
        _copy_snapshot(db_path, partial)
        partial.replace(target)
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    return target


def _timestamped(backup_dir: Path, name_template: Path) -> list[tuple[datetime, Path]]:
    """Return the files in ``backup_dir`` named after ``name_template`` with their timestamps, newest first."""
    pattern = re.compile(rf"{re.escape(name_template.stem)}-(\d{{8}}T\d{{12}}Z){re.escape(name_template.suffix)}")
    found = [
        (datetime.strptime(match[1], _STAMP_FORMAT).replace(tzinfo=UTC), path)
        for path in backup_dir.iterdir()
        if (match := pattern.fullmatch(path.name))
    ]
    return sorted(found, reverse=True)


def _newest_per_bucket(backups: list[tuple[datetime, Path]], bucket: Callable[[datetime], Hashable]) -> set[Path]:
    """Return the newest backup of each bucket; ``backups`` is newest first."""
    newest: dict[Hashable, Path] = {}
    for stamp, path in backups:
        newest.setdefault(bucket(stamp), path)
    return set(newest.values())


def prune_backups(
    backup_dir: Path, name_template: Path, now: datetime, *, keep_daily: int, keep_weekly: int, keep_monthly: int
) -> None:
    """Delete the files named after ``name_template`` (its stem and suffix) outside the tiered retention windows.

    Keeps every backup from the last 24 hours, then the newest per day for ``keep_daily`` days,
    per ISO week for ``keep_weekly`` weeks and per month for ``keep_monthly`` months.
    """
    backups = _timestamped(backup_dir, name_template)
    daily = [(stamp, path) for stamp, path in backups if now - stamp < timedelta(days=keep_daily)]
    weekly = [(stamp, path) for stamp, path in backups if now - stamp < timedelta(weeks=keep_weekly)]
    monthly = [
        (stamp, path)
        for stamp, path in backups
        if (now.year - stamp.year) * _MONTHS_PER_YEAR + now.month - stamp.month < keep_monthly
    ]
    keep = {path for stamp, path in backups if now - stamp < _KEEP_ALL_WINDOW}
    keep |= _newest_per_bucket(daily, lambda stamp: stamp.date())
    keep |= _newest_per_bucket(weekly, lambda stamp: stamp.isocalendar()[:2])
    keep |= _newest_per_bucket(monthly, lambda stamp: (stamp.year, stamp.month))
    for _, path in backups:
        if path not in keep:
            path.unlink()
            for suffix in _SIDECAR_SUFFIXES:
                path.with_name(path.name + suffix).unlink(missing_ok=True)


def backup_database() -> Path:
    """Back up the configured database now, prune per the retention settings, and return the new file."""
    db_path = get_db_path()
    backup_dir = get_backup_dir()
    now = datetime.now(UTC)
    created = create_backup(db_path, backup_dir, now)
    prune_backups(
        backup_dir,
        db_path,
        now,
        keep_daily=get_backup_keep_daily(),
        keep_weekly=get_backup_keep_weekly(),
        keep_monthly=get_backup_keep_monthly(),
    )
    return created


def _preserve_files(db_path: Path, backup_dir: Path, now: datetime) -> Path | None:
    """Copy the database file and its WAL sidecars into ``backup_dir`` without opening them, returning the copy."""
    sources = [db_path.with_name(db_path.name + suffix) for suffix in ("", *_SIDECAR_SUFFIXES)]
    if not any(source.exists() for source in sources):
        return None
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / f"{db_path.stem}-{now.strftime(_STAMP_FORMAT)}{db_path.suffix}"
    copies: list[Path] = []
    try:
        for source in sources:
            if source.exists():
                copies.append(target.with_name(target.name + source.name.removeprefix(db_path.name)))
                shutil.copy2(source, copies[-1])
    except OSError:
        for copy in copies:
            copy.unlink(missing_ok=True)
        raise
    return target


def restore_backup(backup_file: Path, db_path: Path, backup_dir: Path) -> Path | None:
    """Replace the database with ``backup_file``, returning a copy of the replaced database files if there were any.

    The server MUST be stopped. Raises ``sqlite3.DatabaseError`` and leaves the database untouched
    when ``backup_file`` is not a valid SQLite database, and ``OSError`` when the replaced database
    cannot be preserved. The replaced files are copied byte for byte, so a damaged database is kept too.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    staged = db_path.with_name(db_path.name + ".restoring")
    try:
        _copy_snapshot(backup_file, staged)
        previous = _preserve_files(db_path, backup_dir, datetime.now(UTC))
        for suffix in _SIDECAR_SUFFIXES:
            db_path.with_name(db_path.name + suffix).unlink(missing_ok=True)
        staged.replace(db_path)
    finally:
        staged.unlink(missing_ok=True)
    return previous
