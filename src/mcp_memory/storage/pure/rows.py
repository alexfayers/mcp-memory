"""Row-to-model mapping and pure observation helpers."""

from __future__ import annotations

import hashlib
import re
import sqlite3

from mcp_memory.models import Entity, Observation, Relation

_MONTH = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?"
_DATE = rf"(?:\d{{4}}-\d{{2}}-\d{{2}}|\d{{1,2}}\s+{_MONTH}(?:\s+\d{{4}})?|{_MONTH}\s+\d{{1,2}}(?:,?\s+\d{{4}})?)"
_TIME = r"\d{1,2}:\d{2}(?::\d{2})?\s*(?:Z|UTC)?"
_STAMP = rf"(?:{_DATE}(?:[T\s]+{_TIME})?|{_TIME})"
_STAMP_LABEL_RE = re.compile(rf"^\(?{_STAMP}\)?[ \t]*[:,-][ \t]+(?=\S)", re.IGNORECASE)
_STAMPED_LABEL_RE = re.compile(
    rf"^(?P<label>[^\n:]{{1,60}}?)\s*(?:\(\s*{_STAMP}\s*\)|\b(?:on|at)\s+{_STAMP})\s*:\s+(?=\S)", re.IGNORECASE
)
_STATUS_OBSERVATION = re.compile(r"\s*status:", re.IGNORECASE)


def reject_status_observation(content: str) -> None:
    """Raise if the date-stripped observation content starts with ``status:``, since status has its own field."""
    if _STATUS_OBSERVATION.match(content):
        raise ValueError(
            "Observation starts with 'status:'. Set the entity status with set_entity_status instead, "
            "not as an observation."
        )


def hash_observation(content: str) -> str:
    """Return the 8-char content-derived hash used to address an observation."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:8]


def strip_redundant_label(content: str) -> str:
    """Strip a leading date or time label, since created_at already carries it.

    Strips a label that is only a date or time ("26 Nov: fact"), and a date or time in
    parentheses or after "on"/"at" at the end of a longer label ("Decision (26 Nov): fact"
    becomes "Decision: fact"). A date elsewhere in the text is real content and stays.
    """
    if match := _STAMP_LABEL_RE.match(content):
        return content[match.end() :]
    if match := _STAMPED_LABEL_RE.match(content):
        return f"{match['label']}: {content[match.end() :]}"
    return content


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
        provenance=row["provenance"],
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
