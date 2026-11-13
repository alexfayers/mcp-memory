"""Apply the deterministic, needs_review-false steps of an audit plan without a model."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, cast

from .config import get_archive_enabled
from .models import Relation

if TYPE_CHECKING:
    from .storage import Storage

logger = logging.getLogger("mcp-memory")

Step = dict[str, object]


def _apply_step(db: Storage, step: Step) -> str:
    """Run one safe plan step and return its audit line, raising ValueError when the graph rejects it."""
    tool = step["tool"]
    args = cast("dict[str, object]", step["arguments"])
    project = cast("str", args["project"])
    reason = cast("str", step["reason"])
    if tool == "merge_entities":
        source, target = cast("str", args["source"]), cast("str", args["target"])
        db.entities.merge(project, source, target)
        return f"[{project}/{source}] - merged into {target}: {reason} (auto)"
    if tool == "rename_entity":
        old_name, new_name = cast("str", args["old_name"]), cast("str", args["new_name"])
        db.entities.rename(project, old_name, new_name)
        return f"[{project}/{old_name}] - renamed to {new_name}: {reason} (auto)"
    if tool == "merge_observations":
        entity, source_hash = cast("str", args["entityName"]), cast("str", args["sourceHash"])
        target_hash = cast("str", args["targetHash"])
        db.observations.merge(project, entity, source_hash, target_hash)
        return f"[{project}/{entity}#{source_hash[:8]}] - merged observation into #{target_hash[:8]}: {reason} (auto)"
    if tool == "create_relations":
        relations = cast("list[dict[str, str]]", args["relations"])
        db.relations.create(project, [Relation(r["source"], r["target"], r["relationType"]) for r in relations])
        return "\n".join(
            f"[{project}/{r['source']}] - linked {r['relationType']} -> {r['target']}: {reason} (auto)"
            for r in relations
        )
    raise ValueError(f"Unsupported plan step tool '{tool}'")


def apply_safe_steps(db: Storage, steps: list[Step]) -> list[str]:
    """Apply every step with needs_review false, returning one audit line per change.

    A step the graph rejects is logged and skipped, so one stale step never blocks the rest.
    """
    lines: list[str] = []
    for step in steps:
        if step["needs_review"]:
            continue
        try:
            lines.append(_apply_step(db, step))
        except ValueError:
            logger.warning("skipped safe plan step %s", step, exc_info=True)
    return lines


def archive_stale(db: Storage, project: str | None = None) -> list[str]:
    """Archive stale resolved entities (of one project, or all) when enabled, returning one audit line each."""
    if not get_archive_enabled():
        return []
    return [
        f"[{project}/{name}] - archived: resolved and untouched past the archive window (auto)"
        for project, name in db.maintenance.archive_stale_entities(project=project)
    ]
