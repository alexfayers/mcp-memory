"""Data models for the MCP memory server."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, get_args

EntityStatus = Literal["planned", "in-progress", "blocked", "resolved", "archived"]

VALID_STATUSES = get_args(EntityStatus)

# Entity types exempt from autonomous orphan GC.
STRUCTURAL_ENTITY_TYPES = frozenset({"project", "user-preferences"})

MAX_VOTE_MAGNITUDE = 3

VALID_RELATION_TYPES = (
    "implements",
    "depends-on",
    "blocks",
    "relates-to",
    "belongs-to",
    "part-of",
    "used-by",
    "used-in",
)

RELATION_TYPE_ALIASES = {
    "related-to": "relates-to",
    "extends": "implements",
    "uses": "implements",
    "tests": "implements",
    "blocked-by": "depends-on",
    "follows": "depends-on",
    "subproject-of": "part-of",
    "has-feature": "part-of",
    "overlay-for": "used-in",
    "has-overlay": "used-by",
    "informs": "relates-to",
    "constrains": "relates-to",
    "applies-to": "relates-to",
    "supports": "relates-to",
    "participates-in": "relates-to",
    "explores": "relates-to",
    "examines": "relates-to",
    "enables": "relates-to",
    "finding-in": "relates-to",
    "assigned-to": "belongs-to",
    "owned-by": "belongs-to",
    "changes": "implements",
    "modifies": "implements",
    "modified": "implements",
    "verifies": "implements",
    "continues": "depends-on",
    "replaces": "relates-to",
    "discovered-by": "relates-to",
    "research-for": "relates-to",
    "context-for": "relates-to",
    "has-architecture": "part-of",
    "has-todos": "relates-to",
    "self": "relates-to",
}

_CAMEL_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def normalize_relation_type(relation_type: str) -> str:
    """Canonicalize a relation type to its preferred form.

    Trims whitespace, splits camelCase, converts underscores to hyphens,
    lowercases, then applies the synonym alias map.
    """
    canonical = _CAMEL_BOUNDARY_RE.sub("-", relation_type.strip())
    canonical = re.sub(r"[_-]+", "-", canonical).strip("-").lower()
    if not canonical:
        raise ValueError(f"Relation type must be a non-empty string, got: {relation_type!r}")
    return RELATION_TYPE_ALIASES.get(canonical, canonical)


@dataclass
class Observation:
    content: str
    content_hash: str
    vote_score: int = 0


@dataclass
class Entity:
    name: str
    entity_type: str
    observations: list[Observation]
    status: EntityStatus | None = None
    created_at: str | None = None
    updated_at: str | None = None
    project_name: str | None = None
    vote_score: int = 0


@dataclass
class Relation:
    source: str
    target: str
    relation_type: str
