"""Relation-graph integrity policy: orphan checks shared by entities and relations."""

from __future__ import annotations

from typing import TYPE_CHECKING

from mcp_memory.models import STRUCTURAL_ENTITY_TYPES

from .ids import get_entity_type_name

if TYPE_CHECKING:
    from mcp_memory.storage.connection import Connection


def _is_structural_entity(connection: Connection, entity_id: int) -> bool:
    """Return whether an entity is exempt from orphan checks."""
    return get_entity_type_name(connection, entity_id) in STRUCTURAL_ENTITY_TYPES


def would_be_orphaned_by_relation_delete(
    connection: Connection, entity_id: int, source_id: int, target_id: int, relation_type_id: int
) -> bool:
    """Return whether deleting one specific relation would leave the entity orphaned."""
    if _is_structural_entity(connection, entity_id):
        return False
    row = connection.query_one(
        "SELECT COUNT(*) AS n FROM relations r "
        "JOIN entities src ON r.source_id = src.id "
        "JOIN entities tgt ON r.target_id = tgt.id "
        "WHERE src.deleted_at IS NULL AND tgt.deleted_at IS NULL "
        "AND (r.source_id = ? OR r.target_id = ?) "
        "AND NOT (r.source_id = ? AND r.target_id = ? AND r.relation_type_id = ?)",
        (entity_id, entity_id, source_id, target_id, relation_type_id),
    )
    assert row is not None
    return int(row["n"]) == 0


def orphaned_neighbors_if_entity_deleted(connection: Connection, entity_id: int) -> list[str]:
    """Return neighbors that would be orphaned if the entity were deleted."""
    rows = connection.query_all(
        "SELECT DISTINCT other.id AS id, other.name AS name "
        "FROM relations r "
        "JOIN entities src ON r.source_id = src.id "
        "JOIN entities tgt ON r.target_id = tgt.id "
        "JOIN entities other "
        "ON other.id = CASE WHEN r.source_id = ? THEN r.target_id ELSE r.source_id END "
        "WHERE src.deleted_at IS NULL AND tgt.deleted_at IS NULL "
        "AND (r.source_id = ? OR r.target_id = ?)",
        (entity_id, entity_id, entity_id),
    )
    orphaned: list[str] = []
    for row in rows:
        other_id = int(row["id"])
        if _is_structural_entity(connection, other_id):
            continue
        remaining = connection.query_one(
            "SELECT COUNT(*) AS n FROM relations r "
            "JOIN entities src ON r.source_id = src.id "
            "JOIN entities tgt ON r.target_id = tgt.id "
            "WHERE src.deleted_at IS NULL AND tgt.deleted_at IS NULL "
            "AND (r.source_id = ? OR r.target_id = ?) "
            "AND r.source_id != ? AND r.target_id != ?",
            (other_id, other_id, entity_id, entity_id),
        )
        assert remaining is not None
        if int(remaining["n"]) == 0:
            orphaned.append(str(row["name"]))
    return orphaned
