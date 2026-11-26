"""Data models for the MCP memory server."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Literal, get_args

EntityStatus = Literal["planned", "in-progress", "blocked", "resolved", "archived"]

VALID_STATUSES = get_args(EntityStatus)

Provenance = Literal["user", "observed", "inferred"]

VALID_PROVENANCES = get_args(Provenance)

# Trust rank of a stored observation's provenance, which only ever rises: user > observed > inferred > untagged.
PROVENANCE_RANK: dict[str | None, int] = {"user": 3, "observed": 2, "inferred": 1, None: 0}

# Entity types exempt from autonomous orphan GC.
STRUCTURAL_ENTITY_TYPES = frozenset({"project", "user-preferences"})

# Observations recording a user correction, decision, lesson or outcome are never dropped by an automated trim.
_PROTECTED_OBSERVATION = re.compile(
    r"Correction|User corrected|pushed back|\bdecision|\bdecided\b|User chose|Lesson|Resolved:|Outcome:",
    re.IGNORECASE,
)


def is_protected_observation(content: str) -> bool:
    """Return whether an observation records a user correction, decision, lesson or outcome."""
    return _PROTECTED_OBSERVATION.search(content) is not None


MAX_VOTE_MAGNITUDE = 3


def validate_provenance(provenance: str | None) -> None:
    """Raise ValueError unless provenance is None or one of VALID_PROVENANCES."""
    if provenance is not None and provenance not in VALID_PROVENANCES:
        raise ValueError(f"Invalid provenance '{provenance}'. Must be one of: {VALID_PROVENANCES}")


# The action each trigger kind takes when it matches.
TRIGGER_ACTIONS = {"pattern": "block", "path": "inject", "mention": "inject", "change": "inject"}
_REGEX_TRIGGER_KINDS = frozenset({"pattern", "mention"})
MAX_TRIGGER_CHARS = 200
# A `git patch-id --stable` id: a SHA-1 or SHA-256 hex digest.
_PATCH_ID = re.compile(r"[0-9a-f]{40}([0-9a-f]{24})?")
# A quantified group that itself holds a quantifier, such as (a+)+, can backtrack exponentially.
_NESTED_QUANTIFIER = re.compile(r"\([^()]*[+*}][^()]*\)[+*{]")


def validate_triggers(triggers: list[dict[str, str]]) -> list[tuple[str, str, str, str]]:
    """Return each trigger as (kind, value, tool, action), raising ValueError for an invalid one.

    A trigger needs a known kind and a non-empty value. A pattern or mention value must compile as a regex,
    a change value must be a patch-id, and only a pattern may name a tool.
    """
    rows: list[tuple[str, str, str, str]] = []
    for trigger in triggers:
        kind, value, tool = trigger.get("kind", ""), trigger.get("value", ""), trigger.get("tool") or ""
        if kind not in TRIGGER_ACTIONS:
            raise ValueError(f"Invalid trigger kind '{kind}'. Must be one of: {tuple(TRIGGER_ACTIONS)}")
        if not value:
            raise ValueError("A trigger needs a non-empty value")
        if tool and kind != "pattern":
            raise ValueError("Only a pattern trigger may name a tool")
        if len(value) > MAX_TRIGGER_CHARS:
            raise ValueError(f"A trigger value must be at most {MAX_TRIGGER_CHARS} characters")
        if kind == "change" and not _PATCH_ID.fullmatch(value):
            raise ValueError(f"Invalid change trigger '{value}': expected a `git patch-id --stable` id")
        if kind in _REGEX_TRIGGER_KINDS:
            if _NESTED_QUANTIFIER.search(value):
                raise ValueError(f"Invalid {kind} trigger regex '{value}': nested quantifiers can hang the hook")
            try:
                re.compile(value)
            except re.error as exc:
                raise ValueError(f"Invalid {kind} trigger regex '{value}': {exc}") from exc
        rows.append((kind, value, tool, TRIGGER_ACTIONS[kind]))
    return rows


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

# Relation types whose loss would sever a task from the feature or task it builds on.
STRUCTURAL_RELATION_TYPES = ("implements", "depends-on")

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


def validate_vote(vote: int) -> None:
    """Raise ValueError unless vote is a nonzero integer within MAX_VOTE_MAGNITUDE."""
    if vote == 0 or abs(vote) > MAX_VOTE_MAGNITUDE:
        raise ValueError(
            f"Invalid vote '{vote}'. Must be a nonzero integer from -{MAX_VOTE_MAGNITUDE} to {MAX_VOTE_MAGNITUDE}"
        )


@dataclass
class Observation:
    content: str
    content_hash: str
    vote_score: int = 0
    created_at: str | None = None
    provenance: Provenance | None = None


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
    observations_omitted: int = 0


@dataclass
class Relation:
    source: str
    target: str
    relation_type: str
