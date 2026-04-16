"""Tests for the DatabaseManager."""

from __future__ import annotations

from pathlib import Path

import pytest

from mcp_memory.database import DatabaseManager
from mcp_memory.models import Entity, Relation


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
            "UPDATE entities SET created_at = datetime('now', '-90 days') WHERE name = 'old'"
        )
        db._db.commit()
        result = db.search_nodes("proj", "keyword")
        assert len(result["entities"]) == 2
        assert result["entities"][0].name == "new"
        assert result["entities"][1].name == "old"

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
