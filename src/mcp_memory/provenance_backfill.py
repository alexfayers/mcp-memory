"""Opt-in backfill of observation provenance from wording markers, with a dry run and a revert."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
import json
from pathlib import Path
import random
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Collection

    from mcp_memory.storage import Storage

_SAMPLES_PER_RULE = 5
_SAMPLE_CHARS = 200
_REVERT_PREFIX = "provenance-backfill-"
_REVERT_SUFFIX = ".json"
_REVERTED_SUFFIX = ".reverted"
_STAMP_FORMAT = "%Y%m%dT%H%M%SZ"
_SCHEMA_VERSION = 29


@dataclass(frozen=True)
class Rule:
    """A wording marker that assigns a provenance to a NULL-provenance observation."""

    name: str
    provenance: str
    where: str


@dataclass
class RuleResult:
    """The observations one rule claims."""

    name: str
    provenance: str
    ids: list[int] = field(default_factory=list)
    samples: list[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        """Return how many observations the rule claims."""
        return len(self.ids)


# Ordered: the first rule to match an observation wins, so an inferred marker beats a user lead.
_RULES = (
    Rule(
        "inferred-marker",
        "inferred",
        "content GLOB 'INFERRED*' OR content GLOB 'Inferred*' OR content GLOB 'UNVERIFIED*' "
        "OR content GLOB 'Likely*' OR content GLOB '*hypothesis:*'",
    ),
    Rule(
        "observed-marker",
        "observed",
        "content GLOB 'VERIFIED*' OR content GLOB 'OBSERVED*' OR content GLOB 'Verified*' "
        "OR content GLOB 'Source-verified*' OR content GLOB 'Confirmed*'",
    ),
    Rule("user-lead", "user", "content GLOB 'User *' OR content GLOB 'The user *'"),
    Rule("user-quote", "user", "content LIKE '%the user: ''%' OR content LIKE '%User: ''%'"),
)


RULE_NAMES = tuple(rule.name for rule in _RULES)


def plan_backfill(db: Storage, skip: Collection[str] = ()) -> list[RuleResult]:
    """Return, per rule not skipped, the live NULL-provenance observations it claims."""
    if db.connection.query_one("SELECT 1 FROM schema_version WHERE version = ?", (_SCHEMA_VERSION,)) is None:
        raise ValueError("Database is not at schema v29 yet: restart the server to migrate it")
    claimed: set[int] = set()
    results: list[RuleResult] = []
    for rule in _RULES:
        rows = db.connection.query_all(
            f"SELECT id, content FROM observations WHERE provenance IS NULL AND deleted_at IS NULL AND ({rule.where})"
        )
        fresh = [row for row in rows if row["id"] not in claimed]
        claimed.update(row["id"] for row in fresh)
        if rule.name in skip:
            continue
        sampled = random.sample(fresh, min(_SAMPLES_PER_RULE, len(fresh)))
        results.append(
            RuleResult(
                rule.name,
                rule.provenance,
                [row["id"] for row in fresh],
                [str(row["content"])[:_SAMPLE_CHARS] for row in sampled],
            )
        )
    return results


def apply_backfill(db: Storage, revert_dir: Path, skip: Collection[str] = ()) -> tuple[int, Path]:
    """Write the planned provenance values, returning the rows tagged and the file that records them for revert."""
    plan = plan_backfill(db, skip)
    with db.connection.transaction():
        for result in plan:
            db.connection.write_many(
                "UPDATE observations SET provenance = ? WHERE id = ?", [(result.provenance, i) for i in result.ids]
            )
    revert_dir.mkdir(parents=True, exist_ok=True)
    revert_file = revert_dir / f"{_REVERT_PREFIX}{datetime.now(UTC).strftime(_STAMP_FORMAT)}{_REVERT_SUFFIX}"
    tagged = [[i, result.provenance] for result in plan for i in result.ids]
    revert_file.write_text(json.dumps(tagged), encoding="utf-8")
    return len(tagged), revert_file


def revert_backfill(db: Storage, revert_dir: Path) -> int:
    """Undo the newest apply_backfill, leaving rows whose provenance changed since, and return the rows reset."""
    recorded = sorted(revert_dir.glob(f"{_REVERT_PREFIX}*{_REVERT_SUFFIX}")) if revert_dir.is_dir() else []
    if not recorded:
        return 0
    revert_file = recorded[-1]
    rows = json.loads(revert_file.read_text(encoding="utf-8"))
    reverted = 0
    with db.connection.transaction():
        for row_id, provenance in rows:
            reverted += db.connection.write(
                "UPDATE observations SET provenance = NULL WHERE id = ? AND provenance = ?", (row_id, provenance)
            ).rowcount
    revert_file.rename(revert_file.with_name(revert_file.name + _REVERTED_SUFFIX))
    return reverted
