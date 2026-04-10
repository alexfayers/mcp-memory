"""Tests for the graph visualisation data functions and routes."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from mcp.server.fastmcp import FastMCP

from mcp_memory.database import DatabaseManager
from mcp_memory.models import Relation
from mcp_memory.visualise import get_all_graph_data, get_projects, register_visualise_routes


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
        result = get_all_graph_data(db, "proj")
        assert len(result["entities"]) == 1
        entity = result["entities"][0]
        assert entity["name"] == "e1"
        assert entity["entity_type"] == "task"
        assert entity["observations"] == ["obs1", "obs2"]
        assert entity["status"] == "planned"

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
