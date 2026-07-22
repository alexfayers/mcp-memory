"""Read-only structural audit of the memory graph.

Backs the memory-review skill's mechanical checks: enumerates the deterministic,
zero-judgment hygiene violations (orphans, naming, bloat, relation/star-graph
errors, strongly-downvoted entities) as one JSON report. All fix decisions
(merge/delete/rename, duplicate detection) stay LLM-driven in the skill - this
only finds the violations. Never mutates the graph.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .database import DatabaseManager

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

# The only types allowed to carry zero relations (see server.RELATION_EXEMPT_TYPES).
_RELATION_EXEMPT_TYPES = frozenset({"project", "user-preferences"})

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

# vote_score at or below which an entity is surfaced as a rot-review prompt (SKILL.md
# section 5). Section 5 gives no literal number, only "strongly negative" and a reference
# to the dream/GC saturation floor of -10 (database.py _GC_DOWNVOTE_FLOOR); -5 is well past
# incidental single downvotes yet short of the floor where the GC would reap the entity.
_NEGATIVE_VOTE_THRESHOLD = -5

_ENTITY_SQL = (
    "SELECT e.name, et.name AS entity_type, e.status, e.vote_score, p.name AS project, "
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

        if entity_type not in _RELATION_EXEMPT_TYPES and row["rel_count"] == 0:
            orphans.append(ref)

        if entity_type == "project" and row["name"] != f"project/{row['project']}":
            misused_project_type.append(ref)

        if not row["name"].startswith(STANDARD_PREFIXES):
            unprefixed.append(ref)

        ceiling = _ceiling_for(entity_type, row["status"])
        if ceiling is not None and row["obs_count"] > ceiling:
            oversized.append({**ref, "count": row["obs_count"], "threshold": ceiling})

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
