"""Observation repository: append, trim, vote and merge operations on entity observations."""

from __future__ import annotations

from typing import TYPE_CHECKING

from mcp_memory.config import get_max_observation_chars
from mcp_memory.models import validate_vote
from mcp_memory.storage.pure.rows import budget_observations, build_observation, hash_observation
from mcp_memory.storage.pure.sql import placeholders
from mcp_memory.storage.services.ids import get_entity_id, get_or_create_project_id

if TYPE_CHECKING:
    from mcp_memory.models import Observation
    from mcp_memory.storage.connection import Connection


class ObservationRepository:
    """Repository for reading, appending, trimming, voting and merging observations."""

    def __init__(self, connection: Connection) -> None:
        self._conn = connection

    def for_entity(self, entity_id: int) -> list[Observation]:
        """Return an entity's observations best-first (vote_score DESC, then insertion order)."""
        rows = self._conn.query_all(
            "SELECT content, content_hash, vote_score FROM observations WHERE entity_id = ? "
            "ORDER BY vote_score DESC, id",
            (entity_id,),
        )
        return [build_observation(row) for row in rows]

    def budgeted(self, entity_id: int, max_chars: int | None = None) -> tuple[list[Observation], int]:
        """Fetch an entity's observations and trim them to a character budget, best-first.

        Args:
            entity_id: The entity to fetch observations for.
            max_chars: Cumulative content-char budget. None reads the config default, a
                negative value means unlimited, and 0 keeps only the first observation.

        Returns:
            The trimmed observations and the count omitted.
        """
        budget = get_max_observation_chars() if max_chars is None else max_chars
        full = self.for_entity(entity_id)
        kept = budget_observations(full, budget)
        return kept, len(full) - len(kept)

    def add(self, project: str, entity_name: str, observations: list[str]) -> list[str]:
        """Append deduplicated observations to an entity, returning the new ones' content hashes."""
        project_id = get_or_create_project_id(self._conn, project)
        entity_id = get_entity_id(self._conn, entity_name, project_id)
        if entity_id is None:
            raise ValueError(f"Entity '{entity_name}' not found in project '{project}'")

        existing = {obs.content for obs in self.for_entity(entity_id)}
        new_observations = [obs for obs in observations if obs not in existing]

        if new_observations:
            with self._conn.transaction():
                self._conn.write_many(
                    "INSERT INTO observations (entity_id, content, content_hash) VALUES (?, ?, ?)",
                    [(entity_id, obs, hash_observation(obs)) for obs in new_observations],
                )

        return [hash_observation(obs) for obs in new_observations]

    def delete(
        self,
        project: str,
        entity_name: str,
        observations: list[str] | None = None,
        hashes: list[str] | None = None,
    ) -> int:
        """Delete observations by exact content match and/or by content_hash."""
        if not observations and not hashes:
            raise ValueError("Provide at least one of 'observations' or 'hashes'")

        project_id = get_or_create_project_id(self._conn, project)
        entity_id = get_entity_id(self._conn, entity_name, project_id)
        if entity_id is None:
            raise ValueError(f"Entity '{entity_name}' not found in project '{project}'")

        count = 0
        with self._conn.transaction():
            for obs in observations or []:
                cursor = self._conn.write(
                    "DELETE FROM observations WHERE entity_id = ? AND content = ?",
                    (entity_id, obs),
                )
                count += cursor.rowcount
            for obs_hash in hashes or []:
                cursor = self._conn.write(
                    "DELETE FROM observations WHERE entity_id = ? AND content_hash = ?",
                    (entity_id, obs_hash),
                )
                count += cursor.rowcount

        return count

    def trim_to_outcome(self, project: str, entity_name: str, keep_hashes: list[str]) -> int:
        """Delete all observations on an entity except those with a content_hash in keep_hashes."""
        if not keep_hashes:
            raise ValueError("Provide at least one hash in keep_hashes")

        project_id = get_or_create_project_id(self._conn, project)
        entity_id = get_entity_id(self._conn, entity_name, project_id)
        if entity_id is None:
            raise ValueError(f"Entity '{entity_name}' not found in project '{project}'")

        with self._conn.transaction():
            cursor = self._conn.write(
                "DELETE FROM observations WHERE entity_id = ? "
                f"AND content_hash NOT IN ({placeholders(len(keep_hashes))})",
                (entity_id, *keep_hashes),
            )

        return cursor.rowcount

    def vote(
        self,
        project: str,
        entity_name: str,
        vote: int,
        *,
        content: str | None = None,
        content_hash: str | None = None,
    ) -> int:
        """Apply a usefulness vote to a single observation and return its new score.

        Any nonzero integer up to MAX_VOTE_MAGNITUDE in magnitude sets the vote's strength.
        The observation is addressed by content_hash (cheap) or exact content within its
        entity; exactly one must be given. Neither is DB-unique, so identical observations
        all receive the vote and share the returned score. Like EntityRepository.vote, this
        leaves updated_at untouched, so a vote does not disturb recency ranking.
        """
        validate_vote(vote)
        if (content is None) == (content_hash is None):
            raise ValueError("Provide exactly one of 'content' or 'content_hash'")

        if content is not None:
            column, value = "content", content
        else:
            assert content_hash is not None
            column, value = "content_hash", content_hash

        project_id = get_or_create_project_id(self._conn, project)
        entity_id = get_entity_id(self._conn, entity_name, project_id)
        if entity_id is None:
            raise ValueError(f"Entity '{entity_name}' not found in project '{project}'")

        with self._conn.transaction():
            cursor = self._conn.write(
                f"UPDATE observations SET vote_score = vote_score + ? WHERE entity_id = ? AND {column} = ?",
                (vote, entity_id, value),
            )
            if cursor.rowcount == 0:
                raise ValueError(f"Observation not found in entity '{entity_name}'")
        row = self._conn.query_one(
            f"SELECT vote_score FROM observations WHERE entity_id = ? AND {column} = ?",
            (entity_id, value),
        )
        assert row is not None
        return int(row["vote_score"])

    def merge(self, project: str, entity_name: str, source_hash: str, target_hash: str) -> dict[str, int]:
        """Fold one observation into another within a single entity, addressed by content_hash.

        Additive on the target: the target keeps the higher of the two vote scores and its
        own timestamp. The source observation is hard-deleted (observations have no
        soft-delete concept, consistent with delete). Only merges observations within one
        entity, never across entities.
        """
        if source_hash == target_hash:
            raise ValueError("Cannot merge an observation into itself")
        project_id = get_or_create_project_id(self._conn, project)
        entity_id = get_entity_id(self._conn, entity_name, project_id)
        if entity_id is None:
            raise ValueError(f"Entity '{entity_name}' not found in project '{project}'")

        source = self._conn.query_one(
            "SELECT vote_score FROM observations WHERE entity_id = ? AND content_hash = ?",
            (entity_id, source_hash),
        )
        if source is None:
            raise ValueError(f"Source observation not found in entity '{entity_name}'")
        target = self._conn.query_one(
            "SELECT vote_score FROM observations WHERE entity_id = ? AND content_hash = ?",
            (entity_id, target_hash),
        )
        if target is None:
            raise ValueError(f"Target observation not found in entity '{entity_name}'")

        with self._conn.transaction():
            self._conn.write(
                "UPDATE observations SET vote_score = ? WHERE entity_id = ? AND content_hash = ?",
                (max(int(source["vote_score"]), int(target["vote_score"])), entity_id, target_hash),
            )
            deleted = self._conn.write(
                "DELETE FROM observations WHERE entity_id = ? AND content_hash = ?",
                (entity_id, source_hash),
            )
        return {"merged": deleted.rowcount}

    def export_rows(self, entity_id: int) -> list[dict[str, object]]:
        """Return an entity's observations as export dicts, best-first, preserving created_at."""
        rows = self._conn.query_all(
            "SELECT content, content_hash, vote_score, created_at FROM observations "
            "WHERE entity_id = ? ORDER BY vote_score DESC, id",
            (entity_id,),
        )
        return [
            {
                "content": row["content"],
                "content_hash": row["content_hash"],
                "vote_score": int(row["vote_score"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]
