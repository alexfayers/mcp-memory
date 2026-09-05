"""Tests for the deterministic implicit-usefulness auto-vote observer."""

from __future__ import annotations

from typing import TYPE_CHECKING

from mcp_memory import usefulness

if TYPE_CHECKING:
    from mcp_memory.models import Entity, Relation
    from mcp_memory.storage import Storage


def _seed(db: Storage, project: str, name: str, keyword: str) -> None:
    db.entities.create(project, [{"name": name, "entityType": "task", "observations": [keyword]}])


class TestObserveSurfacing:
    def test_search_nodes_records_surfaced_rows(self, store: Storage) -> None:
        _seed(store, "proj", "task/a", "needle")
        result = store.reads.search("proj", "needle")

        usefulness.observe(store, "search_nodes", {"project": "proj", "query": "needle"}, result)

        rows = store.connection.query_all("SELECT project, entity_name, tool, query, rank FROM surfaced_entities")
        assert [(r["project"], r["entity_name"]) for r in rows] == [("proj", "task/a")]
        assert rows[0]["tool"] == "search_nodes"
        assert rows[0]["query"] == "needle"
        assert rows[0]["rank"] == 1

    def test_search_all_projects_records_per_entity_project(self, store: Storage) -> None:
        _seed(store, "alpha", "task/a", "shared")
        _seed(store, "beta", "task/b", "shared")
        flat = store.reads.search(None, "shared")
        results: dict[str, dict[str, list[Entity | Relation]]] = {}
        for entity in flat["entities"]:
            assert entity.project_name is not None
            group = results.setdefault(entity.project_name, {"entities": [], "relations": []})
            group["entities"].append(entity)
        grouped = {"results": results, "relations": flat["relations"]}

        usefulness.observe(store, "search_all_projects", {"query": "shared"}, grouped)

        rows = store.connection.query_all("SELECT project, entity_name FROM surfaced_entities ORDER BY project")
        assert {(r["project"], r["entity_name"]) for r in rows} == {
            ("alpha", "task/a"),
            ("beta", "task/b"),
        }

    def test_read_graph_is_not_surfaced(self, store: Storage) -> None:
        _seed(store, "proj", "task/a", "needle")
        result = store.reads.recent("proj")

        usefulness.observe(store, "read_graph", {"project": "proj"}, result)

        count = store.connection.query_one("SELECT COUNT(*) AS n FROM surfaced_entities")["n"]
        assert count == 0

    def test_errored_result_records_nothing(self, store: Storage) -> None:
        usefulness.observe(store, "search_nodes", {"project": "proj", "query": "q"}, {"error": "boom"})

        count = store.connection.query_one("SELECT COUNT(*) AS n FROM surfaced_entities")["n"]
        assert count == 0


class TestObserveUse:
    def test_add_observations_after_search_casts_upvote(self, store: Storage) -> None:
        _seed(store, "proj", "task/a", "needle")
        usefulness.observe(
            store,
            "search_nodes",
            {"project": "proj", "query": "needle"},
            store.reads.search("proj", "needle"),
        )

        usefulness.observe(
            store,
            "add_observations",
            {"project": "proj", "entityName": "task/a", "observations": ["more"]},
            {"count": 1},
        )

        assert store.reads.get_entity("proj", "task/a").vote_score == 1

    def test_create_relations_casts_upvote_for_both_endpoints(self, store: Storage) -> None:
        _seed(store, "proj", "task/a", "alpha")
        store.entities.create("proj", [{"name": "feature/x", "entityType": "feature", "observations": ["beta"]}])
        for name in ("task/a", "feature/x"):
            store.telemetry.record_surfaced("search_nodes", "q", f"rid-{name}", [("proj", name, 1)])

        usefulness.observe(
            store,
            "create_relations",
            {
                "project": "proj",
                "relations": [{"source": "task/a", "target": "feature/x", "type": "implements"}],
            },
            {"message": "ok"},
        )

        assert store.reads.get_entity("proj", "task/a").vote_score == 1
        assert store.reads.get_entity("proj", "feature/x").vote_score == 1

    def test_set_entity_status_after_search_casts_upvote(self, store: Storage) -> None:
        _seed(store, "proj", "task/a", "needle")
        store.telemetry.record_surfaced("search_nodes", "q", "rid", [("proj", "task/a", 1)])

        usefulness.observe(
            store,
            "set_entity_status",
            {"project": "proj", "name": "task/a", "status": "resolved"},
            {"message": "ok"},
        )

        assert store.reads.get_entity("proj", "task/a").vote_score == 1

    def test_create_entities_for_brand_new_entity_is_safe_noop(self, store: Storage) -> None:
        usefulness.observe(
            store,
            "create_entities",
            {"project": "proj", "entities": [{"name": "task/new", "entityType": "task"}]},
            {"message": "ok"},
        )
        # Nothing was surfaced, so no vote and no error.
        count = store.connection.query_one("SELECT COUNT(*) AS n FROM surfaced_entities")["n"]
        assert count == 0

    def test_delete_entity_is_not_a_use(self, store: Storage) -> None:
        _seed(store, "proj", "task/a", "needle")
        store.telemetry.record_surfaced("search_nodes", "q", "rid", [("proj", "task/a", 1)])

        usefulness.observe(store, "delete_entity", {"project": "proj", "name": "task/a"}, {"message": "ok"})

        # A surfacing exists but delete is not a "use"; score stays 0.
        assert store.reads.get_entity("proj", "task/a").vote_score == 0

    def test_observe_never_raises_on_bad_input(self, store: Storage) -> None:
        usefulness.observe(store, "add_observations", {}, {"count": 0})
        usefulness.observe(store, "search_nodes", {"project": "proj"}, "not a dict")
