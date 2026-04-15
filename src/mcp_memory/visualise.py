"""Graph visualisation endpoint for the MCP memory server."""

from __future__ import annotations

import importlib.resources
from typing import TYPE_CHECKING

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse

if TYPE_CHECKING:
    from collections.abc import Callable

    from mcp.server.fastmcp import FastMCP

    from .database import DatabaseManager

_VISUALISE_HTML = (
    importlib.resources.files("mcp_memory").joinpath("templates/visualise.html").read_text()
)


def get_projects(db: DatabaseManager) -> list[str]:
    """Return all project names from the database."""
    rows = db._db.execute("SELECT name FROM projects ORDER BY name").fetchall()
    return [row["name"] for row in rows]


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
        "SELECT e.id, e.name, et.name AS entity_type, e.status, e.project_id "
        "FROM entities e "
        "JOIN entity_types et ON e.entity_type_id = et.id " + where_clause,
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
                "status": row["status"],
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


def register_visualise_routes(mcp: FastMCP, get_db: Callable[[], DatabaseManager]) -> None:
    """Register the /visualise and /api/* custom routes on the FastMCP server."""

    @mcp.custom_route("/api/projects", methods=["GET"], include_in_schema=False)  # type: ignore[untyped-decorator]
    async def api_projects(request: Request) -> JSONResponse:
        return JSONResponse(get_projects(get_db()))

    @mcp.custom_route("/api/graph", methods=["GET"], include_in_schema=False)  # type: ignore[untyped-decorator]
    async def api_graph(request: Request) -> JSONResponse:
        project = request.query_params.get("project") or None
        return JSONResponse(get_all_graph_data(get_db(), project))

    @mcp.custom_route("/visualise", methods=["GET"], include_in_schema=False)  # type: ignore[untyped-decorator]
    async def visualise_page(request: Request) -> HTMLResponse:
        return HTMLResponse(_VISUALISE_HTML)
