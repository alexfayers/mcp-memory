"""Graph visualisation endpoint for the MCP memory server."""

from __future__ import annotations

import importlib.resources
from typing import TYPE_CHECKING, cast

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse

from . import activity

if TYPE_CHECKING:
    from collections.abc import Callable

    from mcp.server.fastmcp import FastMCP

    from .database import DatabaseManager
    from .models import Entity, Relation

_VISUALISE_HTML = (
    importlib.resources.files("mcp_memory").joinpath("templates/visualise.html").read_text()
)


def get_projects(db: DatabaseManager) -> list[str]:
    """Return all project names from the database."""
    return db.list_projects()


def get_project_paths(db: DatabaseManager) -> dict[str, list[str]]:
    """Return registered on-disk paths grouped by project name."""
    grouped: dict[str, list[str]] = {}
    for project, path in db.list_project_paths():
        grouped.setdefault(project, []).append(path)
    return grouped


def get_all_graph_data(
    db: DatabaseManager, project: str | None = None
) -> dict[str, list[dict[str, object]]]:
    """Return all entities and relations for a project (or all projects) as serialisable dicts."""
    if project:
        where_clause = "WHERE e.project_id = ?"
        params: tuple[object, ...] = (db._get_or_create_project_id(project),)
    else:
        where_clause = ""
        params = ()

    entity_rows = db._db.execute(
        "SELECT e.id, e.name, et.name AS entity_type, e.status, e.project_id, p.name AS project, "
        "e.created_at, e.updated_at, e.vote_score "
        "FROM entities e "
        "JOIN entity_types et ON e.entity_type_id = et.id "
        "JOIN projects p ON e.project_id = p.id " + where_clause,
        params,
    ).fetchall()

    entities: list[dict[str, object]] = []
    entity_ids: list[int] = []
    project_ids: set[int] = set()
    for row in entity_rows:
        entities.append(
            {
                "name": row["name"],
                "entity_type": row["entity_type"],
                "project": row["project"],
                "status": row["status"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "vote_score": row["vote_score"],
                "observations": db._get_observations(row["id"]),
            }
        )
        entity_ids.append(row["id"])
        project_ids.add(row["project_id"])

    all_relations: list[dict[str, object]] = []
    for pid in project_ids:
        for r in db._get_relations_for_entities(pid, entity_ids):
            all_relations.append(
                {"source": r.source, "target": r.target, "relation_type": r.relation_type}
            )

    return {"entities": entities, "relations": all_relations}


def search_graph(
    db: DatabaseManager,
    query: str,
    project: str | None = None,
    match_all: bool = False,
    limit: int | None = None,
) -> dict[str, list[dict[str, object]]]:
    """Run the same recency-weighted BM25 search the LLM tools use, as serialisable dicts.

    Faithful to the MCP search tools: calls db.search_nodes directly with the tool default
    limit for the chosen scope (10 when project-scoped, 50 for all projects). Each entity
    carries a 1-based rank matching its position in the ranked list.
    """
    effective_limit = limit if limit is not None else (10 if project else 50)
    result = db.search_nodes(project, query, limit=effective_limit, match_all=match_all)

    entities: list[dict[str, object]] = [
        {
            "rank": position,
            "name": entity.name,
            "entity_type": entity.entity_type,
            "project": entity.project_name,
            "status": entity.status,
            "created_at": entity.created_at,
            "updated_at": entity.updated_at,
            "vote_score": entity.vote_score,
            "observations": entity.observations,
        }
        for position, entity in enumerate(cast("list[Entity]", result["entities"]), start=1)
    ]
    relations: list[dict[str, object]] = [
        {"source": r.source, "target": r.target, "relation_type": r.relation_type}
        for r in cast("list[Relation]", result["relations"])
    ]

    return {"entities": entities, "relations": relations}


def register_visualise_routes(mcp: FastMCP, get_db: Callable[[], DatabaseManager]) -> None:
    """Register the /visualise and /api/* custom routes on the FastMCP server."""

    @mcp.custom_route("/api/projects", methods=["GET"], include_in_schema=False)  # type: ignore[untyped-decorator]
    async def api_projects(request: Request) -> JSONResponse:
        return JSONResponse(get_projects(get_db()))

    @mcp.custom_route("/api/project-paths", methods=["GET"], include_in_schema=False)  # type: ignore[untyped-decorator]
    async def api_project_paths(request: Request) -> JSONResponse:
        return JSONResponse(get_project_paths(get_db()))

    @mcp.custom_route("/api/graph", methods=["GET"], include_in_schema=False)  # type: ignore[untyped-decorator]
    async def api_graph(request: Request) -> JSONResponse:
        project = request.query_params.get("project") or None
        return JSONResponse(get_all_graph_data(get_db(), project))

    @mcp.custom_route("/api/search", methods=["GET"], include_in_schema=False)  # type: ignore[untyped-decorator]
    async def api_search(request: Request) -> JSONResponse:
        query = request.query_params.get("q") or ""
        project = request.query_params.get("project") or None
        match_all = request.query_params.get("match_all", "").lower() in ("1", "true", "yes")
        return JSONResponse(search_graph(get_db(), query, project, match_all=match_all))

    @mcp.custom_route("/api/activity", methods=["GET"], include_in_schema=False)  # type: ignore[untyped-decorator]
    async def api_activity(request: Request) -> JSONResponse:
        raw = request.query_params.get("since")
        try:
            since = int(raw) if raw is not None else 0
        except ValueError:
            since = 0
        return JSONResponse({"events": activity.recent(since), "seq": activity.latest_seq()})

    @mcp.custom_route("/visualise", methods=["GET"], include_in_schema=False)  # type: ignore[untyped-decorator]
    async def visualise_page(request: Request) -> HTMLResponse:
        return HTMLResponse(_VISUALISE_HTML)
