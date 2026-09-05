"""Interning helpers that map a name to its row id, creating the row if needed."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mcp_memory.storage.connection import Connection


def _intern(connection: Connection, table: str, name: str) -> int:
    row = connection.query_one(f"SELECT id FROM {table} WHERE name = ?", (name,))
    if row is not None:
        return int(row["id"])
    connection.write(f"INSERT OR IGNORE INTO {table} (name) VALUES (?)", (name,))
    row = connection.query_one(f"SELECT id FROM {table} WHERE name = ?", (name,))
    assert row is not None
    return int(row["id"])


def get_or_create_project_id(connection: Connection, project: str) -> int:
    return _intern(connection, "projects", project)


def get_project_id(connection: Connection, project: str) -> int | None:
    """Return the project's row id, or None when the project does not exist."""
    row = connection.query_one("SELECT id FROM projects WHERE name = ?", (project,))
    return int(row["id"]) if row else None


def get_or_create_entity_type_id(connection: Connection, entity_type: str) -> int:
    return _intern(connection, "entity_types", entity_type)


def get_or_create_relation_type_id(connection: Connection, relation_type: str) -> int:
    return _intern(connection, "relation_types", relation_type)


def get_entity_id(connection: Connection, name: str, project_id: int, *, include_deleted: bool = False) -> int | None:
    sql = "SELECT id FROM entities WHERE name = ? AND project_id = ?"
    if not include_deleted:
        sql += " AND deleted_at IS NULL"
    row = connection.query_one(sql, (name, project_id))
    return int(row["id"]) if row else None


def get_entity_type_name(connection: Connection, entity_id: int) -> str:
    """Return the entity type name for an entity id."""
    row = connection.query_one(
        "SELECT et.name FROM entities e JOIN entity_types et ON e.entity_type_id = et.id WHERE e.id = ?",
        (entity_id,),
    )
    assert row is not None
    return str(row["name"])
