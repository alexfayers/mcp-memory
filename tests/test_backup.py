"""Tests for automatic database backups, tiered retention and restore."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import re
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

    def test_deletes_the_wal_files_of_a_pruned_backup(self, tmp_path: Path) -> None:
        old = _touch(tmp_path, NOW - timedelta(days=400))
        sidecar = old.with_name(old.name + "-wal")
        sidecar.touch()
        _prune(tmp_path)
        assert not sidecar.exists()

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


class TestSaveTranscript:
    def test_writes_the_prompt_then_the_agent_output_beside_the_backups(self) -> None:
        saved = backup.save_transcript("heavy", "the prompt", '{"type":"result"}\n')

        assert saved.parent == get_db_path().parent / "backups"
        assert re.fullmatch(r"dream-heavy-\d{8}T\d{12}Z\.jsonl", saved.name)
        first, second = saved.read_text().splitlines()
        assert json.loads(first) == {"type": "dream_prompt", "prompt": "the prompt"}
        assert second == '{"type":"result"}'

    def test_prunes_old_transcripts_of_the_same_tier_only(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backup_dir = tmp_path / "backups"
        monkeypatch.setenv("MCP_MEMORY_BACKUP_DIR", str(backup_dir))
        backup_dir.mkdir()
        old = datetime.now(UTC) - timedelta(days=800)
        stale = _touch(backup_dir, old, "dream-heavy", ".jsonl")
        other_tier = _touch(backup_dir, old, "dream-light", ".jsonl")

        backup.save_transcript("heavy", "p", "")

        assert not stale.exists()
        assert other_tier.exists()


class TestRestoreBackup:
    def test_replaces_a_damaged_database_keeping_its_files(self, tmp_path: Path) -> None:
        source = tmp_path / "source.db"
        _make_db(source, 2)
        snapshot = backup.create_backup(source, tmp_path / "backups", NOW)
        db_path = tmp_path / "memory.db"
        db_path.write_bytes(b"damaged")
        db_path.with_name("memory.db-wal").write_bytes(b"wal")

        previous = backup.restore_backup(snapshot, db_path, tmp_path / "backups")

        assert previous is not None
        assert previous.read_bytes() == b"damaged"
        assert previous.with_name(previous.name + "-wal").read_bytes() == b"wal"
        assert len(open_readonly(db_path).reads.all_entities("proj")["entities"]) == 2
        assert not db_path.with_name("memory.db-wal").exists()

    def test_restores_when_there_is_no_current_database(self, tmp_path: Path) -> None:
        source = tmp_path / "source.db"
        _make_db(source)
        snapshot = backup.create_backup(source, tmp_path / "backups", NOW)
        assert backup.restore_backup(snapshot, tmp_path / "fresh" / "memory.db", tmp_path / "backups") is None

    def test_rejects_a_file_that_is_not_a_database_and_leaves_the_current_one(self, tmp_path: Path) -> None:
        db_path = tmp_path / "memory.db"
        _make_db(db_path)
        bogus = tmp_path / "bogus.db"
        bogus.write_text("not a database" * 100)

        with pytest.raises(sqlite3.DatabaseError):
            backup.restore_backup(bogus, db_path, tmp_path / "backups")

        assert len(open_readonly(db_path).reads.all_entities("proj")["entities"]) == 1
        assert not list(tmp_path.glob("*.restoring"))
