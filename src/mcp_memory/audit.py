"""Read-only structural audit of the memory graph.

Backs the memory-review skill's mechanical checks: enumerates the deterministic,
zero-judgment hygiene violations (orphans, naming, bloat, relation/star-graph
errors, strongly-downvoted entities) as one JSON report. All fix decisions
(merge/delete/rename, duplicate detection) stay LLM-driven in the skill - this
only finds the violations. Never mutates the graph.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import sqlite3
from typing import TYPE_CHECKING, cast

from .models import is_protected_observation
from .storage.pure.rows import build_observation

if TYPE_CHECKING:
    from .models import Observation
    from .storage import Storage

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
# A resolved task holds only the ask, decisions and why, and outcomes (SKILL.md section 3: 0-3 observations).
_RESOLVED_TASK_CEILING = 3

# Case-sensitive markers of an outcome/decision observation, always kept when trimming.
_OUTCOME_KEYWORDS = ("Decided:", "Resolved:", "RESOLVED")

# vote_score at or below which an entity is surfaced as a rot-review prompt (SKILL.md
# section 5). Section 5 gives no literal number, only "strongly negative" and a reference
# to the dream/GC saturation floor of -10 (storage/maintenance.py GC_DOWNVOTE_FLOOR); -5 is well past
# incidental single downvotes yet short of the floor where the GC would reap the entity.
_NEGATIVE_VOTE_THRESHOLD = -5

_ENTITY_SQL = (
    "SELECT e.id AS entity_id, e.name, et.name AS entity_type, e.status, e.vote_score, "
    "p.name AS project, "
    "(SELECT COUNT(*) FROM observations o WHERE o.entity_id = e.id AND o.deleted_at IS NULL) AS obs_count, "
    "(SELECT COUNT(*) FROM relations r "
    "JOIN entities se ON r.source_id = se.id "
    "JOIN entities te ON r.target_id = te.id "
    "WHERE (r.source_id = e.id OR r.target_id = e.id) "
    "AND r.deleted_at IS NULL AND se.deleted_at IS NULL AND te.deleted_at IS NULL) AS rel_count "
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
    "WHERE r.deleted_at IS NULL AND se.deleted_at IS NULL AND te.deleted_at IS NULL"
)

_SCOPE_COUNT_SQL = (
    "SELECT p.name AS project, COUNT(e.id) AS n "
    "FROM projects p "
    "LEFT JOIN entities e ON e.project_id = p.id AND e.deleted_at IS NULL "
    "GROUP BY p.id, p.name "
    "ORDER BY p.name"
)


_OBSERVATION_SQL = (
    "SELECT e.id AS entity_id, e.name, p.name AS project, "
    "o.content, o.content_hash, o.vote_score, o.created_at "
    "FROM observations o "
    "JOIN entities e ON o.entity_id = e.id "
    "JOIN projects p ON e.project_id = p.id "
    "WHERE o.deleted_at IS NULL AND e.deleted_at IS NULL"
)


@dataclass
class _EntityObservations:
    """An entity's live observations and the ones a containment merge folds away."""

    project: str
    name: str
    observations: list[Observation] = field(default_factory=list)
    absorbed: dict[str, str] = field(default_factory=dict)

    @property
    def survivors(self) -> list[Observation]:
        """Observations left once every containment merge is applied."""
        return [o for o in self.observations if o.content_hash not in self.absorbed]


def _ceiling_for(entity_type: str, status: str | None) -> int | None:
    """Return the observation-count ceiling for an entity, or None if it has no target."""
    if entity_type == "task":
        return _RESOLVED_TASK_CEILING if status == "resolved" else None
    return _OBS_CEILINGS.get(entity_type)


def _scan_entities(rows: list[sqlite3.Row]) -> dict[str, list[dict[str, object]]]:
    """Classify entity rows into the orphan/misused/unprefixed/oversized/negative-vote buckets."""
    orphans: list[dict[str, object]] = []
    misused_project_type: list[dict[str, object]] = []
    unprefixed: list[dict[str, object]] = []
    oversized: list[dict[str, object]] = []
    negative_vote_entities: list[dict[str, object]] = []

    for row in rows:
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
            oversized.append({
                **ref,
                "count": row["obs_count"],
                "threshold": ceiling,
                "status": row["status"],
                "entity_id": row["entity_id"],
            })

        if row["vote_score"] <= _NEGATIVE_VOTE_THRESHOLD:
            negative_vote_entities.append({**ref, "vote_score": row["vote_score"]})

    return {
        "orphans": orphans,
        "misused_project_type": misused_project_type,
        "unprefixed": unprefixed,
        "oversized": oversized,
        "negative_vote_entities": negative_vote_entities,
    }


def _scan_relations(rows: list[sqlite3.Row]) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Classify relation rows into star-graph task-to-project edges and belongs-to violations among them."""
    relation_violations: list[dict[str, object]] = []
    star_graph_tasks: list[dict[str, object]] = []
    for row in rows:
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
    return relation_violations, star_graph_tasks


def audit_graph(db: Storage, project: str | None = None) -> dict[str, object]:
    """Build the structural-hygiene report for one project scope, or all when project is None.

    The report is informational: it locates mechanical violations for the memory-review
    skill to act on. It never mutates the graph. Every finding carries its project, so an
    all-projects report stays attributable.
    """
    entity_params = () if project is None else (project,)
    entity_sql = _ENTITY_SQL + ("" if project is None else " AND p.name = ?")
    entity_rows = db.connection.query_all(entity_sql, entity_params)

    relation_sql = _RELATION_SQL + ("" if project is None else " AND sp.name = ?")
    relation_rows = db.connection.query_all(relation_sql, entity_params)

    entity_findings = _scan_entities(entity_rows)
    relation_violations, star_graph_tasks = _scan_relations(relation_rows)

    scope_rows = db.connection.query_all(_SCOPE_COUNT_SQL)
    ghost_scopes = [r["project"] for r in scope_rows if r["n"] == 0 and (project is None or r["project"] == project)]

    return {
        "project": project,
        "ghost_scopes": ghost_scopes,
        "relation_violations": relation_violations,
        "star_graph_tasks": star_graph_tasks,
        **entity_findings,
    }


def _keep_hashes(observations: list[Observation], ceiling: int) -> list[str]:
    """Deterministically pick which observations to keep when trimming, never dropping a protected one."""
    protected = {o.content_hash for o in observations if is_protected_observation(o.content)}
    keep = protected | {o.content_hash for o in observations if any(k in o.content for k in _OUTCOME_KEYWORDS)}
    top = min(observations, key=lambda o: (-o.vote_score, o.content_hash))
    keep.add(top.content_hash)

    ranked = sorted(observations, key=lambda o: (o.content_hash not in protected, -o.vote_score, o.content_hash))
    ordered = [o.content_hash for o in ranked if o.content_hash in keep]
    return ordered[: max(ceiling, len(protected))]


def _outcome_obs_count(observations: list[Observation]) -> int:
    """Count observations that carry an outcome/decision marker."""
    return sum(1 for o in observations if any(keyword in o.content for keyword in _OUTCOME_KEYWORDS))


def _implements_step(edge: Finding, reason: str) -> dict[str, object]:
    """A needs-review step that relinks a task straight-to-project edge to a feature."""
    return {
        "tool": "create_relations",
        "arguments": {
            "project": edge["project"],
            "relations": [{"source": edge["task"], "relationType": "implements", "target": "<FEATURE_TBD>"}],
        },
        "reason": reason,
        "needs_review": True,
    }


def _findings(report: dict[str, object], key: str) -> list[Finding]:
    """Cast a report category to its list-of-findings type for the plan builder."""
    return cast("list[Finding]", report[key])


def _observation_rank(observation: Observation) -> tuple[int, int, str]:
    """Total order that decides which of two overlapping observations survives."""
    return (len(observation.content), observation.vote_score, observation.content_hash)


def _absorbed_into(observations: list[Observation]) -> dict[str, str]:
    """Map each observation whose text sits inside a higher-ranked one to the longest such container.

    Case-insensitive containment. The longest container is never itself absorbed, so a merge
    never targets an observation that another merge removes.
    """
    folded = {o.content_hash: o.content.casefold() for o in observations}
    absorbed: dict[str, str] = {}
    for observation in observations:
        containers = [
            other
            for other in observations
            if _observation_rank(other) > _observation_rank(observation)
            and folded[observation.content_hash] in folded[other.content_hash]
        ]
        if containers:
            absorbed[observation.content_hash] = max(containers, key=_observation_rank).content_hash
    return absorbed


def _load_observations(db: Storage, project: str | None) -> dict[int, _EntityObservations]:
    """Group every live observation by entity, with the containment merges already worked out."""
    rows = db.connection.query_all(
        _OBSERVATION_SQL + ("" if project is None else " AND p.name = ?") + " ORDER BY e.id, o.vote_score DESC, o.id",
        () if project is None else (project,),
    )
    grouped: dict[int, _EntityObservations] = {}
    for row in rows:
        entity = grouped.setdefault(row["entity_id"], _EntityObservations(row["project"], row["name"]))
        entity.observations.append(build_observation(row))
    for entity in grouped.values():
        entity.absorbed = _absorbed_into(entity.observations)
    return grouped


def _merge_observation_steps(grouped: dict[int, _EntityObservations]) -> list[dict[str, object]]:
    """Steps folding each observation into the longer one that contains it.

    Every merge needs review. Containment can flip the meaning ("Use TLS" inside "Do not use TLS")
    and a case-only difference can separate distinct facts ("Use API_TOKEN" vs "Use api_token").
    """
    steps: list[dict[str, object]] = []
    for entity in grouped.values():
        steps.extend(
            {
                "tool": "merge_observations",
                "arguments": {
                    "project": entity.project,
                    "entityName": entity.name,
                    "sourceHash": source_hash,
                    "targetHash": target_hash,
                },
                "reason": "observation text is contained in a longer observation of the same entity",
                "needs_review": True,
            }
            for source_hash, target_hash in entity.absorbed.items()
        )
    return steps


def _trim_step(entity: Finding, observations: list[Observation]) -> dict[str, object]:
    """A trim step for one oversized entity - always flagged for review, since a trim drops facts."""
    ceiling = (
        _ceiling_for(cast("str", entity["entity_type"]), cast("str | None", entity["status"])) or _RESOLVED_TASK_CEILING
    )
    return {
        "tool": "trim_observations_to_outcome",
        "arguments": {
            "project": entity["project"],
            "name": entity["name"],
            "keep_hashes": _keep_hashes(observations, ceiling),
        },
        "reason": (
            f"{entity['entity_type']} '{entity['name']}' has {entity['count']} "
            f"observations (ceiling {ceiling}); review before applying"
        ),
        "needs_review": True,
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


def _oversized_step(entity: Finding, observations: list[Observation]) -> dict[str, object] | None:
    """Propose a split for a resolved task bundling multiple outcomes, else a trim - or, for
    non-task types, an advisory review step (see _review_step). None when the observations
    left after merging fit the ceiling."""
    entity_type = cast("str", entity["entity_type"])
    ceiling = _ceiling_for(entity_type, cast("str | None", entity["status"])) or _RESOLVED_TASK_CEILING
    if len(observations) <= ceiling:
        return None
    if entity_type != "task":
        return _review_step(entity)
    outcome_count = _outcome_obs_count(observations)
    if outcome_count > ceiling:
        return _split_step(entity, ceiling, outcome_count)
    return _trim_step(entity, observations)


def propose_plan(db: Storage, report: dict[str, object]) -> list[dict[str, object]]:
    """Turn an audit report into a structured, deterministic list of fix-it tool calls."""
    grouped = _load_observations(db, cast("str | None", report["project"]))
    steps: list[dict[str, object]] = []

    steps.extend(_merge_observation_steps(grouped))

    for entity in _findings(report, "oversized"):
        step = _oversized_step(entity, grouped[cast("int", entity["entity_id"])].survivors)
        if step is not None:
            steps.append(step)

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
