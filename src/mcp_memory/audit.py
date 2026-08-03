"""Read-only structural audit of the memory graph.

Backs the memory-review skill's mechanical checks: enumerates the deterministic,
zero-judgment hygiene violations (orphans, naming, bloat, relation/star-graph
errors, strongly-downvoted entities) as one JSON report. All fix decisions
(merge/delete/rename, duplicate detection) stay LLM-driven in the skill - this
only finds the violations. Never mutates the graph.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .database import DatabaseManager
    from .models import Observation

Finding = dict[str, object]
_ORPHAN_EXEMPT_ENTITY_TYPES = frozenset({"project"})

# A well-formed entity name starts with one of these type prefixes; anything else
# is a legacy unprefixed entity (create_entities rejects new ones).
STANDARD_PREFIXES = (
    "project/",
    "feature/",
    "task/",
    "user-preferences/",
    "pattern/",
    "knowledge/",
)

# Observation-count ceilings per entity type, from memory-review SKILL.md section 3's
# "Size targets" table. An entity whose observation count exceeds its ceiling is oversized.
_OBS_CEILINGS = {
    "user-preferences": 30,
    "project": 30,
    "knowledge": 20,
    "pattern": 15,
    "feature": 10,
}
# A resolved task holds only an outcome summary (SKILL.md section 3: 0-3 observations).
_RESOLVED_TASK_CEILING = 3

# Case-sensitive markers of an outcome/decision observation, always kept when trimming.
_OUTCOME_KEYWORDS = ("Decided:", "Resolved:", "RESOLVED")

# vote_score at or below which an entity is surfaced as a rot-review prompt (SKILL.md
# section 5). Section 5 gives no literal number, only "strongly negative" and a reference
# to the dream/GC saturation floor of -10 (database.py _GC_DOWNVOTE_FLOOR); -5 is well past
# incidental single downvotes yet short of the floor where the GC would reap the entity.
_NEGATIVE_VOTE_THRESHOLD = -5

_ENTITY_SQL = (
    "SELECT e.id AS entity_id, e.name, et.name AS entity_type, e.status, e.vote_score, "
    "p.name AS project, "
    "(SELECT COUNT(*) FROM observations o WHERE o.entity_id = e.id) AS obs_count, "
    "(SELECT COUNT(*) FROM relations r "
    "JOIN entities se ON r.source_id = se.id "
    "JOIN entities te ON r.target_id = te.id "
    "WHERE (r.source_id = e.id OR r.target_id = e.id) "
    "AND se.deleted_at IS NULL AND te.deleted_at IS NULL) AS rel_count "
    "FROM entities e "
    "JOIN entity_types et ON e.entity_type_id = et.id "
    "JOIN projects p ON e.project_id = p.id "
    "WHERE e.deleted_at IS NULL"
)

_RELATION_SQL = (
    "SELECT se.name AS source, st.name AS source_type, "
    "te.name AS target, tt.name AS target_type, "
    "rt.name AS relation_type, sp.name AS project "
    "FROM relations r "
    "JOIN entities se ON r.source_id = se.id "
    "JOIN entities te ON r.target_id = te.id "
    "JOIN entity_types st ON se.entity_type_id = st.id "
    "JOIN entity_types tt ON te.entity_type_id = tt.id "
    "JOIN relation_types rt ON r.relation_type_id = rt.id "
    "JOIN projects sp ON se.project_id = sp.id "
    "WHERE se.deleted_at IS NULL AND te.deleted_at IS NULL"
)

_SCOPE_COUNT_SQL = (
    "SELECT p.name AS project, COUNT(e.id) AS n "
    "FROM projects p "
    "LEFT JOIN entities e ON e.project_id = p.id AND e.deleted_at IS NULL "
    "GROUP BY p.id, p.name "
    "ORDER BY p.name"
)


def _ceiling_for(entity_type: str, status: str | None) -> int | None:
    """Return the observation-count ceiling for an entity, or None if it has no target."""
    if entity_type == "task":
        return _RESOLVED_TASK_CEILING if status == "resolved" else None
    return _OBS_CEILINGS.get(entity_type)


def audit_graph(db: DatabaseManager, project: str | None = None) -> dict[str, object]:
    """Build the structural-hygiene report for one project scope, or all when project is None.

    The report is informational: it locates mechanical violations for the memory-review
    skill to act on. It never mutates the graph. Every finding carries its project, so an
    all-projects report stays attributable.
    """
    entity_params = () if project is None else (project,)
    entity_sql = _ENTITY_SQL + ("" if project is None else " AND p.name = ?")
    entity_rows = db._db.execute(entity_sql, entity_params).fetchall()

    relation_sql = _RELATION_SQL + ("" if project is None else " AND sp.name = ?")
    relation_rows = db._db.execute(relation_sql, entity_params).fetchall()

    orphans: list[dict[str, object]] = []
    misused_project_type: list[dict[str, object]] = []
    unprefixed: list[dict[str, object]] = []
    oversized: list[dict[str, object]] = []
    negative_vote_entities: list[dict[str, object]] = []

    for row in entity_rows:
        ref = {"name": row["name"], "entity_type": row["entity_type"], "project": row["project"]}
        entity_type = row["entity_type"]

        if entity_type not in _ORPHAN_EXEMPT_ENTITY_TYPES and row["rel_count"] == 0:
            orphans.append(ref)

        if entity_type == "project" and row["name"] != f"project/{row['project']}":
            misused_project_type.append(ref)

        if not row["name"].startswith(STANDARD_PREFIXES):
            unprefixed.append(ref)

        ceiling = _ceiling_for(entity_type, row["status"])
        if ceiling is not None and row["obs_count"] > ceiling:
            oversized.append(
                {
                    **ref,
                    "count": row["obs_count"],
                    "threshold": ceiling,
                    "status": row["status"],
                    "entity_id": row["entity_id"],
                }
            )

        if row["vote_score"] <= _NEGATIVE_VOTE_THRESHOLD:
            negative_vote_entities.append({**ref, "vote_score": row["vote_score"]})

    relation_violations: list[dict[str, object]] = []
    star_graph_tasks: list[dict[str, object]] = []
    for row in relation_rows:
        if row["source_type"] == "task" and row["target_type"] == "project":
            edge = {
                "task": row["source"],
                "target": row["target"],
                "relation_type": row["relation_type"],
                "project": row["project"],
            }
            star_graph_tasks.append(edge)
            if row["relation_type"] == "belongs-to":
                relation_violations.append(edge)

    scope_rows = db._db.execute(_SCOPE_COUNT_SQL).fetchall()
    ghost_scopes = [
        r["project"]
        for r in scope_rows
        if r["n"] == 0 and (project is None or r["project"] == project)
    ]

    return {
        "project": project,
        "orphans": orphans,
        "misused_project_type": misused_project_type,
        "unprefixed": unprefixed,
        "ghost_scopes": ghost_scopes,
        "oversized": oversized,
        "relation_violations": relation_violations,
        "star_graph_tasks": star_graph_tasks,
        "negative_vote_entities": negative_vote_entities,
    }


def _keep_hashes(observations: list[Observation], ceiling: int) -> list[str]:
    """Deterministically pick which observations to keep when trimming to the ceiling."""
    keep = {
        o.content_hash
        for o in observations
        if any(keyword in o.content for keyword in _OUTCOME_KEYWORDS)
    }
    top = min(observations, key=lambda o: (-o.vote_score, o.content_hash))
    keep.add(top.content_hash)

    ranked = sorted(observations, key=lambda o: (-o.vote_score, o.content_hash))
    ordered = [o.content_hash for o in ranked if o.content_hash in keep]
    return ordered[:ceiling] if len(ordered) > ceiling else ordered


def _outcome_obs_count(observations: list[Observation]) -> int:
    """Count observations that carry an outcome/decision marker."""
    return sum(
        1 for o in observations if any(keyword in o.content for keyword in _OUTCOME_KEYWORDS)
    )


def _implements_step(edge: Finding, reason: str) -> dict[str, object]:
    """A needs-review step that relinks a task straight-to-project edge to a feature."""
    return {
        "tool": "create_relations",
        "arguments": {
            "project": edge["project"],
            "relations": [
                {"source": edge["task"], "relationType": "implements", "target": "<FEATURE_TBD>"}
            ],
        },
        "reason": reason,
        "needs_review": True,
    }


def _findings(report: dict[str, object], key: str) -> list[Finding]:
    """Cast a report category to its list-of-findings type for the plan builder."""
    return cast("list[Finding]", report[key])


def _is_subsumed(dropped: Observation, kept: list[Observation]) -> bool:
    """Whether a dropped observation is a near-duplicate of some kept observation.

    Case-insensitive substring containment either way - cheap and, per the user's rule
    that trim must never delete a distinct fact, deliberately conservative: anything not
    caught here forces needs_review rather than risking a silent loss.
    """
    dropped_text = dropped.content.casefold()
    return any(
        dropped_text in k.content.casefold() or k.content.casefold() in dropped_text for k in kept
    )


def _trim_step(db: DatabaseManager, entity: Finding) -> dict[str, object]:
    """A trim step for one oversized entity - auto-applied only if every dropped
    observation is a near-duplicate of a kept one, else flagged for review."""
    ceiling = (
        _ceiling_for(cast("str", entity["entity_type"]), cast("str | None", entity["status"]))
        or _RESOLVED_TASK_CEILING
    )
    observations = db._get_observations_full(cast("int", entity["entity_id"]))
    keep_hashes = _keep_hashes(observations, ceiling)
    kept = [o for o in observations if o.content_hash in keep_hashes]
    dropped = [o for o in observations if o.content_hash not in keep_hashes]
    needs_review = not all(_is_subsumed(o, kept) for o in dropped)
    return {
        "tool": "trim_observations_to_outcome",
        "arguments": {
            "project": entity["project"],
            "name": entity["name"],
            "keep_hashes": keep_hashes,
        },
        "reason": (
            f"{entity['entity_type']} '{entity['name']}' has {entity['count']} "
            f"observations (ceiling {ceiling})"
            + (
                "; some dropped observations are not near-duplicates of a kept one - review "
                "before applying"
                if needs_review
                else ""
            )
        ),
        "needs_review": needs_review,
    }


def _split_step(entity: Finding, ceiling: int, outcome_count: int) -> dict[str, object]:
    """An advisory step: the entity bundles multiple distinct outcomes; split it, don't trim."""
    return {
        "action": "consider_split",
        "entity": entity["name"],
        "project": entity["project"],
        "reason": (
            f"{entity['entity_type']} '{entity['name']}' has {outcome_count} distinct outcome "
            f"observations (ceiling {ceiling}); trimming would discard real outcomes. Consider "
            f"splitting into separate single-scope entities - give each new entity a relation "
            f"(e.g. implements to its feature) or create_entities will reject it."
        ),
        "needs_review": True,
    }


def _review_step(entity: Finding) -> dict[str, object]:
    """An advisory step for a non-task oversized entity.

    project/feature/pattern/knowledge/user-preferences entities hold ongoing documentation,
    not a one-time outcome, so the Decided:/Resolved: keyword trim that works for a resolved
    task has nothing to anchor on and would discard real, current facts. Leave curation to a
    human/LLM instead of trimming blindly.
    """
    return {
        "action": "review_oversized",
        "entity": entity["name"],
        "project": entity["project"],
        "reason": (
            f"{entity['entity_type']} '{entity['name']}' has {entity['count']} observations "
            f"(ceiling {entity['threshold']}); this type holds ongoing documentation rather "
            "than a one-time outcome, so it cannot be trimmed safely by outcome keyword. "
            "Manually curate down to the ceiling (demote or merge stale observations, or "
            "split into a new single-scope entity with its own relation)."
        ),
        "needs_review": True,
    }


def _oversized_step(db: DatabaseManager, entity: Finding) -> dict[str, object]:
    """Propose a split for a resolved task bundling multiple outcomes, else a deterministic
    trim - or, for non-task types, an advisory review step (see _review_step)."""
    entity_type = cast("str", entity["entity_type"])
    if entity_type != "task":
        return _review_step(entity)
    ceiling = (
        _ceiling_for(entity_type, cast("str | None", entity["status"])) or _RESOLVED_TASK_CEILING
    )
    observations = db._get_observations_full(cast("int", entity["entity_id"]))
    outcome_count = _outcome_obs_count(observations)
    if outcome_count > ceiling:
        return _split_step(entity, ceiling, outcome_count)
    return _trim_step(db, entity)


def propose_plan(db: DatabaseManager, report: dict[str, object]) -> list[dict[str, object]]:
    """Turn an audit report into a structured, deterministic list of fix-it tool calls."""
    steps: list[dict[str, object]] = []

    steps.extend(_oversized_step(db, e) for e in _findings(report, "oversized"))

    steps.extend(
        {
            "tool": "rename_entity",
            "arguments": {
                "project": e["project"],
                "old_name": e["name"],
                "new_name": f"{e['entity_type']}/{e['name']}",
            },
            "reason": f"name '{e['name']}' lacks a standard type prefix",
            "needs_review": False,
        }
        for e in _findings(report, "unprefixed")
    )

    steps.extend(
        {
            "tool": "delete_project",
            "arguments": {"project": scope},
            "reason": "project scope has 0 entities",
            "needs_review": True,
        }
        for scope in cast("list[str]", report["ghost_scopes"])
    )

    steps.extend(
        {
            "tool": "delete_entity",
            "arguments": {"project": e["project"], "name": e["name"]},
            "reason": f"vote_score {e['vote_score']} below threshold",
            "needs_review": True,
        }
        for e in _findings(report, "negative_vote_entities")
    )

    steps.extend(
        {
            "tool": "create_relations",
            "arguments": {
                "project": e["project"],
                "relations": [
                    {
                        "source": e["name"],
                        "relationType": "<RELATION_TBD>",
                        "target": "<TARGET_TBD>",
                    }
                ],
            },
            "reason": "entity has no relations; link it to a feature/parent",
            "needs_review": True,
        }
        for e in _findings(report, "orphans")
    )

    steps.extend(
        {
            "tool": "delete_entity",
            "arguments": {"project": e["project"], "name": e["name"]},
            "reason": (
                f"'{e['name']}' uses the reserved project type but is not the repo root; "
                "recreate it as a task/feature/pattern with the right type and a relation"
            ),
            "needs_review": True,
        }
        for e in _findings(report, "misused_project_type")
    )

    steps.extend(
        _implements_step(
            edge,
            f"task '{edge['task']}' belongs-to a project; replace with an implements edge to "
            "the feature it modifies, then delete the belongs-to edge",
        )
        for edge in _findings(report, "relation_violations")
    )

    steps.extend(
        _implements_step(
            edge,
            f"task '{edge['task']}' links straight to a project via "
            f"'{edge['relation_type']}'; prefer an implements edge to a feature",
        )
        for edge in _findings(report, "star_graph_tasks")
        if edge["relation_type"] != "belongs-to"
    )

    return steps
