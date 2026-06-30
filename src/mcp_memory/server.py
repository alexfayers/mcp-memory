"""FastMCP server exposing the memory knowledge graph as MCP tools."""

from __future__ import annotations

import functools
import inspect
import os
from typing import TYPE_CHECKING, Any, ParamSpec, TypeVar, cast

from mcp.server.fastmcp import FastMCP

from .activity import record_tool
from .config import get_db_path
from .database import DatabaseManager
from .models import VALID_RELATION_TYPES, Entity, Relation, normalize_relation_type
from .visualise import register_visualise_routes

if TYPE_CHECKING:
    from collections.abc import Callable

_P = ParamSpec("_P")
_R = TypeVar("_R")


def _track(fn: Callable[_P, _R]) -> Callable[_P, _R]:
    """Record each tool call's activity without altering its behaviour or schema."""

    @functools.wraps(fn)
    def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        result = fn(*args, **kwargs)
        try:
            bound = inspect.signature(fn).bind_partial(*args, **kwargs)
            record_tool(fn.__name__, dict(bound.arguments), result)
        except Exception:  # noqa: S110 - instrumentation must never break a tool call
            pass
        return result

    return wrapper


mcp = FastMCP(
    "mcp-memory",
    stateless_http=True,
    json_response=True,
    port=int(os.environ.get("MCP_MEMORY_PORT", "8000")),
)

RELATION_EXEMPT_TYPES = frozenset({"user-preferences", "project"})
VALID_ENTITY_TYPES = frozenset(
    {
        "project",
        "feature",
        "task",
        "user-preferences",
        "pattern",
        "knowledge",
    }
)

# Tool descriptions
CREATE_ENTITIES_DESC = (
    "Create or update entities with observations in the knowledge graph. "
    "All data is scoped to the given project. "
    "create_entities OVERWRITES all observations; use add_observations to append safely. "
    "Valid entity types: project, feature, task, user-preferences, pattern, knowledge. "
    "Each entity name MUST start with its type prefix (e.g. task/<id>, feature/<area>); "
    "a 'project' entity MUST be named exactly 'project/<project>' (one root per scope). "
    "Non-exempt entity types (everything except user-preferences and project) MUST include at "
    "least one relation. "
    "Each entity dict must have keys: name (str), entityType (str), observations (list[str]). "
    "Optional keys: status (str), relations (list of {target, type} dicts)."
)
SEARCH_NODES_DESC = (
    "Search entities and relations by text query within a project. "
    "Uses FTS5 full-text search with BM25 relevance ranking, weighted by recency "
    "(newer entities rank higher). "
    "A multi-word query matches entities containing ANY of the terms by default, with "
    "entities matching more terms ranked first; pass match_all=true to require ALL terms. "
    "Optionally filter by entityType, status, and/or date range "
    "(start_date/end_date support relative formats like '7d', '2w', '3m' and ISO dates). "
    "Use compact=true to omit observations for a lightweight summary."
)
READ_GRAPH_DESC = (
    "Get the most recent entities and their relations for a project. "
    "Returns up to 10 recent entities ordered by creation time. "
    "Use compact=true to omit observations for a lightweight summary."
)
CREATE_RELATIONS_DESC = (
    "Create relations between entities in a project. "
    "Relations are the core of the graph model. Each relation has source, target, and type."
)
DELETE_ENTITY_DESC = (
    "Delete an entity and all its associated observations and relations from a project."
)
DELETE_RELATION_DESC = "Delete a specific relation between two entities in a project."
GET_ENTITY_WITH_RELATIONS_DESC = (
    "Get an entity along with all its relations and related entities within a project. "
    "Traverses the graph to discover linked context."
)
ADD_OBSERVATIONS_DESC = (
    "Append observations to an existing entity without overwriting. "
    "Skips duplicates. Throws if the entity does not exist."
)
DELETE_OBSERVATIONS_DESC = (
    "Delete specific observations from an existing entity by exact content match. "
    "Returns the count of deleted observations. Throws if the entity does not exist."
)
SET_ENTITY_STATUS_DESC = (
    "Set or clear the status of an entity. "
    "Valid statuses: planned, in-progress, blocked, resolved, archived. Use null to clear."
)
SEARCH_RELATED_NODES_DESC = (
    "Get an entity along with all its directly related entities within a project. "
    "Optionally filter by entityType and/or relationType."
)
LIST_PROJECTS_DESC = "List all project names in the knowledge graph."

SET_PROJECT_PATHS_DESC = (
    "Register filesystem paths for a project. When the working directory falls under a "
    "registered path, that project becomes the active memory scope. Replaces any paths "
    "previously registered to the project. A path can belong to only one project."
)
GET_PROJECT_FOR_PATH_DESC = (
    "Return the project whose registered path contains the given filesystem path, "
    "or null if none match. The longest matching registered path wins."
)
LIST_PROJECT_PATHS_DESC = "List all registered (project, path) mappings in the knowledge graph."
GET_PATHS_FOR_PROJECT_DESC = (
    "Return the filesystem path(s) registered to a project, or an empty list if the project "
    "is unknown or has no registered paths. Read-only: does not create the project."
)
GET_PATHS_FOR_ENTITY_DESC = (
    "Find which project(s) contain an entity with the given name and return their registered "
    "filesystem paths, grouped by project. Entity names are unique only within a project, so "
    "the same name may appear in several projects. A matching project with no registered path "
    "is still listed (with an empty paths list). Returns an empty matches list if no entity "
    "has that name."
)
DELETE_PROJECT_DESC = (
    "Delete an empty project and its registered paths. Refuses to delete the 'global' "
    "project or any project that still has entities - delete those entities first."
)
MOVE_PROJECT_ENTITIES_DESC = (
    "Move all entities (with their observations and relations) from one project scope into "
    "another. Useful for consolidating a mis-scoped folder-name project into its real project. "
    "Fails if any entity name exists in both scopes."
)

SEARCH_ALL_PROJECTS_DESC = (
    "Search entities and relations across ALL projects in a single call. "
    "Returns results grouped by project name. "
    "Uses FTS5 full-text search with BM25 relevance ranking, weighted by recency. "
    "A multi-word query matches entities containing ANY of the terms by default, with "
    "entities matching more terms ranked first; pass match_all=true to require ALL terms. "
    "Optionally filter by entityType, status, and/or date range "
    "(start_date/end_date support relative formats like '7d', '2w', '3m' and ISO dates). "
    "Use compact=true to omit observations for a lightweight summary."
)

_db: DatabaseManager | None = None


def _get_db() -> DatabaseManager:
    """Lazily initialise and return the database manager."""
    global _db  # noqa: PLW0603
    if _db is None:
        _db = DatabaseManager(get_db_path())
    return _db


_GLOBAL_PROJECT = "global"


def _ensure_project_root(db: DatabaseManager, project: str) -> None:
    """Auto-create a project/<name> root entity if it doesn't exist yet."""
    if project == _GLOBAL_PROJECT:
        return
    root_name = f"project/{project}"
    try:
        db.get_entity(project, root_name)
    except ValueError:
        entity: dict[str, object] = {
            "name": root_name,
            "entityType": "project",
            "observations": [f"Root entity for {project}"],
        }
        db.create_entities(project, [entity])


register_visualise_routes(mcp, _get_db)


# Transitional: surfaces legacy relation types that predate the canonical
# vocabulary so the agent recreates them. Remove once all memory DBs conform
# (tracked by task/remove-relation-type-warning).
def _attach_relation_type_warnings(result: dict[str, Any]) -> dict[str, Any]:
    """Flag any relation types in a read result that fall outside the canonical vocabulary."""
    if "error" in result:
        return result
    relations = result.get("relations")
    if not isinstance(relations, list):
        return result
    offenders = sorted(
        {
            rel.relation_type
            for rel in relations
            if isinstance(rel, Relation) and rel.relation_type not in VALID_RELATION_TYPES
        }
    )
    if offenders:
        result["relationTypeWarnings"] = offenders
    return result


def _validate_relation_type(raw: str) -> str:
    """Normalize a relation type and enforce the canonical vocabulary."""
    relation_type = normalize_relation_type(raw)
    if relation_type not in VALID_RELATION_TYPES:
        raise ValueError(
            f"Invalid relation type '{raw}' (normalized to '{relation_type}'). "
            f"Valid types: {sorted(VALID_RELATION_TYPES)}"
        )
    return relation_type


def _extract_relation_type(rel: dict[str, str]) -> str:
    """Extract and canonicalize a relation type, accepting 'type' or 'relation_type' keys."""
    if "type" in rel:
        return _validate_relation_type(str(rel["type"]))
    if "relation_type" in rel:
        return _validate_relation_type(str(rel["relation_type"]))
    raise KeyError("Relation must have a 'type' or 'relation_type' key.")


def _validate_entity_type_and_name(project: str, entity_type: object, name: str) -> None:
    """Enforce entity-type validity and the type-prefix naming convention.

    Names must start with their type prefix (e.g. ``task/``), and a ``project`` entity
    must be named exactly ``project/<project>`` so each scope keeps a single root.
    """
    if not isinstance(entity_type, str) or not entity_type:
        raise ValueError(f"Entity type must be a non-empty string, got: {entity_type!r}")
    if entity_type not in VALID_ENTITY_TYPES:
        raise ValueError(
            f"Invalid entity type '{entity_type}'. Valid types: {sorted(VALID_ENTITY_TYPES)}"
        )
    if not name.startswith(f"{entity_type}/"):
        raise ValueError(
            f"Entity name '{name}' must start with '{entity_type}/' "
            f"(convention: <entityType>/<identifier>)."
        )
    if entity_type == "project" and name != f"project/{project}":
        raise ValueError(
            f"A 'project' entity must be named 'project/{project}' for scope '{project}', "
            f"got '{name}'. Use task/, feature/, etc. for work items."
        )


def _validate_and_extract_relations(
    project: str,
    entities: list[dict[str, str | list[str] | list[dict[str, str]] | None]],
) -> list[Relation]:
    """Validate entity types and names, and extract inline relations."""
    all_relations: list[Relation] = []
    for entity_data in entities:
        entity_type = entity_data.get("entityType", "")
        name = str(entity_data.get("name", ""))
        relations_raw = entity_data.get("relations")

        _validate_entity_type_and_name(project, entity_type, name)

        if entity_type not in RELATION_EXEMPT_TYPES:
            if not relations_raw or not isinstance(relations_raw, list):
                raise ValueError(
                    f"Entity type '{entity_type}' requires at least one relation. "
                    f"Only {sorted(RELATION_EXEMPT_TYPES)} are exempt."
                )

        if isinstance(relations_raw, list):
            for rel in relations_raw:
                if isinstance(rel, dict):
                    all_relations.append(
                        Relation(
                            source=str(rel.get("source", name)),
                            target=str(rel["target"]),
                            relation_type=_extract_relation_type(rel),
                        )
                    )
    return all_relations


@mcp.tool(description=CREATE_ENTITIES_DESC)
@_track
def create_entities(
    project: str,
    entities: list[dict[str, str | list[str] | list[dict[str, str]] | None]],
) -> dict[str, str]:
    """Create or update entities with observations, enforcing relation requirements."""
    try:
        db = _get_db()
        _ensure_project_root(db, project)
        all_relations = _validate_and_extract_relations(project, entities)

        for entity_data in entities:
            name = str(entity_data.get("name", ""))
            if project == _GLOBAL_PROJECT:
                conflict = db.entity_exists_outside_project(name, _GLOBAL_PROJECT)
                if conflict:
                    raise ValueError(
                        f"Entity '{name}' already exists in project '{conflict}'. "
                        f"Cannot duplicate in global scope."
                    )
            elif db.entity_exists_in_project(name, _GLOBAL_PROJECT):
                raise ValueError(
                    f"Entity '{name}' already exists in global scope. "
                    f"Cannot duplicate in project '{project}'."
                )

        db.create_entities(project, entities)  # type: ignore[arg-type]

        if all_relations:
            db.create_relations(project, all_relations)

        return {"message": f"Created {len(entities)} entities in project '{project}'."}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool(description=SEARCH_NODES_DESC)
@_track
def search_nodes(
    project: str,
    query: str,
    limit: int = 10,
    entityType: str | None = None,
    status: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    compact: bool = False,
    match_all: bool = False,
) -> dict[str, object]:
    """Search entities using FTS5 full-text search with recency-weighted BM25 ranking."""
    try:
        db = _get_db()
        return db.search_nodes(  # type: ignore[return-value]
            project,
            query,
            limit=limit,
            entity_type=entityType,
            status=status,  # type: ignore[arg-type]
            start_date=start_date,
            end_date=end_date,
            compact=compact,
            match_all=match_all,
        )
    except Exception as e:
        return {"error": str(e)}


@mcp.tool(description=READ_GRAPH_DESC)
@_track
def read_graph(
    project: str,
    status: str | None = None,
    compact: bool = False,
) -> dict[str, object]:
    """Return the most recent entities and their relations for a project."""
    try:
        db = _get_db()
        result: dict[str, Any] = db.read_graph(project, status=status, compact=compact)  # type: ignore[arg-type]
        return _attach_relation_type_warnings(result)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool(description=LIST_PROJECTS_DESC)
@_track
def list_projects() -> dict[str, object]:
    """List all project names in the knowledge graph."""
    try:
        db = _get_db()
        return {"projects": db.list_projects()}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool(description=SET_PROJECT_PATHS_DESC)
@_track
def set_project_paths(
    project: str,
    paths: list[str],
) -> dict[str, object]:
    """Register filesystem paths for a project, replacing any existing ones."""
    try:
        db = _get_db()
        _ensure_project_root(db, project)
        db.set_project_paths(project, paths)
        return {"project": project, "paths": db.get_paths_for_project(project)}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool(description=GET_PROJECT_FOR_PATH_DESC)
@_track
def get_project_for_path(path: str) -> dict[str, object]:
    """Return the project associated with a filesystem path, or null."""
    try:
        db = _get_db()
        return {"project": db.get_project_for_path(path)}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool(description=LIST_PROJECT_PATHS_DESC)
@_track
def list_project_paths() -> dict[str, object]:
    """List all registered project-path mappings."""
    try:
        db = _get_db()
        return {"mappings": [{"project": n, "path": p} for n, p in db.list_project_paths()]}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool(description=GET_PATHS_FOR_PROJECT_DESC)
@_track
def get_paths_for_project(project: str) -> dict[str, object]:
    """Return the registered filesystem path(s) for a project."""
    try:
        db = _get_db()
        return {"paths": db.get_paths_for_project(project)}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool(description=GET_PATHS_FOR_ENTITY_DESC)
@_track
def get_paths_for_entity(name: str) -> dict[str, object]:
    """Return the project(s) and registered path(s) for an entity name."""
    try:
        db = _get_db()
        return {
            "matches": [
                {"project": project, "paths": paths}
                for project, paths in db.paths_for_entity_name(name)
            ]
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool(description=MOVE_PROJECT_ENTITIES_DESC)
@_track
def move_project_entities(source: str, target: str) -> dict[str, object]:
    """Move all entities from one project scope into another."""
    try:
        db = _get_db()
        moved = db.move_project_entities(source, target)
        return {"message": f"Moved {moved} entities from '{source}' to '{target}'.", "moved": moved}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool(description=DELETE_PROJECT_DESC)
@_track
def delete_project(project: str) -> dict[str, str]:
    """Delete an empty project and its registered paths."""
    try:
        db = _get_db()
        db.delete_project(project)
        return {"message": f"Deleted project '{project}'."}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool(description=SEARCH_ALL_PROJECTS_DESC)
@_track
def search_all_projects(
    query: str,
    limit: int = 50,
    entityType: str | None = None,
    status: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    compact: bool = False,
    match_all: bool = False,
) -> dict[str, object]:
    """Search entities across all projects, returning results grouped by project."""
    try:
        db = _get_db()
        result = db.search_nodes(
            None,
            query,
            limit=limit,
            entity_type=entityType,
            status=status,  # type: ignore[arg-type]
            start_date=start_date,
            end_date=end_date,
            compact=compact,
            match_all=match_all,
        )

        grouped: dict[str, dict[str, list[object]]] = {}
        entity_names_by_project: dict[str, set[str]] = {}
        for entity in cast("list[Entity]", result["entities"]):
            project_name = entity.project_name or "unknown"
            if project_name not in grouped:
                grouped[project_name] = {"entities": [], "relations": []}
                entity_names_by_project[project_name] = set()
            grouped[project_name]["entities"].append(entity)
            entity_names_by_project[project_name].add(entity.name)

        relations = cast("list[Relation]", result["relations"])
        for relation in relations:
            for project_name, names in entity_names_by_project.items():
                if relation.source in names or relation.target in names:
                    grouped[project_name]["relations"].append(relation)
                    break

        grouped_result: dict[str, Any] = {"results": grouped, "relations": relations}
        return _attach_relation_type_warnings(grouped_result)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool(description=CREATE_RELATIONS_DESC)
@_track
def create_relations(
    project: str,
    relations: list[dict[str, str]],
) -> dict[str, str]:
    """Create relations between entities in a project."""
    try:
        db = _get_db()
        relation_objects = [
            Relation(
                source=rel["source"],
                target=rel["target"],
                relation_type=_extract_relation_type(rel),
            )
            for rel in relations
        ]
        db.create_relations(project, relation_objects)
        return {"message": f"Created {len(relation_objects)} relations in project '{project}'."}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool(description=DELETE_ENTITY_DESC)
@_track
def delete_entity(
    project: str,
    name: str,
) -> dict[str, str]:
    """Delete an entity and all its observations and relations."""
    try:
        db = _get_db()
        db.delete_entity(project, name)
        return {"message": f"Deleted entity '{name}' from project '{project}'."}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool(description=DELETE_RELATION_DESC)
@_track
def delete_relation(
    project: str,
    source: str,
    target: str,
    type: str,
) -> dict[str, str]:
    """Delete a specific relation between two entities."""
    try:
        db = _get_db()
        db.delete_relation(project, source, target, type)
        return {
            "message": (
                f"Deleted relation '{source}' -> '{target}' ({type}) from project '{project}'."
            ),
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool(description=GET_ENTITY_WITH_RELATIONS_DESC)
@_track
def get_entity_with_relations(
    project: str,
    name: str,
) -> dict[str, object]:
    """Get an entity with all its relations and related entities."""
    try:
        db = _get_db()
        result: dict[str, Any] = db.get_entity_with_relations(project, name)
        return _attach_relation_type_warnings(result)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool(description=ADD_OBSERVATIONS_DESC)
@_track
def add_observations(
    project: str,
    entityName: str,
    observations: list[str],
) -> dict[str, str | int]:
    """Append deduplicated observations to an existing entity."""
    try:
        db = _get_db()
        count = db.add_observations(project, entityName, observations)
        return {"message": f"Added {count} observations to '{entityName}'.", "count": count}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool(description=DELETE_OBSERVATIONS_DESC)
@_track
def delete_observations(
    project: str,
    entityName: str,
    observations: list[str],
) -> dict[str, str | int]:
    """Delete observations by exact content match."""
    try:
        db = _get_db()
        count = db.delete_observations(project, entityName, observations)
        return {"message": f"Deleted {count} observations from '{entityName}'.", "count": count}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool(description=SET_ENTITY_STATUS_DESC)
@_track
def set_entity_status(
    project: str,
    name: str,
    status: str | None,
) -> dict[str, str]:
    """Set or clear the status of an entity."""
    try:
        db = _get_db()
        db.set_entity_status(project, name, status)  # type: ignore[arg-type]
        return {"message": f"Status of '{name}' set to {status!r}."}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool(description=SEARCH_RELATED_NODES_DESC)
@_track
def search_related_nodes(
    project: str,
    name: str,
    entityType: str | None = None,
    relationType: str | None = None,
) -> dict[str, object]:
    """Get an entity with filtered relations and related entities."""
    try:
        db = _get_db()
        result: dict[str, Any] = db.search_related_nodes(
            project, name, entity_type=entityType, relation_type=relationType
        )
        return _attach_relation_type_warnings(result)
    except Exception as e:
        return {"error": str(e)}


def main() -> None:
    """Run the MCP server with streamable HTTP transport."""
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
