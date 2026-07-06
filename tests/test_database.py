"""Tests for the DatabaseManager."""

from __future__ import annotations

from pathlib import Path

import pytest

from mcp_memory.database import DatabaseManager
from mcp_memory.migrations.schema import _relation_type_backfill_statements
from mcp_memory.models import Entity, Relation
from mcp_memory.path_resolver import normalize_path


@pytest.fixture
def db(tmp_path: Path) -> DatabaseManager:
    """Create a fresh database for each test."""
    return DatabaseManager(tmp_path / "test.db")


class TestCreateEntities:
    def test_create_single_entity(self, db: DatabaseManager) -> None:
        db.create_entities("proj", [{"name": "e1", "entityType": "task", "observations": ["obs1"]}])
        entity = db.get_entity("proj", "e1")
        assert entity.name == "e1"
        assert entity.entity_type == "task"
        assert entity.observations == ["obs1"]

    def test_create_entity_with_status(self, db: DatabaseManager) -> None:
        db.create_entities(
            "proj",
            [{"name": "e1", "entityType": "task", "observations": ["obs1"], "status": "planned"}],
        )
        assert db.get_entity("proj", "e1").status == "planned"

    def test_upsert_overwrites_observations(self, db: DatabaseManager) -> None:
        db.create_entities("proj", [{"name": "e1", "entityType": "task", "observations": ["old"]}])
        db.create_entities("proj", [{"name": "e1", "entityType": "task", "observations": ["new"]}])
        assert db.get_entity("proj", "e1").observations == ["new"]

    def test_project_isolation(self, db: DatabaseManager) -> None:
        db.create_entities("p1", [{"name": "e1", "entityType": "task", "observations": ["a"]}])
        db.create_entities("p2", [{"name": "e1", "entityType": "task", "observations": ["b"]}])
        assert db.get_entity("p1", "e1").observations == ["a"]
        assert db.get_entity("p2", "e1").observations == ["b"]

    def test_empty_name_raises(self, db: DatabaseManager) -> None:
        with pytest.raises(ValueError, match="non-empty string"):
            db.create_entities("proj", [{"name": "", "entityType": "task", "observations": ["x"]}])

    def test_empty_observations_raises(self, db: DatabaseManager) -> None:
        with pytest.raises(ValueError, match="non-empty list"):
            db.create_entities("proj", [{"name": "e1", "entityType": "task", "observations": []}])

    def test_invalid_status_raises(self, db: DatabaseManager) -> None:
        with pytest.raises(ValueError, match="Invalid status"):
            db.create_entities(
                "proj",
                [{"name": "e1", "entityType": "task", "observations": ["x"], "status": "bad"}],
            )


class TestMoveProjectEntities:
    def test_moves_entities_to_target(self, db: DatabaseManager) -> None:
        db.create_entities("src", [{"name": "e1", "entityType": "task", "observations": ["a"]}])
        db.create_entities("dst", [{"name": "d0", "entityType": "task", "observations": ["x"]}])
        moved = db.move_project_entities("src", "dst")
        assert moved == 1
        assert db.get_entity("dst", "e1").observations == ["a"]

    def test_preserves_relations(self, db: DatabaseManager) -> None:
        db.create_entities(
            "src",
            [
                {"name": "a", "entityType": "task", "observations": ["a"]},
                {"name": "b", "entityType": "feature", "observations": ["b"]},
            ],
        )
        db.create_relations("src", [Relation(source="a", target="b", relation_type="implements")])
        db.create_entities("dst", [{"name": "d0", "entityType": "task", "observations": ["x"]}])
        db.move_project_entities("src", "dst")
        result = db.get_entity_with_relations("dst", "a")
        assert any(r.target == "b" for r in result["relations"])

    def test_source_scope_emptied(self, db: DatabaseManager) -> None:
        db.create_entities("src", [{"name": "e1", "entityType": "task", "observations": ["a"]}])
        db.create_entities("dst", [{"name": "d0", "entityType": "task", "observations": ["x"]}])
        db.move_project_entities("src", "dst")
        assert db.read_graph("src")["entities"] == []

    def test_name_collision_raises(self, db: DatabaseManager) -> None:
        db.create_entities("src", [{"name": "dup", "entityType": "task", "observations": ["a"]}])
        db.create_entities("dst", [{"name": "dup", "entityType": "task", "observations": ["b"]}])
        with pytest.raises(ValueError, match="collision"):
            db.move_project_entities("src", "dst")

    def test_missing_source_raises(self, db: DatabaseManager) -> None:
        db.create_entities("dst", [{"name": "d0", "entityType": "task", "observations": ["x"]}])
        with pytest.raises(ValueError, match="not found"):
            db.move_project_entities("nope", "dst")

    def test_moved_entities_are_searchable_in_target(self, db: DatabaseManager) -> None:
        db.create_entities(
            "src", [{"name": "findme", "entityType": "task", "observations": ["needle"]}]
        )
        db.create_entities("dst", [{"name": "d0", "entityType": "task", "observations": ["x"]}])
        db.move_project_entities("src", "dst")
        hits = db.search_nodes("dst", "needle")["entities"]
        assert any(e.name == "findme" for e in hits)


class TestDeleteProject:
    def test_deletes_empty_project(self, db: DatabaseManager) -> None:
        db.set_project_paths("doomed", [])
        assert "doomed" in db.list_projects()
        db.delete_project("doomed")
        assert "doomed" not in db.list_projects()

    def test_deletes_project_paths(self, db: DatabaseManager, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        db.set_project_paths("doomed", [str(repo)])
        db.delete_project("doomed")
        assert db.get_project_for_path(str(repo)) is None

    def test_missing_project_raises(self, db: DatabaseManager) -> None:
        with pytest.raises(ValueError, match="not found"):
            db.delete_project("never-existed")

    def test_non_empty_project_raises(self, db: DatabaseManager) -> None:
        db.create_entities("busy", [{"name": "e1", "entityType": "task", "observations": ["a"]}])
        with pytest.raises(ValueError, match="entit"):
            db.delete_project("busy")

    def test_refuses_global(self, db: DatabaseManager) -> None:
        with pytest.raises(ValueError, match="global"):
            db.delete_project("global")


class TestProjectCaseInsensitivity:
    def test_project_names_are_case_insensitive(self, db: DatabaseManager) -> None:
        db.create_entities(
            "MyProject", [{"name": "e1", "entityType": "task", "observations": ["a"]}]
        )
        entity = db.get_entity("myproject", "e1")
        assert entity.observations == ["a"]

    def test_case_insensitive_project_does_not_duplicate(self, db: DatabaseManager) -> None:
        db.create_entities("Proj", [{"name": "e1", "entityType": "task", "observations": ["a"]}])
        db.create_entities("proj", [{"name": "e1", "entityType": "task", "observations": ["b"]}])
        assert db.get_entity("PROJ", "e1").observations == ["b"]


class TestMigrations:
    def test_reopening_db_is_idempotent(self, tmp_path: Path) -> None:
        db_path = tmp_path / "memory.db"
        repo = tmp_path / "repo"
        repo.mkdir()
        first = DatabaseManager(db_path)
        first.set_project_paths("platform", [str(repo)])
        first.close()

        reopened = DatabaseManager(db_path)
        assert reopened.get_project_for_path(str(repo)) == "platform"
        reopened.close()

    def test_vote_score_backfills_to_zero(self, tmp_path: Path) -> None:
        db_path = tmp_path / "memory.db"
        first = DatabaseManager(db_path)
        first.create_entities(
            "proj", [{"name": "task/a", "entityType": "task", "observations": ["x"]}]
        )
        first.close()

        reopened = DatabaseManager(db_path)
        assert reopened.get_entity("proj", "task/a").vote_score == 0
        reopened.close()


class TestVoteScoreReadPaths:
    def test_new_entity_reports_zero_vote_score(self, db: DatabaseManager) -> None:
        db.create_entities(
            "proj", [{"name": "task/a", "entityType": "task", "observations": ["keyword"]}]
        )

        assert db.get_entity("proj", "task/a").vote_score == 0
        assert db.search_nodes("proj", "keyword")["entities"][0].vote_score == 0
        assert db.read_graph("proj")["entities"][0].vote_score == 0


class TestRelationTypeBackfill:
    def _seed_variant_relation(
        self, db: DatabaseManager, source: str, target: str, variant: str
    ) -> None:
        """Insert a relation using a raw (unvalidated) relation type, bypassing the server layer."""
        db._db.execute("INSERT OR IGNORE INTO relation_types (name) VALUES (?)", (variant,))
        src_id = db._db.execute("SELECT id FROM entities WHERE name = ?", (source,)).fetchone()[0]
        tgt_id = db._db.execute("SELECT id FROM entities WHERE name = ?", (target,)).fetchone()[0]
        type_id = db._db.execute(
            "SELECT id FROM relation_types WHERE name = ?", (variant,)
        ).fetchone()[0]
        db._db.execute(
            "INSERT OR IGNORE INTO relations (source_id, target_id, relation_type_id) "
            "VALUES (?, ?, ?)",
            (src_id, tgt_id, type_id),
        )
        db._db.commit()

    def _rerun_backfill(self, db: DatabaseManager, db_path: Path) -> DatabaseManager:
        """Re-run v19's backfill statements directly to prove the backfill is idempotent.

        Rolling back schema_version and reopening would also re-run any later, non-idempotent
        migrations, so apply the v19 statements straight against the open connection instead.
        """
        for statement in _relation_type_backfill_statements():
            db._db.execute(statement)
        db._db.commit()
        return db

    def test_variant_merged_into_canonical(self, tmp_path: Path) -> None:
        db_path = tmp_path / "memory.db"
        db = DatabaseManager(db_path)
        db.create_entities(
            "proj",
            [
                {"name": "a", "entityType": "task", "observations": ["x"]},
                {"name": "b", "entityType": "project", "observations": ["y"]},
            ],
        )
        self._seed_variant_relation(db, "a", "b", "related-to")

        db = self._rerun_backfill(db, db_path)

        result = db.get_entity_with_relations("proj", "a")
        assert [r.relation_type for r in result["relations"]] == ["relates-to"]
        orphan = db._db.execute("SELECT 1 FROM relation_types WHERE name = 'related-to'").fetchone()
        assert orphan is None

    def test_long_tail_collapsed(self, tmp_path: Path) -> None:
        db_path = tmp_path / "memory.db"
        db = DatabaseManager(db_path)
        db.create_entities(
            "proj",
            [
                {"name": "a", "entityType": "task", "observations": ["x"]},
                {"name": "c", "entityType": "feature", "observations": ["z"]},
            ],
        )
        self._seed_variant_relation(db, "a", "c", "extends")

        db = self._rerun_backfill(db, db_path)

        result = db.get_entity_with_relations("proj", "a")
        assert [r.relation_type for r in result["relations"]] == ["implements"]

    def test_underscore_and_camel_variants_merged(self, tmp_path: Path) -> None:
        db_path = tmp_path / "memory.db"
        db = DatabaseManager(db_path)
        db.create_entities(
            "proj",
            [
                {"name": "a", "entityType": "task", "observations": ["x"]},
                {"name": "b", "entityType": "project", "observations": ["y"]},
                {"name": "c", "entityType": "feature", "observations": ["z"]},
            ],
        )
        self._seed_variant_relation(db, "a", "b", "related_to")
        self._seed_variant_relation(db, "a", "c", "blockedBy")

        db = self._rerun_backfill(db, db_path)

        result = db.get_entity_with_relations("proj", "a")
        types = sorted(r.relation_type for r in result["relations"])
        assert types == ["depends-on", "relates-to"]

    def test_collision_drops_duplicate(self, tmp_path: Path) -> None:
        db_path = tmp_path / "memory.db"
        db = DatabaseManager(db_path)
        db.create_entities(
            "proj",
            [
                {"name": "a", "entityType": "task", "observations": ["x"]},
                {"name": "b", "entityType": "project", "observations": ["y"]},
            ],
        )
        db.create_relations("proj", [Relation(source="a", target="b", relation_type="relates-to")])
        self._seed_variant_relation(db, "a", "b", "related-to")

        db = self._rerun_backfill(db, db_path)

        result = db.get_entity_with_relations("proj", "a")
        assert [r.relation_type for r in result["relations"]] == ["relates-to"]


class TestProjectPaths:
    def test_set_and_get_round_trip(self, db: DatabaseManager, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        db.set_project_paths("platform", [str(repo)])
        assert db.get_project_for_path(str(repo / "src" / "x.py")) == "platform"

    def test_set_normalises_stored_paths(self, db: DatabaseManager, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        db.set_project_paths("platform", [str(tmp_path / "repo" / "." / "")])
        assert db.list_project_paths() == [("platform", normalize_path(str(repo)))]

    def test_set_replaces_existing_paths(self, db: DatabaseManager, tmp_path: Path) -> None:
        first = tmp_path / "first"
        second = tmp_path / "second"
        first.mkdir()
        second.mkdir()
        db.set_project_paths("platform", [str(first)])
        db.set_project_paths("platform", [str(second)])
        assert db.get_project_for_path(str(first)) is None
        assert db.get_project_for_path(str(second)) == "platform"

    def test_set_creates_project_row(self, db: DatabaseManager, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        db.set_project_paths("brand-new", [str(repo)])
        assert "brand-new" in db.list_projects()

    def test_path_owned_by_another_project_raises(
        self, db: DatabaseManager, tmp_path: Path
    ) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        db.set_project_paths("platform", [str(repo)])
        with pytest.raises(ValueError, match="already registered"):
            db.set_project_paths("other", [str(repo)])

    def test_get_returns_none_when_unmatched(self, db: DatabaseManager, tmp_path: Path) -> None:
        assert db.get_project_for_path(str(tmp_path / "nowhere")) is None

    def test_empty_project_raises(self, db: DatabaseManager, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            db.set_project_paths("", [str(tmp_path)])

    def test_non_list_paths_raises(self, db: DatabaseManager) -> None:
        with pytest.raises(TypeError, match="list"):
            db.set_project_paths("platform", "not-a-list")  # type: ignore[arg-type]

    def test_get_paths_for_project_returns_registered_paths(
        self, db: DatabaseManager, tmp_path: Path
    ) -> None:
        first = tmp_path / "first"
        second = tmp_path / "second"
        first.mkdir()
        second.mkdir()
        db.set_project_paths("platform", [str(first), str(second)])
        assert db.get_paths_for_project("platform") == [
            normalize_path(str(first)),
            normalize_path(str(second)),
        ]

    def test_get_paths_for_unknown_project_returns_empty_without_creating(
        self, db: DatabaseManager
    ) -> None:
        assert db.get_paths_for_project("ghost") == []
        assert "ghost" not in db.list_projects()

    def test_paths_for_entity_name_single_project(
        self, db: DatabaseManager, tmp_path: Path
    ) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        db.create_entities(
            "platform", [{"name": "task/x", "entityType": "task", "observations": ["o"]}]
        )
        db.set_project_paths("platform", [str(repo)])
        assert db.paths_for_entity_name("task/x") == [("platform", [normalize_path(str(repo))])]

    def test_paths_for_entity_name_groups_by_project(
        self, db: DatabaseManager, tmp_path: Path
    ) -> None:
        first = tmp_path / "first"
        second = tmp_path / "second"
        first.mkdir()
        second.mkdir()
        db.create_entities(
            "alpha", [{"name": "shared", "entityType": "task", "observations": ["o"]}]
        )
        db.create_entities(
            "beta", [{"name": "shared", "entityType": "task", "observations": ["o"]}]
        )
        db.set_project_paths("alpha", [str(first)])
        db.set_project_paths("beta", [str(second)])
        assert db.paths_for_entity_name("shared") == [
            ("alpha", [normalize_path(str(first))]),
            ("beta", [normalize_path(str(second))]),
        ]

    def test_paths_for_entity_name_includes_pathless_project(self, db: DatabaseManager) -> None:
        db.create_entities(
            "platform", [{"name": "task/x", "entityType": "task", "observations": ["o"]}]
        )
        assert db.paths_for_entity_name("task/x") == [("platform", [])]

    def test_paths_for_entity_name_missing_entity_returns_empty(self, db: DatabaseManager) -> None:
        assert db.paths_for_entity_name("nope") == []


class TestObservations:
    def test_add_observations(self, db: DatabaseManager) -> None:
        db.create_entities("proj", [{"name": "e1", "entityType": "task", "observations": ["a"]}])
        count = db.add_observations("proj", "e1", ["b", "c"])
        assert count == 2
        assert db.get_entity("proj", "e1").observations == ["a", "b", "c"]

    def test_add_observations_deduplicates(self, db: DatabaseManager) -> None:
        db.create_entities("proj", [{"name": "e1", "entityType": "task", "observations": ["a"]}])
        count = db.add_observations("proj", "e1", ["a", "b"])
        assert count == 1

    def test_add_observations_missing_entity_raises(self, db: DatabaseManager) -> None:
        with pytest.raises(ValueError, match="not found"):
            db.add_observations("proj", "missing", ["x"])

    def test_delete_observations(self, db: DatabaseManager) -> None:
        db.create_entities(
            "proj", [{"name": "e1", "entityType": "task", "observations": ["a", "b", "c"]}]
        )
        count = db.delete_observations("proj", "e1", ["b"])
        assert count == 1
        assert db.get_entity("proj", "e1").observations == ["a", "c"]

    def test_delete_observations_nonexistent_content(self, db: DatabaseManager) -> None:
        db.create_entities("proj", [{"name": "e1", "entityType": "task", "observations": ["a"]}])
        count = db.delete_observations("proj", "e1", ["missing"])
        assert count == 0

    def test_delete_observations_missing_entity_raises(self, db: DatabaseManager) -> None:
        with pytest.raises(ValueError, match="not found"):
            db.delete_observations("proj", "missing", ["x"])


class TestEntityStatus:
    def test_set_status(self, db: DatabaseManager) -> None:
        db.create_entities("proj", [{"name": "e1", "entityType": "task", "observations": ["x"]}])
        db.set_entity_status("proj", "e1", "in-progress")
        assert db.get_entity("proj", "e1").status == "in-progress"

    def test_clear_status(self, db: DatabaseManager) -> None:
        db.create_entities(
            "proj",
            [{"name": "e1", "entityType": "task", "observations": ["x"], "status": "planned"}],
        )
        db.set_entity_status("proj", "e1", None)
        assert db.get_entity("proj", "e1").status is None

    def test_invalid_status_raises(self, db: DatabaseManager) -> None:
        db.create_entities("proj", [{"name": "e1", "entityType": "task", "observations": ["x"]}])
        with pytest.raises(ValueError, match="Invalid status"):
            db.set_entity_status("proj", "e1", "bad")  # type: ignore[arg-type]

    def test_missing_entity_raises(self, db: DatabaseManager) -> None:
        with pytest.raises(ValueError, match="not found"):
            db.set_entity_status("proj", "missing", "planned")


class TestRelations:
    def test_create_and_retrieve_relations(self, db: DatabaseManager) -> None:
        db.create_entities(
            "proj",
            [
                {"name": "a", "entityType": "task", "observations": ["x"]},
                {"name": "b", "entityType": "project", "observations": ["y"]},
            ],
        )
        db.create_relations("proj", [Relation(source="a", target="b", relation_type="belongs-to")])
        result = db.get_entity_with_relations("proj", "a")
        assert len(result["relations"]) == 1
        rel = result["relations"][0]
        assert isinstance(rel, Relation)
        assert rel.source == "a"
        assert rel.target == "b"
        assert rel.relation_type == "belongs-to"

    def test_duplicate_relation_ignored(self, db: DatabaseManager) -> None:
        db.create_entities(
            "proj",
            [
                {"name": "a", "entityType": "task", "observations": ["x"]},
                {"name": "b", "entityType": "project", "observations": ["y"]},
            ],
        )
        rel = Relation(source="a", target="b", relation_type="belongs-to")
        db.create_relations("proj", [rel])
        db.create_relations("proj", [rel])
        result = db.get_entity_with_relations("proj", "a")
        assert len(result["relations"]) == 1

    def test_missing_source_raises(self, db: DatabaseManager) -> None:
        db.create_entities("proj", [{"name": "b", "entityType": "task", "observations": ["x"]}])
        with pytest.raises(ValueError, match="Source entity"):
            db.create_relations("proj", [Relation(source="missing", target="b", relation_type="x")])

    def test_delete_relation(self, db: DatabaseManager) -> None:
        db.create_entities(
            "proj",
            [
                {"name": "a", "entityType": "task", "observations": ["x"]},
                {"name": "b", "entityType": "project", "observations": ["y"]},
            ],
        )
        db.create_relations("proj", [Relation(source="a", target="b", relation_type="belongs-to")])
        db.delete_relation("proj", "a", "b", "belongs-to")
        result = db.get_entity_with_relations("proj", "a")
        assert len(result["relations"]) == 0

    def test_delete_nonexistent_relation_raises(self, db: DatabaseManager) -> None:
        db.create_entities(
            "proj",
            [
                {"name": "a", "entityType": "task", "observations": ["x"]},
                {"name": "b", "entityType": "project", "observations": ["y"]},
            ],
        )
        with pytest.raises(ValueError, match="not found"):
            db.delete_relation("proj", "a", "b", "nonexistent")

    def test_self_referential_relation_rejected(self, db: DatabaseManager) -> None:
        db.create_entities("proj", [{"name": "a", "entityType": "pattern", "observations": ["x"]}])
        with pytest.raises(ValueError, match="Self-referential"):
            db.create_relations("proj", [Relation(source="a", target="a", relation_type="x")])


class TestDeleteEntity:
    def test_delete_entity(self, db: DatabaseManager) -> None:
        db.create_entities("proj", [{"name": "e1", "entityType": "task", "observations": ["x"]}])
        db.delete_entity("proj", "e1")
        with pytest.raises(ValueError, match="not found"):
            db.get_entity("proj", "e1")

    def test_delete_entity_cascades_relations(self, db: DatabaseManager) -> None:
        db.create_entities(
            "proj",
            [
                {"name": "a", "entityType": "task", "observations": ["x"]},
                {"name": "b", "entityType": "project", "observations": ["y"]},
            ],
        )
        db.create_relations("proj", [Relation(source="a", target="b", relation_type="belongs-to")])
        db.delete_entity("proj", "a")
        result = db.get_entity_with_relations("proj", "b")
        assert len(result["relations"]) == 0

    def test_delete_missing_entity_raises(self, db: DatabaseManager) -> None:
        with pytest.raises(ValueError, match="not found"):
            db.delete_entity("proj", "missing")

    def test_delete_blocked_by_incoming_relations(self, db: DatabaseManager) -> None:
        db.create_entities(
            "proj",
            [
                {"name": "a", "entityType": "task", "observations": ["x"]},
                {"name": "b", "entityType": "project", "observations": ["y"]},
            ],
        )
        db.create_relations("proj", [Relation(source="a", target="b", relation_type="belongs-to")])
        with pytest.raises(ValueError, match=r"Cannot delete 'b'.*incoming relation.*from: a"):
            db.delete_entity("proj", "b")


class TestGetEntityWithRelations:
    def test_returns_related_entities(self, db: DatabaseManager) -> None:
        db.create_entities(
            "proj",
            [
                {"name": "a", "entityType": "task", "observations": ["x"]},
                {"name": "b", "entityType": "project", "observations": ["y"]},
                {"name": "c", "entityType": "feature", "observations": ["z"]},
            ],
        )
        db.create_relations(
            "proj",
            [
                Relation(source="a", target="b", relation_type="belongs-to"),
                Relation(source="a", target="c", relation_type="implements"),
            ],
        )
        result = db.get_entity_with_relations("proj", "a")
        assert isinstance(result["entity"], Entity)
        assert result["entity"].name == "a"
        related_names = {e.name for e in result["relatedEntities"] if isinstance(e, Entity)}
        assert related_names == {"b", "c"}


class TestSearchRelatedNodes:
    def test_filter_by_entity_type(self, db: DatabaseManager) -> None:
        db.create_entities(
            "proj",
            [
                {"name": "a", "entityType": "task", "observations": ["x"]},
                {"name": "b", "entityType": "project", "observations": ["y"]},
                {"name": "c", "entityType": "feature", "observations": ["z"]},
            ],
        )
        db.create_relations(
            "proj",
            [
                Relation(source="a", target="b", relation_type="belongs-to"),
                Relation(source="a", target="c", relation_type="implements"),
            ],
        )
        result = db.search_related_nodes("proj", "a", entity_type="project")
        related_names = {e.name for e in result["relatedEntities"] if isinstance(e, Entity)}
        assert related_names == {"b"}

    def test_filter_by_relation_type(self, db: DatabaseManager) -> None:
        db.create_entities(
            "proj",
            [
                {"name": "a", "entityType": "task", "observations": ["x"]},
                {"name": "b", "entityType": "project", "observations": ["y"]},
                {"name": "c", "entityType": "feature", "observations": ["z"]},
            ],
        )
        db.create_relations(
            "proj",
            [
                Relation(source="a", target="b", relation_type="belongs-to"),
                Relation(source="a", target="c", relation_type="implements"),
            ],
        )
        result = db.search_related_nodes("proj", "a", relation_type="implements")
        assert len(result["relations"]) == 1
        assert result["relations"][0].relation_type == "implements"


class TestSearchNodes:
    def test_fts_search(self, db: DatabaseManager) -> None:
        db.create_entities(
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
        result = db.search_nodes("proj", "Python")
        entities = result["entities"]
        assert len(entities) == 1
        assert isinstance(entities[0], Entity)
        assert entities[0].name == "project/myrepo"

    def test_fts_search_by_name(self, db: DatabaseManager) -> None:
        db.create_entities(
            "proj",
            [
                {"name": "project/myrepo", "entityType": "project", "observations": ["obs"]},
            ],
        )
        result = db.search_nodes("proj", "myrepo")
        assert len(result["entities"]) == 1

    def test_multi_term_query_matches_any_term_by_default(self, db: DatabaseManager) -> None:
        db.create_entities(
            "proj",
            [
                {"name": "a", "entityType": "task", "observations": ["alpha only"]},
                {"name": "b", "entityType": "task", "observations": ["beta only"]},
                {"name": "c", "entityType": "task", "observations": ["unrelated"]},
            ],
        )
        result = db.search_nodes("proj", "alpha beta")
        assert {e.name for e in result["entities"]} == {"a", "b"}

    def test_match_all_requires_every_term(self, db: DatabaseManager) -> None:
        db.create_entities(
            "proj",
            [
                {"name": "a", "entityType": "task", "observations": ["alpha only"]},
                {"name": "both", "entityType": "task", "observations": ["alpha and beta"]},
            ],
        )
        result = db.search_nodes("proj", "alpha beta", match_all=True)
        assert {e.name for e in result["entities"]} == {"both"}

    def test_or_query_ranks_all_term_matches_first(self, db: DatabaseManager) -> None:
        db.create_entities(
            "proj",
            [
                {"name": "partial", "entityType": "task", "observations": ["alpha only"]},
                {"name": "full", "entityType": "task", "observations": ["alpha beta"]},
            ],
        )
        result = db.search_nodes("proj", "alpha beta")
        assert [e.name for e in result["entities"]] == ["full", "partial"]

    def test_single_term_query_unaffected_by_match_all(self, db: DatabaseManager) -> None:
        db.create_entities(
            "proj",
            [{"name": "a", "entityType": "task", "observations": ["keyword"]}],
        )
        default = db.search_nodes("proj", "keyword")
        strict = db.search_nodes("proj", "keyword", match_all=True)
        assert [e.name for e in default["entities"]] == ["a"]
        assert [e.name for e in strict["entities"]] == ["a"]

    def test_fts_search_with_entity_type_filter(self, db: DatabaseManager) -> None:
        db.create_entities(
            "proj",
            [
                {"name": "a", "entityType": "task", "observations": ["shared keyword"]},
                {"name": "b", "entityType": "project", "observations": ["shared keyword"]},
            ],
        )
        result = db.search_nodes("proj", "shared", entity_type="task")
        assert len(result["entities"]) == 1
        assert result["entities"][0].name == "a"

    def test_fts_search_with_status_filter(self, db: DatabaseManager) -> None:
        db.create_entities(
            "proj",
            [
                {"name": "a", "entityType": "task", "observations": ["x"], "status": "planned"},
                {"name": "b", "entityType": "task", "observations": ["x"], "status": "resolved"},
            ],
        )
        result = db.search_nodes("proj", "x", status="planned")
        assert len(result["entities"]) == 1
        assert result["entities"][0].name == "a"

    def test_fts_hyphenated_query(self, db: DatabaseManager) -> None:
        db.create_entities(
            "proj",
            [
                {
                    "name": "user-preferences/test",
                    "entityType": "user-preferences",
                    "observations": ["x"],
                }
            ],
        )
        result = db.search_nodes("proj", "user-preferences")
        assert len(result["entities"]) == 1

    def test_empty_query_returns_empty(self, db: DatabaseManager) -> None:
        result = db.search_nodes("proj", "   ")
        assert result["entities"] == []

    def test_search_respects_project_scope(self, db: DatabaseManager) -> None:
        db.create_entities("p1", [{"name": "e1", "entityType": "task", "observations": ["hello"]}])
        db.create_entities("p2", [{"name": "e2", "entityType": "task", "observations": ["hello"]}])
        result = db.search_nodes("p1", "hello")
        assert len(result["entities"]) == 1
        assert result["entities"][0].name == "e1"

    def test_recency_decay_favours_newer_entities(self, db: DatabaseManager) -> None:
        db.create_entities(
            "proj",
            [
                {"name": "old", "entityType": "task", "observations": ["keyword"]},
                {"name": "new", "entityType": "task", "observations": ["keyword"]},
            ],
        )
        db._db.execute(
            "UPDATE entities SET created_at = datetime('now', '-90 days'), "
            "updated_at = datetime('now', '-90 days') WHERE name = 'old'"
        )
        db._db.commit()
        result = db.search_nodes("proj", "keyword")
        assert len(result["entities"]) == 2
        assert result["entities"][0].name == "new"
        assert result["entities"][1].name == "old"

    def test_upvote_outranks_identical_unvoted_entity(self, db: DatabaseManager) -> None:
        db.create_entities(
            "proj",
            [
                {"name": "quiet", "entityType": "task", "observations": ["keyword"]},
                {"name": "useful", "entityType": "task", "observations": ["keyword"]},
            ],
        )
        db.vote_entity("proj", "useful", 1)
        result = db.search_nodes("proj", "keyword")
        assert result["entities"][0].name == "useful"

    def test_start_date_filters_old_entities(self, db: DatabaseManager) -> None:
        db.create_entities(
            "proj",
            [
                {"name": "old", "entityType": "task", "observations": ["keyword"]},
                {"name": "new", "entityType": "task", "observations": ["keyword"]},
            ],
        )
        db._db.execute(
            "UPDATE entities SET created_at = datetime('now', '-90 days') WHERE name = 'old'"
        )
        db._db.commit()
        result = db.search_nodes("proj", "keyword", start_date="30d")
        assert len(result["entities"]) == 1
        assert result["entities"][0].name == "new"

    def test_end_date_filters_new_entities(self, db: DatabaseManager) -> None:
        db.create_entities(
            "proj",
            [
                {"name": "old", "entityType": "task", "observations": ["keyword"]},
                {"name": "new", "entityType": "task", "observations": ["keyword"]},
            ],
        )
        db._db.execute(
            "UPDATE entities SET created_at = datetime('now', '-90 days') WHERE name = 'old'"
        )
        db._db.commit()
        result = db.search_nodes("proj", "keyword", end_date="30d")
        assert len(result["entities"]) == 1
        assert result["entities"][0].name == "old"

    def test_iso_date_filtering(self, db: DatabaseManager) -> None:
        db.create_entities(
            "proj",
            [{"name": "e1", "entityType": "task", "observations": ["keyword"]}],
        )
        result = db.search_nodes("proj", "keyword", start_date="2099-01-01")
        assert len(result["entities"]) == 0

    def test_cross_project_search(self, db: DatabaseManager) -> None:
        db.create_entities(
            "p1",
            [{"name": "t1", "entityType": "task", "observations": ["hello"]}],
        )
        db.create_entities(
            "p2",
            [{"name": "t2", "entityType": "task", "observations": ["hello"]}],
        )
        result = db.search_nodes(None, "hello")
        names = {e.name for e in result["entities"]}
        assert names == {"t1", "t2"}

    def test_cross_project_search_includes_project_name(self, db: DatabaseManager) -> None:
        db.create_entities(
            "alpha",
            [{"name": "e1", "entityType": "task", "observations": ["keyword"]}],
        )
        result = db.search_nodes(None, "keyword")
        assert len(result["entities"]) == 1
        assert result["entities"][0].project_name == "alpha"

    def test_cross_project_search_with_status_filter(self, db: DatabaseManager) -> None:
        db.create_entities(
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
        db.create_entities(
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
        result = db.search_nodes(None, "x", status="in-progress")
        assert len(result["entities"]) == 1
        assert result["entities"][0].name == "a"

    def test_cross_project_search_returns_relations(self, db: DatabaseManager) -> None:
        db.create_entities(
            "p1",
            [
                {"name": "t1", "entityType": "task", "observations": ["hello"]},
                {"name": "f1", "entityType": "feature", "observations": ["other"]},
            ],
        )
        db.create_relations(
            "p1",
            [Relation(source="t1", target="f1", relation_type="implements")],
        )
        result = db.search_nodes(None, "hello")
        assert len(result["entities"]) == 1
        assert len(result["relations"]) == 1
        assert result["relations"][0].source == "t1"

    def test_scoped_search_includes_project_name(self, db: DatabaseManager) -> None:
        db.create_entities(
            "proj",
            [{"name": "e1", "entityType": "task", "observations": ["keyword"]}],
        )
        result = db.search_nodes("proj", "keyword")
        assert result["entities"][0].project_name == "proj"


class TestReadGraph:
    def test_returns_recent_entities(self, db: DatabaseManager) -> None:
        for i in range(15):
            db.create_entities(
                "proj", [{"name": f"e{i}", "entityType": "task", "observations": [f"obs{i}"]}]
            )
        result = db.read_graph("proj")
        assert len(result["entities"]) == 10

    def test_filter_by_status(self, db: DatabaseManager) -> None:
        db.create_entities(
            "proj",
            [
                {"name": "a", "entityType": "task", "observations": ["x"], "status": "planned"},
                {"name": "b", "entityType": "task", "observations": ["y"], "status": "resolved"},
            ],
        )
        result = db.read_graph("proj", status="planned")
        assert len(result["entities"]) == 1
        assert result["entities"][0].name == "a"

    def test_includes_relations(self, db: DatabaseManager) -> None:
        db.create_entities(
            "proj",
            [
                {"name": "a", "entityType": "task", "observations": ["x"]},
                {"name": "b", "entityType": "project", "observations": ["y"]},
            ],
        )
        db.create_relations("proj", [Relation(source="a", target="b", relation_type="belongs-to")])
        result = db.read_graph("proj")
        assert len(result["relations"]) == 1


class TestUpdatedAt:
    def test_updated_at_set_on_creation(self, db: DatabaseManager) -> None:
        db.create_entities("proj", [{"name": "e1", "entityType": "task", "observations": ["x"]}])
        entity = db.get_entity("proj", "e1")
        assert entity.updated_at is not None
        assert entity.updated_at == entity.created_at

    def test_updated_at_changes_on_add_observations(self, db: DatabaseManager) -> None:
        db.create_entities("proj", [{"name": "e1", "entityType": "task", "observations": ["x"]}])
        db._db.execute(
            "UPDATE entities SET updated_at = datetime('now', '-1 day') WHERE name = 'e1'"
        )
        db._db.commit()
        backdated = db.get_entity("proj", "e1").updated_at
        db.add_observations("proj", "e1", ["new obs"])
        assert db.get_entity("proj", "e1").updated_at != backdated

    def test_updated_at_changes_on_status_change(self, db: DatabaseManager) -> None:
        db.create_entities("proj", [{"name": "e1", "entityType": "task", "observations": ["x"]}])
        db._db.execute(
            "UPDATE entities SET updated_at = datetime('now', '-1 day') WHERE name = 'e1'"
        )
        db._db.commit()
        before = db.get_entity("proj", "e1").updated_at
        db.set_entity_status("proj", "e1", "resolved")
        assert db.get_entity("proj", "e1").updated_at != before


class TestVoteEntity:
    def test_upvote_increments_score(self, db: DatabaseManager) -> None:
        db.create_entities("proj", [{"name": "e1", "entityType": "task", "observations": ["x"]}])
        assert db.vote_entity("proj", "e1", 1) == 1

    def test_downvote_decrements_score(self, db: DatabaseManager) -> None:
        db.create_entities("proj", [{"name": "e1", "entityType": "task", "observations": ["x"]}])
        assert db.vote_entity("proj", "e1", -1) == -1

    def test_votes_accumulate(self, db: DatabaseManager) -> None:
        db.create_entities("proj", [{"name": "e1", "entityType": "task", "observations": ["x"]}])
        db.vote_entity("proj", "e1", 1)
        db.vote_entity("proj", "e1", 1)
        db.vote_entity("proj", "e1", -1)
        assert db.get_entity("proj", "e1").vote_score == 1

    @pytest.mark.parametrize("vote", [0, 2, -3])
    def test_invalid_vote_raises(self, db: DatabaseManager, vote: int) -> None:
        db.create_entities("proj", [{"name": "e1", "entityType": "task", "observations": ["x"]}])
        with pytest.raises(ValueError, match="Invalid vote"):
            db.vote_entity("proj", "e1", vote)

    def test_missing_entity_raises(self, db: DatabaseManager) -> None:
        with pytest.raises(ValueError, match="not found"):
            db.vote_entity("proj", "nope", 1)

    def test_vote_does_not_change_updated_at(self, db: DatabaseManager) -> None:
        db.create_entities("proj", [{"name": "e1", "entityType": "task", "observations": ["x"]}])
        db._db.execute(
            "UPDATE entities SET updated_at = datetime('now', '-1 day') WHERE name = 'e1'"
        )
        db._db.commit()
        before = db.get_entity("proj", "e1").updated_at
        db.vote_entity("proj", "e1", 1)
        assert db.get_entity("proj", "e1").updated_at == before


class TestCompactMode:
    def test_read_graph_compact_omits_observations(self, db: DatabaseManager) -> None:
        db.create_entities(
            "proj", [{"name": "e1", "entityType": "task", "observations": ["obs1", "obs2"]}]
        )
        result = db.read_graph("proj", compact=True)
        assert len(result["entities"]) == 1
        assert result["entities"][0].name == "e1"
        assert result["entities"][0].observations == []

    def test_read_graph_non_compact_includes_observations(self, db: DatabaseManager) -> None:
        db.create_entities(
            "proj", [{"name": "e1", "entityType": "task", "observations": ["obs1", "obs2"]}]
        )
        result = db.read_graph("proj", compact=False)
        assert result["entities"][0].observations == ["obs1", "obs2"]

    def test_search_nodes_compact_omits_observations(self, db: DatabaseManager) -> None:
        db.create_entities(
            "proj", [{"name": "task/foo", "entityType": "task", "observations": ["some detail"]}]
        )
        result = db.search_nodes("proj", "foo", compact=True)
        assert len(result["entities"]) == 1
        assert result["entities"][0].name == "task/foo"
        assert result["entities"][0].observations == []

    def test_search_nodes_compact_preserves_metadata(self, db: DatabaseManager) -> None:
        db.create_entities(
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
        result = db.search_nodes("proj", "bar", compact=True)
        entity = result["entities"][0]
        assert entity.entity_type == "task"
        assert entity.status == "planned"
        assert entity.created_at is not None

    def test_cross_project_search_compact(self, db: DatabaseManager) -> None:
        db.create_entities(
            "alpha", [{"name": "task/a1", "entityType": "task", "observations": ["alpha detail"]}]
        )
        db.create_entities(
            "beta", [{"name": "task/b1", "entityType": "task", "observations": ["beta detail"]}]
        )
        result = db.search_nodes(None, "task", compact=True)
        for entity in result["entities"]:
            assert entity.observations == []
            assert entity.name in {"task/a1", "task/b1"}

    def test_compact_still_returns_relations(self, db: DatabaseManager) -> None:
        db.create_entities(
            "proj",
            [
                {"name": "task/x", "entityType": "task", "observations": ["o"]},
                {"name": "project/p", "entityType": "project", "observations": ["o"]},
            ],
        )
        db.create_relations(
            "proj", [Relation(source="task/x", target="project/p", relation_type="belongs-to")]
        )
        result = db.read_graph("proj", compact=True)
        assert len(result["relations"]) == 1
        assert result["relations"][0].relation_type == "belongs-to"
