"""Graph visualisation endpoint for the MCP memory server."""

from __future__ import annotations

import importlib.resources
from dataclasses import asdict
from typing import TYPE_CHECKING

import httpx
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse

from . import activity, dream_status, recall_status
from .config import get_agent_url
from .eval import evaluate_cached_async
from .metrics import usage_over_time

if TYPE_CHECKING:
    from collections.abc import Callable

    from mcp.server.fastmcp import FastMCP

    from .database import DatabaseManager

__all__ = ["httpx"]

_VISUALISE_HTML = (
    importlib.resources.files("mcp_memory").joinpath("templates/visualise.html").read_text()
)

# The trigger only kicks off a background pass on the agent and returns at once, so
# a short timeout is enough; it bounds how long an unreachable agent blocks the UI.
_TRIGGER_TIMEOUT_SECONDS = 5.0


def get_projects(db: DatabaseManager) -> list[str]:
    """Return all project names from the database."""
    return db.list_projects()


def get_project_paths(db: DatabaseManager) -> dict[str, list[str]]:
    """Return registered on-disk paths grouped by project name."""
    grouped: dict[str, list[str]] = {}
    for project, path in db.list_project_paths():
        grouped.setdefault(project, []).append(path)
    return grouped


def get_project_groups(db: DatabaseManager) -> dict[str, list[str]]:
    """Return group memberships grouped by project name."""
    grouped: dict[str, list[str]] = {}
    for project, group in db.list_project_groups():
        grouped.setdefault(project, []).append(group)
    return grouped


def get_dream_state() -> dict[str, object]:
    """Compose the persisted dream status with live idle so the UI polls it in one call.

    The dream runs in the separate memory-agent process and persists each tier's
    config, the latest pass, and any in-flight pass (``running``) to a shared marker;
    this reads that marker and pairs it with the server's own live idle time. When the
    marker is absent (the dream never ran or is disabled), ``tiers`` is empty but live
    idle is still included.
    """
    status = dream_status.read_status()
    return {
        "available": status is not None,
        "tiers": status["configs"] if status else {},
        "last_pass": status["last_pass"] if status else None,
        "running": status["running"] if status else None,
        "idle_seconds": activity.idle_seconds(),
        "last_activity": activity.last_activity(),
    }


def get_recall_state() -> dict[str, object]:
    """Return the memory-agent's recall activity for the UI: in-flight count and history.

    Recall runs in the separate memory-agent process and persists its live count and
    recent finished recalls to a shared marker; this reads that marker. There is no
    server-side signal to compose (recall does not run in mcp-memory), so an absent
    marker reports as unavailable with an empty history.
    """
    status = recall_status.read_status()
    return {
        "available": status is not None,
        "active": status["active"] if status else 0,
        "recent": status["recent"] if status else [],
    }


def get_all_graph_data(
    db: DatabaseManager, project: str | None = None
) -> dict[str, list[dict[str, object]]]:
    """Return all entities and relations for a project (or all projects) as serialisable dicts."""
    if project:
        where_clause = "WHERE e.deleted_at IS NULL AND e.project_id = ?"
        params: tuple[object, ...] = (db._get_or_create_project_id(project),)
    else:
        where_clause = "WHERE e.deleted_at IS NULL"
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
        scored = db._get_observations_full(row["id"])
        entities.append(
            {
                "name": row["name"],
                "entity_type": row["entity_type"],
                "project": row["project"],
                "status": row["status"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "vote_score": row["vote_score"],
                "observations": [
                    {
                        "content": o.content,
                        "content_hash": o.content_hash,
                        "vote_score": o.vote_score,
                    }
                    for o in scored
                ],
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
            "observations": [
                {"content": o.content, "content_hash": o.content_hash, "vote_score": o.vote_score}
                for o in entity.observations
            ],
        }
        for position, entity in enumerate(result["entities"], start=1)
    ]
    relations: list[dict[str, object]] = [
        {"source": r.source, "target": r.target, "relation_type": r.relation_type}
        for r in result["relations"]
    ]

    return {"entities": entities, "relations": relations}


async def _parse_vote_body(
    request: Request, *, require_hash: bool = False
) -> tuple[str, str, int, str | None] | JSONResponse:
    """Validate a vote request body shared by /api/vote and /api/vote-observation.

    Returns ``(project, name, vote, observation_hash)`` on success (observation_hash is None
    unless required), or a 400 JSONResponse mirroring the entity-vote validation ladder.
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    project = body.get("project")
    name = body.get("name")
    vote = body.get("vote")
    if not isinstance(project, str) or not isinstance(name, str):
        return JSONResponse({"error": "project and name are required"}, status_code=400)
    observation_hash = body.get("observationHash")
    if require_hash and not isinstance(observation_hash, str):
        return JSONResponse({"error": "observationHash is required"}, status_code=400)
    if not isinstance(vote, int) or isinstance(vote, bool) or vote not in (1, -1):
        return JSONResponse({"error": "vote must be 1 or -1"}, status_code=400)
    return project, name, vote, (observation_hash if require_hash else None)


def register_visualise_routes(mcp: FastMCP, get_db: Callable[[], DatabaseManager]) -> None:
    """Register the /visualise and /api/* custom routes on the FastMCP server."""

    @mcp.custom_route("/api/projects", methods=["GET"], include_in_schema=False)  # type: ignore[untyped-decorator]
    async def api_projects(request: Request) -> JSONResponse:
        return JSONResponse(get_projects(get_db()))

    @mcp.custom_route("/api/project-paths", methods=["GET"], include_in_schema=False)  # type: ignore[untyped-decorator]
    async def api_project_paths(request: Request) -> JSONResponse:
        return JSONResponse(get_project_paths(get_db()))

    @mcp.custom_route("/api/project-groups", methods=["GET"], include_in_schema=False)  # type: ignore[untyped-decorator]
    async def api_project_groups(request: Request) -> JSONResponse:
        return JSONResponse(get_project_groups(get_db()))

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

    @mcp.custom_route("/api/idle", methods=["GET"], include_in_schema=False)  # type: ignore[untyped-decorator]
    async def api_idle(request: Request) -> JSONResponse:
        return JSONResponse(
            {"last_activity": activity.last_activity(), "idle_seconds": activity.idle_seconds()}
        )

    @mcp.custom_route("/api/dream", methods=["GET"], include_in_schema=False)  # type: ignore[untyped-decorator]
    async def api_dream(request: Request) -> JSONResponse:
        return JSONResponse(get_dream_state())

    @mcp.custom_route("/api/dream/trigger", methods=["POST"], include_in_schema=False)  # type: ignore[untyped-decorator]
    async def api_dream_trigger(request: Request) -> JSONResponse:
        # The dream runs in the separate memory-agent process; the browser is
        # same-origin only with mcp-memory, so this forwards the trigger there.
        try:
            body = await request.body()
        except Exception:
            return JSONResponse({"started": False, "reason": "invalid request"}, status_code=400)
        try:
            async with httpx.AsyncClient(timeout=_TRIGGER_TIMEOUT_SECONDS) as client:
                agent_response = await client.post(
                    get_agent_url() + "/api/dream/trigger",
                    content=body,
                    headers={"content-type": "application/json"},
                )
        except httpx.HTTPError:
            return JSONResponse({"started": False, "reason": "agent unreachable"}, status_code=503)
        try:
            payload = agent_response.json()
        except ValueError:
            # The agent lacks the route (or something else answered): its body is not
            # the JSON envelope we relay, so report a gateway error rather than 500.
            return JSONResponse({"started": False, "reason": "agent error"}, status_code=502)
        return JSONResponse(payload, status_code=agent_response.status_code)

    @mcp.custom_route("/api/recall", methods=["GET"], include_in_schema=False)  # type: ignore[untyped-decorator]
    async def api_recall(request: Request) -> JSONResponse:
        return JSONResponse(get_recall_state())

    @mcp.custom_route("/api/usage-trend", methods=["GET"], include_in_schema=False)  # type: ignore[untyped-decorator]
    async def api_usage_trend(request: Request) -> JSONResponse:
        bucket = request.query_params.get("bucket", "day")
        since = request.query_params.get("since")
        buckets = usage_over_time(get_db(), bucket, since)
        return JSONResponse(
            {"bucket": bucket, "since": since, "series": [asdict(b) for b in buckets]}
        )

    @mcp.custom_route("/api/eval", methods=["GET"], include_in_schema=False)  # type: ignore[untyped-decorator]
    async def api_eval(request: Request) -> JSONResponse:
        k = int(request.query_params.get("k", 10))
        since = request.query_params.get("since")
        try:
            report = await evaluate_cached_async(get_db(), k=k, since=since)
        except Exception:
            return JSONResponse({"error": "eval failed"}, status_code=500)
        return JSONResponse(asdict(report))

    @mcp.custom_route("/api/vote", methods=["POST"], include_in_schema=False)  # type: ignore[untyped-decorator]
    async def api_vote(request: Request) -> JSONResponse:
        parsed = await _parse_vote_body(request)
        if isinstance(parsed, JSONResponse):
            return parsed
        project, name, vote, _ = parsed
        try:
            new_score = get_db().vote_entity(project, name, vote)
        except ValueError:
            return JSONResponse({"error": "entity not found"}, status_code=404)
        result = {"name": name, "project": project, "vote_score": new_score}
        activity.record_tool("vote", {"project": project, "name": name, "vote": vote}, result)
        return JSONResponse(result)

    @mcp.custom_route("/api/vote-observation", methods=["POST"], include_in_schema=False)  # type: ignore[untyped-decorator]
    async def api_vote_observation(request: Request) -> JSONResponse:
        parsed = await _parse_vote_body(request, require_hash=True)
        if isinstance(parsed, JSONResponse):
            return parsed
        project, name, vote, observation_hash = parsed
        try:
            new_score = get_db().vote_observation(
                project, name, vote, content_hash=observation_hash or ""
            )
        except ValueError:
            return JSONResponse({"error": "observation not found"}, status_code=404)
        result = {
            "name": name,
            "project": project,
            "observationHash": observation_hash,
            "vote_score": new_score,
        }
        activity.record_tool(
            "vote",
            {
                "project": project,
                "name": name,
                "observationHash": observation_hash,
                "vote": vote,
            },
            result,
        )
        return JSONResponse(result)

    @mcp.custom_route("/api/merge-observation", methods=["POST"], include_in_schema=False)  # type: ignore[untyped-decorator]
    async def api_merge_observation(request: Request) -> JSONResponse:
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid JSON body"}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"error": "invalid JSON body"}, status_code=400)
        project = body.get("project")
        name = body.get("name")
        if not isinstance(project, str) or not isinstance(name, str):
            return JSONResponse({"error": "project and name are required"}, status_code=400)
        source_hash = body.get("sourceHash")
        target_hash = body.get("targetHash")
        if not isinstance(source_hash, str) or not isinstance(target_hash, str):
            return JSONResponse(
                {"error": "sourceHash and targetHash are required"}, status_code=400
            )
        try:
            result = get_db().merge_observations(project, name, source_hash, target_hash)
        except ValueError:
            return JSONResponse({"error": "observation not found"}, status_code=404)
        activity.record_tool(
            "merge_observations",
            {
                "project": project,
                "entityName": name,
                "sourceHash": source_hash,
                "targetHash": target_hash,
            },
            result,
        )
        return JSONResponse({"name": name, "project": project, "merged": result["merged"]})

    @mcp.custom_route("/visualise", methods=["GET"], include_in_schema=False)  # type: ignore[untyped-decorator]
    async def visualise_page(request: Request) -> HTMLResponse:
        return HTMLResponse(_VISUALISE_HTML)
