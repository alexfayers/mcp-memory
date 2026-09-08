"""Row-to-model mapping and pure observation helpers."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import re
import sqlite3

from mcp_memory.models import Entity, Observation, Relation

_TODAY_DATE_PREFIX_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})[ \t]*[:,-][ \t]+")


def hash_observation(content: str) -> str:
    """Return the 8-char content-derived hash used to address an observation."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:8]


def strip_today_date_prefix(content: str, today: str | None = None) -> str:
    """Strip a leading "<today's date><separator>" label, since created_at already carries it.

    Only strips when the captured date equals `today` (UTC, defaults to the current date) and
    the remainder after the required separator and whitespace is non-empty - a bare date with
    no separator (e.g. "2026-09-08 was the day...") is left untouched as real content, and a
    past date is left untouched as real history rather than a redundant label.
    """
    match = _TODAY_DATE_PREFIX_RE.match(content)
    if match is None:
        return content
    if today is None:
        today = datetime.now(tz=UTC).strftime("%Y-%m-%d")
    if match.group(1) != today:
        return content
    return content[match.end() :] or content


def budget_observations(observations: list[Observation], max_chars: int) -> list[Observation]:
    """Trim an already-vote-sorted observation list to a cumulative content-char budget.

    Keeps whole observations best-first while the running total of len(content) stays within
    max_chars, always keeping at least the first observation. Callers report how many were
    dropped via Entity.observations_omitted. A negative max_chars means unlimited (returned
    unchanged). A max_chars of 0 naturally yields just the first observation via the
    always-keep-first rule below - it is NOT special-cased separately.
    """
    if max_chars < 0 or not observations:
        return observations
    kept: list[Observation] = []
    total = 0
    for obs in observations:
        projected = total + len(obs.content)
        if not kept or projected <= max_chars:
            kept.append(obs)
            total = projected
        else:
            break
    return kept


def build_observation(row: sqlite3.Row) -> Observation:
    return Observation(
        content=row["content"],
        content_hash=row["content_hash"],
        vote_score=int(row["vote_score"]),
        created_at=row["created_at"],
    )


def build_relation(row: sqlite3.Row) -> Relation:
    return Relation(source=row["source"], target=row["target"], relation_type=row["relation_type"])


def build_entity(
    row: sqlite3.Row,
    observations: list[Observation],
    omitted: int,
    project_name: str | None = None,
) -> Entity:
    return Entity(
        name=row["name"],
        entity_type=row["entity_type"],
        observations=observations,
        status=row["status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        project_name=project_name,
        vote_score=int(row["vote_score"]),
        observations_omitted=omitted,
    )
