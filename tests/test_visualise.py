"""Tests for the graph visualisation data functions and routes."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from mcp.server.fastmcp import FastMCP

from mcp_memory import activity
from mcp_memory.database import DatabaseManager
from mcp_memory.models import Relation
from mcp_memory.visualise import (
    get_all_graph_data,
    get_project_paths,
    get_projects,
    register_visualise_routes,
    search_graph,
)


@pytest.fixture(autouse=True)
def _clear_activity() -> None:
    """Reset the process-global activity buffer before each test."""
    activity.clear()


@pytest.fixture
def db(tmp_path: Path) -> DatabaseManager:
    """Create a fresh database for each test."""
    return DatabaseManager(tmp_path / "test.db")


@pytest.fixture
def client(db: DatabaseManager) -> httpx.AsyncClient:
    """Create an async test client with visualise routes registered."""
    mcp = FastMCP("test", stateless_http=True, json_response=True)
    register_visualise_routes(mcp, lambda: db)
    app = mcp.streamable_http_app()
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


class TestGetProjects:
    def test_empty_database(self, db: DatabaseManager) -> None:
        assert get_projects(db) == []

    def test_returns_project_names(self, db: DatabaseManager) -> None:
        db.create_entities(
            "alpha", [{"name": "e1", "entityType": "pattern", "observations": ["o"]}]
        )
        db.create_entities("beta", [{"name": "e2", "entityType": "pattern", "observations": ["o"]}])
        assert get_projects(db) == ["alpha", "beta"]


class TestGetAllGraphData:
    def test_empty_project(self, db: DatabaseManager) -> None:
        result = get_all_graph_data(db, "empty")
        assert result == {"entities": [], "relations": []}

    def test_returns_entities(self, db: DatabaseManager) -> None:
        db.create_entities(
            "proj",
            [
                {
                    "name": "e1",
                    "entityType": "task",
                    "observations": ["obs1", "obs2"],
                    "status": "planned",
                }
            ],
        )
        db.vote_entity("proj", "e1", 1)
        result = get_all_graph_data(db, "proj")
        assert len(result["entities"]) == 1
        entity = result["entities"][0]
        assert entity["name"] == "e1"
        assert entity["entity_type"] == "task"
        assert entity["observations"] == ["obs1", "obs2"]
        assert entity["status"] == "planned"
        assert entity["vote_score"] == 1

    def test_returns_relations(self, db: DatabaseManager) -> None:
        db.create_entities(
            "proj",
            [
                {"name": "e1", "entityType": "pattern", "observations": ["o"]},
                {"name": "e2", "entityType": "pattern", "observations": ["o"]},
            ],
        )
        db.create_relations(
            "proj", [Relation(source="e1", target="e2", relation_type="related-to")]
        )
        result = get_all_graph_data(db, "proj")
        assert len(result["relations"]) == 1
        assert result["relations"][0] == {
            "source": "e1",
            "target": "e2",
            "relation_type": "related-to",
        }

    def test_project_isolation(self, db: DatabaseManager) -> None:
        db.create_entities("p1", [{"name": "e1", "entityType": "pattern", "observations": ["o"]}])
        db.create_entities("p2", [{"name": "e2", "entityType": "pattern", "observations": ["o"]}])
        result = get_all_graph_data(db, "p1")
        assert len(result["entities"]) == 1
        assert result["entities"][0]["name"] == "e1"

    def test_all_projects(self, db: DatabaseManager) -> None:
        db.create_entities("p1", [{"name": "e1", "entityType": "pattern", "observations": ["o"]}])
        db.create_entities("p2", [{"name": "e2", "entityType": "pattern", "observations": ["o"]}])
        result = get_all_graph_data(db)
        assert len(result["entities"]) == 2


class TestVisualisePage:
    @pytest.mark.anyio
    async def test_returns_html(self, client: httpx.AsyncClient) -> None:
        resp = await client.get("/visualise")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "mcp-memory graph" in resp.text


class TestApiProjects:
    @pytest.mark.anyio
    async def test_returns_projects(self, client: httpx.AsyncClient, db: DatabaseManager) -> None:
        db.create_entities(
            "alpha", [{"name": "e1", "entityType": "pattern", "observations": ["o"]}]
        )
        resp = await client.get("/api/projects")
        assert resp.status_code == 200
        assert "alpha" in resp.json()


class TestApiGraph:
    @pytest.mark.anyio
    async def test_no_project_returns_all(
        self, client: httpx.AsyncClient, db: DatabaseManager
    ) -> None:
        db.create_entities("p1", [{"name": "e1", "entityType": "pattern", "observations": ["o"]}])
        db.create_entities("p2", [{"name": "e2", "entityType": "pattern", "observations": ["o"]}])
        resp = await client.get("/api/graph")
        assert resp.status_code == 200
        assert len(resp.json()["entities"]) == 2

    @pytest.mark.anyio
    async def test_returns_graph_data(self, client: httpx.AsyncClient, db: DatabaseManager) -> None:
        db.create_entities("proj", [{"name": "e1", "entityType": "pattern", "observations": ["o"]}])
        resp = await client.get("/api/graph", params={"project": "proj"})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["entities"]) == 1
        assert data["entities"][0]["name"] == "e1"

    def test_entities_carry_their_project(self, db: DatabaseManager) -> None:
        db.create_entities("proj", [{"name": "e1", "entityType": "pattern", "observations": ["o"]}])
        result = get_all_graph_data(db, "proj")
        assert result["entities"][0]["project"] == "proj"


class TestApiActivity:
    @pytest.mark.anyio
    async def test_empty_when_nothing_recorded(self, client: httpx.AsyncClient) -> None:
        resp = await client.get("/api/activity")
        assert resp.status_code == 200
        assert resp.json() == {"events": [], "seq": 0}

    @pytest.mark.anyio
    async def test_returns_recorded_event(self, client: httpx.AsyncClient) -> None:
        activity.record_tool(
            "create_entities",
            {"project": "p", "entities": [{"name": "e1"}]},
            {"message": "ok"},
        )
        resp = await client.get("/api/activity")
        data = resp.json()
        assert data["seq"] == 1
        assert len(data["events"]) == 1
        assert data["events"][0]["entities"] == ["e1"]
        assert data["events"][0]["kind"] == "create"

    @pytest.mark.anyio
    async def test_since_filters_seen_events(self, client: httpx.AsyncClient) -> None:
        activity.record_tool("list_projects", {}, {"projects": []})
        activity.record_tool("list_projects", {}, {"projects": []})
        resp = await client.get("/api/activity", params={"since": 1})
        data = resp.json()
        assert [e["id"] for e in data["events"]] == [2]
        assert data["seq"] == 2

    @pytest.mark.anyio
    async def test_invalid_since_is_treated_as_zero(self, client: httpx.AsyncClient) -> None:
        activity.record_tool("list_projects", {}, {"projects": []})
        resp = await client.get("/api/activity", params={"since": "abc"})
        assert len(resp.json()["events"]) == 1


class TestGetProjectPaths:
    def test_empty_when_none_registered(self, db: DatabaseManager) -> None:
        assert get_project_paths(db) == {}

    def test_groups_paths_by_project(self, db: DatabaseManager) -> None:
        db.create_entities("alpha", [{"name": "e", "entityType": "pattern", "observations": ["o"]}])
        db.set_project_paths("alpha", ["/work/one", "/work/two"])
        assert get_project_paths(db) == {"alpha": ["/work/one", "/work/two"]}


class TestApiProjectPaths:
    @pytest.mark.anyio
    async def test_returns_mapping(self, client: httpx.AsyncClient, db: DatabaseManager) -> None:
        db.create_entities("alpha", [{"name": "e", "entityType": "pattern", "observations": ["o"]}])
        db.set_project_paths("alpha", ["/work/one"])
        resp = await client.get("/api/project-paths")
        assert resp.status_code == 200
        assert resp.json() == {"alpha": ["/work/one"]}


class TestSearchGraph:
    def test_single_match_ranked_and_serialised(self, db: DatabaseManager) -> None:
        db.create_entities(
            "proj",
            [{"name": "e1", "entityType": "pattern", "observations": ["deployment pipeline"]}],
        )
        result = search_graph(db, "deployment", "proj")
        assert len(result["entities"]) == 1
        entity = result["entities"][0]
        assert entity == {
            "rank": 1,
            "name": "e1",
            "entity_type": "pattern",
            "project": "proj",
            "status": None,
            "created_at": entity["created_at"],
            "updated_at": entity["updated_at"],
            "vote_score": 0,
            "observations": ["deployment pipeline"],
        }

    def test_empty_query_returns_empty(self, db: DatabaseManager) -> None:
        db.create_entities("proj", [{"name": "e1", "entityType": "pattern", "observations": ["o"]}])
        assert search_graph(db, "", "proj") == {"entities": [], "relations": []}

    def test_whitespace_query_returns_empty(self, db: DatabaseManager) -> None:
        db.create_entities("proj", [{"name": "e1", "entityType": "pattern", "observations": ["o"]}])
        assert search_graph(db, "   ", "proj") == {"entities": [], "relations": []}

    def test_no_matches_returns_empty(self, db: DatabaseManager) -> None:
        db.create_entities(
            "proj", [{"name": "e1", "entityType": "pattern", "observations": ["alpha"]}]
        )
        assert search_graph(db, "zzzznomatch", "proj")["entities"] == []

    def test_ranking_order_matches_search_nodes(self, db: DatabaseManager) -> None:
        db.create_entities(
            "proj",
            [
                {"name": "a", "entityType": "pattern", "observations": ["search search search"]},
                {"name": "b", "entityType": "pattern", "observations": ["search term here"]},
                {
                    "name": "c",
                    "entityType": "pattern",
                    "observations": ["search one two three four"],
                },
            ],
        )
        graph_names = [e["name"] for e in search_graph(db, "search", "proj")["entities"]]
        core = db.search_nodes("proj", "search")
        core_names = [e.name for e in core["entities"]]  # type: ignore[union-attr]
        assert graph_names == core_names
        assert [e["rank"] for e in search_graph(db, "search", "proj")["entities"]] == [
            1,
            2,
            3,
        ]

    def test_project_scoping(self, db: DatabaseManager) -> None:
        db.create_entities(
            "p1", [{"name": "e1", "entityType": "pattern", "observations": ["kafka"]}]
        )
        db.create_entities(
            "p2", [{"name": "e2", "entityType": "pattern", "observations": ["kafka"]}]
        )
        result = search_graph(db, "kafka", "p1")
        assert [e["name"] for e in result["entities"]] == ["e1"]
        assert result["entities"][0]["project"] == "p1"

    def test_all_projects_returns_project_per_entity(self, db: DatabaseManager) -> None:
        db.create_entities(
            "p1", [{"name": "e1", "entityType": "pattern", "observations": ["kafka"]}]
        )
        db.create_entities(
            "p2", [{"name": "e2", "entityType": "pattern", "observations": ["kafka"]}]
        )
        result = search_graph(db, "kafka")
        assert {e["project"] for e in result["entities"]} == {"p1", "p2"}

    def test_match_all_narrows(self, db: DatabaseManager) -> None:
        db.create_entities(
            "proj",
            [
                {"name": "e1", "entityType": "pattern", "observations": ["red blue"]},
                {"name": "e2", "entityType": "pattern", "observations": ["red"]},
            ],
        )
        assert len(search_graph(db, "red blue", "proj")["entities"]) == 2
        narrowed = search_graph(db, "red blue", "proj", match_all=True)
        assert [e["name"] for e in narrowed["entities"]] == ["e1"]

    def test_relations_among_results_serialised(self, db: DatabaseManager) -> None:
        db.create_entities(
            "proj",
            [
                {"name": "e1", "entityType": "pattern", "observations": ["kafka"]},
                {"name": "e2", "entityType": "pattern", "observations": ["kafka"]},
            ],
        )
        db.create_relations("proj", [Relation("e1", "e2", "relates-to")])
        relations = search_graph(db, "kafka", "proj")["relations"]
        assert {"source": "e1", "target": "e2", "relation_type": "relates-to"} in relations

    def test_scoped_limit_defaults_to_ten(self, db: DatabaseManager) -> None:
        db.create_entities(
            "proj",
            [
                {"name": f"e{i}", "entityType": "pattern", "observations": ["xterm"]}
                for i in range(12)
            ],
        )
        assert len(search_graph(db, "xterm", "proj")["entities"]) == 10

    def test_all_projects_limit_exceeds_ten(self, db: DatabaseManager) -> None:
        db.create_entities(
            "proj",
            [
                {"name": f"e{i}", "entityType": "pattern", "observations": ["xterm"]}
                for i in range(12)
            ],
        )
        assert len(search_graph(db, "xterm")["entities"]) == 12

    def test_explicit_limit_overrides(self, db: DatabaseManager) -> None:
        db.create_entities(
            "proj",
            [
                {"name": f"e{i}", "entityType": "pattern", "observations": ["xterm"]}
                for i in range(5)
            ],
        )
        result = search_graph(db, "xterm", "proj", limit=2)
        assert [e["rank"] for e in result["entities"]] == [1, 2]


class TestApiSearch:
    @pytest.mark.anyio
    async def test_returns_ranked_results(
        self, client: httpx.AsyncClient, db: DatabaseManager
    ) -> None:
        db.create_entities(
            "proj", [{"name": "e1", "entityType": "pattern", "observations": ["deployment"]}]
        )
        resp = await client.get("/api/search", params={"q": "deployment", "project": "proj"})
        assert resp.status_code == 200
        assert resp.json()["entities"][0]["rank"] == 1

    @pytest.mark.anyio
    async def test_empty_query_returns_empty(self, client: httpx.AsyncClient) -> None:
        resp = await client.get("/api/search", params={"q": ""})
        assert resp.json() == {"entities": [], "relations": []}

    @pytest.mark.anyio
    async def test_missing_query_returns_empty(self, client: httpx.AsyncClient) -> None:
        resp = await client.get("/api/search")
        assert resp.json() == {"entities": [], "relations": []}

    @pytest.mark.anyio
    async def test_match_all_true_narrows(
        self, client: httpx.AsyncClient, db: DatabaseManager
    ) -> None:
        db.create_entities(
            "proj",
            [
                {"name": "e1", "entityType": "pattern", "observations": ["red blue"]},
                {"name": "e2", "entityType": "pattern", "observations": ["red"]},
            ],
        )
        resp = await client.get(
            "/api/search", params={"q": "red blue", "project": "proj", "match_all": "true"}
        )
        assert [e["name"] for e in resp.json()["entities"]] == ["e1"]

    @pytest.mark.anyio
    async def test_falsey_match_all_uses_or(
        self, client: httpx.AsyncClient, db: DatabaseManager
    ) -> None:
        db.create_entities(
            "proj",
            [
                {"name": "e1", "entityType": "pattern", "observations": ["red blue"]},
                {"name": "e2", "entityType": "pattern", "observations": ["red"]},
            ],
        )
        resp = await client.get(
            "/api/search", params={"q": "red blue", "project": "proj", "match_all": "nope"}
        )
        assert len(resp.json()["entities"]) == 2

    @pytest.mark.anyio
    async def test_all_projects_carries_project(
        self, client: httpx.AsyncClient, db: DatabaseManager
    ) -> None:
        db.create_entities(
            "p1", [{"name": "e1", "entityType": "pattern", "observations": ["kafka"]}]
        )
        db.create_entities(
            "p2", [{"name": "e2", "entityType": "pattern", "observations": ["kafka"]}]
        )
        resp = await client.get("/api/search", params={"q": "kafka"})
        assert {e["project"] for e in resp.json()["entities"]} == {"p1", "p2"}

    @pytest.mark.anyio
    async def test_search_records_no_activity(
        self, client: httpx.AsyncClient, db: DatabaseManager
    ) -> None:
        db.create_entities(
            "proj", [{"name": "e1", "entityType": "pattern", "observations": ["kafka"]}]
        )
        await client.get("/api/search", params={"q": "kafka", "project": "proj"})
        resp = await client.get("/api/activity")
        assert resp.json() == {"events": [], "seq": 0}


class TestApiVote:
    @pytest.mark.anyio
    async def test_valid_vote_returns_new_score(
        self, client: httpx.AsyncClient, db: DatabaseManager
    ) -> None:
        db.create_entities("proj", [{"name": "e1", "entityType": "pattern", "observations": ["o"]}])
        resp = await client.post("/api/vote", json={"project": "proj", "name": "e1", "vote": 1})
        assert resp.status_code == 200
        assert resp.json() == {"name": "e1", "project": "proj", "vote_score": 1}

        resp = await client.post("/api/vote", json={"project": "proj", "name": "e1", "vote": 1})
        assert resp.json()["vote_score"] == 2

        resp = await client.post("/api/vote", json={"project": "proj", "name": "e1", "vote": -1})
        assert resp.json()["vote_score"] == 1

    @pytest.mark.anyio
    async def test_vote_records_activity_and_ripples(
        self, client: httpx.AsyncClient, db: DatabaseManager
    ) -> None:
        db.create_entities("proj", [{"name": "e1", "entityType": "pattern", "observations": ["o"]}])
        await client.post("/api/vote", json={"project": "proj", "name": "e1", "vote": 1})
        events = (await client.get("/api/activity")).json()["events"]
        assert len(events) == 1
        assert events[0]["tool"] == "vote_entity"
        assert events[0]["kind"] == "update"
        assert events[0]["entities"] == ["e1"]
        assert events[0]["project"] == "proj"

    @pytest.mark.anyio
    async def test_invalid_vote_value_rejected(
        self, client: httpx.AsyncClient, db: DatabaseManager
    ) -> None:
        db.create_entities("proj", [{"name": "e1", "entityType": "pattern", "observations": ["o"]}])
        for bad in (5, 0):
            resp = await client.post(
                "/api/vote", json={"project": "proj", "name": "e1", "vote": bad}
            )
            assert resp.status_code == 400
            assert resp.json() == {"error": "vote must be 1 or -1"}
        graph = (await client.get("/api/graph", params={"project": "proj"})).json()
        assert graph["entities"][0]["vote_score"] == 0
        assert (await client.get("/api/activity")).json() == {"events": [], "seq": 0}

    @pytest.mark.anyio
    async def test_boolean_vote_rejected(
        self, client: httpx.AsyncClient, db: DatabaseManager
    ) -> None:
        db.create_entities("proj", [{"name": "e1", "entityType": "pattern", "observations": ["o"]}])
        resp = await client.post("/api/vote", json={"project": "proj", "name": "e1", "vote": True})
        assert resp.status_code == 400
        assert resp.json() == {"error": "vote must be 1 or -1"}

    @pytest.mark.anyio
    async def test_unknown_entity_returns_404(self, client: httpx.AsyncClient) -> None:
        resp = await client.post("/api/vote", json={"project": "proj", "name": "ghost", "vote": 1})
        assert resp.status_code == 404
        assert resp.json() == {"error": "entity not found"}
        assert (await client.get("/api/activity")).json() == {"events": [], "seq": 0}

    @pytest.mark.anyio
    async def test_malformed_body_returns_400(self, client: httpx.AsyncClient) -> None:
        resp = await client.post("/api/vote", content=b"not json")
        assert resp.status_code == 400
        assert resp.json() == {"error": "invalid JSON body"}

    @pytest.mark.anyio
    async def test_missing_fields_returns_400(self, client: httpx.AsyncClient) -> None:
        resp = await client.post("/api/vote", json={"vote": 1})
        assert resp.status_code == 400
        assert resp.json() == {"error": "project and name are required"}
