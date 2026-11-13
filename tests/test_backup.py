"""Tests for automatic database backups and tiered retention."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
import sqlite3

import pytest

from mcp_memory import backup
from mcp_memory.config import get_db_path
from mcp_memory.storage import open_readonly, open_writable


def _make_db(path: Path, entities: int = 1) -> None:
    db = open_writable(path)
    for i in range(entities):
        db.entities.create("proj", [{"name": f"e{i}", "entityType": "task", "observations": ["x"]}])
    db.connection.close()


class TestCreateBackup:
    def test_writes_a_timestamped_copy_holding_the_live_data(self, tmp_path: Path) -> None:
        db_path = tmp_path / "memory.db"
        _make_db(db_path, 3)
        backup_dir = tmp_path / "backups"

        created = backup.create_backup(db_path, backup_dir, datetime(2026, 3, 4, 5, 6, 7, tzinfo=UTC))

        assert created == backup_dir / "memory-20260304T050607000000Z.db"
        copy = open_readonly(created)
        assert len(copy.reads.all_entities("proj")["entities"]) == 3
        assert list(backup_dir.iterdir()) == [created]

    def test_keeps_both_backups_made_within_the_same_second(self, tmp_path: Path) -> None:
        db_path = tmp_path / "memory.db"
        _make_db(db_path, 1)
        backup_dir = tmp_path / "backups"

        first = backup.create_backup(db_path, backup_dir, datetime(2026, 3, 4, 5, 6, 7, 100, tzinfo=UTC))
        second = backup.create_backup(db_path, backup_dir, datetime(2026, 3, 4, 5, 6, 7, 200, tzinfo=UTC))

        assert first != second
        assert sorted(backup_dir.iterdir()) == [first, second]

    def test_leaves_no_partial_file_when_the_copy_fails(self, tmp_path: Path) -> None:
        db_path = tmp_path / "memory.db"
        db_path.write_bytes(b"not a sqlite database" * 100)
        backup_dir = tmp_path / "backups"

        with pytest.raises(sqlite3.DatabaseError):
            backup.create_backup(db_path, backup_dir, datetime(2026, 3, 4, 5, 6, 7, tzinfo=UTC))

        assert list(backup_dir.iterdir()) == []


NOW = datetime(2026, 6, 15, 12, 0, 0, tzinfo=UTC)


def _touch(backup_dir: Path, when: datetime, stem: str = "memory", suffix: str = ".db") -> Path:
    path = backup_dir / f"{stem}-{when.strftime('%Y%m%dT%H%M%S%fZ')}{suffix}"
    path.touch()
    return path


def _prune(backup_dir: Path, daily: int = 7, weekly: int = 4, monthly: int = 6) -> set[str]:
    backup.prune_backups(
        backup_dir, backup_dir / "memory.db", NOW, keep_daily=daily, keep_weekly=weekly, keep_monthly=monthly
    )
    return {path.name for path in backup_dir.iterdir()}


class TestPruneBackups:
    def test_keeps_every_backup_from_the_last_24_hours(self, tmp_path: Path) -> None:
        recent = [_touch(tmp_path, NOW - timedelta(hours=hours)) for hours in (1, 5, 23)]
        assert _prune(tmp_path) == {path.name for path in recent}

    def test_keeps_only_the_newest_backup_per_day_beyond_24_hours(self, tmp_path: Path) -> None:
        day = NOW - timedelta(days=3)
        older = _touch(tmp_path, day.replace(hour=2))
        newest = _touch(tmp_path, day.replace(hour=20))
        assert _prune(tmp_path) == {newest.name}
        assert not older.exists()

    def test_thins_to_one_backup_per_week_after_the_daily_window(self, tmp_path: Path) -> None:
        older = _touch(tmp_path, NOW - timedelta(days=21))
        newest = _touch(tmp_path, NOW - timedelta(days=19))
        kept = _prune(tmp_path, daily=1, weekly=4)
        assert kept == {newest.name}
        assert not older.exists()

    def test_thins_to_one_backup_per_month_after_the_weekly_window(self, tmp_path: Path) -> None:
        older = _touch(tmp_path, datetime(2026, 2, 3, tzinfo=UTC))
        newest = _touch(tmp_path, datetime(2026, 2, 20, tzinfo=UTC))
        assert _prune(tmp_path, daily=1, weekly=1) == {newest.name}
        assert not older.exists()

    def test_deletes_backups_older_than_the_monthly_window(self, tmp_path: Path) -> None:
        _touch(tmp_path, NOW - timedelta(days=400))
        assert _prune(tmp_path) == set()

    def test_leaves_files_outside_its_naming_untouched(self, tmp_path: Path) -> None:
        foreign = [tmp_path / "notes.txt", tmp_path / "memory-latest.db", tmp_path / "other-20200101T000000Z.db"]
        for path in foreign:
            path.touch()
        assert _prune(tmp_path) == {path.name for path in foreign}


class TestBackupDatabase:
    def test_backs_up_into_the_configured_dir_and_prunes_old_backups(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _make_db(get_db_path())
        backup_dir = tmp_path / "elsewhere"
        monkeypatch.setenv("MCP_MEMORY_BACKUP_DIR", str(backup_dir))
        backup_dir.mkdir()
        stale = _touch(backup_dir, datetime.now(UTC) - timedelta(days=800))

        created = backup.backup_database()

        assert created.parent == backup_dir
        assert created.exists()
        assert not stale.exists()
