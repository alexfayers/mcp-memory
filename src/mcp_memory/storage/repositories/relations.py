"""Relation-graph repository: create, delete and query relations between entities."""

from __future__ import annotations

from typing import TYPE_CHECKING

from mcp_memory.config import get_strict_policy_enabled
from mcp_memory.models import VALID_RELATION_TYPES, Relation, normalize_relation_type
from mcp_memory.storage.pure.rows import build_relation
from mcp_memory.storage.pure.sql import placeholders
from mcp_memory.storage.services.ids import (
    get_entity_id,
    get_entity_type_name,
    get_or_create_project_id,
    get_or_create_relation_type_id,
)
from mcp_memory.storage.services.integrity import would_be_orphaned_by_relation_delete

if TYPE_CHECKING:
    from mcp_memory.storage.connection import Connection


def _relation_select(placeholders_sql: str, *, project_scoped: bool) -> str:
    """Build the relation-lookup SELECT shared by the project-scoped and cross-project reads.

    The two callers differ only in whether they filter to one project (returning bare rows) or
    join projects to group results by owning project name across all projects.
    """
    project_column = "" if project_scoped else ", p.name AS project_name"
    project_join = "" if project_scoped else "JOIN projects p ON e_src.project_id = p.id "
    project_filter = "e_src.project_id = ? AND " if project_scoped else ""
    return (
        f"SELECT e_src.name AS source, e_tgt.name AS target, rt.name AS relation_type"
        f"{project_column} "
        f"FROM relations r "
        f"JOIN entities e_src ON r.source_id = e_src.id "
        f"JOIN entities e_tgt ON r.target_id = e_tgt.id "
        f"JOIN relation_types rt ON r.relation_type_id = rt.id "
        f"{project_join}"
        f"WHERE {project_filter}"
        f"e_src.deleted_at IS NULL AND e_tgt.deleted_at IS NULL "
        f"AND (r.source_id IN ({placeholders_sql}) "
        f"OR r.target_id IN ({placeholders_sql}))"
    )


class RelationRepository:
    """Repository for creating, deleting and querying relations between entities."""

    def __init__(self, connection: Connection) -> None:
        self._conn = connection

    def create(self, project: str, relations: list[Relation]) -> None:
        """Create relations between entities, ignoring duplicates."""
        project_id = get_or_create_project_id(self._conn, project)

        for relation in relations:
            if relation.source == relation.target:
                raise ValueError(f"Self-referential relation not allowed: '{relation.source}'")
            source_id = get_entity_id(self._conn, relation.source, project_id)
            if source_id is None:
                raise ValueError(f"Source entity '{relation.source}' not found in project '{project}'")
            target_id = get_entity_id(self._conn, relation.target, project_id)
            if target_id is None:
                raise ValueError(f"Target entity '{relation.target}' not found in project '{project}'")
            source_type = get_entity_type_name(self._conn, source_id)
            target_type = get_entity_type_name(self._conn, target_id)
            if source_type == "task" and target_type == "project" and relation.relation_type == "belongs-to":
                raise ValueError(
                    "task -> project 'belongs-to' relations are not allowed; link the task "
                    "to the feature it implements instead."
                )
            if get_strict_policy_enabled() and source_type == "task" and target_type == "project":
                raise ValueError(
                    "Direct task -> project relations are forbidden when MCP_MEMORY_STRICT_POLICY "
                    "is enabled; use a feature or knowledge entity as the parent instead."
                )
            relation_type = normalize_relation_type(relation.relation_type)
            if relation_type not in VALID_RELATION_TYPES:
                raise ValueError(
                    f"Invalid relation type '{relation.relation_type}' "
                    f"(normalized to '{relation_type}'). "
                    f"Valid types: {sorted(VALID_RELATION_TYPES)}"
                )
            relation_type_id = get_or_create_relation_type_id(self._conn, relation_type)
            self._conn.write(
                "INSERT OR IGNORE INTO relations (source_id, target_id, relation_type_id) VALUES (?, ?, ?)",
                (source_id, target_id, relation_type_id),
            )

        # Commits the writes above without wrapping them: a validation failure mid-loop
        # must leave earlier inserts pending, exactly as today. Wrapping the loop would
        # roll them back, which is a behaviour change reserved for a separate commit.
        # Do not "simplify" this block away.
        with self._conn.transaction():
            pass

    def delete(self, project: str, source: str, target: str, relation_type: str) -> None:
        """Delete a specific relation between two entities."""
        project_id = get_or_create_project_id(self._conn, project)
        source_id = get_entity_id(self._conn, source, project_id)
        if source_id is None:
            raise ValueError(f"Source entity '{source}' not found in project '{project}'")
        target_id = get_entity_id(self._conn, target, project_id)
        if target_id is None:
            raise ValueError(f"Target entity '{target}' not found in project '{project}'")

        row = self._conn.query_one("SELECT id FROM relation_types WHERE name = ?", (relation_type,))
        if row is None:
            raise ValueError(f"Relation type '{relation_type}' not found")

        orphaned: list[str] = []
        if would_be_orphaned_by_relation_delete(self._conn, source_id, source_id, target_id, row["id"]):
            orphaned.append(source)
        if would_be_orphaned_by_relation_delete(self._conn, target_id, source_id, target_id, row["id"]):
            orphaned.append(target)
        if orphaned:
            raise ValueError(
                "Cannot delete relation: it would orphan non-structural "
                f"entit{'y' if len(orphaned) == 1 else 'ies'}: {', '.join(orphaned)}"
            )

        cursor = self._conn.write(
            "DELETE FROM relations WHERE source_id = ? AND target_id = ? AND relation_type_id = ?",
            (source_id, target_id, row["id"]),
        )
        if cursor.rowcount == 0:
            raise ValueError(f"Relation '{source}' -> '{target}' ({relation_type}) not found in project '{project}'")

        # Commits the write above without wrapping it in the same transaction: this mirrors
        # create's commit-after-the-fact shape rather than atomically covering the delete
        # and its rowcount check. Do not "simplify" this block away.
        with self._conn.transaction():
            pass

    def for_entities(self, project_id: int, entity_ids: list[int]) -> list[Relation]:
        """Return relations touching any of the given entities, scoped to one project."""
        if not entity_ids:
            return []
        ph = placeholders(len(entity_ids))
        rows = self._conn.query_all(
            _relation_select(ph, project_scoped=True),
            [project_id, *entity_ids, *entity_ids],
        )
        return [build_relation(row) for row in rows]

    def for_entity_ids(self, entity_ids: list[int]) -> dict[str, list[Relation]]:
        """Return relations touching any of the given entities across all projects, keyed by
        owning project name.

        Both endpoints of a relation always live in one project, so the source entity's
        project identifies the relation's owner.
        """
        if not entity_ids:
            return {}
        ph = placeholders(len(entity_ids))
        rows = self._conn.query_all(
            _relation_select(ph, project_scoped=False),
            [*entity_ids, *entity_ids],
        )
        by_project: dict[str, list[Relation]] = {}
        for row in rows:
            by_project.setdefault(row["project_name"], []).append(build_relation(row))
        return by_project
