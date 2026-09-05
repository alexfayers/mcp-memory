"""Best-effort maintenance sweeps: telemetry pruning, orphan GC, purge and archival."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

from mcp_memory.config import (
    get_archive_enabled,
    get_call_metrics_retention_days,
    get_gc_enabled,
    get_purge_enabled,
    get_purge_grace_days,
    get_surfaced_retention_days,
)
from mcp_memory.models import STRUCTURAL_ENTITY_TYPES
from mcp_memory.storage.pure.ranking import ARCHIVE_STALE_DAYS
from mcp_memory.storage.pure.rows import hash_observation
from mcp_memory.storage.pure.sql import days_ago, placeholders

if TYPE_CHECKING:
    from mcp_memory.storage.connection import Connection
    from mcp_memory.storage.repositories.entities import EntityRepository
    from mcp_memory.storage.repositories.telemetry import TelemetryRepository

# Vote score at or below which an orphan becomes eligible for autonomous GC. agent.py imports
# this so the dream's saturation-floor prose and the GC reap threshold share one value.
GC_DOWNVOTE_FLOOR = -10


class Maintenance:
    """Best-effort sweeps: telemetry pruning, orphan GC, tombstone purge, and archival."""

    def __init__(self, connection: Connection, entities: EntityRepository, telemetry: TelemetryRepository) -> None:
        self._conn = connection
        self._entities = entities
        self._telemetry = telemetry

    def backfill_observation_hashes(self) -> None:
        """Populate content_hash for any observation missing one. No-op after the first run."""
        rows = self._conn.query_all("SELECT id, content FROM observations WHERE content_hash IS NULL")
        if not rows:
            return
        with self._conn.transaction():
            for row in rows:
                self._conn.write(
                    "UPDATE observations SET content_hash = ? WHERE id = ?",
                    (hash_observation(row["content"]), row["id"]),
                )

    def _purge_soft_deleted(self, grace_days: int) -> int:
        """Hard-delete entities soft-deleted longer than the grace window, returning the count."""
        rows = self._conn.query_all(
            "SELECT e.id, e.name, p.name AS project FROM entities e "
            "JOIN projects p ON e.project_id = p.id "
            "WHERE e.deleted_at IS NOT NULL AND e.deleted_at < datetime('now', ?)",
            (days_ago(grace_days),),
        )
        with self._conn.transaction():
            for row in rows:
                self._entities.hard_delete_row(row["id"], row["project"], row["name"])
        return len(rows)

    def _gc_downvoted_orphans(self, threshold: int = GC_DOWNVOTE_FLOOR) -> int:
        """Soft-delete downvoted orphan entities, returning the number reaped.

        Reaps an entity that is all of: live, at or below the downvote floor, not a
        relation-exempt type, and with no live incoming relation. Removal is a reversible
        soft-delete, so the grace-window purge stays the sole path to permanent removal.
        """
        with self._conn.transaction():
            cursor = self._conn.write(
                f"UPDATE entities SET deleted_at = CURRENT_TIMESTAMP "
                f"WHERE deleted_at IS NULL "
                f"AND vote_score <= ? "
                f"AND entity_type_id NOT IN "
                f"(SELECT id FROM entity_types WHERE name IN "
                f"({placeholders(len(STRUCTURAL_ENTITY_TYPES))})) "
                f"AND NOT EXISTS ("
                f"SELECT 1 FROM relations r "
                f"JOIN entities src ON r.source_id = src.id "
                f"WHERE r.target_id = entities.id AND src.deleted_at IS NULL)",
                (threshold, *STRUCTURAL_ENTITY_TYPES),
            )
        return cursor.rowcount

    def _archive_stale_entities(self, threshold_days: int = ARCHIVE_STALE_DAYS) -> int:
        """Auto-archive stale resolved entities, returning the number archived.

        Archives an entity that is all of: live, status='resolved', last updated more than
        threshold_days ago, and outside the never-evict set - entities ever acted on after
        being surfaced (a row in surfaced_entities with used_at set). Any write or acted-on
        retrieval bumps updated_at, so renewal is already handled elsewhere.
        """
        with self._conn.transaction():
            cursor = self._conn.write(
                "UPDATE entities SET status = 'archived' "
                "WHERE deleted_at IS NULL "
                "AND status = 'resolved' "
                "AND updated_at < datetime('now', ?) "
                "AND name NOT IN "
                "(SELECT entity_name FROM surfaced_entities WHERE used_at IS NOT NULL)",
                (days_ago(threshold_days),),
            )
        return cursor.rowcount

    def run_startup_sweeps(self) -> None:
        """Run best-effort startup maintenance in a fixed order, tolerating lock contention.

        Order: prune tool-call telemetry, prune surfaced-entity telemetry, then - each gated
        by its own config flag - orphan GC, soft-delete purge, and stale-entity archival. A
        short-lived process (e.g. the CLI) opening its own connection can race a long-running
        service's write and hit "database is locked" past busy_timeout. None of this is
        required for the caller's own request to succeed, so the whole block is skipped
        silently on contention rather than crashing the command. Does NOT run the
        observation-hash backfill: the composition root calls that separately and
        unprotected, since it is required for correctness rather than best-effort.
        """
        try:
            self._telemetry.prune_tool_calls(get_call_metrics_retention_days())
            self._telemetry.prune_surfaced(get_surfaced_retention_days())
            self._run_gated_sweeps()
        except sqlite3.OperationalError:
            pass

    def _run_gated_sweeps(self) -> None:
        """Run the config-gated startup sweeps: orphan GC, soft-delete purge, and stale archival."""
        if get_gc_enabled():
            self._gc_downvoted_orphans()
        if get_purge_enabled():
            self._purge_soft_deleted(get_purge_grace_days())
        if get_archive_enabled():
            self._archive_stale_entities()
