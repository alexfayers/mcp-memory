"""Row-to-model mapping and pure observation helpers."""

from __future__ import annotations

import hashlib
import re
import sqlite3

from mcp_memory.models import Entity, Observation, Relation

_STATUS_OBSERVATION = re.compile(r"\s*status:", re.IGNORECASE)

_MONTH = (
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
    r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b"
)
_DATE_RE = re.compile(rf"(\d{{4}}-\d{{2}}-\d{{2}})|(\d{{1,2}}) ({_MONTH})(?: (\d{{4}}))?", re.IGNORECASE)
_TIME_RE = re.compile(r"\d{1,2}:\d{2}(?::\d{2})?Z?")
_STAMP = rf"(?:(?:{_DATE_RE.pattern})(?:[ T,]+{_TIME_RE.pattern})?|{_TIME_RE.pattern})"
_PROVENANCE_LABEL_RE = re.compile(
    rf"^(user|observed|inferred)(?: [a-z]+)?(?: (?:on|at) {_STAMP}| \({_STAMP}\))?: +", re.IGNORECASE
)
_STAMP_LABEL_RE = re.compile(rf"^\(?{_STAMP}\)? *[:,-] +", re.IGNORECASE)
_LABEL_STAMP_RE = re.compile(rf"^[^:()\n]{{1,60}}?( \({_STAMP}\))(?=:)", re.IGNORECASE)


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


def strip_redundant_labels(content: str, provenance: str | None) -> tuple[str, list[str]]:
    """Strip leading labels that repeat a date, a time or the matching provenance, returning the text and the labels.

    Handles "User decision on 26 Nov: X", "2026-11-20 11:14Z: X" and "Design preference (26 Nov): X".
    A date inside the content itself ("the cert expires 2027-01-01") is kept, and so is an empty remainder.
    """
    labels: list[str] = []
    while True:
        match = _PROVENANCE_LABEL_RE.match(content)
        if (match is not None and match.group(1).lower() == provenance) or (match := _STAMP_LABEL_RE.match(content)):
            span = match.span()
        elif match := _LABEL_STAMP_RE.match(content):
            span = match.span(1)
        else:
            return content, labels
        rest = content[: span[0]] + content[span[1] :]
        if not rest.strip():
            return content, labels
        labels.append(content[span[0] : span[1]])
        content = rest


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
