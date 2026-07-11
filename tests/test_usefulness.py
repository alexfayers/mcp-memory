"""Tests for the deterministic implicit-usefulness auto-vote observer."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from mcp_memory import usefulness
from mcp_memory.database import DatabaseManager

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def db(tmp_path: Path) -> DatabaseManager:
    """Create a fresh database for each test."""
    return DatabaseManager(tmp_path / "test.db")


def _seed(db: DatabaseManager, project: str, name: str, keyword: str) -> None:
    db.create_entities(project, [{"name": name, "entityType": "task", "observations": [keyword]}])


class TestObserveSurfacing:
    def test_search_nodes_records_surfaced_rows(self, db: DatabaseManager) -> None:
        _seed(db, "proj", "task/a", "needle")
        result = db.search_nodes("proj", "needle")

        usefulness.observe(db, "search_nodes", {"project": "proj", "query": "needle"}, result)

        rows = db._db.execute(
            "SELECT project, entity_name, tool, query, rank FROM surfaced_entities"
        ).fetchall()
        assert [(r["project"], r["entity_name"]) for r in rows] == [("proj", "task/a")]
        assert rows[0]["tool"] == "search_nodes"
        assert rows[0]["query"] == "needle"
        assert rows[0]["rank"] == 1

    def test_search_all_projects_records_per_entity_project(self, db: DatabaseManager) -> None:
        _seed(db, "alpha", "task/a", "shared")
        _seed(db, "beta", "task/b", "shared")
        flat = db.search_nodes(None, "shared")
        grouped: dict[str, object] = {"results": {}, "relations": flat["relations"]}
        for entity in flat["entities"]:
            group = grouped["results"].setdefault(  # type: ignore[union-attr]
                entity.project_name, {"entities": [], "relations": []}
            )
            group["entities"].append(entity)

        usefulness.observe(db, "search_all_projects", {"query": "shared"}, grouped)

        rows = db._db.execute(
            "SELECT project, entity_name FROM surfaced_entities ORDER BY project"
        ).fetchall()
        assert {(r["project"], r["entity_name"]) for r in rows} == {
            ("alpha", "task/a"),
            ("beta", "task/b"),
        }

    def test_read_graph_is_not_surfaced(self, db: DatabaseManager) -> None:
        _seed(db, "proj", "task/a", "needle")
        result = db.read_graph("proj")

        usefulness.observe(db, "read_graph", {"project": "proj"}, result)

        count = db._db.execute("SELECT COUNT(*) AS n FROM surfaced_entities").fetchone()["n"]
        assert count == 0

    def test_errored_result_records_nothing(self, db: DatabaseManager) -> None:
        usefulness.observe(db, "search_nodes", {"project": "proj", "query": "q"}, {"error": "boom"})

        count = db._db.execute("SELECT COUNT(*) AS n FROM surfaced_entities").fetchone()["n"]
        assert count == 0


class TestObserveUse:
    def test_add_observations_after_search_casts_upvote(self, db: DatabaseManager) -> None:
        _seed(db, "proj", "task/a", "needle")
        usefulness.observe(
            db,
            "search_nodes",
            {"project": "proj", "query": "needle"},
            db.search_nodes("proj", "needle"),
        )

        usefulness.observe(
            db,
            "add_observations",
            {"project": "proj", "entityName": "task/a", "observations": ["more"]},
            {"count": 1},
        )

        assert db.get_entity("proj", "task/a").vote_score == 1

    def test_create_relations_casts_upvote_for_both_endpoints(self, db: DatabaseManager) -> None:
        _seed(db, "proj", "task/a", "alpha")
        db.create_entities(
            "proj", [{"name": "feature/x", "entityType": "feature", "observations": ["beta"]}]
        )
        for name in ("task/a", "feature/x"):
            db.record_surfaced("search_nodes", "q", f"rid-{name}", [("proj", name, 1)])

        usefulness.observe(
            db,
            "create_relations",
            {
                "project": "proj",
                "relations": [{"source": "task/a", "target": "feature/x", "type": "implements"}],
            },
            {"message": "ok"},
        )

        assert db.get_entity("proj", "task/a").vote_score == 1
        assert db.get_entity("proj", "feature/x").vote_score == 1

    def test_set_entity_status_after_search_casts_upvote(self, db: DatabaseManager) -> None:
        _seed(db, "proj", "task/a", "needle")
        db.record_surfaced("search_nodes", "q", "rid", [("proj", "task/a", 1)])

        usefulness.observe(
            db,
            "set_entity_status",
            {"project": "proj", "name": "task/a", "status": "resolved"},
            {"message": "ok"},
        )

        assert db.get_entity("proj", "task/a").vote_score == 1

    def test_create_entities_for_brand_new_entity_is_safe_noop(self, db: DatabaseManager) -> None:
        usefulness.observe(
            db,
            "create_entities",
            {"project": "proj", "entities": [{"name": "task/new", "entityType": "task"}]},
            {"message": "ok"},
        )
        # Nothing was surfaced, so no vote and no error.
        count = db._db.execute("SELECT COUNT(*) AS n FROM surfaced_entities").fetchone()["n"]
        assert count == 0

    def test_delete_entity_is_not_a_use(self, db: DatabaseManager) -> None:
        _seed(db, "proj", "task/a", "needle")
        db.record_surfaced("search_nodes", "q", "rid", [("proj", "task/a", 1)])

        usefulness.observe(
            db, "delete_entity", {"project": "proj", "name": "task/a"}, {"message": "ok"}
        )

        # A surfacing exists but delete is not a "use"; score stays 0.
        assert db.get_entity("proj", "task/a").vote_score == 0

    def test_observe_never_raises_on_bad_input(self, db: DatabaseManager) -> None:
        usefulness.observe(db, "add_observations", {}, {"count": 0})
        usefulness.observe(db, "search_nodes", {"project": "proj"}, "not a dict")
