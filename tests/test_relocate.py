"""Tests for relocating the memory database to a new filesystem location."""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

import pytest

from mcp_memory import cli
from mcp_memory.config import get_db_path, get_default_db_path
from mcp_memory.database import DatabaseManager
from mcp_memory.relocate import (
    parse_db_path_from_plist,
    parse_db_path_from_systemd,
    relocate_db,
)


def _make_db(path: Path, entities: int = 1) -> None:
    db = DatabaseManager(path)
    for i in range(entities):
        db.create_entities("proj", [{"name": f"e{i}", "entityType": "task", "observations": ["x"]}])
    db.close()


class TestRelocateDb:
    def test_moves_data_to_empty_target(self, tmp_path: Path) -> None:
        src = tmp_path / "src" / "memory.db"
        src.parent.mkdir()
        _make_db(src, 3)
        dst = tmp_path / "dst" / "memory.db"
        moved = relocate_db(src, dst)
        assert moved == 3
        assert not src.exists()
        assert DatabaseManager(dst).list_projects() == ["proj"]

    def test_noop_when_source_equals_target(self, tmp_path: Path) -> None:
        path = tmp_path / "memory.db"
        _make_db(path, 1)
        assert relocate_db(path, path) == 0
        assert path.exists()

    def test_missing_source_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            relocate_db(tmp_path / "nope.db", tmp_path / "dst.db")

    def test_refuses_nonempty_target(self, tmp_path: Path) -> None:
        src = tmp_path / "src.db"
        dst = tmp_path / "dst.db"
        _make_db(src, 1)
        _make_db(dst, 1)
        with pytest.raises(ValueError, match="already contains data"):
            relocate_db(src, dst)

    def test_overwrites_empty_target(self, tmp_path: Path) -> None:
        src = tmp_path / "src.db"
        _make_db(src, 2)
        dst = tmp_path / "dst.db"
        DatabaseManager(dst).close()
        assert relocate_db(src, dst) == 2

    def test_survives_open_connection_on_source(self, tmp_path: Path) -> None:
        src = tmp_path / "src.db"
        _make_db(src, 2)
        # Hold a connection in WAL mode so checkpoint/journal-mode switch cannot fully acquire.
        holder = sqlite3.connect(str(src))
        holder.execute("PRAGMA journal_mode=WAL")
        holder.execute("SELECT 1").fetchone()
        try:
            moved = relocate_db(src, tmp_path / "dst.db")
        finally:
            holder.close()
        assert moved == 2
        assert DatabaseManager(tmp_path / "dst.db").list_projects() == ["proj"]

    def test_moves_wal_sidecars_when_present(self, tmp_path: Path) -> None:
        src = tmp_path / "src.db"
        _make_db(src, 1)
        conn = sqlite3.connect(str(src))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("INSERT INTO projects (name) VALUES ('walproj')")
        conn.commit()
        # leave WAL uncheckpointed and the connection open
        dst = tmp_path / "dst.db"
        relocate_db(src, dst)
        conn.close()
        assert "walproj" in DatabaseManager(dst).list_projects()

    def test_cleans_source_sidecars(self, tmp_path: Path) -> None:
        src = tmp_path / "src.db"
        _make_db(src, 1)
        Path(f"{src}-wal").write_bytes(b"")
        Path(f"{src}-shm").write_bytes(b"")
        relocate_db(src, tmp_path / "dst.db")
        assert not Path(f"{src}-wal").exists()
        assert not Path(f"{src}-shm").exists()


class TestDefaultDbPath:
    def test_ignores_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MCP_MEMORY_DB_PATH", "/tmp/custom.db")
        assert get_db_path() == Path("/tmp/custom.db")
        assert get_default_db_path() == Path("~/.local/share/mcp-memory/memory.db").expanduser()


class TestMigrateDbCommand:
    def test_migrates_via_cli_without_service(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        src = tmp_path / "old" / "memory.db"
        src.parent.mkdir()
        _make_db(src, 2)
        target = tmp_path / "new" / "memory.db"

        monkeypatch.setattr(cli, "get_default_db_path", lambda: target)
        monkeypatch.setattr(cli, "_stop_service", lambda: False)

        cli._cmd_migrate_db(argparse.Namespace(source=str(src)))

        out = capsys.readouterr().out
        assert "Moved 2 entities" in out
        assert not src.exists()
        assert DatabaseManager(target).list_projects() == ["proj"]

    def test_noop_when_already_default(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        target = tmp_path / "memory.db"
        _make_db(target, 1)
        monkeypatch.setattr(cli, "get_default_db_path", lambda: target)

        cli._cmd_migrate_db(argparse.Namespace(source=str(target)))

        assert "already at the default location" in capsys.readouterr().out
        assert target.exists()


class TestParseServiceConfig:
    def test_parse_plist(self) -> None:
        content = (
            "<key>MCP_MEMORY_DB_PATH</key>\n        <string>/Users/x/.memory/memory.db</string>"
        )
        assert parse_db_path_from_plist(content) == "/Users/x/.memory/memory.db"

    def test_parse_plist_missing(self) -> None:
        assert parse_db_path_from_plist("<plist></plist>") is None

    def test_parse_systemd(self) -> None:
        content = "[Service]\nEnvironment=MCP_MEMORY_DB_PATH=/home/x/.memory/memory.db\n"
        assert parse_db_path_from_systemd(content) == "/home/x/.memory/memory.db"

    def test_parse_systemd_missing(self) -> None:
        assert parse_db_path_from_systemd("[Service]\nExecStart=/bin/mcp-memory\n") is None
