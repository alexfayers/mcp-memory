"""Retrieval and tool-call telemetry: implicit-usefulness voting and usage tracking."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from mcp_memory.storage.pure.sql import days_ago, seconds_ago
from mcp_memory.storage.services.ids import get_entity_id, get_or_create_project_id

if TYPE_CHECKING:
    from mcp_memory.storage.connection import Connection


class TelemetryRepository:
    """Records retrieval surfacings and tool calls, and casts implicit-usefulness votes."""

    def __init__(self, connection: Connection) -> None:
        self._conn = connection

    def record_surfaced(
        self,
        tool: str,
        query: str,
        retrieval_id: str,
        hits: list[tuple[str, str, int]],
    ) -> None:
        """Record the entities a ranked search surfaced, for implicit-usefulness telemetry.

        Each hit is ``(project, entity_name, rank)`` where rank is 1-based. Grouped by
        ``retrieval_id`` so the ranking eval can reconstruct the exact ranked list later.
        """
        if not hits:
            return
        with self._conn.transaction():
            self._conn.write_many(
                "INSERT INTO surfaced_entities "
                "(retrieval_id, project, query, tool, entity_name, rank) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [(retrieval_id, project, query, tool, name, rank) for project, name, rank in hits],
            )

    def register_use(self, project: str, name: str, *, window_seconds: float, max_per_day: int) -> int | None:
        """Cast a deterministic implicit-usefulness vote if this entity was recently surfaced.

        Consumes the newest not-yet-matched surfacing of ``name`` within ``window_seconds``:
        an in-window edit following a search is an observed "this was useful". Casts a bounded
        ``+1`` (via the vote_score column directly, not entities.vote, so instrumentation does
        not recurse) unless the per-entity daily cap is already reached, in which case the
        surfacing is still consumed but no vote fires. Returns the new vote_score, or ``None``
        when there was no eligible surfacing, the cap was hit, or the entity is gone.
        """
        with self._conn.transaction():
            surfaced = self._conn.query_one(
                "SELECT id FROM surfaced_entities "
                "WHERE project = ? AND entity_name = ? AND used_at IS NULL "
                "AND surfaced_at >= datetime('now', ?) "
                "ORDER BY surfaced_at DESC, id DESC LIMIT 1",
                (project, name, seconds_ago(window_seconds)),
            )
            if surfaced is None:
                return None

            self._conn.write(
                "UPDATE surfaced_entities SET used_at = CURRENT_TIMESTAMP WHERE id = ?",
                (surfaced["id"],),
            )

            project_id = get_or_create_project_id(self._conn, project)
            entity_id = get_entity_id(self._conn, name, project_id)
            if entity_id is None:
                return None

            votes_today_row = self._conn.query_one(
                "SELECT COUNT(*) AS n FROM surfaced_entities "
                "WHERE project = ? AND entity_name = ? AND vote_cast = 1 "
                "AND used_at >= date('now')",
                (project, name),
            )
            assert votes_today_row is not None
            if votes_today_row["n"] >= max_per_day:
                return None

            self._conn.write("UPDATE surfaced_entities SET vote_cast = 1 WHERE id = ?", (surfaced["id"],))
            self._conn.write("UPDATE entities SET vote_score = vote_score + 1 WHERE id = ?", (entity_id,))
            row = self._conn.query_one("SELECT vote_score FROM entities WHERE id = ?", (entity_id,))
            assert row is not None
            return int(row["vote_score"])

    def prune_surfaced(self, retention_days: int) -> int:
        """Delete retrieval telemetry older than the retention window, returning the row count.

        A negative window means unlimited retention: nothing is deleted and 0 is returned.
        """
        if retention_days < 0:
            return 0
        with self._conn.transaction():
            cursor = self._conn.write(
                "DELETE FROM surfaced_entities WHERE surfaced_at < datetime('now', ?)",
                (days_ago(retention_days),),
            )
        return cursor.rowcount

    def record_tool_call(self, tool: str, input_bytes: int, output_bytes: int, options: dict[str, object]) -> None:
        """Record one @_track-wrapped tool call's byte-size proxies and allowlisted options."""
        with self._conn.transaction():
            self._conn.write(
                "INSERT INTO tool_calls (tool, input_bytes, output_bytes, options) VALUES (?, ?, ?, ?)",
                (tool, input_bytes, output_bytes, json.dumps(options, sort_keys=True)),
            )

    def prune_tool_calls(self, retention_days: int) -> int:
        """Delete tool-call usage telemetry older than the retention window, returning row count.

        A negative window means unlimited retention: nothing is deleted and 0 is returned.
        """
        if retention_days < 0:
            return 0
        with self._conn.transaction():
            cursor = self._conn.write(
                "DELETE FROM tool_calls WHERE called_at < datetime('now', ?)",
                (days_ago(retention_days),),
            )
        return cursor.rowcount
