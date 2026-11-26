"""Observation repository: append, trim, vote and merge operations on entity observations."""

from __future__ import annotations

from typing import TYPE_CHECKING

from mcp_memory.config import get_max_observation_chars
from mcp_memory.models import (
    is_protected_observation,
    validate_provenance,
    validate_triggers,
    validate_vote,
)
from mcp_memory.storage.pure.rows import (
    budget_observations,
    build_observation,
    hash_observation,
    reject_status_observation,
    strip_today_date_prefix,
)
from mcp_memory.storage.pure.sql import placeholders
from mcp_memory.storage.services.fts import refresh_for_entity
from mcp_memory.storage.services.ids import get_entity_id, get_or_create_project_id
from mcp_memory.storage.services.provenance import raise_provenance
from mcp_memory.storage.services.triggers import add_triggers, copy_triggers, triggers_of

if TYPE_CHECKING:
    from collections.abc import Sequence
    import sqlite3

    from mcp_memory.models import Observation
    from mcp_memory.storage.connection import Connection


class ObservationRepository:
    """Repository for reading, appending, trimming, voting and merging observations."""

    def __init__(self, connection: Connection) -> None:
        self._conn = connection

    def for_entity(self, entity_id: int) -> list[Observation]:
        """Return an entity's observations best-first (vote_score DESC, then insertion order)."""
        rows = self._conn.query_all(
            "SELECT content, content_hash, vote_score, created_at, provenance FROM observations "
            "WHERE entity_id = ? AND deleted_at IS NULL ORDER BY vote_score DESC, id",
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

    def add(
        self,
        project: str,
        entity_name: str,
        observations: list[str],
        provenance: str | None = None,
        *,
        triggers: list[dict[str, str]] | None = None,
    ) -> list[str]:
        """Append deduplicated observations, returning their hashes; a duplicate takes a higher-ranked provenance.

        Every observation in the call, new or duplicate, gains the triggers.
        """
        validate_provenance(provenance)
        trigger_rows = validate_triggers(triggers or [])
        project_id = get_or_create_project_id(self._conn, project)
        entity_id = get_entity_id(self._conn, entity_name, project_id)
        if entity_id is None:
            raise ValueError(f"Entity '{entity_name}' not found in project '{project}'")

        observations = [strip_today_date_prefix(obs) for obs in observations]
        rows = self._conn.query_all(
            "SELECT id, content, provenance FROM observations WHERE entity_id = ? AND deleted_at IS NULL",
            (entity_id,),
        )
        existing = {row["content"] for row in rows}
        new_observations = [obs for obs in observations if obs not in existing]

        with self._conn.transaction():
            self._conn.write_many(
                "INSERT INTO observations (entity_id, content, content_hash, provenance) VALUES (?, ?, ?, ?)",
                [(entity_id, obs, hash_observation(obs), provenance) for obs in new_observations],
            )
            raise_provenance(self._conn, [(provenance, row["id"]) for row in rows if row["content"] in observations])
            if trigger_rows:
                ids = self._conn.query_all(
                    "SELECT id FROM observations WHERE entity_id = ? AND deleted_at IS NULL "
                    f"AND content IN ({placeholders(len(observations))})",
                    (entity_id, *observations),
                )
                add_triggers(self._conn, [row["id"] for row in ids], trigger_rows)

        return [hash_observation(obs) for obs in new_observations]

    def trigger_rows(self) -> list[dict[str, object]]:
        """Return every live observation's triggers with the observation they surface, best-voted first."""
        rows = self._conn.query_all(
            "SELECT t.kind, t.value, t.tool, t.action, p.name AS project, e.name AS entity, "
            "o.content, o.content_hash, o.vote_score FROM observation_triggers t "
            "JOIN observations o ON o.id = t.observation_id JOIN entities e ON e.id = o.entity_id "
            "JOIN projects p ON p.id = e.project_id WHERE o.deleted_at IS NULL AND e.deleted_at IS NULL "
            "ORDER BY o.vote_score DESC, t.id"
        )
        return [dict(row) for row in rows]

    def delete(
        self,
        project: str,
        entity_name: str,
        observations: list[str] | None = None,
        hashes: list[str] | None = None,
    ) -> list[str]:
        """Soft-delete observations by exact content match and/or by content_hash, returning their hashes."""
        if not observations and not hashes:
            raise ValueError("Provide at least one of 'observations' or 'observationHashes'")

        entity_id = self._require_entity_id(project, entity_name)
        conditions: list[str] = []
        params: list[str] = []
        if observations:
            conditions.append(f"content IN ({placeholders(len(observations))})")
            params.extend(observations)
        if hashes:
            conditions.append(f"content_hash IN ({placeholders(len(hashes))})")
            params.extend(hashes)

        with self._conn.transaction():
            return self._soft_delete(entity_id, f"({' OR '.join(conditions)})", params)

    def restore(
        self,
        project: str,
        entity_name: str,
        observations: list[str] | None = None,
        hashes: list[str] | None = None,
    ) -> list[str]:
        """Restore soft-deleted observations by content and/or content_hash, returning the restored hashes.

        The newest soft-deleted row per hash comes back, and a hash that already has a live copy is skipped.
        """
        wanted = [*(hashes or []), *(hash_observation(obs) for obs in observations or [])]
        if not wanted:
            raise ValueError("Provide at least one of 'observations' or 'observationHashes'")

        entity_id = self._require_entity_id(project, entity_name)
        with self._conn.transaction():
            refresh_for_entity(self._conn, entity_id, delete=True)
            rows = self._conn.write(
                "UPDATE observations SET deleted_at = NULL WHERE id IN ("
                "SELECT MAX(id) FROM observations WHERE entity_id = ? AND deleted_at IS NOT NULL "
                f"AND content_hash IN ({placeholders(len(wanted))}) "
                "AND content_hash NOT IN (SELECT content_hash FROM observations "
                "WHERE entity_id = ? AND deleted_at IS NULL) GROUP BY content_hash) "
                "RETURNING content_hash",
                [entity_id, *wanted, entity_id],
            ).fetchall()
            if rows:
                self._conn.write("UPDATE entities SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (entity_id,))
            refresh_for_entity(self._conn, entity_id, delete=False)

        return [str(row["content_hash"]) for row in rows]

    def _soft_delete(self, entity_id: int, condition: str, params: Sequence[str]) -> list[str]:
        """Soft-delete the entity's live observations matching a SQL condition, returning their hashes."""
        refresh_for_entity(self._conn, entity_id, delete=True)
        rows = self._conn.write(
            "UPDATE observations SET deleted_at = CURRENT_TIMESTAMP "
            f"WHERE entity_id = ? AND deleted_at IS NULL AND {condition} RETURNING content_hash",
            (entity_id, *params),
        ).fetchall()
        if rows:
            self._conn.write("UPDATE entities SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (entity_id,))
        refresh_for_entity(self._conn, entity_id, delete=False)
        return [str(row["content_hash"]) for row in rows]

    def _require_entity_id(self, project: str, entity_name: str) -> int:
        entity_id = get_entity_id(self._conn, entity_name, get_or_create_project_id(self._conn, project))
        if entity_id is None:
            raise ValueError(f"Entity '{entity_name}' not found in project '{project}'")
        return entity_id

    def refuse_user_loss(
        self, project: str, entity_name: str, hashes: Sequence[str] | None = None, contents: Sequence[str] = ()
    ) -> None:
        """Raise ValueError if an addressed observation has user provenance; hashes None addresses them all."""
        own = self.for_entity(self._require_entity_id(project, entity_name))
        if any(
            o.provenance == "user" and (hashes is None or o.content_hash in hashes or o.content in contents)
            for o in own
        ):
            raise ValueError("Refused: a dream may not remove or demote a user-provenance observation.")

    def inherit_provenance(self, project: str, entity_name: str, contents: Sequence[str]) -> None:
        """Give each untagged observation in contents the provenance of a live copy elsewhere in the project.

        An inferred copy wins over an observed one, and a user copy is never inherited.
        """
        entity_id = self._require_entity_id(project, entity_name)
        copies = self._conn.query_all(
            "SELECT o.id, (SELECT src.provenance FROM observations src JOIN entities e ON src.entity_id = e.id "
            "WHERE src.content = o.content AND src.entity_id != o.entity_id AND src.deleted_at IS NULL "
            "AND e.deleted_at IS NULL AND e.project_id = (SELECT project_id FROM entities WHERE id = o.entity_id) "
            "AND src.provenance IN ('inferred', 'observed') "
            "ORDER BY src.provenance = 'inferred' DESC LIMIT 1) AS source "
            "FROM observations o WHERE o.entity_id = ? AND o.deleted_at IS NULL "
            f"AND o.content IN ({placeholders(len(contents))})",
            (entity_id, *contents),
        )
        raise_provenance(self._conn, [(row["source"], row["id"]) for row in copies])

    def trim_to_outcome(
        self, project: str, entity_name: str, keep_hashes: list[str], *, protect_corrections: bool = False
    ) -> list[str]:
        """Soft-delete all observations on an entity except those with a content_hash in keep_hashes.

        With protect_corrections, user corrections, decisions, lessons and user-provenance observations are kept too.
        Returns the content hashes of the trimmed observations.
        """
        if not keep_hashes:
            raise ValueError("Provide at least one hash in keep_hashes")

        entity_id = self._require_entity_id(project, entity_name)
        if protect_corrections:
            keep_hashes = [
                *keep_hashes,
                *(
                    o.content_hash
                    for o in self.for_entity(entity_id)
                    if o.provenance == "user" or is_protected_observation(o.content)
                ),
            ]
        with self._conn.transaction():
            return self._soft_delete(entity_id, f"content_hash NOT IN ({placeholders(len(keep_hashes))})", keep_hashes)

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
                "UPDATE observations SET vote_score = vote_score + ? "
                f"WHERE entity_id = ? AND {column} = ? AND deleted_at IS NULL",
                (vote, entity_id, value),
            )
            if cursor.rowcount == 0:
                raise ValueError(f"Observation not found in entity '{entity_name}'")
        row = self._conn.query_one(
            f"SELECT vote_score FROM observations WHERE entity_id = ? AND {column} = ? AND deleted_at IS NULL",
            (entity_id, value),
        )
        assert row is not None
        return int(row["vote_score"])

    def merge(self, project: str, entity_name: str, source_hash: str, target_hash: str) -> dict[str, int | list[str]]:
        """Fold one observation into another within a single entity, addressed by content_hash.

        Additive on the target: the target keeps the higher of the two vote scores and its
        own timestamp, a bump that restoring the source does not undo. The source observation is
        soft-deleted and restorable. Only merges observations within one entity, never across entities.
        """
        if source_hash == target_hash:
            raise ValueError("Cannot merge an observation into itself")
        project_id = get_or_create_project_id(self._conn, project)
        entity_id = get_entity_id(self._conn, entity_name, project_id)
        if entity_id is None:
            raise ValueError(f"Entity '{entity_name}' not found in project '{project}'")

        self._find_by_hash(entity_id, entity_name, source_hash, "Source observation")
        self._find_by_hash(entity_id, entity_name, target_hash, "Target observation")
        rows_sql = (
            "SELECT id, vote_score, provenance FROM observations "
            "WHERE entity_id = ? AND content_hash = ? AND deleted_at IS NULL"
        )
        sources = self._conn.query_all(rows_sql, (entity_id, source_hash))
        targets = self._conn.query_all(rows_sql, (entity_id, target_hash))
        with self._conn.transaction():
            self._conn.write_many(
                "UPDATE observations SET vote_score = MAX(vote_score, ?) WHERE id = ?",
                [(max(int(r["vote_score"]) for r in sources), t["id"]) for t in targets],
            )
            raise_provenance(self._conn, [(s["provenance"], t["id"]) for t in targets for s in sources])
            copy_triggers(self._conn, [(s["id"], t["id"]) for t in targets for s in sources])
            hashes = self._soft_delete(entity_id, "content_hash = ?", [source_hash])
        return {"merged": len(hashes), "hashes": hashes}

    def edit(
        self,
        project: str,
        entity_name: str,
        content_hash: str,
        new_text: str,
        old_text: str | None = None,
        *,
        provenance: str | None = None,
        triggers: list[dict[str, str]] | None = None,
    ) -> str:
        """Rewrite one observation in place, addressed by content_hash, returning its new content_hash.

        Without old_text, new_text replaces the whole content; with it, new_text replaces the single
        occurrence of old_text. The observation keeps its id, vote score, timestamp and, unless
        provenance is passed, its provenance. Passed triggers replace the observation's triggers.
        """
        validate_provenance(provenance)
        trigger_rows = None if triggers is None else validate_triggers(triggers)
        if old_text is not None and not old_text:
            raise ValueError("oldText must not be empty")
        project_id = get_or_create_project_id(self._conn, project)
        entity_id = get_entity_id(self._conn, entity_name, project_id)
        if entity_id is None:
            raise ValueError(f"Entity '{entity_name}' not found in project '{project}'")

        row = self._find_by_hash(entity_id, entity_name, content_hash, "Observation")
        content = str(row["content"])
        if old_text is not None:
            occurrences = content.count(old_text)
            if occurrences == 0:
                raise ValueError("oldText not found in observation")
            if occurrences > 1:
                raise ValueError(f"oldText matches {occurrences} times; include more surrounding text")
            new_text = content.replace(old_text, new_text)
        new_content = strip_today_date_prefix(new_text)
        if not new_content.strip():
            raise ValueError("Edited observation must not be empty")
        reject_status_observation(new_content)
        new_hash = hash_observation(new_content)
        if new_hash != content_hash and any(obs.content_hash == new_hash for obs in self.for_entity(entity_id)):
            raise ValueError("Edit duplicates an existing observation; use delete_observations or merge_observations")
        if trigger_rows is not None:
            with self._conn.transaction():
                self._conn.write("DELETE FROM observation_triggers WHERE observation_id = ?", (row["id"],))
                add_triggers(self._conn, [row["id"]], trigger_rows)
        if new_content == content and provenance is None:
            return content_hash

        with self._conn.transaction():
            refresh_for_entity(self._conn, entity_id, delete=True)
            self._conn.write(
                "UPDATE observations SET content = ?, content_hash = ?, provenance = COALESCE(?, provenance) "
                "WHERE id = ?",
                (new_content, new_hash, provenance, row["id"]),
            )
            self._conn.write("UPDATE entities SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (entity_id,))
            refresh_for_entity(self._conn, entity_id, delete=False)
        return new_hash

    def _find_by_hash(self, entity_id: int, entity_name: str, content_hash: str, label: str) -> sqlite3.Row:
        row = self._conn.query_one(
            "SELECT id, content, vote_score, provenance FROM observations "
            "WHERE entity_id = ? AND content_hash = ? AND deleted_at IS NULL",
            (entity_id, content_hash),
        )
        if row is None:
            raise ValueError(f"{label} not found in entity '{entity_name}'")
        return row

    def export_rows(self, entity_id: int) -> list[dict[str, object]]:
        """Return an entity's observations as export dicts, best-first, preserving created_at."""
        rows = self._conn.query_all(
            "SELECT id, content, content_hash, vote_score, created_at, provenance FROM observations "
            "WHERE entity_id = ? AND deleted_at IS NULL ORDER BY vote_score DESC, id",
            (entity_id,),
        )
        return [
            {
                "content": row["content"],
                "content_hash": row["content_hash"],
                "vote_score": int(row["vote_score"]),
                "created_at": row["created_at"],
                "provenance": row["provenance"],
                **({"triggers": triggers} if (triggers := triggers_of(self._conn, row["id"])) else {}),
            }
            for row in rows
        ]
