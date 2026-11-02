"""The server's generic /api/call route, which lets the hooks and CLI use the database through the server."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from pathlib import Path
import textwrap
from typing import TYPE_CHECKING, Any

from starlette.responses import JSONResponse

from .audit import audit_graph, propose_plan
from .eval import evaluate_cached_async
from .export_import import export_database, import_projects, validate_export
from .metrics import usage_report
from .path_resolver import normalize_path
from .visualise import get_project_paths

if TYPE_CHECKING:
    from collections.abc import Callable

    from mcp.server.mcpserver import MCPServer
    from starlette.requests import Request

    from .models import Entity
    from .storage import Storage

_START_OBSERVATION_CHARS = 80
_START_PROJECT_RELATIONS = 15


def _safe_project(anchor: Path, basename: str, db: Storage) -> str | None:
    """Return the project an anchor may be safely registered to, or None.

    Only identifies a project that already exists, never invents one:
    - if the anchor (or an ancestor) is already registered, reuse that project - this
      also means an existing mapping is never overwritten;
    - else if a sibling package under the anchor is already mapped to a single project,
      reuse it so the whole workspace collapses to that scope;
    - else if a project scope already exists whose name matches the repository-root
      folder name (case-insensitively), pin to that project;
    - otherwise return None so the caller prompts for deliberate registration.
    """
    existing = db.projects.get_project_for_path(str(anchor))
    if existing:
        return existing
    anchor_norm = Path(normalize_path(str(anchor)))
    below = {project for project, registered in db.projects.paths() if Path(registered).is_relative_to(anchor_norm)}
    if len(below) == 1:
        return next(iter(below))
    for project in db.projects.names():
        if project.casefold() == basename.casefold():
            return project
    return None


def _entity_lines(entity: Entity) -> tuple[str, str]:
    """Return an entity's line without and with its first observation."""
    details = [entity.entity_type]
    if entity.status:
        details.append(entity.status)
    brief = f"- {entity.name} ({', '.join(details)})"
    if not entity.observations:
        return brief, brief
    observation = textwrap.shorten(entity.observations[0].content, _START_OBSERVATION_CHARS, placeholder="...")
    return brief, f"{brief}: {observation}"


def _recent_memory(db: Storage, repo: str, left: int | None) -> str:
    """Return the recent memory of `global` and the repo project, fitted to the characters left.

    Every entity name is included. First observations, then the repo project entity's relations,
    are added in order while they fit.

    Args:
        db: The database to read.
        repo: The workspace's memory project.
        left: The characters available, or None for no limit.

    Returns:
        The rendered memory.
    """
    project_entity = f"project/{repo}"
    global_entities = db.reads.recent("global", max_observation_chars=0)["entities"]
    repo_entities = db.reads.recent(repo, max_observation_chars=0)["entities"]
    try:
        graph = db.reads.get_entity_with_relations(repo, project_entity, max_observation_chars=0)
    except ValueError:
        graph = None
    relations: list[str] = []
    if graph is not None:
        repo_entities = [graph["entity"], *(entity for entity in repo_entities if entity.name != project_entity)]
        relations = [
            f"{relation.source} -{relation.relation_type}-> {relation.target}"
            for relation in graph["relations"][:_START_PROJECT_RELATIONS]
        ]
    lines: list[tuple[str, str]] = []
    for heading, entities, extra in (("## global", global_entities, []), (f"\n## {repo}", repo_entities, relations)):
        if entities:
            lines.append((heading, heading))
            lines.extend(_entity_lines(entity) for entity in entities)
            lines.extend(("", relation) for relation in extra)
    shown = [brief for brief, _ in lines]
    for index, (_, full) in enumerate(lines):
        fuller = [*shown[:index], full, *shown[index + 1 :]]
        if left is not None and len("\n".join(line for line in fuller if line)) > left:
            break
        shown = fuller
    return "\n".join(line for line in shown if line).strip("\n")


def _register_workspace(db: Storage, args: dict[str, Any]) -> str | None:
    project = _safe_project(Path(args["anchor"]), args["basename"], db)
    if project is not None:
        db.projects.add_path(project, args["anchor"])
    return project


def _audit(db: Storage, args: dict[str, Any]) -> object:
    report = audit_graph(db, args["project"])
    return {"steps": propose_plan(db, report)} if args["plan"] else report


def _import(db: Storage, args: dict[str, Any]) -> dict[str, Any]:
    summary = import_projects(db, validate_export(args["data"]), args["projects"], dry_run=args["dry_run"])
    return asdict(summary)


async def _eval(db: Storage, args: dict[str, Any]) -> dict[str, Any]:
    return asdict(await evaluate_cached_async(db, args["k"], args["since"], args["min_content_tokens"]))


_OPERATIONS: dict[str, Callable[[Storage, dict[str, Any]], Any]] = {
    "project-paths": lambda db, _args: get_project_paths(db),
    "group-members": lambda db, args: db.projects.group_members(args["project"]),
    "recent-memory": lambda db, args: _recent_memory(db, args["project"], args["left"]),
    "register-workspace": _register_workspace,
    "audit": _audit,
    "eval": _eval,
    "metrics": lambda db, args: asdict(usage_report(db, args["since"])),
    "export": lambda db, _args: export_database(db),
    "import": _import,
}


def register_api_routes(mcp: MCPServer, get_db: Callable[[], Storage]) -> None:
    """Register ``POST /api/call/<operation>``, which runs a named operation on the database."""

    @mcp.custom_route("/api/call/{operation}", methods=["POST"], include_in_schema=False)  # type: ignore[untyped-decorator]
    async def api_call(request: Request) -> JSONResponse:
        operation = _OPERATIONS.get(request.path_params["operation"])
        if operation is None:
            return JSONResponse({"error": "unknown operation"}, status_code=404)
        try:
            args = await request.json()
            if asyncio.iscoroutinefunction(operation):
                return JSONResponse(await operation(get_db(), args))
            return JSONResponse(await asyncio.to_thread(operation, get_db(), args))
        except KeyError as exc:
            return JSONResponse({"error": f"missing argument: {exc.args[0]}"}, status_code=400)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
