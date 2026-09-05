"""Tests for the storage layer."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from mcp_memory.migrations.runner import run_migrations
from mcp_memory.migrations.schema import MIGRATIONS, _relation_type_backfill_statements
from mcp_memory.models import MAX_VOTE_MAGNITUDE, Entity, Observation, Relation
from mcp_memory.path_resolver import normalize_path
from mcp_memory.storage import open_readonly, open_writable
from mcp_memory.storage.connection import Connection
from mcp_memory.storage.pure.rows import budget_observations, hash_observation
from mcp_memory.storage.pure.sql import parse_date
from mcp_memory.storage.repositories.telemetry import TelemetryRepository
from mcp_memory.storage.services.ids import get_entity_id, get_or_create_project_id
from tests import obs_contents, obs_votes, soft_delete_store

if TYPE_CHECKING:
    from mcp_memory.storage import GraphResult, Storage


class TestCreateEntities:
    def test_create_single_entity(self, store: Storage) -> None:
        store.entities.create("proj", [{"name": "e1", "entityType": "task", "observations": ["obs1"]}])
        entity = store.reads.get_entity("proj", "e1")
        assert entity.name == "e1"
        assert entity.entity_type == "task"
        assert obs_contents(entity) == ["obs1"]

    def test_create_entity_with_status(self, store: Storage) -> None:
        store.entities.create(
            "proj",
            [{"name": "e1", "entityType": "task", "observations": ["obs1"], "status": "planned"}],
        )
        assert store.reads.get_entity("proj", "e1").status == "planned"

    def test_upsert_overwrites_observations(self, store: Storage) -> None:
        store.entities.create("proj", [{"name": "e1", "entityType": "task", "observations": ["old"]}])
        store.entities.create("proj", [{"name": "e1", "entityType": "task", "observations": ["new"]}])
        assert obs_contents(store.reads.get_entity("proj", "e1")) == ["new"]

    def test_project_isolation(self, store: Storage) -> None:
        store.entities.create("p1", [{"name": "e1", "entityType": "task", "observations": ["a"]}])
        store.entities.create("p2", [{"name": "e1", "entityType": "task", "observations": ["b"]}])
        assert obs_contents(store.reads.get_entity("p1", "e1")) == ["a"]
        assert obs_contents(store.reads.get_entity("p2", "e1")) == ["b"]

    def test_empty_name_raises(self, store: Storage) -> None:
        with pytest.raises(ValueError, match="non-empty string"):
            store.entities.create("proj", [{"name": "", "entityType": "task", "observations": ["x"]}])

    def test_empty_observations_raises(self, store: Storage) -> None:
        with pytest.raises(ValueError, match="non-empty list"):
            store.entities.create("proj", [{"name": "e1", "entityType": "task", "observations": []}])

    def test_invalid_status_raises(self, store: Storage) -> None:
        with pytest.raises(ValueError, match="Invalid status"):
            store.entities.create(
                "proj",
                [{"name": "e1", "entityType": "task", "observations": ["x"], "status": "bad"}],
            )


class TestMoveProjectEntities:
    def test_moves_entities_to_target(self, store: Storage) -> None:
        store.entities.create("src", [{"name": "e1", "entityType": "task", "observations": ["a"]}])
        store.entities.create("dst", [{"name": "d0", "entityType": "task", "observations": ["x"]}])
        moved = store.projects.move_entities("src", "dst")
        assert moved == 1
        assert obs_contents(store.reads.get_entity("dst", "e1")) == ["a"]

    def test_preserves_relations(self, store: Storage) -> None:
        store.entities.create(
            "src",
            [
                {"name": "a", "entityType": "task", "observations": ["a"]},
                {"name": "b", "entityType": "feature", "observations": ["b"]},
            ],
        )
        store.relations.create("src", [Relation(source="a", target="b", relation_type="implements")])
        store.entities.create("dst", [{"name": "d0", "entityType": "task", "observations": ["x"]}])
        store.projects.move_entities("src", "dst")
        result = store.reads.get_entity_with_relations("dst", "a")
        assert any(r.target == "b" for r in result["relations"])

    def test_source_scope_emptied(self, store: Storage) -> None:
        store.entities.create("src", [{"name": "e1", "entityType": "task", "observations": ["a"]}])
        store.entities.create("dst", [{"name": "d0", "entityType": "task", "observations": ["x"]}])
        store.projects.move_entities("src", "dst")
        assert store.reads.recent("src")["entities"] == []

    def test_name_collision_raises(self, store: Storage) -> None:
        store.entities.create("src", [{"name": "dup", "entityType": "task", "observations": ["a"]}])
        store.entities.create("dst", [{"name": "dup", "entityType": "task", "observations": ["b"]}])
        with pytest.raises(ValueError, match="collision"):
            store.projects.move_entities("src", "dst")

    def test_missing_source_raises(self, store: Storage) -> None:
        store.entities.create("dst", [{"name": "d0", "entityType": "task", "observations": ["x"]}])
        with pytest.raises(ValueError, match="not found"):
            store.projects.move_entities("nope", "dst")

    def test_moved_entities_are_searchable_in_target(self, store: Storage) -> None:
        store.entities.create("src", [{"name": "findme", "entityType": "task", "observations": ["needle"]}])
        store.entities.create("dst", [{"name": "d0", "entityType": "task", "observations": ["x"]}])
        store.projects.move_entities("src", "dst")
        hits = store.reads.search("dst", "needle")["entities"]
        assert any(e.name == "findme" for e in hits)


class TestDeleteProject:
    def test_deletes_empty_project(self, store: Storage) -> None:
        store.projects.set_paths("doomed", [])
        assert "doomed" in store.projects.names()
        store.projects.delete("doomed")
        assert "doomed" not in store.projects.names()

    def test_deletes_project_paths(self, store: Storage, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        store.projects.set_paths("doomed", [str(repo)])
        store.projects.delete("doomed")
        assert store.projects.get_project_for_path(str(repo)) is None

    def test_missing_project_raises(self, store: Storage) -> None:
        with pytest.raises(ValueError, match="not found"):
            store.projects.delete("never-existed")

    def test_non_empty_project_raises(self, store: Storage) -> None:
        store.entities.create("busy", [{"name": "e1", "entityType": "task", "observations": ["a"]}])
        with pytest.raises(ValueError, match="entit"):
            store.projects.delete("busy")

    def test_refuses_global(self, store: Storage) -> None:
        with pytest.raises(ValueError, match="global"):
            store.projects.delete("global")


class TestProjectCaseInsensitivity:
    def test_project_names_are_case_insensitive(self, store: Storage) -> None:
        store.entities.create("MyProject", [{"name": "e1", "entityType": "task", "observations": ["a"]}])
        entity = store.reads.get_entity("myproject", "e1")
        assert obs_contents(entity) == ["a"]

    def test_case_insensitive_project_does_not_duplicate(self, store: Storage) -> None:
        store.entities.create("Proj", [{"name": "e1", "entityType": "task", "observations": ["a"]}])
        store.entities.create("proj", [{"name": "e1", "entityType": "task", "observations": ["b"]}])
        assert obs_contents(store.reads.get_entity("PROJ", "e1")) == ["b"]


class TestMigrations:
    def test_reopening_db_is_idempotent(self, tmp_path: Path) -> None:
        db_path = tmp_path / "memory.db"
        repo = tmp_path / "repo"
        repo.mkdir()
        first = open_writable(db_path)
        first.projects.set_paths("platform", [str(repo)])
        first.connection.close()

        reopened = open_writable(db_path)
        assert reopened.projects.get_project_for_path(str(repo)) == "platform"
        reopened.connection.close()

    def test_vote_score_backfills_to_zero(self, tmp_path: Path) -> None:
        db_path = tmp_path / "memory.db"
        first = open_writable(db_path)
        first.entities.create("proj", [{"name": "task/a", "entityType": "task", "observations": ["x"]}])
        first.connection.close()

        reopened = open_writable(db_path)
        assert reopened.reads.get_entity("proj", "task/a").vote_score == 0
        reopened.connection.close()

    def test_surfaced_entities_table_and_indexes_exist(self, store: Storage) -> None:
        tables = {row[0] for row in store.connection.query_all("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "surfaced_entities" in tables

        columns = {row[1] for row in store.connection.query_all("PRAGMA table_info(surfaced_entities)")}
        assert columns == {
            "id",
            "retrieval_id",
            "project",
            "query",
            "tool",
            "entity_name",
            "rank",
            "surfaced_at",
            "used_at",
            "vote_cast",
        }

        indexes = {
            row[0]
            for row in store.connection.query_all(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='surfaced_entities'"
            )
        }
        assert "idx_surfaced_project_name" in indexes
        assert "idx_surfaced_retrieval" in indexes

    def test_surfaced_entities_migration_is_idempotent(self, store: Storage) -> None:
        v21 = next(m for m in MIGRATIONS if m.version == 21)
        with store.connection.transaction():
            for statement in v21.statements:
                store.connection.write(statement)

    def test_observation_vote_score_column_backfills_to_zero(self, tmp_path: Path) -> None:
        db_path = tmp_path / "memory.db"
        first = open_writable(db_path)
        first.entities.create("proj", [{"name": "task/a", "entityType": "task", "observations": ["x"]}])
        first.connection.close()

        reopened = open_writable(db_path)
        scores = [row[0] for row in reopened.connection.query_all("SELECT vote_score FROM observations")]
        assert scores == [0]
        reopened.connection.close()

    def test_hash_observation_is_deterministic(self) -> None:
        expected = hashlib.sha256(b"abc").hexdigest()[:8]
        assert hash_observation("abc") == expected
        assert hash_observation("abc") == hash_observation("abc")
        assert len(hash_observation("abc")) == 8

    def test_content_hash_backfills_for_preexisting_rows(self, tmp_path: Path) -> None:
        db_path = tmp_path / "memory.db"
        first = open_writable(db_path)
        first.entities.create("proj", [{"name": "task/a", "entityType": "task", "observations": ["x", "y"]}])
        with first.connection.transaction():
            first.connection.write("UPDATE observations SET content_hash = NULL")
        first.connection.close()

        reopened = open_writable(db_path)
        rows = reopened.connection.query_all("SELECT content, content_hash FROM observations")
        assert rows
        for row in rows:
            assert row["content_hash"] is not None
            assert row["content_hash"] == hash_observation(row["content"])
        reopened.connection.close()

    def test_content_hash_backfill_is_idempotent(self, tmp_path: Path) -> None:
        db_path = tmp_path / "memory.db"
        first = open_writable(db_path)
        first.entities.create("proj", [{"name": "task/a", "entityType": "task", "observations": ["x", "y"]}])
        before = {
            row["id"]: row["content_hash"]
            for row in first.connection.query_all("SELECT id, content_hash FROM observations")
        }
        first.connection.close()

        reopened = open_writable(db_path)
        after = {
            row["id"]: row["content_hash"]
            for row in reopened.connection.query_all("SELECT id, content_hash FROM observations")
        }
        assert after == before
        reopened.connection.close()

    def test_content_hash_column_and_index_exist(self, store: Storage) -> None:
        columns = {row[1] for row in store.connection.query_all("PRAGMA table_info(observations)")}
        assert "content_hash" in columns

        indexes = {
            row[0]
            for row in store.connection.query_all(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='observations'"
            )
        }
        assert "idx_observations_content_hash" in indexes

    def test_insert_sites_populate_content_hash(self, store: Storage) -> None:
        store.entities.create("proj", [{"name": "task/a", "entityType": "task", "observations": ["created"]}])
        store.observations.add("proj", "task/a", ["appended"])

        rows = store.connection.query_all("SELECT content, content_hash FROM observations")
        assert {row["content"] for row in rows} == {"created", "appended"}
        for row in rows:
            assert row["content_hash"] == hash_observation(row["content"])

    def test_archive_backfill_archives_resolved_stale_entities(self, store: Storage) -> None:
        store.entities.create(
            "proj",
            [
                {
                    "name": "stale",
                    "entityType": "task",
                    "observations": ["x"],
                    "status": "resolved",
                },
                {
                    "name": "fresh",
                    "entityType": "task",
                    "observations": ["x"],
                    "status": "resolved",
                },
            ],
        )
        with store.connection.transaction():
            store.connection.write("UPDATE entities SET updated_at = datetime('now', '-60 days') WHERE name = 'stale'")
            store.connection.write("DELETE FROM schema_version WHERE version = 27")

        run_migrations(store.connection.raw)

        assert store.reads.get_entity("proj", "stale").status == "archived"
        assert store.reads.get_entity("proj", "fresh").status == "resolved"

    def test_archive_backfill_spares_never_evict_entities(self, store: Storage) -> None:
        store.entities.create(
            "proj",
            [{"name": "used", "entityType": "task", "observations": ["x"], "status": "resolved"}],
        )
        with store.connection.transaction():
            store.connection.write("UPDATE entities SET updated_at = datetime('now', '-60 days') WHERE name = 'used'")
        store.telemetry.record_surfaced("search_nodes", "q", "rid", [("proj", "used", 1)])
        store.telemetry.register_use("proj", "used", window_seconds=1800, max_per_day=3)
        with store.connection.transaction():
            store.connection.write("DELETE FROM schema_version WHERE version = 27")

        run_migrations(store.connection.raw)

        assert store.reads.get_entity("proj", "used").status == "resolved"

    def test_archive_backfill_with_empty_telemetry_archives_all_stale(self, store: Storage) -> None:
        store.entities.create(
            "proj",
            [
                {"name": "a", "entityType": "task", "observations": ["x"], "status": "resolved"},
                {"name": "b", "entityType": "task", "observations": ["x"], "status": "resolved"},
            ],
        )
        with store.connection.transaction():
            store.connection.write("UPDATE entities SET updated_at = datetime('now', '-60 days')")
            store.connection.write("DELETE FROM schema_version WHERE version = 27")
        assert store.connection.query_one("SELECT COUNT(*) FROM surfaced_entities")[0] == 0

        run_migrations(store.connection.raw)

        assert store.reads.get_entity("proj", "a").status == "archived"
        assert store.reads.get_entity("proj", "b").status == "archived"

    def test_archive_backfill_is_idempotent(self, store: Storage) -> None:
        store.entities.create(
            "proj",
            [{"name": "stale", "entityType": "task", "observations": ["x"], "status": "resolved"}],
        )
        with store.connection.transaction():
            store.connection.write("UPDATE entities SET updated_at = datetime('now', '-60 days') WHERE name = 'stale'")
            store.connection.write("DELETE FROM schema_version WHERE version = 27")
        run_migrations(store.connection.raw)
        assert store.reads.get_entity("proj", "stale").status == "archived"

        with store.connection.transaction():
            store.connection.write("DELETE FROM schema_version WHERE version = 27")
        run_migrations(store.connection.raw)

        assert store.reads.get_entity("proj", "stale").status == "archived"


class TestConnectionPragmas:
    def test_busy_timeout_is_set(self, store: Storage) -> None:
        assert store.connection.query_one("PRAGMA busy_timeout")[0] == 5000


class TestVoteScoreReadPaths:
    def test_new_entity_reports_zero_vote_score(self, store: Storage) -> None:
        store.entities.create("proj", [{"name": "task/a", "entityType": "task", "observations": ["keyword"]}])

        assert store.reads.get_entity("proj", "task/a").vote_score == 0
        assert store.reads.search("proj", "keyword")["entities"][0].vote_score == 0
        assert store.reads.recent("proj")["entities"][0].vote_score == 0


class TestRecordSurfaced:
    def test_records_one_row_per_hit_with_rank_query_and_retrieval_id(self, store: Storage) -> None:
        store.telemetry.record_surfaced(
            "search_nodes",
            "cache miss",
            "rid-1",
            [("proj", "task/a", 1), ("proj", "task/b", 2)],
        )

        rows = store.connection.query_all(
            "SELECT retrieval_id, project, query, tool, entity_name, rank, "
            "used_at, vote_cast FROM surfaced_entities ORDER BY rank"
        )
        assert [(r["entity_name"], r["rank"]) for r in rows] == [("task/a", 1), ("task/b", 2)]
        assert all(r["retrieval_id"] == "rid-1" for r in rows)
        assert all(r["query"] == "cache miss" for r in rows)
        assert all(r["tool"] == "search_nodes" for r in rows)
        assert all(r["project"] == "proj" for r in rows)
        assert all(r["used_at"] is None for r in rows)
        assert all(r["vote_cast"] == 0 for r in rows)

    def test_empty_hits_records_nothing(self, store: Storage) -> None:
        store.telemetry.record_surfaced("search_nodes", "q", "rid-empty", [])
        count = store.connection.query_one("SELECT COUNT(*) AS n FROM surfaced_entities")["n"]
        assert count == 0


class TestRegisterUse:
    def _surface(self, store: Storage, name: str, retrieval_id: str = "rid") -> None:
        store.telemetry.record_surfaced("search_nodes", "q", retrieval_id, [("proj", name, 1)])

    def _backdate_surfaced(self, store: Storage, name: str, seconds: int) -> None:
        with store.connection.transaction():
            store.connection.write(
                "UPDATE surfaced_entities SET surfaced_at = datetime('now', ?) WHERE entity_name = ?",
                (f"-{seconds} seconds", name),
            )

    def _make_entity(self, store: Storage, name: str) -> None:
        store.entities.create("proj", [{"name": name, "entityType": "task", "observations": ["x"]}])

    def test_in_window_edit_casts_one_upvote(self, store: Storage) -> None:
        self._make_entity(store, "task/a")
        self._surface(store, "task/a")

        new_score = store.telemetry.register_use("proj", "task/a", window_seconds=1800.0, max_per_day=3)

        assert new_score == 1
        assert store.reads.get_entity("proj", "task/a").vote_score == 1

    def test_out_of_window_edit_casts_nothing(self, store: Storage) -> None:
        self._make_entity(store, "task/a")
        self._surface(store, "task/a")
        self._backdate_surfaced(store, "task/a", 3600)

        new_score = store.telemetry.register_use("proj", "task/a", window_seconds=1800, max_per_day=3)

        assert new_score is None
        assert store.reads.get_entity("proj", "task/a").vote_score == 0

    def test_no_surfacing_casts_nothing(self, store: Storage) -> None:
        self._make_entity(store, "task/a")

        assert store.telemetry.register_use("proj", "task/a", window_seconds=1800, max_per_day=3) is None
        assert store.reads.get_entity("proj", "task/a").vote_score == 0

    def test_re_edit_after_one_search_does_not_double_vote(self, store: Storage) -> None:
        self._make_entity(store, "task/a")
        self._surface(store, "task/a")

        first = store.telemetry.register_use("proj", "task/a", window_seconds=1800, max_per_day=3)
        second = store.telemetry.register_use("proj", "task/a", window_seconds=1800, max_per_day=3)

        assert first == 1
        assert second is None
        assert store.reads.get_entity("proj", "task/a").vote_score == 1

    def test_two_separate_searches_each_earn_a_vote(self, store: Storage) -> None:
        self._make_entity(store, "task/a")
        self._surface(store, "task/a", "rid-1")
        store.telemetry.register_use("proj", "task/a", window_seconds=1800, max_per_day=3)
        self._surface(store, "task/a", "rid-2")
        second = store.telemetry.register_use("proj", "task/a", window_seconds=1800, max_per_day=3)

        assert second == 2
        assert store.reads.get_entity("proj", "task/a").vote_score == 2

    def test_daily_cap_records_use_but_skips_vote(self, store: Storage) -> None:
        self._make_entity(store, "task/a")
        scores = []
        for i in range(3):
            self._surface(store, "task/a", f"rid-{i}")
            scores.append(store.telemetry.register_use("proj", "task/a", window_seconds=1800, max_per_day=2))

        assert scores == [1, 2, None]
        assert store.reads.get_entity("proj", "task/a").vote_score == 2
        used = store.connection.query_one("SELECT COUNT(*) AS n FROM surfaced_entities WHERE used_at IS NOT NULL")["n"]
        assert used == 3

    def test_auto_vote_leaves_updated_at_untouched(self, store: Storage) -> None:
        self._make_entity(store, "task/a")
        before = store.reads.get_entity("proj", "task/a").updated_at
        self._surface(store, "task/a")

        store.telemetry.register_use("proj", "task/a", window_seconds=1800, max_per_day=3)

        assert store.reads.get_entity("proj", "task/a").updated_at == before

    def test_deleted_entity_is_skipped(self, store: Storage) -> None:
        self._surface(store, "task/gone")

        assert store.telemetry.register_use("proj", "task/gone", window_seconds=1800, max_per_day=3) is None


class TestPruneSurfaced:
    def test_prune_drops_rows_older_than_retention(self, store: Storage) -> None:
        store.telemetry.record_surfaced("search_nodes", "q", "old", [("proj", "task/a", 1)])
        store.telemetry.record_surfaced("search_nodes", "q", "new", [("proj", "task/b", 1)])
        with store.connection.transaction():
            store.connection.write(
                "UPDATE surfaced_entities SET surfaced_at = datetime('now', '-40 days') WHERE retrieval_id = 'old'"
            )

        removed = store.telemetry.prune_surfaced(retention_days=30)

        assert removed == 1
        remaining = {
            row["retrieval_id"] for row in store.connection.query_all("SELECT retrieval_id FROM surfaced_entities")
        }
        assert remaining == {"new"}

    def test_negative_retention_window_prunes_nothing(self, store: Storage) -> None:
        store.telemetry.record_surfaced("search_nodes", "q", "old", [("proj", "task/a", 1)])
        with store.connection.transaction():
            store.connection.write("UPDATE surfaced_entities SET surfaced_at = datetime('now', '-400 days')")

        assert store.telemetry.prune_surfaced(-1) == 0
        assert store.connection.query_one("SELECT COUNT(*) AS n FROM surfaced_entities")["n"] == 1

    def test_startup_keeps_old_surfacings_by_default(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MCP_MEMORY_SURFACED_RETENTION_DAYS", raising=False)
        db_path = tmp_path / "memory.db"
        first = open_writable(db_path)
        first.telemetry.record_surfaced("search_nodes", "q", "old", [("proj", "task/a", 1)])
        with first.connection.transaction():
            first.connection.write("UPDATE surfaced_entities SET surfaced_at = datetime('now', '-400 days')")
        first.connection.close()

        reopened = open_writable(db_path)
        count = reopened.connection.query_one("SELECT COUNT(*) AS n FROM surfaced_entities")["n"]
        assert count == 1
        reopened.connection.close()

    def test_startup_retention_respects_env_override(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MCP_MEMORY_SURFACED_RETENTION_DAYS", "5")
        db_path = tmp_path / "memory.db"
        first = open_writable(db_path)
        first.telemetry.record_surfaced("search_nodes", "q", "old", [("proj", "task/a", 1)])
        with first.connection.transaction():
            first.connection.write("UPDATE surfaced_entities SET surfaced_at = datetime('now', '-10 days')")
        first.connection.close()

        reopened = open_writable(db_path)
        count = reopened.connection.query_one("SELECT COUNT(*) AS n FROM surfaced_entities")["n"]
        assert count == 0
        reopened.connection.close()

    def test_startup_survives_locked_database_during_maintenance(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        db_path = tmp_path / "memory.db"
        open_writable(db_path).connection.close()

        def _raise_locked(self: TelemetryRepository, retention_days: int) -> int:
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(TelemetryRepository, "prune_surfaced", _raise_locked)

        reopened = open_writable(db_path)
        reopened.connection.close()


class TestArchiveStale:
    def test_archives_resolved_entity_past_threshold(self, store: Storage) -> None:
        store.entities.create(
            "proj",
            [{"name": "e1", "entityType": "task", "observations": ["x"], "status": "resolved"}],
        )
        with store.connection.transaction():
            store.connection.write("UPDATE entities SET updated_at = datetime('now', '-60 days') WHERE name = 'e1'")

        assert store.maintenance._archive_stale_entities(threshold_days=56) == 1
        assert store.reads.get_entity("proj", "e1").status == "archived"

    def test_keeps_resolved_entity_inside_threshold(self, store: Storage) -> None:
        store.entities.create(
            "proj",
            [{"name": "e1", "entityType": "task", "observations": ["x"], "status": "resolved"}],
        )
        with store.connection.transaction():
            store.connection.write("UPDATE entities SET updated_at = datetime('now', '-10 days') WHERE name = 'e1'")

        assert store.maintenance._archive_stale_entities(threshold_days=56) == 0
        assert store.reads.get_entity("proj", "e1").status == "resolved"

    def test_keeps_entity_used_after_surfacing(self, store: Storage) -> None:
        store.entities.create(
            "proj",
            [{"name": "e1", "entityType": "task", "observations": ["x"], "status": "resolved"}],
        )
        with store.connection.transaction():
            store.connection.write("UPDATE entities SET updated_at = datetime('now', '-60 days') WHERE name = 'e1'")
        store.telemetry.record_surfaced("search_nodes", "q", "rid", [("proj", "e1", 1)])
        store.telemetry.register_use("proj", "e1", window_seconds=1800, max_per_day=3)

        assert store.maintenance._archive_stale_entities(threshold_days=56) == 0
        assert store.reads.get_entity("proj", "e1").status == "resolved"

    def test_keeps_surfaced_but_unused_entity_archivable(self, store: Storage) -> None:
        store.entities.create(
            "proj",
            [{"name": "e1", "entityType": "task", "observations": ["x"], "status": "resolved"}],
        )
        with store.connection.transaction():
            store.connection.write("UPDATE entities SET updated_at = datetime('now', '-60 days') WHERE name = 'e1'")
        store.telemetry.record_surfaced("search_nodes", "q", "rid", [("proj", "e1", 1)])

        assert store.maintenance._archive_stale_entities(threshold_days=56) == 1
        assert store.reads.get_entity("proj", "e1").status == "archived"

    def test_ignores_non_resolved_statuses(self, store: Storage) -> None:
        store.entities.create(
            "proj",
            [{"name": "e1", "entityType": "task", "observations": ["x"], "status": "in-progress"}],
        )
        with store.connection.transaction():
            store.connection.write("UPDATE entities SET updated_at = datetime('now', '-60 days') WHERE name = 'e1'")

        assert store.maintenance._archive_stale_entities(threshold_days=56) == 0
        assert store.reads.get_entity("proj", "e1").status == "in-progress"

    def test_ignores_soft_deleted_entity(self, store: Storage) -> None:
        store.entities.create(
            "proj",
            [{"name": "e1", "entityType": "task", "observations": ["x"], "status": "resolved"}],
        )
        with store.connection.transaction():
            store.connection.write("UPDATE entities SET updated_at = datetime('now', '-60 days') WHERE name = 'e1'")
        soft_delete_store(store, "proj", "e1")

        assert store.maintenance._archive_stale_entities(threshold_days=56) == 0

    def test_startup_archives_stale_entities_by_default(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MCP_MEMORY_ARCHIVE_ENABLED", raising=False)
        db_path = tmp_path / "memory.db"
        first = open_writable(db_path)
        first.entities.create(
            "proj",
            [{"name": "e1", "entityType": "task", "observations": ["x"], "status": "resolved"}],
        )
        with first.connection.transaction():
            first.connection.write("UPDATE entities SET updated_at = datetime('now', '-60 days') WHERE name = 'e1'")
        first.connection.close()

        reopened = open_writable(db_path)
        assert reopened.reads.get_entity("proj", "e1").status == "archived"
        reopened.connection.close()

    def test_startup_archives_nothing_when_disabled(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MCP_MEMORY_ARCHIVE_ENABLED", "false")
        db_path = tmp_path / "memory.db"
        first = open_writable(db_path)
        first.entities.create(
            "proj",
            [{"name": "e1", "entityType": "task", "observations": ["x"], "status": "resolved"}],
        )
        with first.connection.transaction():
            first.connection.write("UPDATE entities SET updated_at = datetime('now', '-60 days') WHERE name = 'e1'")
        first.connection.close()

        reopened = open_writable(db_path)
        assert reopened.reads.get_entity("proj", "e1").status == "resolved"
        reopened.connection.close()


class TestArchivedExclusion:
    def test_null_status_entity_is_still_returned_by_default(self, store: Storage) -> None:
        store.entities.create("proj", [{"name": "e1", "entityType": "task", "observations": ["keyword"]}])
        result = store.reads.search("proj", "keyword")
        assert [e.name for e in result["entities"]] == ["e1"]

    def test_archived_entity_is_absent_from_search_by_default(self, store: Storage) -> None:
        store.entities.create(
            "proj",
            [
                {
                    "name": "e1",
                    "entityType": "task",
                    "observations": ["keyword"],
                    "status": "archived",
                }
            ],
        )
        result = store.reads.search("proj", "keyword")
        assert result["entities"] == []

    def test_explicit_archived_status_filter_returns_archived(self, store: Storage) -> None:
        store.entities.create(
            "proj",
            [
                {
                    "name": "e1",
                    "entityType": "task",
                    "observations": ["keyword"],
                    "status": "archived",
                }
            ],
        )
        result = store.reads.search("proj", "keyword", status="archived")
        assert [e.name for e in result["entities"]] == ["e1"]

    def test_include_archived_returns_archived_alongside_live(self, store: Storage) -> None:
        store.entities.create(
            "proj",
            [
                {
                    "name": "a",
                    "entityType": "task",
                    "observations": ["keyword"],
                    "status": "archived",
                },
                {"name": "b", "entityType": "task", "observations": ["keyword"]},
            ],
        )
        result = store.reads.search("proj", "keyword", include_archived=True)
        assert {e.name for e in result["entities"]} == {"a", "b"}

    def test_explicit_status_filter_ignores_include_archived(self, store: Storage) -> None:
        store.entities.create(
            "proj",
            [
                {
                    "name": "a",
                    "entityType": "task",
                    "observations": ["keyword"],
                    "status": "resolved",
                },
                {
                    "name": "b",
                    "entityType": "task",
                    "observations": ["keyword"],
                    "status": "archived",
                },
            ],
        )
        result = store.reads.search("proj", "keyword", status="resolved", include_archived=True)
        assert [e.name for e in result["entities"]] == ["a"]


class TestDeleteEntityPurgesSurfaced:
    def test_delete_entity_removes_its_surfaced_rows(self, store: Storage) -> None:
        store.entities.create("proj", [{"name": "task/a", "entityType": "task", "observations": ["x"]}])
        store.telemetry.record_surfaced("search_nodes", "q", "rid", [("proj", "task/a", 1)])

        store.entities.delete("proj", "task/a")

        count = store.connection.query_one("SELECT COUNT(*) AS n FROM surfaced_entities WHERE entity_name = 'task/a'")[
            "n"
        ]
        assert count == 0


class TestRelationTypeBackfill:
    def _seed_variant_relation(self, store: Storage, source: str, target: str, variant: str) -> None:
        """Insert a relation using a raw (unvalidated) relation type, bypassing the server layer."""
        with store.connection.transaction():
            store.connection.write("INSERT OR IGNORE INTO relation_types (name) VALUES (?)", (variant,))
            src_id = store.connection.query_one("SELECT id FROM entities WHERE name = ?", (source,))[0]
            tgt_id = store.connection.query_one("SELECT id FROM entities WHERE name = ?", (target,))[0]
            type_id = store.connection.query_one("SELECT id FROM relation_types WHERE name = ?", (variant,))[0]
            store.connection.write(
                "INSERT OR IGNORE INTO relations (source_id, target_id, relation_type_id) VALUES (?, ?, ?)",
                (src_id, tgt_id, type_id),
            )

    def _rerun_backfill(self, store: Storage, db_path: Path) -> Storage:
        """Re-run v19's backfill statements directly to prove the backfill is idempotent.

        Rolling back schema_version and reopening would also re-run any later, non-idempotent
        migrations, so apply the v19 statements straight against the open connection instead.
        """
        with store.connection.transaction():
            for statement in _relation_type_backfill_statements():
                store.connection.write(statement)
        return store

    def test_variant_merged_into_canonical(self, tmp_path: Path) -> None:
        db_path = tmp_path / "memory.db"
        store = open_writable(db_path)
        store.entities.create(
            "proj",
            [
                {"name": "a", "entityType": "task", "observations": ["x"]},
                {"name": "b", "entityType": "project", "observations": ["y"]},
            ],
        )
        self._seed_variant_relation(store, "a", "b", "related-to")

        store = self._rerun_backfill(store, db_path)

        result = store.reads.get_entity_with_relations("proj", "a")
        assert [r.relation_type for r in result["relations"]] == ["relates-to"]
        orphan = store.connection.query_one("SELECT 1 FROM relation_types WHERE name = 'related-to'")
        assert orphan is None

    def test_long_tail_collapsed(self, tmp_path: Path) -> None:
        db_path = tmp_path / "memory.db"
        store = open_writable(db_path)
        store.entities.create(
            "proj",
            [
                {"name": "a", "entityType": "task", "observations": ["x"]},
                {"name": "c", "entityType": "feature", "observations": ["z"]},
            ],
        )
        self._seed_variant_relation(store, "a", "c", "extends")

        store = self._rerun_backfill(store, db_path)

        result = store.reads.get_entity_with_relations("proj", "a")
        assert [r.relation_type for r in result["relations"]] == ["implements"]

    def test_underscore_and_camel_variants_merged(self, tmp_path: Path) -> None:
        db_path = tmp_path / "memory.db"
        store = open_writable(db_path)
        store.entities.create(
            "proj",
            [
                {"name": "a", "entityType": "task", "observations": ["x"]},
                {"name": "b", "entityType": "project", "observations": ["y"]},
                {"name": "c", "entityType": "feature", "observations": ["z"]},
            ],
        )
        self._seed_variant_relation(store, "a", "b", "related_to")
        self._seed_variant_relation(store, "a", "c", "blockedBy")

        store = self._rerun_backfill(store, db_path)

        result = store.reads.get_entity_with_relations("proj", "a")
        types = sorted(r.relation_type for r in result["relations"])
        assert types == ["depends-on", "relates-to"]

    def test_collision_drops_duplicate(self, tmp_path: Path) -> None:
        db_path = tmp_path / "memory.db"
        store = open_writable(db_path)
        store.entities.create(
            "proj",
            [
                {"name": "a", "entityType": "task", "observations": ["x"]},
                {"name": "b", "entityType": "project", "observations": ["y"]},
            ],
        )
        store.relations.create("proj", [Relation(source="a", target="b", relation_type="relates-to")])
        self._seed_variant_relation(store, "a", "b", "related-to")

        store = self._rerun_backfill(store, db_path)

        result = store.reads.get_entity_with_relations("proj", "a")
        assert [r.relation_type for r in result["relations"]] == ["relates-to"]


class TestProjectPaths:
    def test_set_and_get_round_trip(self, store: Storage, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        store.projects.set_paths("platform", [str(repo)])
        assert store.projects.get_project_for_path(str(repo / "src" / "x.py")) == "platform"

    def test_set_normalises_stored_paths(self, store: Storage, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        store.projects.set_paths("platform", [str(tmp_path / "repo" / "." / "")])
        assert store.projects.paths() == [("platform", normalize_path(str(repo)))]

    def test_set_replaces_existing_paths(self, store: Storage, tmp_path: Path) -> None:
        first = tmp_path / "first"
        second = tmp_path / "second"
        first.mkdir()
        second.mkdir()
        store.projects.set_paths("platform", [str(first)])
        store.projects.set_paths("platform", [str(second)])
        assert store.projects.get_project_for_path(str(first)) is None
        assert store.projects.get_project_for_path(str(second)) == "platform"

    def test_set_creates_project_row(self, store: Storage, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        store.projects.set_paths("brand-new", [str(repo)])
        assert "brand-new" in store.projects.names()

    def test_path_owned_by_another_project_raises(self, store: Storage, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        store.projects.set_paths("platform", [str(repo)])
        with pytest.raises(ValueError, match="already registered"):
            store.projects.set_paths("other", [str(repo)])

    def test_get_returns_none_when_unmatched(self, store: Storage, tmp_path: Path) -> None:
        assert store.projects.get_project_for_path(str(tmp_path / "nowhere")) is None

    def test_empty_project_raises(self, store: Storage, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            store.projects.set_paths("", [str(tmp_path)])

    def test_non_list_paths_raises(self, store: Storage) -> None:
        with pytest.raises(TypeError, match="list"):
            store.projects.set_paths("platform", "not-a-list")  # type: ignore[arg-type]

    def test_get_paths_for_project_returns_registered_paths(self, store: Storage, tmp_path: Path) -> None:
        first = tmp_path / "first"
        second = tmp_path / "second"
        first.mkdir()
        second.mkdir()
        store.projects.set_paths("platform", [str(first), str(second)])
        assert store.projects.paths_for("platform") == [
            normalize_path(str(first)),
            normalize_path(str(second)),
        ]

    def test_get_paths_for_unknown_project_returns_empty_without_creating(self, store: Storage) -> None:
        assert store.projects.paths_for("ghost") == []
        assert "ghost" not in store.projects.names()

    def test_add_project_path_registers_without_replacing(self, store: Storage, tmp_path: Path) -> None:
        first = tmp_path / "first"
        second = tmp_path / "second"
        first.mkdir()
        second.mkdir()
        store.projects.set_paths("platform", [str(first)])
        store.projects.add_path("platform", str(second))
        assert store.projects.get_project_for_path(str(first)) == "platform"
        assert store.projects.get_project_for_path(str(second)) == "platform"

    def test_add_project_path_is_idempotent(self, store: Storage, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        store.projects.add_path("platform", str(repo))
        store.projects.add_path("platform", str(repo))
        assert store.projects.paths_for("platform") == [normalize_path(str(repo))]

    def test_add_project_path_creates_project_row(self, store: Storage, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        store.projects.add_path("brand-new", str(repo))
        assert "brand-new" in store.projects.names()

    def test_add_project_path_ignores_path_owned_by_another_project(self, store: Storage, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        store.projects.set_paths("platform", [str(repo)])
        store.projects.add_path("other", str(repo))
        assert store.projects.get_project_for_path(str(repo)) == "platform"
        assert store.projects.paths_for("other") == []


class TestProjectGroups:
    def test_get_group_members_empty_when_no_group(self, store: Storage) -> None:
        assert store.projects.group_members("solo") == []

    def test_set_and_get_group_members(self, store: Storage) -> None:
        store.projects.set_groups("llm-prompts", ["tooling"])
        store.projects.set_groups("cline-hooks", ["tooling"])
        assert store.projects.group_members("llm-prompts") == ["cline-hooks"]
        assert store.projects.group_members("cline-hooks") == ["llm-prompts"]

    def test_get_group_members_excludes_self(self, store: Storage) -> None:
        store.projects.set_groups("a", ["g"])
        assert store.projects.group_members("a") == []

    def test_get_group_members_unions_multiple_matching_groups(self, store: Storage) -> None:
        store.projects.set_groups("a", ["g1", "g2"])
        store.projects.set_groups("b", ["g1"])
        store.projects.set_groups("c", ["g2"])
        assert store.projects.group_members("a") == ["b", "c"]

    def test_set_replaces_existing_groups(self, store: Storage) -> None:
        store.projects.set_groups("a", ["g1"])
        store.projects.set_groups("b", ["g1"])
        store.projects.set_groups("a", ["g2"])
        store.projects.set_groups("c", ["g2"])
        assert store.projects.group_members("a") == ["c"]

    def test_set_creates_project_row(self, store: Storage) -> None:
        store.projects.set_groups("brand-new", ["g"])
        assert "brand-new" in store.projects.names()

    def test_empty_project_raises(self, store: Storage) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            store.projects.set_groups("", ["g"])

    def test_non_list_groups_raises(self, store: Storage) -> None:
        with pytest.raises(TypeError, match="list"):
            store.projects.set_groups("a", "not-a-list")  # type: ignore[arg-type]

    def test_list_project_groups_filters_by_project(self, store: Storage) -> None:
        store.projects.set_groups("a", ["g1"])
        store.projects.set_groups("b", ["g1"])
        assert store.projects.groups("a") == [("a", "g1")]

    def test_list_project_groups_returns_all_when_unfiltered(self, store: Storage) -> None:
        store.projects.set_groups("a", ["g1"])
        store.projects.set_groups("b", ["g2"])
        assert sorted(store.projects.groups()) == [("a", "g1"), ("b", "g2")]


class TestObservations:
    def test_add_observations(self, store: Storage) -> None:
        store.entities.create("proj", [{"name": "e1", "entityType": "task", "observations": ["a"]}])
        hashes = store.observations.add("proj", "e1", ["b", "c"])
        assert hashes == [hash_observation("b"), hash_observation("c")]
        assert obs_contents(store.reads.get_entity("proj", "e1")) == ["a", "b", "c"]

    def test_add_observations_deduplicates(self, store: Storage) -> None:
        store.entities.create("proj", [{"name": "e1", "entityType": "task", "observations": ["a"]}])
        hashes = store.observations.add("proj", "e1", ["a", "b"])
        assert hashes == [hash_observation("b")]

    def test_add_observations_missing_entity_raises(self, store: Storage) -> None:
        with pytest.raises(ValueError, match="not found"):
            store.observations.add("proj", "missing", ["x"])

    def test_delete_observations(self, store: Storage) -> None:
        store.entities.create("proj", [{"name": "e1", "entityType": "task", "observations": ["a", "b", "c"]}])
        count = store.observations.delete("proj", "e1", ["b"])
        assert count == 1
        assert obs_contents(store.reads.get_entity("proj", "e1")) == ["a", "c"]

    def test_delete_observations_nonexistent_content(self, store: Storage) -> None:
        store.entities.create("proj", [{"name": "e1", "entityType": "task", "observations": ["a"]}])
        count = store.observations.delete("proj", "e1", ["missing"])
        assert count == 0

    def test_delete_observations_missing_entity_raises(self, store: Storage) -> None:
        with pytest.raises(ValueError, match="not found"):
            store.observations.delete("proj", "missing", ["x"])

    def test_delete_observations_requires_addressing(self, store: Storage) -> None:
        store.entities.create("proj", [{"name": "e1", "entityType": "task", "observations": ["a"]}])
        with pytest.raises(ValueError, match="at least one"):
            store.observations.delete("proj", "e1")

    def test_delete_observations_by_hash(self, store: Storage) -> None:
        store.entities.create("proj", [{"name": "e1", "entityType": "task", "observations": ["a", "b", "c"]}])
        count = store.observations.delete("proj", "e1", hashes=[hash_observation("b")])
        assert count == 1
        assert obs_contents(store.reads.get_entity("proj", "e1")) == ["a", "c"]

    def test_delete_observations_unknown_hash_deletes_nothing(self, store: Storage) -> None:
        store.entities.create("proj", [{"name": "e1", "entityType": "task", "observations": ["a"]}])
        assert store.observations.delete("proj", "e1", hashes=["deadbeef"]) == 0

    def test_trim_observations_to_outcome(self, store: Storage) -> None:
        store.entities.create(
            "proj",
            [{"name": "e1", "entityType": "task", "observations": ["a", "b", "c", "d", "e"]}],
        )
        keep = [hash_observation("a"), hash_observation("c")]
        assert store.observations.trim_to_outcome("proj", "e1", keep) == 3
        assert obs_contents(store.reads.get_entity("proj", "e1")) == ["a", "c"]

    def test_trim_observations_to_outcome_empty_keep_raises(self, store: Storage) -> None:
        store.entities.create("proj", [{"name": "e1", "entityType": "task", "observations": ["a"]}])
        with pytest.raises(ValueError, match="at least one"):
            store.observations.trim_to_outcome("proj", "e1", [])

    def test_trim_observations_to_outcome_missing_entity_raises(self, store: Storage) -> None:
        with pytest.raises(ValueError, match="not found"):
            store.observations.trim_to_outcome("proj", "missing", ["deadbeef"])

    def test_upvoted_observation_leads(self, store: Storage) -> None:
        store.entities.create("proj", [{"name": "e1", "entityType": "task", "observations": ["a", "b", "c"]}])
        store.observations.vote("proj", "e1", 1, content="c")
        assert obs_contents(store.reads.get_entity("proj", "e1")) == ["c", "a", "b"]

    def test_downvoted_observation_sinks(self, store: Storage) -> None:
        store.entities.create("proj", [{"name": "e1", "entityType": "task", "observations": ["a", "b", "c"]}])
        store.observations.vote("proj", "e1", -1, content="a")
        assert obs_contents(store.reads.get_entity("proj", "e1")) == ["b", "c", "a"]

    def test_unvoted_observations_keep_insertion_order(self, store: Storage) -> None:
        store.entities.create("proj", [{"name": "e1", "entityType": "task", "observations": ["a", "b", "c"]}])
        assert obs_contents(store.reads.get_entity("proj", "e1")) == ["a", "b", "c"]

    def test_observation_votes_align_with_observation_order(self, store: Storage) -> None:
        store.entities.create("proj", [{"name": "e1", "entityType": "task", "observations": ["a", "b", "c"]}])
        store.observations.vote("proj", "e1", 1, content="c")
        assert obs_contents(store.reads.get_entity("proj", "e1")) == ["c", "a", "b"]
        assert obs_votes(store.reads.get_entity("proj", "e1")) == [1, 0, 0]

    def test_missing_entity_raises(self, store: Storage) -> None:
        with pytest.raises(ValueError, match="not found"):
            store.reads.get_entity("proj", "ghost")

    def test_get_entity_returns_observation_objects(self, store: Storage) -> None:
        store.entities.create("proj", [{"name": "e1", "entityType": "task", "observations": ["a"]}])
        observations = store.reads.get_entity("proj", "e1").observations
        assert len(observations) == 1
        assert observations[0].content == "a"
        assert observations[0].content_hash == hash_observation("a")
        assert observations[0].vote_score == 0

    def test_compact_read_yields_no_observations(self, store: Storage) -> None:
        store.entities.create("proj", [{"name": "e1", "entityType": "task", "observations": ["a"]}])
        assert store.reads.search("proj", "e1", compact=True)["entities"][0].observations == []


class TestEntityStatus:
    def test_set_status(self, store: Storage) -> None:
        store.entities.create("proj", [{"name": "e1", "entityType": "task", "observations": ["x"]}])
        store.entities.set_status("proj", "e1", "in-progress")
        assert store.reads.get_entity("proj", "e1").status == "in-progress"

    def test_clear_status(self, store: Storage) -> None:
        store.entities.create(
            "proj",
            [{"name": "e1", "entityType": "task", "observations": ["x"], "status": "planned"}],
        )
        store.entities.set_status("proj", "e1", None)
        assert store.reads.get_entity("proj", "e1").status is None

    def test_invalid_status_raises(self, store: Storage) -> None:
        store.entities.create("proj", [{"name": "e1", "entityType": "task", "observations": ["x"]}])
        with pytest.raises(ValueError, match="Invalid status"):
            store.entities.set_status("proj", "e1", "bad")  # type: ignore[arg-type]

    def test_missing_entity_raises(self, store: Storage) -> None:
        with pytest.raises(ValueError, match="not found"):
            store.entities.set_status("proj", "missing", "planned")

    def test_returns_observation_count(self, store: Storage) -> None:
        store.entities.create(
            "proj",
            [{"name": "e1", "entityType": "task", "observations": ["a", "b", "c", "d"]}],
        )
        assert store.entities.set_status("proj", "e1", "resolved") == 4


class TestRenameEntity:
    def test_rename_in_place_preserves_relations(self, store: Storage) -> None:
        store.entities.create(
            "proj",
            [
                {"name": "a", "entityType": "feature", "observations": ["x"]},
                {"name": "b", "entityType": "project", "observations": ["y"]},
            ],
        )
        store.relations.create("proj", [Relation(source="a", target="b", relation_type="belongs-to")])
        store.entities.rename("proj", "a", "a2")
        assert get_entity_id(store.connection, "a", get_or_create_project_id(store.connection, "proj")) is None
        result = store.reads.get_entity_with_relations("proj", "a2")
        assert result["entity"].name == "a2"
        assert result["relations"][0].source == "a2"
        assert result["relations"][0].target == "b"

    def test_rename_collision_raises(self, store: Storage) -> None:
        store.entities.create(
            "proj",
            [
                {"name": "a", "entityType": "task", "observations": ["x"]},
                {"name": "b", "entityType": "task", "observations": ["y"]},
            ],
        )
        with pytest.raises(ValueError, match="already exists"):
            store.entities.rename("proj", "a", "b")

    def test_rename_missing_entity_raises(self, store: Storage) -> None:
        with pytest.raises(ValueError, match="not found"):
            store.entities.rename("proj", "missing", "new")


class TestMoveEntityCrossScope:
    def test_move_relocates_entity(self, store: Storage) -> None:
        store.entities.create("src", [{"name": "e1", "entityType": "task", "observations": ["x"]}])
        store.entities.move_cross_scope("src", "dst", "e1")
        assert store.entities.exists_in("e1", "dst")
        assert not store.entities.exists_in("e1", "src")

    def test_move_drops_and_returns_relations(self, store: Storage) -> None:
        store.entities.create(
            "src",
            [
                {"name": "a", "entityType": "feature", "observations": ["x"]},
                {"name": "b", "entityType": "project", "observations": ["y"]},
            ],
        )
        store.relations.create("src", [Relation(source="a", target="b", relation_type="belongs-to")])
        dropped = store.entities.move_cross_scope("src", "dst", "a")
        assert dropped == [Relation(source="a", target="b", relation_type="belongs-to")]
        assert store.reads.get_entity_with_relations("src", "b")["relations"] == []

    def test_move_target_collision_raises(self, store: Storage) -> None:
        store.entities.create("src", [{"name": "e1", "entityType": "task", "observations": ["x"]}])
        store.entities.create("dst", [{"name": "e1", "entityType": "task", "observations": ["y"]}])
        with pytest.raises(ValueError, match="already exists"):
            store.entities.move_cross_scope("src", "dst", "e1")

    def test_move_missing_entity_raises(self, store: Storage) -> None:
        with pytest.raises(ValueError, match="not found"):
            store.entities.move_cross_scope("src", "dst", "missing")


class TestRelations:
    def test_create_and_retrieve_relations(self, store: Storage) -> None:
        store.entities.create(
            "proj",
            [
                {"name": "a", "entityType": "feature", "observations": ["x"]},
                {"name": "b", "entityType": "project", "observations": ["y"]},
            ],
        )
        store.relations.create("proj", [Relation(source="a", target="b", relation_type="belongs-to")])
        result = store.reads.get_entity_with_relations("proj", "a")
        assert len(result["relations"]) == 1
        rel = result["relations"][0]
        assert isinstance(rel, Relation)
        assert rel.source == "a"
        assert rel.target == "b"
        assert rel.relation_type == "belongs-to"

    def test_duplicate_relation_ignored(self, store: Storage) -> None:
        store.entities.create(
            "proj",
            [
                {"name": "a", "entityType": "feature", "observations": ["x"]},
                {"name": "b", "entityType": "project", "observations": ["y"]},
            ],
        )
        rel = Relation(source="a", target="b", relation_type="belongs-to")
        store.relations.create("proj", [rel])
        store.relations.create("proj", [rel])
        result = store.reads.get_entity_with_relations("proj", "a")
        assert len(result["relations"]) == 1

    def test_relation_type_alias_normalized_on_insert(self, store: Storage) -> None:
        store.entities.create(
            "proj",
            [
                {"name": "a", "entityType": "feature", "observations": ["x"]},
                {"name": "b", "entityType": "project", "observations": ["y"]},
            ],
        )
        store.relations.create("proj", [Relation(source="a", target="b", relation_type="extends")])
        result = store.reads.get_entity_with_relations("proj", "a")
        assert len(result["relations"]) == 1
        assert result["relations"][0].relation_type == "implements"

    def test_unknown_relation_type_rejected(self, store: Storage) -> None:
        store.entities.create(
            "proj",
            [
                {"name": "a", "entityType": "feature", "observations": ["x"]},
                {"name": "b", "entityType": "project", "observations": ["y"]},
            ],
        )
        with pytest.raises(ValueError, match="Invalid relation type"):
            store.relations.create("proj", [Relation(source="a", target="b", relation_type="frobnicates")])

    def test_missing_source_raises(self, store: Storage) -> None:
        store.entities.create("proj", [{"name": "b", "entityType": "task", "observations": ["x"]}])
        with pytest.raises(ValueError, match="Source entity"):
            store.relations.create("proj", [Relation(source="missing", target="b", relation_type="x")])

    def test_rejected_batch_leaves_none_of_its_relations_behind(self, store: Storage) -> None:
        store.entities.create(
            "proj",
            [
                {"name": "a", "entityType": "feature", "observations": ["x"]},
                {"name": "b", "entityType": "project", "observations": ["y"]},
                {"name": "c", "entityType": "project", "observations": ["z"]},
            ],
        )
        with pytest.raises(ValueError, match="Invalid relation type"):
            store.relations.create(
                "proj",
                [
                    Relation(source="a", target="b", relation_type="belongs-to"),
                    Relation(source="a", target="c", relation_type="frobnicates"),
                ],
            )
        store.entities.vote("proj", "a", 1)
        assert store.reads.get_entity_with_relations("proj", "a")["relations"] == []

    def test_delete_relation(self, store: Storage) -> None:
        store.entities.create(
            "proj",
            [
                {"name": "a", "entityType": "feature", "observations": ["x"]},
                {"name": "b", "entityType": "project", "observations": ["y"]},
                {"name": "c", "entityType": "project", "observations": ["z"]},
            ],
        )
        store.relations.create("proj", [Relation(source="a", target="b", relation_type="belongs-to")])
        store.relations.create("proj", [Relation(source="a", target="c", relation_type="used-in")])
        store.relations.delete("proj", "a", "b", "belongs-to")
        result = store.reads.get_entity_with_relations("proj", "a")
        assert len(result["relations"]) == 1

    def test_task_to_project_belongs_to_rejected(self, store: Storage) -> None:
        store.entities.create(
            "proj",
            [
                {"name": "task/a", "entityType": "task", "observations": ["x"]},
                {"name": "project/proj", "entityType": "project", "observations": ["y"]},
            ],
        )
        with pytest.raises(ValueError, match="task -> project 'belongs-to' relations"):
            store.relations.create(
                "proj",
                [Relation(source="task/a", target="project/proj", relation_type="belongs-to")],
            )

    def test_strict_policy_rejects_any_task_to_project_relation(
        self, store: Storage, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MCP_MEMORY_STRICT_POLICY", "true")
        store.entities.create(
            "proj",
            [
                {"name": "task/a", "entityType": "task", "observations": ["x"]},
                {"name": "project/proj", "entityType": "project", "observations": ["y"]},
            ],
        )
        with pytest.raises(ValueError, match="Direct task -> project relations are forbidden"):
            store.relations.create(
                "proj",
                [Relation(source="task/a", target="project/proj", relation_type="relates-to")],
            )

    def test_delete_relation_blocked_when_it_would_orphan_non_structural_entities(self, store: Storage) -> None:
        store.entities.create(
            "proj",
            [
                {"name": "pattern/a", "entityType": "pattern", "observations": ["x"]},
                {"name": "knowledge/b", "entityType": "knowledge", "observations": ["y"]},
            ],
        )
        store.relations.create("proj", [Relation(source="pattern/a", target="knowledge/b", relation_type="relates-to")])
        with pytest.raises(ValueError, match="would orphan non-structural entities"):
            store.relations.delete("proj", "pattern/a", "knowledge/b", "relates-to")

    def test_delete_nonexistent_relation_raises(self, store: Storage) -> None:
        store.entities.create(
            "proj",
            [
                {"name": "a", "entityType": "task", "observations": ["x"]},
                {"name": "b", "entityType": "project", "observations": ["y"]},
            ],
        )
        with pytest.raises(ValueError, match="not found"):
            store.relations.delete("proj", "a", "b", "nonexistent")

    def test_self_referential_relation_rejected(self, store: Storage) -> None:
        store.entities.create("proj", [{"name": "a", "entityType": "pattern", "observations": ["x"]}])
        with pytest.raises(ValueError, match="Self-referential"):
            store.relations.create("proj", [Relation(source="a", target="a", relation_type="x")])


class TestDeleteEntity:
    def test_delete_entity(self, store: Storage) -> None:
        store.entities.create("proj", [{"name": "e1", "entityType": "task", "observations": ["x"]}])
        store.entities.delete("proj", "e1")
        with pytest.raises(ValueError, match="not found"):
            store.reads.get_entity("proj", "e1")

    def test_delete_entity_cascades_relations(self, store: Storage) -> None:
        store.entities.create(
            "proj",
            [
                {"name": "a", "entityType": "feature", "observations": ["x"]},
                {"name": "b", "entityType": "project", "observations": ["y"]},
            ],
        )
        store.relations.create("proj", [Relation(source="a", target="b", relation_type="belongs-to")])
        store.entities.delete("proj", "a")
        result = store.reads.get_entity_with_relations("proj", "b")
        assert len(result["relations"]) == 0

    def test_delete_entity_blocked_when_it_would_orphan_neighbor(self, store: Storage) -> None:
        store.entities.create(
            "proj",
            [
                {"name": "pattern/a", "entityType": "pattern", "observations": ["x"]},
                {"name": "knowledge/b", "entityType": "knowledge", "observations": ["y"]},
            ],
        )
        store.relations.create("proj", [Relation(source="pattern/a", target="knowledge/b", relation_type="relates-to")])
        with pytest.raises(ValueError, match="would orphan non-structural entity"):
            store.entities.delete("proj", "pattern/a")

    def test_strict_policy_rejects_project_scoped_user_preferences(
        self, store: Storage, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MCP_MEMORY_STRICT_POLICY", "true")
        with pytest.raises(ValueError, match="Project-scoped 'user-preferences' entities are forbidden"):
            store.entities.create(
                "proj",
                [
                    {
                        "name": "user-preferences/local",
                        "entityType": "user-preferences",
                        "observations": ["x"],
                    }
                ],
            )

    def test_delete_missing_entity_raises(self, store: Storage) -> None:
        with pytest.raises(ValueError, match="not found"):
            store.entities.delete("proj", "missing")

    def test_delete_blocked_by_incoming_relations(self, store: Storage) -> None:
        store.entities.create(
            "proj",
            [
                {"name": "a", "entityType": "feature", "observations": ["x"]},
                {"name": "b", "entityType": "project", "observations": ["y"]},
            ],
        )
        store.relations.create("proj", [Relation(source="a", target="b", relation_type="belongs-to")])
        with pytest.raises(ValueError, match=r"Cannot delete 'b'.*incoming relation.*from: a"):
            store.entities.delete("proj", "b")


class TestTombstoneVisibility:
    def test_soft_deleted_entity_hidden_from_get(self, store: Storage) -> None:
        store.entities.create("proj", [{"name": "e1", "entityType": "task", "observations": ["x"]}])
        soft_delete_store(store, "proj", "e1")
        with pytest.raises(ValueError, match="not found"):
            store.reads.get_entity("proj", "e1")

    def test_restore_brings_entity_back(self, store: Storage) -> None:
        store.entities.create("proj", [{"name": "e1", "entityType": "task", "observations": ["x"]}])
        soft_delete_store(store, "proj", "e1")
        store.entities.restore("proj", "e1")
        assert obs_contents(store.reads.get_entity("proj", "e1")) == ["x"]

    def test_restore_missing_entity_raises(self, store: Storage) -> None:
        with pytest.raises(ValueError, match="not found"):
            store.entities.restore("proj", "missing")

    def test_soft_deleted_entity_hidden_from_search(self, store: Storage) -> None:
        store.entities.create("proj", [{"name": "findme", "entityType": "task", "observations": ["needle"]}])
        soft_delete_store(store, "proj", "findme")
        assert store.reads.search("proj", "needle")["entities"] == []

    def test_soft_deleted_entity_hidden_from_read_graph(self, store: Storage) -> None:
        store.entities.create("proj", [{"name": "e1", "entityType": "task", "observations": ["x"]}])
        soft_delete_store(store, "proj", "e1")
        assert store.reads.recent("proj")["entities"] == []

    def test_soft_deleted_entity_edges_hidden(self, store: Storage) -> None:
        store.entities.create(
            "proj",
            [
                {"name": "a", "entityType": "task", "observations": ["x"]},
                {"name": "b", "entityType": "feature", "observations": ["y"]},
            ],
        )
        store.relations.create("proj", [Relation(source="a", target="b", relation_type="implements")])
        soft_delete_store(store, "proj", "a")
        assert store.reads.get_entity_with_relations("proj", "b")["relations"] == []

    def test_soft_deleted_hidden_from_exists_checks(self, store: Storage) -> None:
        store.entities.create("proj", [{"name": "e1", "entityType": "task", "observations": ["x"]}])
        soft_delete_store(store, "proj", "e1")
        assert store.entities.exists_in("e1", "proj") is False
        assert store.entities.exists_outside("e1", "other") is None

    def test_soft_deleted_hidden_from_get_entity(self, store: Storage) -> None:
        store.entities.create("proj", [{"name": "e1", "entityType": "task", "observations": ["x"]}])
        soft_delete_store(store, "proj", "e1")
        with pytest.raises(ValueError, match="not found"):
            store.reads.get_entity("proj", "e1")

    def test_create_replaces_soft_deleted_tombstone(self, store: Storage) -> None:
        store.entities.create("proj", [{"name": "e1", "entityType": "task", "observations": ["old"]}])
        soft_delete_store(store, "proj", "e1")
        store.entities.create("proj", [{"name": "e1", "entityType": "task", "observations": ["new"]}])
        assert obs_contents(store.reads.get_entity("proj", "e1")) == ["new"]

    def test_purge_removes_only_past_grace(self, store: Storage) -> None:
        store.entities.create("proj", [{"name": "e1", "entityType": "task", "observations": ["x"]}])
        soft_delete_store(store, "proj", "e1")
        project_id = get_or_create_project_id(store.connection, "proj")
        assert store.maintenance._purge_soft_deleted(grace_days=30) == 0
        entity_id = get_entity_id(store.connection, "e1", project_id, include_deleted=True)
        with store.connection.transaction():
            store.connection.write(
                "UPDATE entities SET deleted_at = datetime('now', '-40 days') WHERE id = ?",
                (entity_id,),
            )
        assert store.maintenance._purge_soft_deleted(grace_days=30) == 1
        assert get_entity_id(store.connection, "e1", project_id, include_deleted=True) is None


class TestMergeEntities:
    def test_observations_merged_and_deduped(self, store: Storage) -> None:
        store.entities.create(
            "proj",
            [
                {"name": "dup", "entityType": "task", "observations": ["shared", "only-source"]},
                {"name": "canon", "entityType": "task", "observations": ["shared", "only-target"]},
            ],
        )
        store.entities.merge("proj", "dup", "canon")
        obs = set(obs_contents(store.reads.get_entity("proj", "canon")))
        assert obs == {"shared", "only-source", "only-target"}

    def test_source_is_soft_deleted_not_gone(self, store: Storage) -> None:
        store.entities.create(
            "proj",
            [
                {"name": "dup", "entityType": "task", "observations": ["a"]},
                {"name": "canon", "entityType": "task", "observations": ["b"]},
            ],
        )
        store.entities.merge("proj", "dup", "canon")
        with pytest.raises(ValueError, match="not found"):
            store.reads.get_entity("proj", "dup")
        project_id = get_or_create_project_id(store.connection, "proj")
        assert get_entity_id(store.connection, "dup", project_id, include_deleted=True) is not None

    def test_outgoing_relations_repointed(self, store: Storage) -> None:
        store.entities.create(
            "proj",
            [
                {"name": "dup", "entityType": "task", "observations": ["a"]},
                {"name": "canon", "entityType": "task", "observations": ["b"]},
                {"name": "feat", "entityType": "feature", "observations": ["c"]},
            ],
        )
        store.relations.create("proj", [Relation(source="dup", target="feat", relation_type="implements")])
        store.entities.merge("proj", "dup", "canon")
        rels = store.reads.get_entity_with_relations("proj", "canon")["relations"]
        assert any(r.source == "canon" and r.target == "feat" for r in rels)

    def test_incoming_relations_repointed(self, store: Storage) -> None:
        store.entities.create(
            "proj",
            [
                {"name": "dup", "entityType": "feature", "observations": ["a"]},
                {"name": "canon", "entityType": "feature", "observations": ["b"]},
                {"name": "task1", "entityType": "task", "observations": ["c"]},
            ],
        )
        store.relations.create("proj", [Relation(source="task1", target="dup", relation_type="implements")])
        store.entities.merge("proj", "dup", "canon")
        rels = store.reads.get_entity_with_relations("proj", "canon")["relations"]
        assert any(r.source == "task1" and r.target == "canon" for r in rels)

    def test_relation_between_source_and_target_does_not_create_self_loop(self, store: Storage) -> None:
        store.entities.create(
            "proj",
            [
                {"name": "dup", "entityType": "task", "observations": ["a"]},
                {"name": "canon", "entityType": "feature", "observations": ["b"]},
            ],
        )
        store.relations.create("proj", [Relation(source="dup", target="canon", relation_type="implements")])
        store.entities.merge("proj", "dup", "canon")
        rels = store.reads.get_entity_with_relations("proj", "canon")["relations"]
        assert all(not (r.source == "canon" and r.target == "canon") for r in rels)

    def test_vote_score_carried_as_max(self, store: Storage) -> None:
        store.entities.create(
            "proj",
            [
                {"name": "dup", "entityType": "task", "observations": ["a"]},
                {"name": "canon", "entityType": "task", "observations": ["b"]},
            ],
        )
        store.entities.vote("proj", "dup", 1)
        store.entities.vote("proj", "dup", 1)
        store.entities.vote("proj", "canon", 1)
        store.entities.merge("proj", "dup", "canon")
        assert store.reads.get_entity("proj", "canon").vote_score == 2

    def test_target_searchable_on_merged_text(self, store: Storage) -> None:
        store.entities.create(
            "proj",
            [
                {"name": "dup", "entityType": "task", "observations": ["needle"]},
                {"name": "canon", "entityType": "task", "observations": ["hay"]},
            ],
        )
        store.entities.merge("proj", "dup", "canon")
        hits = store.reads.search("proj", "needle")["entities"]
        assert [e.name for e in hits] == ["canon"]

    def test_merge_into_self_raises(self, store: Storage) -> None:
        store.entities.create("proj", [{"name": "e1", "entityType": "task", "observations": ["a"]}])
        with pytest.raises(ValueError, match="itself"):
            store.entities.merge("proj", "e1", "e1")

    def test_missing_source_raises(self, store: Storage) -> None:
        store.entities.create("proj", [{"name": "canon", "entityType": "task", "observations": ["b"]}])
        with pytest.raises(ValueError, match="not found"):
            store.entities.merge("proj", "nope", "canon")

    def test_missing_target_raises(self, store: Storage) -> None:
        store.entities.create("proj", [{"name": "dup", "entityType": "task", "observations": ["a"]}])
        with pytest.raises(ValueError, match="not found"):
            store.entities.merge("proj", "dup", "nope")

    def test_purge_after_merge_with_incoming_edge_succeeds(self, store: Storage) -> None:
        store.entities.create(
            "proj",
            [
                {"name": "dup", "entityType": "feature", "observations": ["a"]},
                {"name": "canon", "entityType": "feature", "observations": ["b"]},
                {"name": "task1", "entityType": "task", "observations": ["c"]},
            ],
        )
        store.relations.create("proj", [Relation(source="task1", target="dup", relation_type="implements")])
        store.entities.merge("proj", "dup", "canon")
        project_id = get_or_create_project_id(store.connection, "proj")
        entity_id = get_entity_id(store.connection, "dup", project_id, include_deleted=True)
        with store.connection.transaction():
            store.connection.write(
                "UPDATE entities SET deleted_at = datetime('now', '-40 days') WHERE id = ?",
                (entity_id,),
            )
        assert store.maintenance._purge_soft_deleted(grace_days=30) == 1
        assert get_entity_id(store.connection, "dup", project_id, include_deleted=True) is None


class TestMergeObservations:
    @staticmethod
    def _hashes(store: Storage, project: str, name: str) -> dict[str, str]:
        return {o.content: o.content_hash for o in store.reads.get_entity(project, name).observations}

    def test_takes_max_vote_score(self, store: Storage) -> None:
        store.entities.create("proj", [{"name": "e1", "entityType": "task", "observations": ["source", "target"]}])
        store.observations.vote("proj", "e1", 1, content="source")
        hashes = self._hashes(store, "proj", "e1")
        store.observations.merge("proj", "e1", hashes["source"], hashes["target"])
        assert obs_votes(store.reads.get_entity("proj", "e1")) == [1]

    def test_source_is_removed(self, store: Storage) -> None:
        store.entities.create("proj", [{"name": "e1", "entityType": "task", "observations": ["source", "target"]}])
        hashes = self._hashes(store, "proj", "e1")
        assert store.observations.merge("proj", "e1", hashes["source"], hashes["target"]) == {"merged": 1}
        assert obs_contents(store.reads.get_entity("proj", "e1")) == ["target"]

    def test_unknown_source_hash_raises(self, store: Storage) -> None:
        store.entities.create("proj", [{"name": "e1", "entityType": "task", "observations": ["t"]}])
        target = self._hashes(store, "proj", "e1")["t"]
        with pytest.raises(ValueError, match="Source observation not found"):
            store.observations.merge("proj", "e1", "deadbeef", target)

    def test_unknown_target_hash_raises(self, store: Storage) -> None:
        store.entities.create("proj", [{"name": "e1", "entityType": "task", "observations": ["s"]}])
        source = self._hashes(store, "proj", "e1")["s"]
        with pytest.raises(ValueError, match="Target observation not found"):
            store.observations.merge("proj", "e1", source, "deadbeef")

    def test_source_equals_target_raises(self, store: Storage) -> None:
        store.entities.create("proj", [{"name": "e1", "entityType": "task", "observations": ["s"]}])
        source = self._hashes(store, "proj", "e1")["s"]
        with pytest.raises(ValueError, match="into itself"):
            store.observations.merge("proj", "e1", source, source)

    def test_missing_entity_raises(self, store: Storage) -> None:
        with pytest.raises(ValueError, match="not found"):
            store.observations.merge("proj", "ghost", "aaaa", "bbbb")


class TestGcDownvotedOrphans:
    @staticmethod
    def _downvote(store: Storage, project: str, name: str, times: int) -> None:
        for _ in range(times):
            store.entities.vote(project, name, -1)

    @staticmethod
    def _is_reaped(store: Storage, project: str, name: str) -> bool:
        project_id = get_or_create_project_id(store.connection, project)
        try:
            store.reads.get_entity(project, name)
        except ValueError:
            return get_entity_id(store.connection, name, project_id, include_deleted=True) is not None
        return False

    def test_floored_orphan_is_reaped(self, store: Storage) -> None:
        store.entities.create("proj", [{"name": "e1", "entityType": "task", "observations": ["x"]}])
        self._downvote(store, "proj", "e1", 10)
        assert store.maintenance._gc_downvoted_orphans() == 1
        assert self._is_reaped(store, "proj", "e1")

    def test_above_threshold_orphan_is_spared(self, store: Storage) -> None:
        store.entities.create("proj", [{"name": "e1", "entityType": "task", "observations": ["x"]}])
        self._downvote(store, "proj", "e1", 9)
        assert store.maintenance._gc_downvoted_orphans() == 0
        assert obs_contents(store.reads.get_entity("proj", "e1")) == ["x"]

    def test_at_floor_boundary_is_reaped(self, store: Storage) -> None:
        store.entities.create("proj", [{"name": "e1", "entityType": "task", "observations": ["x"]}])
        self._downvote(store, "proj", "e1", 10)
        assert store.reads.get_entity("proj", "e1").vote_score == -10
        assert store.maintenance._gc_downvoted_orphans() == 1

    def test_floored_entity_with_live_incoming_edge_is_spared(self, store: Storage) -> None:
        store.entities.create(
            "proj",
            [
                {"name": "pointer", "entityType": "task", "observations": ["p"]},
                {"name": "keeper", "entityType": "feature", "observations": ["k"]},
            ],
        )
        store.relations.create("proj", [Relation(source="pointer", target="keeper", relation_type="implements")])
        self._downvote(store, "proj", "keeper", 10)
        assert store.maintenance._gc_downvoted_orphans() == 0
        assert obs_contents(store.reads.get_entity("proj", "keeper")) == ["k"]

    def test_floored_orphan_whose_only_source_is_soft_deleted_is_reaped(self, store: Storage) -> None:
        store.entities.create(
            "proj",
            [
                {"name": "srcdel", "entityType": "task", "observations": ["s"]},
                {"name": "victim", "entityType": "feature", "observations": ["v"]},
            ],
        )
        store.relations.create("proj", [Relation(source="srcdel", target="victim", relation_type="implements")])
        soft_delete_store(store, "proj", "srcdel")
        self._downvote(store, "proj", "victim", 10)
        assert store.maintenance._gc_downvoted_orphans() == 1
        assert self._is_reaped(store, "proj", "victim")

    @pytest.mark.parametrize("entity_type", ["project", "user-preferences"])
    def test_exempt_type_is_spared(self, store: Storage, entity_type: str) -> None:
        name = f"{entity_type}/proj" if entity_type == "project" else f"{entity_type}/jdoe"
        store.entities.create("proj", [{"name": name, "entityType": entity_type, "observations": ["x"]}])
        self._downvote(store, "proj", name, 10)
        assert store.maintenance._gc_downvoted_orphans() == 0
        assert obs_contents(store.reads.get_entity("proj", name)) == ["x"]

    def test_already_soft_deleted_is_untouched(self, store: Storage) -> None:
        store.entities.create("proj", [{"name": "e1", "entityType": "task", "observations": ["x"]}])
        self._downvote(store, "proj", "e1", 10)
        soft_delete_store(store, "proj", "e1")
        project_id = get_or_create_project_id(store.connection, "proj")
        assert store.maintenance._gc_downvoted_orphans() == 0
        assert get_entity_id(store.connection, "e1", project_id, include_deleted=True) is not None

    def test_returns_count_reaped(self, store: Storage) -> None:
        store.entities.create(
            "proj",
            [
                {"name": "o1", "entityType": "task", "observations": ["a"]},
                {"name": "o2", "entityType": "task", "observations": ["b"]},
                {"name": "fresh", "entityType": "task", "observations": ["c"]},
            ],
        )
        self._downvote(store, "proj", "o1", 10)
        self._downvote(store, "proj", "o2", 10)
        self._downvote(store, "proj", "fresh", 9)
        assert store.maintenance._gc_downvoted_orphans() == 2
        assert obs_contents(store.reads.get_entity("proj", "fresh")) == ["c"]

    def test_gc_disabled_by_default_on_boot(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MCP_MEMORY_GC_ENABLED", raising=False)
        db_path = tmp_path / "boot.db"
        seed = open_writable(db_path)
        seed.entities.create("proj", [{"name": "e1", "entityType": "task", "observations": ["x"]}])
        self._downvote(seed, "proj", "e1", 10)
        seed.connection.close()
        reopened = open_writable(db_path)
        assert obs_contents(reopened.reads.get_entity("proj", "e1")) == ["x"]

    def test_gc_runs_on_boot_when_enabled(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MCP_MEMORY_GC_ENABLED", raising=False)
        db_path = tmp_path / "boot.db"
        seed = open_writable(db_path)
        seed.entities.create("proj", [{"name": "e1", "entityType": "task", "observations": ["x"]}])
        self._downvote(seed, "proj", "e1", 10)
        seed.connection.close()
        monkeypatch.setenv("MCP_MEMORY_GC_ENABLED", "true")
        reopened = open_writable(db_path)
        assert self._is_reaped(reopened, "proj", "e1")


class TestGetEntityWithRelations:
    def test_returns_related_entities(self, store: Storage) -> None:
        store.entities.create(
            "proj",
            [
                {"name": "a", "entityType": "feature", "observations": ["x"]},
                {"name": "b", "entityType": "project", "observations": ["y"]},
                {"name": "c", "entityType": "feature", "observations": ["z"]},
            ],
        )
        store.relations.create(
            "proj",
            [
                Relation(source="a", target="b", relation_type="belongs-to"),
                Relation(source="a", target="c", relation_type="implements"),
            ],
        )
        result = store.reads.get_entity_with_relations("proj", "a")
        assert isinstance(result["entity"], Entity)
        assert result["entity"].name == "a"
        related_names = {e.name for e in result["relatedEntities"] if isinstance(e, Entity)}
        assert related_names == {"b", "c"}


class TestGetEntityWithRelationsFilters:
    def test_filter_by_entity_type(self, store: Storage) -> None:
        store.entities.create(
            "proj",
            [
                {"name": "a", "entityType": "feature", "observations": ["x"]},
                {"name": "b", "entityType": "project", "observations": ["y"]},
                {"name": "c", "entityType": "feature", "observations": ["z"]},
            ],
        )
        store.relations.create(
            "proj",
            [
                Relation(source="a", target="b", relation_type="belongs-to"),
                Relation(source="a", target="c", relation_type="implements"),
            ],
        )
        result = store.reads.get_entity_with_relations("proj", "a", entity_type="project")
        related_names = {e.name for e in result["relatedEntities"] if isinstance(e, Entity)}
        assert related_names == {"b"}

    def test_filter_by_relation_type(self, store: Storage) -> None:
        store.entities.create(
            "proj",
            [
                {"name": "a", "entityType": "feature", "observations": ["x"]},
                {"name": "b", "entityType": "project", "observations": ["y"]},
                {"name": "c", "entityType": "feature", "observations": ["z"]},
            ],
        )
        store.relations.create(
            "proj",
            [
                Relation(source="a", target="b", relation_type="belongs-to"),
                Relation(source="a", target="c", relation_type="implements"),
            ],
        )
        result = store.reads.get_entity_with_relations("proj", "a", relation_type="implements")
        assert len(result["relations"]) == 1
        assert result["relations"][0].relation_type == "implements"


class TestParseDate:
    def test_mo_and_m_produce_different_results(self) -> None:
        month_ago = parse_date("3mo")
        minute_ago = parse_date("3m")
        assert month_ago != minute_ago


class TestSearchNodes:
    def test_fts_search(self, store: Storage) -> None:
        store.entities.create(
            "proj",
            [
                {
                    "name": "project/myrepo",
                    "entityType": "project",
                    "observations": ["uses Python"],
                },
                {
                    "name": "task/fix-bug",
                    "entityType": "task",
                    "observations": ["fix the login bug"],
                },
            ],
        )
        result = store.reads.search("proj", "Python")
        entities = result["entities"]
        assert len(entities) == 1
        assert isinstance(entities[0], Entity)
        assert entities[0].name == "project/myrepo"

    def test_fts_search_by_name(self, store: Storage) -> None:
        store.entities.create(
            "proj",
            [
                {"name": "project/myrepo", "entityType": "project", "observations": ["obs"]},
            ],
        )
        result = store.reads.search("proj", "myrepo")
        assert len(result["entities"]) == 1

    def test_multi_term_query_matches_any_term_by_default(self, store: Storage) -> None:
        store.entities.create(
            "proj",
            [
                {"name": "a", "entityType": "task", "observations": ["alpha only"]},
                {"name": "b", "entityType": "task", "observations": ["beta only"]},
                {"name": "c", "entityType": "task", "observations": ["unrelated"]},
            ],
        )
        result = store.reads.search("proj", "alpha beta")
        assert {e.name for e in result["entities"]} == {"a", "b"}

    def test_match_all_requires_every_term(self, store: Storage) -> None:
        store.entities.create(
            "proj",
            [
                {"name": "a", "entityType": "task", "observations": ["alpha only"]},
                {"name": "both", "entityType": "task", "observations": ["alpha and beta"]},
            ],
        )
        result = store.reads.search("proj", "alpha beta", match_all=True)
        assert {e.name for e in result["entities"]} == {"both"}

    def test_or_query_ranks_all_term_matches_first(self, store: Storage) -> None:
        store.entities.create(
            "proj",
            [
                {"name": "partial", "entityType": "task", "observations": ["alpha only"]},
                {"name": "full", "entityType": "task", "observations": ["alpha beta"]},
            ],
        )
        result = store.reads.search("proj", "alpha beta")
        assert [e.name for e in result["entities"]] == ["full", "partial"]

    def test_single_term_query_unaffected_by_match_all(self, store: Storage) -> None:
        store.entities.create(
            "proj",
            [{"name": "a", "entityType": "task", "observations": ["keyword"]}],
        )
        default = store.reads.search("proj", "keyword")
        strict = store.reads.search("proj", "keyword", match_all=True)
        assert [e.name for e in default["entities"]] == ["a"]
        assert [e.name for e in strict["entities"]] == ["a"]

    def test_fts_search_with_entity_type_filter(self, store: Storage) -> None:
        store.entities.create(
            "proj",
            [
                {"name": "a", "entityType": "task", "observations": ["shared keyword"]},
                {"name": "b", "entityType": "project", "observations": ["shared keyword"]},
            ],
        )
        result = store.reads.search("proj", "shared", entity_type="task")
        assert len(result["entities"]) == 1
        assert result["entities"][0].name == "a"

    def test_fts_search_with_status_filter(self, store: Storage) -> None:
        store.entities.create(
            "proj",
            [
                {"name": "a", "entityType": "task", "observations": ["x"], "status": "planned"},
                {"name": "b", "entityType": "task", "observations": ["x"], "status": "resolved"},
            ],
        )
        result = store.reads.search("proj", "x", status="planned")
        assert len(result["entities"]) == 1
        assert result["entities"][0].name == "a"

    def test_fts_search_with_status_list_filter(self, store: Storage) -> None:
        store.entities.create(
            "proj",
            [
                {"name": "a", "entityType": "task", "observations": ["x"], "status": "planned"},
                {
                    "name": "b",
                    "entityType": "task",
                    "observations": ["x"],
                    "status": "in-progress",
                },
                {"name": "c", "entityType": "task", "observations": ["x"], "status": "resolved"},
            ],
        )
        result = store.reads.search("proj", "x", status=["planned", "in-progress"])
        assert {e.name for e in result["entities"]} == {"a", "b"}

    def test_name_match_outranks_oversized_observation_match(self, store: Storage) -> None:
        store.entities.create(
            "proj",
            [
                {
                    "name": "widget-manager",
                    "entityType": "task",
                    "observations": ["manages the inventory system"],
                },
                {
                    "name": "unrelated-large",
                    "entityType": "task",
                    "observations": [f"widget note {i}" for i in range(35)],
                },
            ],
        )
        result = store.reads.search("proj", "widget")
        assert [e.name for e in result["entities"]] == ["widget-manager", "unrelated-large"]

    def test_fts_hyphenated_query(self, store: Storage) -> None:
        store.entities.create(
            "proj",
            [
                {
                    "name": "user-preferences/test",
                    "entityType": "user-preferences",
                    "observations": ["x"],
                }
            ],
        )
        result = store.reads.search("proj", "user-preferences")
        assert len(result["entities"]) == 1

    def test_empty_query_returns_empty(self, store: Storage) -> None:
        result = store.reads.search("proj", "   ")
        assert result["entities"] == []

    def test_search_respects_project_scope(self, store: Storage) -> None:
        store.entities.create("p1", [{"name": "e1", "entityType": "task", "observations": ["hello"]}])
        store.entities.create("p2", [{"name": "e2", "entityType": "task", "observations": ["hello"]}])
        result = store.reads.search("p1", "hello")
        assert len(result["entities"]) == 1
        assert result["entities"][0].name == "e1"

    def test_search_with_project_list_unions_named_projects(self, store: Storage) -> None:
        store.entities.create("p1", [{"name": "e1", "entityType": "task", "observations": ["hello"]}])
        store.entities.create("p2", [{"name": "e2", "entityType": "task", "observations": ["hello"]}])
        store.entities.create("p3", [{"name": "e3", "entityType": "task", "observations": ["hello"]}])
        result = store.reads.search(["p1", "p2"], "hello")
        assert {e.name for e in result["entities"]} == {"e1", "e2"}

    def test_recency_decay_favours_newer_entities(self, store: Storage) -> None:
        store.entities.create(
            "proj",
            [
                {"name": "old", "entityType": "task", "observations": ["keyword"]},
                {"name": "new", "entityType": "task", "observations": ["keyword"]},
            ],
        )
        with store.connection.transaction():
            store.connection.write(
                "UPDATE entities SET created_at = datetime('now', '-90 days'), "
                "updated_at = datetime('now', '-90 days') WHERE name = 'old'"
            )
        result = store.reads.search("proj", "keyword")
        assert len(result["entities"]) == 2
        assert result["entities"][0].name == "new"
        assert result["entities"][1].name == "old"

    def test_upvote_outranks_identical_unvoted_entity(self, store: Storage) -> None:
        store.entities.create(
            "proj",
            [
                {"name": "quiet", "entityType": "task", "observations": ["keyword"]},
                {"name": "useful", "entityType": "task", "observations": ["keyword"]},
            ],
        )
        store.entities.vote("proj", "useful", 1)
        result = store.reads.search("proj", "keyword")
        assert result["entities"][0].name == "useful"

    def test_start_date_filters_old_entities(self, store: Storage) -> None:
        store.entities.create(
            "proj",
            [
                {"name": "old", "entityType": "task", "observations": ["keyword"]},
                {"name": "new", "entityType": "task", "observations": ["keyword"]},
            ],
        )
        with store.connection.transaction():
            store.connection.write("UPDATE entities SET created_at = datetime('now', '-90 days') WHERE name = 'old'")
        result = store.reads.search("proj", "keyword", start="30d")
        assert len(result["entities"]) == 1
        assert result["entities"][0].name == "new"

    def test_end_date_filters_new_entities(self, store: Storage) -> None:
        store.entities.create(
            "proj",
            [
                {"name": "old", "entityType": "task", "observations": ["keyword"]},
                {"name": "new", "entityType": "task", "observations": ["keyword"]},
            ],
        )
        with store.connection.transaction():
            store.connection.write("UPDATE entities SET created_at = datetime('now', '-90 days') WHERE name = 'old'")
        result = store.reads.search("proj", "keyword", end="30d")
        assert len(result["entities"]) == 1
        assert result["entities"][0].name == "old"

    def test_iso_date_filtering(self, store: Storage) -> None:
        store.entities.create(
            "proj",
            [{"name": "e1", "entityType": "task", "observations": ["keyword"]}],
        )
        result = store.reads.search("proj", "keyword", start="2099-01-01")
        assert len(result["entities"]) == 0

    def test_same_day_range_includes_just_created_entity(self, store: Storage) -> None:
        store.entities.create(
            "proj",
            [{"name": "e1", "entityType": "task", "observations": ["keyword"]}],
        )
        result = store.reads.search("proj", "keyword", start="1h")
        assert len(result["entities"]) == 1
        assert result["entities"][0].name == "e1"

    def test_hour_granularity_excludes_and_includes_correctly(self, store: Storage) -> None:
        store.entities.create(
            "proj",
            [{"name": "e1", "entityType": "task", "observations": ["keyword"]}],
        )
        with store.connection.transaction():
            store.connection.write("UPDATE entities SET created_at = datetime('now', '-2 hours') WHERE name = 'e1'")
        assert store.reads.search("proj", "keyword", start="1h")["entities"] == []
        result = store.reads.search("proj", "keyword", end="1h")
        assert len(result["entities"]) == 1
        assert result["entities"][0].name == "e1"

    def test_minute_granularity_excludes_and_includes_correctly(self, store: Storage) -> None:
        store.entities.create(
            "proj",
            [{"name": "e1", "entityType": "task", "observations": ["keyword"]}],
        )
        with store.connection.transaction():
            store.connection.write("UPDATE entities SET created_at = datetime('now', '-90 minutes') WHERE name = 'e1'")
        assert store.reads.search("proj", "keyword", start="60m")["entities"] == []
        result = store.reads.search("proj", "keyword", end="60m")
        assert len(result["entities"]) == 1
        assert result["entities"][0].name == "e1"

    def test_cross_project_search(self, store: Storage) -> None:
        store.entities.create(
            "p1",
            [{"name": "t1", "entityType": "task", "observations": ["hello"]}],
        )
        store.entities.create(
            "p2",
            [{"name": "t2", "entityType": "task", "observations": ["hello"]}],
        )
        result = store.reads.search(None, "hello")
        names = {e.name for e in result["entities"]}
        assert names == {"t1", "t2"}

    def test_cross_project_search_includes_project_name(self, store: Storage) -> None:
        store.entities.create(
            "alpha",
            [{"name": "e1", "entityType": "task", "observations": ["keyword"]}],
        )
        result = store.reads.search(None, "keyword")
        assert len(result["entities"]) == 1
        assert result["entities"][0].project_name == "alpha"

    def test_cross_project_search_with_status_filter(self, store: Storage) -> None:
        store.entities.create(
            "p1",
            [
                {
                    "name": "a",
                    "entityType": "task",
                    "observations": ["x"],
                    "status": "in-progress",
                },
            ],
        )
        store.entities.create(
            "p2",
            [
                {
                    "name": "b",
                    "entityType": "task",
                    "observations": ["x"],
                    "status": "resolved",
                },
            ],
        )
        result = store.reads.search(None, "x", status="in-progress")
        assert len(result["entities"]) == 1
        assert result["entities"][0].name == "a"

    def test_cross_project_search_returns_relations(self, store: Storage) -> None:
        store.entities.create(
            "p1",
            [
                {"name": "t1", "entityType": "task", "observations": ["hello"]},
                {"name": "f1", "entityType": "feature", "observations": ["other"]},
            ],
        )
        store.relations.create(
            "p1",
            [Relation(source="t1", target="f1", relation_type="implements")],
        )
        result = store.reads.search(None, "hello")
        assert len(result["entities"]) == 1
        assert len(result["relations"]) == 1
        assert result["relations"][0].source == "t1"

    def test_scoped_search_includes_project_name(self, store: Storage) -> None:
        store.entities.create(
            "proj",
            [{"name": "e1", "entityType": "task", "observations": ["keyword"]}],
        )
        result = store.reads.search("proj", "keyword")
        assert result["entities"][0].project_name == "proj"


class TestReadGraph:
    def test_returns_recent_entities(self, store: Storage) -> None:
        for i in range(15):
            store.entities.create("proj", [{"name": f"e{i}", "entityType": "task", "observations": [f"obs{i}"]}])
        result = store.reads.recent("proj")
        assert len(result["entities"]) == 10

    def test_filter_by_status(self, store: Storage) -> None:
        store.entities.create(
            "proj",
            [
                {"name": "a", "entityType": "task", "observations": ["x"], "status": "planned"},
                {"name": "b", "entityType": "task", "observations": ["y"], "status": "resolved"},
            ],
        )
        result = store.reads.recent("proj", status="planned")
        assert len(result["entities"]) == 1
        assert result["entities"][0].name == "a"

    def test_includes_relations(self, store: Storage) -> None:
        store.entities.create(
            "proj",
            [
                {"name": "a", "entityType": "feature", "observations": ["x"]},
                {"name": "b", "entityType": "project", "observations": ["y"]},
            ],
        )
        store.relations.create("proj", [Relation(source="a", target="b", relation_type="belongs-to")])
        result = store.reads.recent("proj")
        assert len(result["relations"]) == 1


class TestUpdatedAt:
    def test_updated_at_set_on_creation(self, store: Storage) -> None:
        store.entities.create("proj", [{"name": "e1", "entityType": "task", "observations": ["x"]}])
        entity = store.reads.get_entity("proj", "e1")
        assert entity.updated_at is not None
        assert entity.updated_at == entity.created_at

    def test_updated_at_changes_on_add_observations(self, store: Storage) -> None:
        store.entities.create("proj", [{"name": "e1", "entityType": "task", "observations": ["x"]}])
        with store.connection.transaction():
            store.connection.write("UPDATE entities SET updated_at = datetime('now', '-1 day') WHERE name = 'e1'")
        backdated = store.reads.get_entity("proj", "e1").updated_at
        store.observations.add("proj", "e1", ["new obs"])
        assert store.reads.get_entity("proj", "e1").updated_at != backdated

    def test_updated_at_changes_on_status_change(self, store: Storage) -> None:
        store.entities.create("proj", [{"name": "e1", "entityType": "task", "observations": ["x"]}])
        with store.connection.transaction():
            store.connection.write("UPDATE entities SET updated_at = datetime('now', '-1 day') WHERE name = 'e1'")
        before = store.reads.get_entity("proj", "e1").updated_at
        store.entities.set_status("proj", "e1", "resolved")
        assert store.reads.get_entity("proj", "e1").updated_at != before


class TestVoteEntity:
    def test_upvote_increments_score(self, store: Storage) -> None:
        store.entities.create("proj", [{"name": "e1", "entityType": "task", "observations": ["x"]}])
        assert store.entities.vote("proj", "e1", 1) == 1

    def test_downvote_decrements_score(self, store: Storage) -> None:
        store.entities.create("proj", [{"name": "e1", "entityType": "task", "observations": ["x"]}])
        assert store.entities.vote("proj", "e1", -1) == -1

    @pytest.mark.parametrize("vote", [MAX_VOTE_MAGNITUDE, -MAX_VOTE_MAGNITUDE])
    def test_vote_within_magnitude_range_succeeds(self, store: Storage, vote: int) -> None:
        store.entities.create("proj", [{"name": "e1", "entityType": "task", "observations": ["x"]}])
        assert store.entities.vote("proj", "e1", vote) == vote

    def test_votes_accumulate(self, store: Storage) -> None:
        store.entities.create("proj", [{"name": "e1", "entityType": "task", "observations": ["x"]}])
        store.entities.vote("proj", "e1", 1)
        store.entities.vote("proj", "e1", 1)
        store.entities.vote("proj", "e1", -1)
        assert store.reads.get_entity("proj", "e1").vote_score == 1

    @pytest.mark.parametrize("vote", [0, MAX_VOTE_MAGNITUDE + 1, -(MAX_VOTE_MAGNITUDE + 1)])
    def test_invalid_vote_raises(self, store: Storage, vote: int) -> None:
        store.entities.create("proj", [{"name": "e1", "entityType": "task", "observations": ["x"]}])
        with pytest.raises(ValueError, match="Invalid vote"):
            store.entities.vote("proj", "e1", vote)

    def test_missing_entity_raises(self, store: Storage) -> None:
        with pytest.raises(ValueError, match="not found"):
            store.entities.vote("proj", "nope", 1)

    def test_vote_does_not_change_updated_at(self, store: Storage) -> None:
        store.entities.create("proj", [{"name": "e1", "entityType": "task", "observations": ["x"]}])
        with store.connection.transaction():
            store.connection.write("UPDATE entities SET updated_at = datetime('now', '-1 day') WHERE name = 'e1'")
        before = store.reads.get_entity("proj", "e1").updated_at
        store.entities.vote("proj", "e1", 1)
        assert store.reads.get_entity("proj", "e1").updated_at == before


class TestVoteObservation:
    def test_upvote_returns_new_score(self, store: Storage) -> None:
        store.entities.create("proj", [{"name": "e1", "entityType": "task", "observations": ["x"]}])
        assert store.observations.vote("proj", "e1", 1, content="x") == 1

    def test_votes_accumulate(self, store: Storage) -> None:
        store.entities.create("proj", [{"name": "e1", "entityType": "task", "observations": ["x"]}])
        store.observations.vote("proj", "e1", 1, content="x")
        store.observations.vote("proj", "e1", 1, content="x")
        assert store.observations.vote("proj", "e1", -1, content="x") == 1

    def test_upvote_by_content_hash_matches_by_content(self, store: Storage) -> None:
        store.entities.create("proj", [{"name": "e1", "entityType": "task", "observations": ["x"]}])
        content_hash = store.reads.get_entity("proj", "e1").observations[0].content_hash
        assert store.observations.vote("proj", "e1", 1, content_hash=content_hash) == 1
        assert store.reads.get_entity("proj", "e1").observations[0].vote_score == 1

    @pytest.mark.parametrize("vote", [MAX_VOTE_MAGNITUDE, -MAX_VOTE_MAGNITUDE])
    def test_vote_within_magnitude_range_succeeds(self, store: Storage, vote: int) -> None:
        store.entities.create("proj", [{"name": "e1", "entityType": "task", "observations": ["x"]}])
        assert store.observations.vote("proj", "e1", vote, content="x") == vote

    def test_requires_exactly_one_addressing(self, store: Storage) -> None:
        store.entities.create("proj", [{"name": "e1", "entityType": "task", "observations": ["x"]}])
        with pytest.raises(ValueError, match="exactly one"):
            store.observations.vote("proj", "e1", 1)
        with pytest.raises(ValueError, match="exactly one"):
            store.observations.vote("proj", "e1", 1, content="x", content_hash=hash_observation("x"))

    @pytest.mark.parametrize("vote", [0, MAX_VOTE_MAGNITUDE + 1, -(MAX_VOTE_MAGNITUDE + 1)])
    def test_invalid_vote_raises(self, store: Storage, vote: int) -> None:
        store.entities.create("proj", [{"name": "e1", "entityType": "task", "observations": ["x"]}])
        with pytest.raises(ValueError, match="Invalid vote"):
            store.observations.vote("proj", "e1", vote, content="x")

    def test_missing_entity_raises(self, store: Storage) -> None:
        with pytest.raises(ValueError, match="not found"):
            store.observations.vote("proj", "nope", 1, content="x")

    def test_missing_observation_raises(self, store: Storage) -> None:
        store.entities.create("proj", [{"name": "e1", "entityType": "task", "observations": ["x"]}])
        with pytest.raises(ValueError, match="not found"):
            store.observations.vote("proj", "e1", 1, content="no-such-obs")

    def test_unknown_hash_raises(self, store: Storage) -> None:
        store.entities.create("proj", [{"name": "e1", "entityType": "task", "observations": ["x"]}])
        with pytest.raises(ValueError, match="not found"):
            store.observations.vote("proj", "e1", 1, content_hash="deadbeef")

    def test_vote_does_not_change_updated_at(self, store: Storage) -> None:
        store.entities.create("proj", [{"name": "e1", "entityType": "task", "observations": ["x"]}])
        with store.connection.transaction():
            store.connection.write("UPDATE entities SET updated_at = datetime('now', '-1 day') WHERE name = 'e1'")
        before = store.reads.get_entity("proj", "e1").updated_at
        store.observations.vote("proj", "e1", 1, content="x")
        assert store.reads.get_entity("proj", "e1").updated_at == before

    def test_duplicate_content_observations_move_together(self, store: Storage) -> None:
        store.entities.create("proj", [{"name": "e1", "entityType": "task", "observations": ["dup", "dup"]}])
        store.observations.vote("proj", "e1", 1, content="dup")
        scores = [
            row[0] for row in store.connection.query_all("SELECT vote_score FROM observations WHERE content = 'dup'")
        ]
        assert scores == [1, 1]

    def test_duplicate_content_share_hash_and_move_together(self, store: Storage) -> None:
        store.entities.create("proj", [{"name": "e1", "entityType": "task", "observations": ["dup", "dup"]}])
        store.observations.vote("proj", "e1", 1, content_hash=hash_observation("dup"))
        scores = [
            row[0] for row in store.connection.query_all("SELECT vote_score FROM observations WHERE content = 'dup'")
        ]
        assert scores == [1, 1]

    def test_voting_observation_does_not_change_entity_ranking(self, store: Storage) -> None:
        store.entities.create(
            "proj",
            [
                {"name": "task/a", "entityType": "task", "observations": ["deploy"]},
                {"name": "task/b", "entityType": "task", "observations": ["deploy"]},
            ],
        )
        before = [e.name for e in store.reads.search("proj", "deploy")["entities"]]
        store.observations.vote("proj", "task/a", 1, content="deploy")
        after = [e.name for e in store.reads.search("proj", "deploy")["entities"]]
        assert after == before


class TestCompactMode:
    def test_read_graph_compact_omits_observations(self, store: Storage) -> None:
        store.entities.create("proj", [{"name": "e1", "entityType": "task", "observations": ["obs1", "obs2"]}])
        result = store.reads.recent("proj", compact=True)
        assert len(result["entities"]) == 1
        assert result["entities"][0].name == "e1"
        assert result["entities"][0].observations == []

    def test_read_graph_non_compact_includes_observations(self, store: Storage) -> None:
        store.entities.create("proj", [{"name": "e1", "entityType": "task", "observations": ["obs1", "obs2"]}])
        result = store.reads.recent("proj", compact=False)
        assert obs_contents(result["entities"][0]) == ["obs1", "obs2"]

    def test_search_nodes_compact_omits_observations(self, store: Storage) -> None:
        store.entities.create("proj", [{"name": "task/foo", "entityType": "task", "observations": ["some detail"]}])
        result = store.reads.search("proj", "foo", compact=True)
        assert len(result["entities"]) == 1
        assert result["entities"][0].name == "task/foo"
        assert result["entities"][0].observations == []

    def test_search_nodes_compact_preserves_metadata(self, store: Storage) -> None:
        store.entities.create(
            "proj",
            [
                {
                    "name": "task/bar",
                    "entityType": "task",
                    "observations": ["x"],
                    "status": "planned",
                }
            ],
        )
        result = store.reads.search("proj", "bar", compact=True)
        entity = result["entities"][0]
        assert entity.entity_type == "task"
        assert entity.status == "planned"
        assert entity.created_at is not None

    def test_cross_project_search_compact(self, store: Storage) -> None:
        store.entities.create("alpha", [{"name": "task/a1", "entityType": "task", "observations": ["alpha detail"]}])
        store.entities.create("beta", [{"name": "task/b1", "entityType": "task", "observations": ["beta detail"]}])
        result = store.reads.search(None, "task", compact=True)
        for entity in result["entities"]:
            assert entity.observations == []
            assert entity.name in {"task/a1", "task/b1"}

    def test_compact_still_returns_relations(self, store: Storage) -> None:
        store.entities.create(
            "proj",
            [
                {"name": "feature/x", "entityType": "feature", "observations": ["o"]},
                {"name": "project/p", "entityType": "project", "observations": ["o"]},
            ],
        )
        store.relations.create("proj", [Relation(source="feature/x", target="project/p", relation_type="belongs-to")])
        result = store.reads.recent("proj", compact=True)
        assert len(result["relations"]) == 1
        assert result["relations"][0].relation_type == "belongs-to"


def _obs(*contents: str) -> list[Observation]:
    return [Observation(content=c, content_hash=hash_observation(c), vote_score=0) for c in contents]


class TestBudgetObservations:
    def test_negative_returns_all_unchanged(self) -> None:
        observations = _obs("aaa", "bbb", "ccc")
        for max_chars in (-1, -100):
            result = budget_observations(observations, max_chars)
            assert obs_contents(Entity(name="e", entity_type="task", observations=result)) == [
                "aaa",
                "bbb",
                "ccc",
            ]
            assert result is observations

    def test_empty_returns_unchanged(self) -> None:
        assert budget_observations([], 100) == []

    def test_zero_keeps_only_first_with_sentinel(self) -> None:
        result = budget_observations(_obs("aaa", "bbb", "ccc"), 0)
        assert [o.content for o in result] == ["aaa"]

    def test_zero_single_observation_no_sentinel(self) -> None:
        result = budget_observations(_obs("aaa"), 0)
        assert [o.content for o in result] == ["aaa"]

    def test_prefix_kept_and_sentinel_appended(self) -> None:
        result = budget_observations(_obs("aaa", "bbb", "ccc", "ddd"), 6)
        assert [o.content for o in result] == ["aaa", "bbb"]

    def test_budget_fitting_everything_keeps_all(self) -> None:
        result = budget_observations(_obs("aaa", "bbb", "ccc"), 1000)
        assert [o.content for o in result] == ["aaa", "bbb", "ccc"]

    def test_first_observation_over_budget_still_kept(self) -> None:
        result = budget_observations(_obs("aaaaaaaaaa", "bbb"), 3)
        assert [o.content for o in result] == ["aaaaaaaaaa"]

    def test_every_kept_observation_is_addressable(self) -> None:
        kept = budget_observations(_obs("aaa", "bbb"), 0)
        assert [o.content for o in kept] == ["aaa"]
        assert all(o.content_hash for o in kept)


def _fetch_entity_row(store: Storage, name: str) -> tuple[sqlite3.Row, int]:
    row = store.connection.query_one(
        "SELECT e.id, e.name, et.name AS entity_type, e.status, e.created_at, e.updated_at, "
        "e.vote_score FROM entities e JOIN entity_types et ON e.entity_type_id = et.id "
        "WHERE e.name = ? AND e.deleted_at IS NULL",
        (name,),
    )
    return row, row["id"]


class TestBuildEntityBudget:
    def test_default_budget_applied_via_none(self, store: Storage) -> None:
        store.entities.create("proj", [{"name": "e1", "entityType": "task", "observations": ["obs1", "obs2"]}])
        row, entity_id = _fetch_entity_row(store, "e1")
        entity = store.reads._hydrate_entity(row, entity_id, max_observation_chars=None)
        assert obs_contents(entity) == ["obs1", "obs2"]

    def test_zero_budget_keeps_first_and_counts_omitted(self, store: Storage) -> None:
        store.entities.create("proj", [{"name": "e1", "entityType": "task", "observations": ["obs1", "obs2", "obs3"]}])
        row, entity_id = _fetch_entity_row(store, "e1")
        entity = store.reads._hydrate_entity(row, entity_id, max_observation_chars=0)
        assert obs_contents(entity) == ["obs1"]
        assert entity.observations_omitted == 2

    def test_negative_budget_returns_all(self, store: Storage) -> None:
        store.entities.create("proj", [{"name": "e1", "entityType": "task", "observations": ["obs1", "obs2"]}])
        row, entity_id = _fetch_entity_row(store, "e1")
        entity = store.reads._hydrate_entity(row, entity_id, max_observation_chars=-1)
        assert obs_contents(entity) == ["obs1", "obs2"]

    def test_compact_wins_over_budget(self, store: Storage) -> None:
        store.entities.create("proj", [{"name": "e1", "entityType": "task", "observations": ["obs1", "obs2"]}])
        row, entity_id = _fetch_entity_row(store, "e1")
        entity = store.reads._hydrate_entity(row, entity_id, compact=True, max_observation_chars=100)
        assert entity.observations == []


class TestSearchNodesBudget:
    def test_small_budget_trims_to_budget(self, store: Storage) -> None:
        store.entities.create(
            "proj",
            [{"name": "task/foo", "entityType": "task", "observations": ["aaa", "bbb", "ccc"]}],
        )
        result = store.reads.search("proj", "foo", max_observation_chars=6)
        contents = obs_contents(result["entities"][0])
        assert contents == ["aaa", "bbb"]

    def test_negative_budget_returns_all(self, store: Storage) -> None:
        store.entities.create(
            "proj",
            [{"name": "task/foo", "entityType": "task", "observations": ["aaa", "bbb", "ccc"]}],
        )
        result = store.reads.search("proj", "foo", max_observation_chars=-1)
        assert obs_contents(result["entities"][0]) == ["aaa", "bbb", "ccc"]

    def test_zero_budget_keeps_only_top(self, store: Storage) -> None:
        store.entities.create(
            "proj",
            [{"name": "task/foo", "entityType": "task", "observations": ["aaa", "bbb", "ccc"]}],
        )
        result = store.reads.search("proj", "foo", max_observation_chars=0)
        assert obs_contents(result["entities"][0]) == ["aaa"]

    def test_default_none_uses_config(self, store: Storage) -> None:
        store.entities.create("proj", [{"name": "task/foo", "entityType": "task", "observations": ["aaa", "bbb"]}])
        result = store.reads.search("proj", "foo")
        assert obs_contents(result["entities"][0]) == ["aaa", "bbb"]


class TestReadGraphBudget:
    def test_small_budget_trims_to_budget(self, store: Storage) -> None:
        store.entities.create("proj", [{"name": "e1", "entityType": "task", "observations": ["aaa", "bbb", "ccc"]}])
        result = store.reads.recent("proj", max_observation_chars=6)
        assert obs_contents(result["entities"][0]) == ["aaa", "bbb"]

    def test_negative_budget_returns_all(self, store: Storage) -> None:
        store.entities.create("proj", [{"name": "e1", "entityType": "task", "observations": ["aaa", "bbb", "ccc"]}])
        result = store.reads.recent("proj", max_observation_chars=-1)
        assert obs_contents(result["entities"][0]) == ["aaa", "bbb", "ccc"]

    def test_zero_budget_keeps_only_top(self, store: Storage) -> None:
        store.entities.create("proj", [{"name": "e1", "entityType": "task", "observations": ["aaa", "bbb", "ccc"]}])
        result = store.reads.recent("proj", max_observation_chars=0)
        assert obs_contents(result["entities"][0]) == ["aaa"]


def _seed_primary_and_related(store: Storage) -> None:
    store.entities.create(
        "proj",
        [
            {"name": "a", "entityType": "feature", "observations": ["aaa", "bbb", "ccc"]},
            {"name": "b", "entityType": "project", "observations": ["xxx", "yyy", "zzz"]},
        ],
    )
    store.relations.create("proj", [Relation(source="a", target="b", relation_type="belongs-to")])


def _related(result: GraphResult) -> Entity:
    return next(e for e in result["relatedEntities"] if isinstance(e, Entity))


class TestGetEntityWithRelationsBudget:
    def test_compact_empties_primary_and_related(self, store: Storage) -> None:
        _seed_primary_and_related(store)
        result = store.reads.get_entity_with_relations("proj", "a", compact=True)
        assert result["entity"].observations == []
        assert _related(result).observations == []

    def test_small_budget_trims_primary_and_related(self, store: Storage) -> None:
        _seed_primary_and_related(store)
        result = store.reads.get_entity_with_relations("proj", "a", max_observation_chars=6)
        assert obs_contents(result["entity"]) == ["aaa", "bbb"]
        assert obs_contents(_related(result)) == ["xxx", "yyy"]

    def test_zero_budget_keeps_only_top_on_primary_and_related(self, store: Storage) -> None:
        _seed_primary_and_related(store)
        result = store.reads.get_entity_with_relations("proj", "a", max_observation_chars=0)
        assert obs_contents(result["entity"]) == ["aaa"]
        assert obs_contents(_related(result)) == ["xxx"]

    def test_default_none_returns_all(self, store: Storage) -> None:
        _seed_primary_and_related(store)
        result = store.reads.get_entity_with_relations("proj", "a")
        assert obs_contents(result["entity"]) == ["aaa", "bbb", "ccc"]
        assert obs_contents(_related(result)) == ["xxx", "yyy", "zzz"]

    def test_negative_budget_returns_all(self, store: Storage) -> None:
        _seed_primary_and_related(store)
        result = store.reads.get_entity_with_relations("proj", "a", max_observation_chars=-1)
        assert obs_contents(result["entity"]) == ["aaa", "bbb", "ccc"]
        assert obs_contents(_related(result)) == ["xxx", "yyy", "zzz"]


class TestGetEntityWithRelationsFilteredBudget:
    def test_compact_empties_primary_and_related(self, store: Storage) -> None:
        _seed_primary_and_related(store)
        result = store.reads.get_entity_with_relations("proj", "a", entity_type="project", compact=True)
        assert result["entity"].observations == []
        assert _related(result).observations == []

    def test_small_budget_trims_primary_and_related(self, store: Storage) -> None:
        _seed_primary_and_related(store)
        result = store.reads.get_entity_with_relations("proj", "a", entity_type="project", max_observation_chars=6)
        assert obs_contents(result["entity"]) == ["aaa", "bbb"]
        assert obs_contents(_related(result)) == ["xxx", "yyy"]


class TestConnectReadonly:
    def test_stores_path_on_writable_instance(self, store: Storage, tmp_path: Path) -> None:
        assert store.connection.path == tmp_path / "test.db"

    def test_reads_existing_data(self, store: Storage, tmp_path: Path) -> None:
        store.entities.create("proj", [{"name": "task/a", "entityType": "task", "observations": ["keyword"]}])
        store.connection.close()

        readonly = open_readonly(tmp_path / "test.db")
        try:
            assert readonly.reads.get_entity("proj", "task/a").name == "task/a"
            assert readonly.reads.search("proj", "keyword")["entities"][0].name == "task/a"
        finally:
            readonly.connection.close()

    def test_write_attempt_raises(self, store: Storage, tmp_path: Path) -> None:
        store.connection.close()

        readonly = open_readonly(tmp_path / "test.db")
        try:
            with pytest.raises(sqlite3.OperationalError):
                readonly.entities.create("proj", [{"name": "task/a", "entityType": "task", "observations": ["x"]}])
        finally:
            readonly.connection.close()

    def test_does_not_run_migrations_or_maintenance(self, store: Storage, tmp_path: Path) -> None:
        store.connection.close()
        mtime_before = (tmp_path / "test.db").stat().st_mtime_ns

        readonly = open_readonly(tmp_path / "test.db")
        readonly.connection.close()

        assert (tmp_path / "test.db").stat().st_mtime_ns == mtime_before


def _committed_values(path: Path) -> list[int]:
    """Read the scratch table through a separate read-only connection, seeing only committed rows."""
    probe = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return [row[0] for row in probe.execute("SELECT v FROM t ORDER BY v")]
    finally:
        probe.close()


@pytest.fixture
def conn(tmp_path: Path) -> Connection:
    """Open a writable connection holding one committed scratch table."""
    connection = Connection.open_writable(tmp_path / "conn.db")
    connection.write("CREATE TABLE t (v INTEGER)")
    with connection.transaction():
        pass
    return connection


class TestConnection:
    def test_query_one_returns_a_row_or_none(self, conn: Connection) -> None:
        conn.write("INSERT INTO t VALUES (1)")
        row = conn.query_one("SELECT v FROM t WHERE v = ?", (1,))
        assert row is not None
        assert row["v"] == 1
        assert conn.query_one("SELECT v FROM t WHERE v = ?", (99,)) is None

    def test_write_many_writes_every_row_and_query_all_returns_them(self, conn: Connection) -> None:
        assert conn.write_many("INSERT INTO t VALUES (?)", [(1,), (2,), (3,)]).rowcount == 3
        assert [row["v"] for row in conn.query_all("SELECT v FROM t ORDER BY v")] == [1, 2, 3]
        assert conn.query_all("SELECT v FROM t WHERE v > ?", (99,)) == []

    def test_write_returns_a_cursor_reporting_the_row_count_and_last_id(self, conn: Connection) -> None:
        assert conn.write("INSERT INTO t VALUES (?)", (1,)).lastrowid == 1
        assert conn.write("DELETE FROM t WHERE v = ?", (1,)).rowcount == 1
        assert conn.write("DELETE FROM t WHERE v = ?", (99,)).rowcount == 0

    def test_total_changes_counts_rows_changed_on_the_connection(self, conn: Connection) -> None:
        before = conn.total_changes
        conn.write_many("INSERT INTO t VALUES (?)", [(1,), (2,)])
        assert conn.total_changes - before == 2

    def test_a_completed_transaction_is_visible_to_another_connection(self, conn: Connection) -> None:
        with conn.transaction():
            conn.write("INSERT INTO t VALUES (1)")
        assert _committed_values(conn.path) == [1]

    def test_a_write_outside_a_transaction_is_not_yet_committed(self, conn: Connection) -> None:
        conn.write("INSERT INTO t VALUES (1)")
        assert [row["v"] for row in conn.query_all("SELECT v FROM t")] == [1]
        assert _committed_values(conn.path) == []

    def test_an_exception_rolls_back_the_transaction_and_propagates(self, conn: Connection) -> None:
        def write_then_fail() -> None:
            with conn.transaction():
                conn.write("INSERT INTO t VALUES (1)")
                raise ValueError("boom")

        with pytest.raises(ValueError, match="boom"):
            write_then_fail()
        assert conn.query_all("SELECT v FROM t") == []
        assert _committed_values(conn.path) == []

    def test_a_base_exception_also_rolls_back_and_propagates(self, conn: Connection) -> None:
        def write_then_interrupt() -> None:
            with conn.transaction():
                conn.write("INSERT INTO t VALUES (1)")
                raise KeyboardInterrupt

        with pytest.raises(KeyboardInterrupt):
            write_then_interrupt()
        assert conn.query_all("SELECT v FROM t") == []

    def test_commit_false_discards_the_writes_rather_than_leaving_them_pending(self, conn: Connection) -> None:
        with conn.transaction(commit=False):
            conn.write("INSERT INTO t VALUES (1)")
        assert conn.query_all("SELECT v FROM t") == []
        assert _committed_values(conn.path) == []

    def test_a_nested_transaction_commits_only_at_the_outermost_exit(self, conn: Connection) -> None:
        with conn.transaction():
            conn.write("INSERT INTO t VALUES (1)")
            with conn.transaction():
                conn.write("INSERT INTO t VALUES (2)")
            assert _committed_values(conn.path) == []
        assert _committed_values(conn.path) == [1, 2]

    def test_an_exception_after_a_nested_block_rolls_back_the_inner_writes(self, conn: Connection) -> None:
        def write_nested_then_fail() -> None:
            with conn.transaction():
                conn.write("INSERT INTO t VALUES (1)")
                with conn.transaction():
                    conn.write("INSERT INTO t VALUES (2)")
                raise ValueError("boom")

        with pytest.raises(ValueError, match="boom"):
            write_nested_then_fail()
        assert conn.query_all("SELECT v FROM t") == []

    def test_a_later_transaction_still_commits_after_a_failed_one(self, conn: Connection) -> None:
        def fail_immediately() -> None:
            with conn.transaction():
                raise ValueError("boom")

        with pytest.raises(ValueError, match="boom"):
            fail_immediately()
        with conn.transaction():
            conn.write("INSERT INTO t VALUES (1)")
        assert _committed_values(conn.path) == [1]

    def test_a_read_only_connection_rejects_a_write(self, conn: Connection) -> None:
        with conn.transaction():
            conn.write("INSERT INTO t VALUES (1)")
        readonly = Connection.open_readonly(conn.path)
        try:
            assert [row["v"] for row in readonly.query_all("SELECT v FROM t")] == [1]
            with pytest.raises(sqlite3.OperationalError, match="readonly"):
                readonly.write("INSERT INTO t VALUES (2)")
        finally:
            readonly.close()
