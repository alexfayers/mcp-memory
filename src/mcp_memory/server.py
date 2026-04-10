"""FastMCP server exposing the memory knowledge graph as MCP tools."""

from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP

from .config import get_db_path
from .database import DatabaseManager
from .models import Relation
from .visualise import register_visualise_routes

mcp = FastMCP(
    "mcp-memory",
    stateless_http=True,
    json_response=True,
    port=int(os.environ.get("MCP_MEMORY_PORT", "8000")),
)

RELATION_EXEMPT_TYPES = frozenset({"user-preferences", "pattern", "project"})
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
    "Non-exempt entity types (not user-preferences or pattern) MUST include at least one relation."
)
SEARCH_NODES_DESC = (
    "Search entities and relations by text query within a project. "
    "Uses FTS5 full-text search with BM25 ranking. "
    "Optionally filter by entityType and/or status."
)
READ_GRAPH_DESC = (
    "Get the most recent entities and their relations for a project. "
    "Returns up to 10 recent entities ordered by creation time."
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


def _validate_and_extract_relations(
    entities: list[dict[str, str | list[str] | list[dict[str, str]] | None]],
) -> list[Relation]:
    """Validate entity types and extract inline relations."""
    all_relations: list[Relation] = []
    for entity_data in entities:
        entity_type = entity_data.get("entityType", "")
        relations_raw = entity_data.get("relations")

        if not isinstance(entity_type, str) or not entity_type:
            raise ValueError(f"Entity type must be a non-empty string, got: {entity_type!r}")
        if entity_type not in VALID_ENTITY_TYPES:
            raise ValueError(
                f"Invalid entity type '{entity_type}'. Valid types: {sorted(VALID_ENTITY_TYPES)}"
            )
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
                            source=str(rel["source"]),
                            target=str(rel["target"]),
                            relation_type=str(rel["type"]),
                        )
                    )
    return all_relations


@mcp.tool(description=CREATE_ENTITIES_DESC)
def create_entities(
    project: str,
    entities: list[dict[str, str | list[str] | list[dict[str, str]] | None]],
) -> dict[str, str]:
    """Create or update entities with observations, enforcing relation requirements."""
    try:
        db = _get_db()
        _ensure_project_root(db, project)
        all_relations = _validate_and_extract_relations(entities)

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
def search_nodes(
    project: str,
    query: str,
    limit: int = 10,
    entityType: str | None = None,
    status: str | None = None,
) -> dict[str, object]:
    """Search entities using FTS5 full-text search with BM25 ranking."""
    try:
        db = _get_db()
        return db.search_nodes(  # type: ignore[return-value]
            project,
            query,
            limit=limit,
            entity_type=entityType,
            status=status,  # type: ignore[arg-type]
        )
    except Exception as e:
        return {"error": str(e)}


@mcp.tool(description=READ_GRAPH_DESC)
def read_graph(
    project: str,
    status: str | None = None,
) -> dict[str, object]:
    """Return the most recent entities and their relations for a project."""
    try:
        db = _get_db()
        return db.read_graph(project, status=status)  # type: ignore[arg-type,return-value]
    except Exception as e:
        return {"error": str(e)}


@mcp.tool(description=CREATE_RELATIONS_DESC)
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
                relation_type=rel["type"],
            )
            for rel in relations
        ]
        db.create_relations(project, relation_objects)
        return {"message": f"Created {len(relation_objects)} relations in project '{project}'."}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool(description=DELETE_ENTITY_DESC)
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
def get_entity_with_relations(
    project: str,
    name: str,
) -> dict[str, object]:
    """Get an entity with all its relations and related entities."""
    try:
        db = _get_db()
        return db.get_entity_with_relations(project, name)  # type: ignore[return-value]
    except Exception as e:
        return {"error": str(e)}


@mcp.tool(description=ADD_OBSERVATIONS_DESC)
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
def search_related_nodes(
    project: str,
    name: str,
    entityType: str | None = None,
    relationType: str | None = None,
) -> dict[str, object]:
    """Get an entity with filtered relations and related entities."""
    try:
        db = _get_db()
        return db.search_related_nodes(  # type: ignore[return-value]
            project, name, entity_type=entityType, relation_type=relationType
        )
    except Exception as e:
        return {"error": str(e)}


def main() -> None:
    """Run the MCP server with streamable HTTP transport."""
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
