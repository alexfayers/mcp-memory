"""Entity repository: creation, identity, lifecycle and merge operations."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from mcp_memory.config import get_strict_policy_enabled
from mcp_memory.models import VALID_STATUSES, validate_vote
from mcp_memory.storage.pure.rows import build_relation, hash_observation
from mcp_memory.storage.services.fts import refresh_for_entity
from mcp_memory.storage.services.ids import get_entity_id, get_or_create_entity_type_id, get_or_create_project_id
from mcp_memory.storage.services.integrity import orphaned_neighbors_if_entity_deleted

if TYPE_CHECKING:
    from mcp_memory.models import EntityStatus, Relation
    from mcp_memory.storage.connection import Connection


class EntityRepository:
    """CRUD, identity and lifecycle operations over the entities table."""

    def __init__(self, connection: Connection) -> None:
        self._conn = connection

    def _validate_entity_data(self, project: str, entity_data: dict[str, object]) -> None:
        """Raise ValueError if entity_data does not meet create's input contract."""
        name = entity_data.get("name")
        entity_type = entity_data.get("entityType")
        observations = entity_data.get("observations")
        status = entity_data.get("status")

        if not name or not isinstance(name, str):
            raise ValueError(f"Entity name must be a non-empty string, got: {name!r}")
        if not entity_type or not isinstance(entity_type, str):
            raise ValueError(f"Entity type must be a non-empty string, got: {entity_type!r}")
        if not isinstance(observations, list) or not observations:
            raise ValueError(f"Observations must be a non-empty list for entity '{name}'")
        for obs in observations:
            if not isinstance(obs, str) or not obs:
                raise ValueError(f"Each observation must be a non-empty string for entity '{name}', got: {obs!r}")

        if status is not None and status not in VALID_STATUSES:
            raise ValueError(f"Invalid status '{status}' for entity '{name}'. Must be one of: {VALID_STATUSES}")

        if get_strict_policy_enabled() and project != "global" and entity_type == "user-preferences":
            raise ValueError(
                "Project-scoped 'user-preferences' entities are forbidden when "
                "MCP_MEMORY_STRICT_POLICY is enabled; store them in global scope instead."
            )

    def create(self, project: str, entities: list[dict[str, object]]) -> None:
        """Upsert entities with observations, overwriting existing observations."""
        project_id = get_or_create_project_id(self._conn, project)

        with self._conn.transaction():
            for entity_data in entities:
                self._validate_entity_data(project, entity_data)
                name = entity_data.get("name")
                entity_type = entity_data.get("entityType")
                observations = cast("list[str]", entity_data.get("observations"))
                status = entity_data.get("status")

                entity_type_id = get_or_create_entity_type_id(self._conn, str(entity_type))
                existing_id = get_entity_id(self._conn, str(name), project_id)

                if existing_id is None:
                    self.purge_tombstone(str(name), project, project_id)

                if existing_id is not None:
                    self._conn.write(
                        "UPDATE entities SET entity_type_id = ?, status = ? WHERE id = ?",
                        (entity_type_id, status, existing_id),
                    )
                    self._conn.write("DELETE FROM observations WHERE entity_id = ?", (existing_id,))
                    entity_id = existing_id
                else:
                    cursor = self._conn.write(
                        "INSERT INTO entities (name, entity_type_id, project_id, status) VALUES (?, ?, ?, ?)",
                        (name, entity_type_id, project_id, status),
                    )
                    entity_id = cast("int", cursor.lastrowid)

                self._conn.write_many(
                    "INSERT INTO observations (entity_id, content, content_hash) VALUES (?, ?, ?)",
                    [(entity_id, obs, hash_observation(obs)) for obs in observations],
                )

    def rename(self, project: str, old_name: str, new_name: str) -> None:
        """Rename an entity in place, preserving its relations and observations."""
        project_id = get_or_create_project_id(self._conn, project)
        entity_id = get_entity_id(self._conn, old_name, project_id)
        if entity_id is None:
            raise ValueError(f"Entity '{old_name}' not found in project '{project}'")

        if get_entity_id(self._conn, new_name, project_id) is not None:
            raise ValueError(f"Entity '{new_name}' already exists in project '{project}'")

        if project == "global":
            conflict = self.exists_outside(new_name, "global")
            if conflict:
                raise ValueError(f"Entity '{new_name}' already exists in project '{conflict}'")
        elif self.exists_in(new_name, "global"):
            raise ValueError(f"Entity '{new_name}' already exists in global scope")

        with self._conn.transaction():
            refresh_for_entity(self._conn, entity_id, delete=True)
            self._conn.write("UPDATE entities SET name = ? WHERE id = ?", (new_name, entity_id))
            refresh_for_entity(self._conn, entity_id, delete=False)

    def move_cross_scope(self, source_project: str, target_project: str, name: str) -> list[Relation]:
        """Move an entity to another scope, dropping and returning its cross-scope relations."""
        source_id = get_or_create_project_id(self._conn, source_project)
        target_id = get_or_create_project_id(self._conn, target_project)
        entity_id = get_entity_id(self._conn, name, source_id)
        if entity_id is None:
            raise ValueError(f"Entity '{name}' not found in project '{source_project}'")

        if get_entity_id(self._conn, name, target_id) is not None:
            raise ValueError(f"Entity '{name}' already exists in project '{target_project}'")

        with self._conn.transaction():
            rows = self._conn.query_all(
                "SELECT e_src.name AS source, e_tgt.name AS target, rt.name AS relation_type "
                "FROM relations r "
                "JOIN entities e_src ON r.source_id = e_src.id "
                "JOIN entities e_tgt ON r.target_id = e_tgt.id "
                "JOIN relation_types rt ON r.relation_type_id = rt.id "
                "WHERE r.source_id = ? OR r.target_id = ?",
                (entity_id, entity_id),
            )
            dropped = [build_relation(row) for row in rows]
            self._conn.write(
                "DELETE FROM relations WHERE source_id = ? OR target_id = ?",
                (entity_id, entity_id),
            )
            refresh_for_entity(self._conn, entity_id, delete=True)
            self._conn.write("UPDATE entities SET project_id = ? WHERE id = ?", (target_id, entity_id))
            refresh_for_entity(self._conn, entity_id, delete=False)

        return dropped

    def exists_in(self, name: str, project: str) -> bool:
        """Check if an entity name exists in a specific project scope."""
        row = self._conn.query_one(
            "SELECT 1 FROM entities e JOIN projects p ON e.project_id = p.id "
            "WHERE e.name = ? AND p.name = ? AND e.deleted_at IS NULL",
            (name, project),
        )
        return row is not None

    def exists_outside(self, name: str, project: str) -> str | None:
        """Return the first project where this entity exists, excluding the given project."""
        row = self._conn.query_one(
            "SELECT p.name FROM entities e JOIN projects p ON e.project_id = p.id "
            "WHERE e.name = ? AND p.name != ? AND e.deleted_at IS NULL",
            (name, project),
        )
        return row["name"] if row else None

    def set_status(self, project: str, name: str, status: EntityStatus | None) -> int:
        """Set or clear the status of an entity and return its observation count."""
        if status is not None and status not in VALID_STATUSES:
            raise ValueError(f"Invalid status '{status}'. Must be one of: {VALID_STATUSES}")

        project_id = get_or_create_project_id(self._conn, project)
        entity_id = get_entity_id(self._conn, name, project_id)
        if entity_id is None:
            raise ValueError(f"Entity '{name}' not found in project '{project}'")

        with self._conn.transaction():
            self._conn.write("UPDATE entities SET status = ? WHERE id = ?", (status, entity_id))
        row = self._conn.query_one("SELECT COUNT(*) AS n FROM observations WHERE entity_id = ?", (entity_id,))
        assert row is not None
        return int(row["n"])

    def vote(self, project: str, name: str, vote: int) -> int:
        """Apply a usefulness vote to an entity and return its new net score.

        Any nonzero integer up to MAX_VOTE_MAGNITUDE in magnitude sets the vote's strength.
        Deliberately updates only vote_score, leaving updated_at untouched, so a vote (a
        relevance signal) is not mistaken for a content change by recency ranking.
        """
        validate_vote(vote)

        project_id = get_or_create_project_id(self._conn, project)
        entity_id = get_entity_id(self._conn, name, project_id)
        if entity_id is None:
            raise ValueError(f"Entity '{name}' not found in project '{project}'")

        with self._conn.transaction():
            self._conn.write(
                "UPDATE entities SET vote_score = vote_score + ? WHERE id = ?",
                (vote, entity_id),
            )
        row = self._conn.query_one("SELECT vote_score FROM entities WHERE id = ?", (entity_id,))
        assert row is not None
        return int(row["vote_score"])

    def delete(self, project: str, name: str) -> None:
        """Delete an entity, cascading outgoing relations but blocking on incoming."""
        project_id = get_or_create_project_id(self._conn, project)
        entity_id = get_entity_id(self._conn, name, project_id)
        if entity_id is None:
            raise ValueError(f"Entity '{name}' not found in project '{project}'")

        incoming = self._conn.query_all(
            "SELECT e.name FROM relations r "
            "JOIN entities e ON r.source_id = e.id "
            "WHERE r.target_id = ? AND r.source_id != ?",
            (entity_id, entity_id),
        )
        if incoming:
            sources = [row["name"] for row in incoming]
            raise ValueError(f"Cannot delete '{name}': {len(sources)} incoming relation(s) from: " + ", ".join(sources))

        orphaned = orphaned_neighbors_if_entity_deleted(self._conn, entity_id)
        if orphaned:
            raise ValueError(
                f"Cannot delete '{name}': it would orphan non-structural "
                f"entit{'y' if len(orphaned) == 1 else 'ies'}: {', '.join(orphaned)}"
            )

        with self._conn.transaction():
            self.hard_delete_row(entity_id, project, name)

    def hard_delete_row(self, entity_id: int, project: str, name: str) -> None:
        """Hard-delete an entity and its observations, outgoing relations and telemetry.

        Executes within the caller's transaction (no commit of its own), so it is safe
        to call from inside another ``with self._conn.transaction():`` block. Deletes
        relations in both directions so a force-delete (e.g. the purge of a merged-away
        entity that still holds an incoming edge) cannot orphan a relation row against
        the foreign key.
        """
        self._conn.write("DELETE FROM observations WHERE entity_id = ?", (entity_id,))
        self._conn.write("DELETE FROM relations WHERE source_id = ? OR target_id = ?", (entity_id, entity_id))
        self._conn.write(
            "DELETE FROM surfaced_entities WHERE project = ? AND entity_name = ?",
            (project, name),
        )
        self._conn.write("DELETE FROM entities WHERE id = ?", (entity_id,))

    def purge_tombstone(self, name: str, project: str, project_id: int) -> None:
        """Hard-remove a soft-deleted row holding the (name, project) slot before a re-create.

        The UNIQUE(name, project_id) constraint means a tombstone would otherwise block
        inserting a fresh entity with the same name. Runs within the caller's transaction.
        """
        tombstone_id = get_entity_id(self._conn, name, project_id, include_deleted=True)
        if tombstone_id is not None:
            self.hard_delete_row(tombstone_id, project, name)

    def restore(self, project: str, name: str) -> None:
        """Clear an entity's soft-deleted mark, making it visible to reads again."""
        project_id = get_or_create_project_id(self._conn, project)
        entity_id = get_entity_id(self._conn, name, project_id, include_deleted=True)
        if entity_id is None:
            raise ValueError(f"Entity '{name}' not found in project '{project}'")
        with self._conn.transaction():
            self._conn.write("UPDATE entities SET deleted_at = NULL WHERE id = ?", (entity_id,))

    def merge(self, project: str, source: str, target: str) -> dict[str, int]:
        """Fold a source entity into a target, then soft-delete the source.

        Additive on the target: the source's observations are copied over (deduped by
        exact content, keeping their votes and timestamps), and equivalent relations are
        recreated on the target (skipping any that would become a target->target
        self-loop). The target keeps the higher of the two vote scores. The source is
        soft-deleted rather than removed, so the merge is reversible via restore.
        """
        if source == target:
            raise ValueError("Cannot merge an entity into itself")
        project_id = get_or_create_project_id(self._conn, project)
        source_id = get_entity_id(self._conn, source, project_id)
        if source_id is None:
            raise ValueError(f"Source entity '{source}' not found in project '{project}'")
        target_id = get_entity_id(self._conn, target, project_id)
        if target_id is None:
            raise ValueError(f"Target entity '{target}' not found in project '{project}'")

        with self._conn.transaction():
            obs = self._conn.write(
                "INSERT INTO observations "
                "(entity_id, content, vote_score, created_at, content_hash) "
                "SELECT ?, content, vote_score, created_at, content_hash FROM observations "
                "WHERE entity_id = ? AND content NOT IN "
                "(SELECT content FROM observations WHERE entity_id = ?)",
                (target_id, source_id, target_id),
            )
            outgoing = self._conn.write(
                "INSERT OR IGNORE INTO relations (source_id, target_id, relation_type_id) "
                "SELECT ?, target_id, relation_type_id FROM relations "
                "WHERE source_id = ? AND target_id != ?",
                (target_id, source_id, target_id),
            )
            incoming = self._conn.write(
                "INSERT OR IGNORE INTO relations (source_id, target_id, relation_type_id) "
                "SELECT source_id, ?, relation_type_id FROM relations "
                "WHERE target_id = ? AND source_id != ?",
                (target_id, source_id, target_id),
            )
            self._conn.write(
                "UPDATE entities SET vote_score = "
                "MAX(vote_score, (SELECT vote_score FROM entities WHERE id = ?)) WHERE id = ?",
                (source_id, target_id),
            )
            self._conn.write("UPDATE entities SET deleted_at = CURRENT_TIMESTAMP WHERE id = ?", (source_id,))
        return {
            "observations_merged": obs.rowcount,
            "relations_repointed": outgoing.rowcount + incoming.rowcount,
        }
