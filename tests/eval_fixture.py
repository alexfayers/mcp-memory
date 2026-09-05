"""Populated deterministic eval fixture.

Reproduces the live graph's measured shape - type mix, status mix, project sizes, ages,
votes, observation counts/lengths, and the query/relevance-label distribution - at
roughly 1/10 scale. Content (entity names, project names, query and observation text) is
invented; every count, age, vote and length below is a measured live quantile.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from mcp_memory.eval import evaluate

from .eval_harness import _FIXTURE_NOW, _pinned, mark_used

if TYPE_CHECKING:
    from collections.abc import Iterator
    from datetime import datetime
    from pathlib import Path

    from mcp_memory.eval import EvalReport
    from mcp_memory.storage import Storage

_K = 10
_POOL_TERM = "service"

# archetype -> entity type, updated_at age days, vote score, obs count, obs chars, status
_ARCHETYPES: dict[str, tuple[str, int, int, int, int, str | None]] = {
    "fresh-task": ("task", 0, 0, 3, 138, None),
    "live-task": ("task", 10, 1, 4, 247, "in-progress"),
    "queued-task": ("task", 20, 0, 4, 138, "planned"),
    "recipe": ("pattern", 26, 2, 9, 396, None),
    "mid-task": ("task", 29, 0, 6, 247, "resolved"),
    "aging-task": ("task", 33, -1, 9, 396, "resolved"),
    "stuck-task": ("task", 33, 0, 21, 568, "blocked"),
    "shelved-task": ("task", 50, 0, 3, 247, "archived"),
    "floored-task": ("task", 60, 0, 2, 247, "resolved"),
    "shipped-item": ("feature", 70, 1, 4, 396, "resolved"),
    "closed-note": ("knowledge", 79, 0, 3, 247, "resolved"),
    "old-recipe": ("pattern", 95, 14, 21, 247, None),
    "dead-task": ("task", 110, -6, 2, 12, "resolved"),
    "retired-recipe": ("pattern", 130, -1, 4, 138, "archived"),
    "reference": ("knowledge", 7, 3, 14, 568, None),
    "work-item": ("feature", 5, 0, 9, 396, None),
    "scope": ("project", 8, 2, 45, 247, None),
    "prefs": ("user-preferences", 3, 27, 21, 951, None),
}

# archetype -> total instances across the full 130-entity fixture, drawn down as a
# shared pool across every project in table order
_ARCHETYPE_COUNTS: dict[str, int] = {
    "fresh-task": 10,
    "live-task": 8,
    "queued-task": 17,
    "recipe": 19,
    "mid-task": 16,
    "aging-task": 12,
    "stuck-task": 1,
    "shelved-task": 6,
    "floored-task": 8,
    "shipped-item": 4,
    "closed-note": 3,
    "old-recipe": 4,
    "dead-task": 3,
    "retired-recipe": 4,
    "reference": 7,
    "work-item": 4,
    "scope": 3,
    "prefs": 1,
}

# structural archetypes - "scope" (the 3 "project"-typed entities) and "prefs" (the sole
# "user-preferences" entity, the only carrier of vote saturation) - are never left to the
# competitive fill draw, which can empty out before reaching them. They are placed
# explicitly, project -> archetypes to draw unconditionally ahead of that project's
# competitive fill.
_STRUCTURAL_ARCHETYPES: frozenset[str] = frozenset({"scope", "prefs"})
_STRUCTURAL_SEEDS: dict[str, tuple[str, ...]] = {
    "core-platform": ("scope", "scope", "scope", "prefs"),
}

# age delta days, vote delta, obs count delta, obs char delta - consumed by a running
# counter over build order, so no two structures land on the same offset sequence
_JITTER: tuple[tuple[int, int, int, int], ...] = (
    (0, 0, 0, 0),
    (2, 0, 2, 58),
    (-1, 1, -1, -26),
    (5, 0, 4, 172),
    (1, -1, 0, 12),
    (-2, 0, 7, -70),
    (7, 2, -2, 321),
    (3, 0, 1, -46),
    (9, -3, 0, 90),
    (-3, 0, 3, 14),
    (4, 1, -1, -88),
)

# project name, entity count, topic count
_PROJECTS: tuple[tuple[str, int, int], ...] = (
    ("core-platform", 31, 2),
    ("edge-gateway", 24, 2),
    ("batch-loader", 19, 1),
    ("report-builder", 15, 1),
    ("shared-schema", 9, 0),
    ("job-runner", 8, 0),
    ("alert-router", 7, 0),
    ("token-broker", 6, 0),
    ("data-lint", 5, 0),
    ("legacy-import", 3, 0),
    ("scratch-pad", 2, 0),
    ("one-off", 1, 0),
)

# topic index, hosting project, head term - unique per topic, no shared stem, and no
# term is an entity-type word
_TOPICS: tuple[tuple[int, str, str], ...] = (
    (0, "core-platform", "checkout"),
    (1, "core-platform", "webhook"),
    (2, "edge-gateway", "throttle"),
    (3, "edge-gateway", "session"),
    (4, "batch-loader", "ingest"),
    (5, "report-builder", "export"),
)

# spine role, in fixed order, -> the archetype supplying its type/age/vote/obs shape.
# Every topic emits this spine; "tie-a"/"tie-b" are exempt from jitter.
_SPINE: tuple[tuple[str, str], ...] = (
    ("decoy", "fresh-task"),
    ("durable-hit", "reference"),
    ("decayed-hit", "floored-task"),
    ("upvoted-hit", "old-recipe"),
    ("tie-a", "queued-task"),
    ("tie-b", "queued-task"),
    ("unreachable", "dead-task"),
    ("archived", "shelved-task"),
)

# spine role -> (name slug, head term in the name column?, head term in observations?).
# Only "decoy" and "archived" hold the head term in the name; "unreachable" holds
# neither term in its observations, reaching search only via _POOL_TERM.
_SPINE_CONTENT: dict[str, tuple[str, bool, bool]] = {
    "decoy": ("widget", True, False),
    "durable-hit": ("order-flow-notes", False, True),
    "decayed-hit": ("legacy-cleanup", False, True),
    "upvoted-hit": ("idempotent-retry", False, True),
    "tie-a": ("queue-review-a", False, True),
    "tie-b": ("queue-review-b", False, True),
    "unreachable": ("misc-followup", False, False),
    "archived": ("legacy", True, True),
}

# "old-recipe" (4 instances) and "dead-task" (3) cannot each supply one per topic across
# all 6 topics, so once a role's primary archetype is exhausted its remaining topics draw
# this same-type-and-status fallback instead - never a fresh archetype outside the budget.
_SPINE_FALLBACK: dict[str, str] = {
    "upvoted-hit": "recipe",
    "unreachable": "mid-task",
}

# topic-hosting project -> total queries, unreachable-labelling singles, other singles
# (over durable-hit/decayed-hit/upvoted-hit), doubles, triples, single-token queries.
# Totals sum to 84; unreachable-singles to 15 (the measured 16.4%-beyond-rank-10 share);
# doubles to 16 and triples to 5 (together with the 4 cross-project queries' 2+2+5+9,
# this is what reproduces the live {1:71, 2:18, 3:5, 5:1, 9:1} histogram exactly); and
# single-token counts to 29 (with the 8 scope-marker queries below, 37 of 96 overall).
_TOPIC_QUERY_PLAN: dict[int, tuple[int, int, int, int, int, int]] = {
    0: (22, 4, 12, 4, 2, 8),
    1: (18, 3, 11, 3, 1, 6),
    2: (14, 3, 8, 2, 1, 5),
    3: (12, 2, 7, 2, 1, 4),
    4: (10, 2, 6, 2, 0, 3),
    5: (8, 1, 4, 3, 0, 3),
}

# modifier word lists cycled onto a topic's head term for a non-single-token query, so
# token counts span 2..6 without a single query text literal per row.
_MODIFIERS: tuple[tuple[str, ...], ...] = (
    ("flow",),
    ("retry", "pattern"),
    ("config", "rollout", "plan"),
    ("latency", "budget", "review", "queue"),
    ("error", "rate", "escalation", "handling", "window"),
)

# non-topic-hosting project -> a marker term unique to that scope, planted in exactly
# one of its fill entities so a dedicated small/medium-scope query can label it. Terms
# are invented compounds sharing no stem with any topic head term or with each other.
_SCOPE_MARKERS: dict[str, str] = {
    "data-lint": "lintrule",
    "legacy-import": "migrator",
    "scratch-pad": "sandboxnote",
    "one-off": "onetime",
    "shared-schema": "schemadiff",
    "job-runner": "jobqueue",
    "alert-router": "pagebridge",
    "token-broker": "tokencache",
}

# the four smallest _SCOPE_MARKERS projects (1/2/3/5 entities) get single-entity-scope
# queries; the rest get "medium"-scope queries. Both buckets are capped at 4 by design.
_SMALL_SCOPE_PROJECTS: tuple[str, ...] = (
    "one-off",
    "scratch-pad",
    "legacy-import",
    "data-lint",
)
_MEDIUM_SCOPE_PROJECTS: tuple[str, ...] = (
    "token-broker",
    "alert-router",
    "job-runner",
    "shared-schema",
)

# cross-project retrievals: which topics' terms and roles compete in one project=None
# search. The 9- and 5-relevant rows and two 2-relevant rows are what closes the
# {1:71, 2:18, 3:5, 5:1, 9:1} histogram once the per-topic plan above supplies the rest.
_CROSS_PROJECT_QUERIES: tuple[tuple[tuple[int, ...], tuple[tuple[int, str], ...]], ...] = (
    (
        (0, 2, 4),
        (
            (0, "durable-hit"),
            (0, "decayed-hit"),
            (0, "upvoted-hit"),
            (0, "unreachable"),
            (2, "durable-hit"),
            (2, "decayed-hit"),
            (2, "upvoted-hit"),
            (2, "unreachable"),
            (4, "durable-hit"),
        ),
    ),
    (
        (1, 3),
        (
            (1, "durable-hit"),
            (1, "decayed-hit"),
            (1, "upvoted-hit"),
            (1, "unreachable"),
            (3, "durable-hit"),
        ),
    ),
    ((4, 5), ((4, "durable-hit"), (5, "durable-hit"))),
    ((0, 5), ((0, "upvoted-hit"), (5, "upvoted-hit"))),
)


def _shaped(archetype: str, delta: tuple[int, int, int, int]) -> tuple[str, int, int, int, int, str | None]:
    """Apply `delta` to `archetype`'s base (type, age, vote, obs_count, obs_chars, status)."""
    entity_type, age, vote, obs_count, obs_chars, status = _ARCHETYPES[archetype]
    age_delta, vote_delta, count_delta, chars_delta = delta
    return (
        entity_type,
        max(age + age_delta, 0),
        vote + vote_delta,
        max(obs_count + count_delta, 1),
        max(obs_chars + chars_delta, 12),
        status,
    )


def _filler(terms: tuple[str, ...], index: int, target_chars: int) -> str:
    """Build one observation of about `target_chars` characters by repeating `terms`."""
    unit = " ".join((*terms, f"note-{index}")) + " "
    repeats = target_chars // len(unit) + 1
    return (unit * repeats)[:target_chars].rstrip()


def _observation_set(terms: tuple[str, ...], count: int, chars: int) -> list[str]:
    """Build `count` filler observations of about `chars` characters each."""
    return [_filler(terms, index, chars) for index in range(1, count + 1)]


def _record_retrieval(
    db: Storage,
    retrieval_id: str,
    tool: str,
    query: str,
    hits: list[tuple[str, str, int]],
    used: tuple[str, ...],
) -> None:
    """Record one labelled retrieval with its `used` entities and surfaced_at both pinned."""
    db.telemetry.record_surfaced(tool, query, retrieval_id, hits)
    mark_used(db, retrieval_id, *used)
    with db.connection.transaction():
        db.connection.write(
            "UPDATE surfaced_entities SET surfaced_at = ? WHERE retrieval_id = ?",
            (_pinned(0), retrieval_id),
        )


def _topic_query_specs(
    plan: tuple[int, int, int, int, int, int],
) -> list[tuple[tuple[str, ...], bool]]:
    """Expand one topic's `_TOPIC_QUERY_PLAN` row into (relevant roles, single-token?) specs."""
    total, n_unreachable, n_singles, n_doubles, n_triples, n_single_token = plan
    other_roles = ("durable-hit", "decayed-hit", "upvoted-hit")
    pairs = (
        ("durable-hit", "decayed-hit"),
        ("durable-hit", "upvoted-hit"),
        ("decayed-hit", "upvoted-hit"),
    )

    roles: list[tuple[str, ...]] = [("unreachable",)] * n_unreachable
    roles += [(other_roles[i % len(other_roles)],) for i in range(n_singles)]
    roles += [pairs[i % len(pairs)] for i in range(n_doubles)]
    roles += [other_roles] * n_triples
    assert len(roles) == total, f"plan {plan} produced {len(roles)} specs, not {total}"

    return [(role_set, index < n_single_token) for index, role_set in enumerate(roles)]


def _seed_topic_queries(
    db: Storage,
    topic_index: int,
    term: str,
    project: str,
    spine_names: dict[tuple[int, str], str],
) -> tuple[int, set[str]]:
    """Record one topic's labelled retrievals per `_TOPIC_QUERY_PLAN`.

    Returns (retrieval count, relevant names).
    """

    def slot(role: str) -> str:
        return spine_names[(topic_index, role)]

    decoy = slot("decoy")
    relevant: set[str] = set()
    specs = _topic_query_specs(_TOPIC_QUERY_PLAN[topic_index])

    for index, (roles, single_token) in enumerate(specs, start=1):
        modifier = " ".join(_MODIFIERS[index % len(_MODIFIERS)])
        query = term if single_token else f"{term} {modifier}"
        used = tuple(slot(role) for role in roles)
        ranked = (decoy, *used)
        _record_retrieval(
            db,
            f"rid-{term}-{index}",
            "search_nodes",
            query,
            [(project, name, rank) for rank, name in enumerate(ranked, start=1)],
            used,
        )
        relevant.update(used)

    return len(specs), relevant


def _seed_cross_project_queries(
    db: Storage,
    topics: tuple[tuple[int, str, str], ...],
    spine_names: dict[tuple[int, str], str],
    built_topics: set[int],
) -> tuple[int, set[str]]:
    """Record the project=None retrievals from `_CROSS_PROJECT_QUERIES`.

    Skips any row whose topics are not all in `built_topics`, so a partial build (fewer
    than all six topics) does not raise. Returns (retrieval count, relevant names).
    """
    topic_project = {index: project for index, project, _ in topics}
    topic_term = {index: term for index, _, term in topics}

    relevant: set[str] = set()
    count = 0
    for index, (topics_involved, roles) in enumerate(_CROSS_PROJECT_QUERIES, start=1):
        if not set(topics_involved) <= built_topics:
            continue
        query = " ".join(topic_term[t] for t in topics_involved)
        used = tuple(spine_names[(t, role)] for t, role in roles)
        hits = [(topic_project[t], spine_names[(t, "decoy")], rank) for rank, t in enumerate(topics_involved, start=1)]
        hits += [
            (topic_project[t], spine_names[(t, role)], rank)
            for rank, (t, role) in enumerate(roles, start=len(hits) + 1)
        ]
        _record_retrieval(db, f"rid-cross-{index}", "search_all_projects", query, hits, used)
        relevant.update(used)
        count += 1

    return count, relevant


def _seed_scope_queries(
    db: Storage,
    marker_names: dict[str, str],
    entities_by_project: dict[str, list[str]],
) -> tuple[int, set[str]]:
    """Record one small- or medium-scope retrieval per project in `marker_names`.

    A `_SMALL_SCOPE_PROJECTS` scope is queried by its unique marker term, returning only
    the marker entity. A `_MEDIUM_SCOPE_PROJECTS` scope is queried by the shared pool term
    instead, returning every entity in the scope with the marker placed away from rank 1.
    """
    relevant: set[str] = set()
    count = 0
    for project, name in marker_names.items():
        if project in _MEDIUM_SCOPE_PROJECTS:
            term = _POOL_TERM
            others = [n for n in entities_by_project[project] if n != name]
            middle = len(others) // 2
            ranked = (*others[:middle], name, *others[middle:])
            hits = [(project, n, rank) for rank, n in enumerate(ranked, start=1)]
        else:
            term = _SCOPE_MARKERS[project]
            hits = [(project, name, 1)]
        _record_retrieval(db, f"rid-scope-{project}", "search_nodes", term, hits, (name,))
        relevant.add(name)
        count += 1

    return count, relevant


@dataclass(frozen=True)
class EvalFixture:
    """A built populated fixture: the database plus everything a test needs to measure it."""

    db: Storage
    path: Path
    now: datetime
    k: int
    project_names: tuple[str, ...]
    entity_names: tuple[str, ...]
    manifest: tuple[tuple[str, str, str, int, int, int, str | None], ...]
    query_count: int
    relevant_names: frozenset[str]
    expected_baseline: EvalReport
    archetype_supply: dict[str, int]
    _spine_names: dict[tuple[int, str], str] = field(repr=False)

    def name_for(self, topic: int, slot: str) -> str:
        """Return the entity name at `slot` ('decoy', 'durable-hit', ...) in `topic`."""
        return self._spine_names[(topic, slot)]

    def tie_pair(self, topic: int) -> tuple[str, str]:
        """Return `topic`'s (tie-a, tie-b) entity names, an exact-score tie pair."""
        return self.name_for(topic, "tie-a"), self.name_for(topic, "tie-b")


def _resolve_archetype(role: str, primary_archetype: str, remaining: dict[str, int]) -> str:
    """Return the archetype to draw for `role`, falling back once its primary is exhausted."""
    if remaining[primary_archetype] > 0:
        return primary_archetype
    return _SPINE_FALLBACK.get(role, primary_archetype)


def _seed_spine_entities(
    topic_index: int,
    term: str,
    jitter: Iterator[tuple[int, int, int, int]],
    remaining: dict[str, int],
    ages: dict[str, int],
    votes: dict[str, int],
    spine_names: dict[tuple[int, str], str],
) -> list[dict[str, object]]:
    """Build one topic's `_SPINE` entities, mutating the shared build state.

    Does not append to `manifest` - the caller does that from the final, project-grouped
    entity list, so `manifest` row order matches the entities' actual DB insertion order.
    """
    entities: list[dict[str, object]] = []
    for role, primary_archetype in _SPINE:
        archetype = _resolve_archetype(role, primary_archetype, remaining)
        delta = _JITTER[0] if role in ("tie-a", "tie-b") else next(jitter)
        entity_type, age, vote, obs_count, obs_chars, status = _shaped(archetype, delta)
        remaining[archetype] -= 1
        slug, head_in_name, head_in_obs = _SPINE_CONTENT[role]
        slug = f"{term}-{slug}" if head_in_name else f"t{topic_index}-{slug}"
        name = f"{entity_type}/{slug}"
        terms = (term, _POOL_TERM) if head_in_obs else (_POOL_TERM,)
        entities.append(
            {
                "name": name,
                "entityType": entity_type,
                "observations": _observation_set(terms, obs_count, obs_chars),
                "status": status,
            }
        )
        ages[name] = age
        votes[name] = vote
        spine_names[(topic_index, role)] = name

    return entities


def _seed_structural_entities(
    project: str,
    jitter: Iterator[tuple[int, int, int, int]],
    remaining: dict[str, int],
    ages: dict[str, int],
    votes: dict[str, int],
) -> list[dict[str, object]]:
    """Draw `project`'s `_STRUCTURAL_SEEDS` entities unconditionally, ahead of its fill pool."""
    entities: list[dict[str, object]] = []
    for index, archetype in enumerate(_STRUCTURAL_SEEDS.get(project, ()), start=1):
        delta = next(jitter)
        entity_type, age, vote, obs_count, obs_chars, status = _shaped(archetype, delta)
        remaining[archetype] -= 1
        name = f"{entity_type}/{archetype}-{project}-struct{index}"
        entities.append(
            {
                "name": name,
                "entityType": entity_type,
                "observations": _observation_set((_POOL_TERM,), obs_count, obs_chars),
                "status": status,
            }
        )
        ages[name] = age
        votes[name] = vote

    return entities


def _seed_fill_entities(
    project: str,
    fill_needed: int,
    jitter: Iterator[tuple[int, int, int, int]],
    remaining: dict[str, int],
    ages: dict[str, int],
    votes: dict[str, int],
    marker_names: dict[str, str],
) -> list[dict[str, object]]:
    """Draw `fill_needed` filler entities for `project` from the shared archetype pool.

    A scope-marker project's first draw is biased to a non-archived-status archetype, so
    its marker entity is never hidden from a default (non-`include_archived`) search. The
    structural archetypes (`_STRUCTURAL_ARCHETYPES`) never compete here - see
    `_seed_structural_entities`.
    """
    entities: list[dict[str, object]] = []
    filled = 0
    needs_marker = project in _SCOPE_MARKERS and project not in marker_names

    if needs_marker:
        marker_archetype = next(
            (
                archetype
                for archetype in _ARCHETYPE_COUNTS
                if remaining[archetype] > 0
                and archetype not in _STRUCTURAL_ARCHETYPES
                and _ARCHETYPES[archetype][5] != "archived"
            ),
            None,
        )
        if marker_archetype is None:
            msg = f"no non-archived archetype stock left for {project}'s scope marker"
            raise RuntimeError(msg)
        delta = next(jitter)
        entity_type, age, vote, obs_count, obs_chars, status = _shaped(marker_archetype, delta)
        remaining[marker_archetype] -= 1
        filled += 1
        name = f"{entity_type}/{marker_archetype}-{project}-{filled}"
        marker_names[project] = name
        entities.append(
            {
                "name": name,
                "entityType": entity_type,
                "observations": _observation_set((_SCOPE_MARKERS[project], _POOL_TERM), obs_count, obs_chars),
                "status": status,
            }
        )
        ages[name] = age
        votes[name] = vote

    for archetype in _ARCHETYPE_COUNTS:
        if archetype in _STRUCTURAL_ARCHETYPES:
            continue
        while remaining[archetype] > 0 and filled < fill_needed:
            delta = next(jitter)
            entity_type, age, vote, obs_count, obs_chars, status = _shaped(archetype, delta)
            remaining[archetype] -= 1
            filled += 1
            name = f"{entity_type}/{archetype}-{project}-{filled}"
            entities.append(
                {
                    "name": name,
                    "entityType": entity_type,
                    "observations": _observation_set((_POOL_TERM,), obs_count, obs_chars),
                    "status": status,
                }
            )
            ages[name] = age
            votes[name] = vote
        if filled >= fill_needed:
            break

    return entities


def _manifest_rows(
    project: str,
    entities: list[dict[str, object]],
    ages: dict[str, int],
    votes: dict[str, int],
) -> list[tuple[str, str, str, int, int, int, str | None]]:
    """Derive `project`'s manifest rows from its entities, in their given insertion order."""
    rows: list[tuple[str, str, str, int, int, int, str | None]] = []
    for entity in entities:
        name = entity["name"]
        rows.append(
            (
                project,
                name,
                entity["entityType"],
                ages[name],
                votes[name],
                len(entity["observations"]),
                entity["status"],
            )
        )
    return rows


def _apply_votes(db: Storage, votes: dict[str, int]) -> None:
    """Write every nonzero `votes` entry onto its entity in one batch."""
    with db.connection.transaction():
        db.connection.write_many(
            "UPDATE entities SET vote_score = ? WHERE name = ?",
            [(vote, name) for name, vote in votes.items() if vote != 0],
        )


def _pin_timestamps(db: Storage, ages: dict[str, int]) -> None:
    """Backdate every entity's `created_at`/`updated_at` to its designed age.

    Must run after every other write - see H1 in the design doc.
    """
    with db.connection.transaction():
        for name, age in ages.items():
            pinned = _pinned(age)
            db.connection.write(
                "UPDATE entities SET created_at = ?, updated_at = ? WHERE name = ?",
                (pinned, pinned, name),
            )


def _build_populated_fixture(
    db: Storage,
    *,
    projects: tuple[tuple[str, int, int], ...] = _PROJECTS,
    topics: tuple[tuple[int, str, str], ...] = _TOPICS,
) -> EvalFixture:
    """Seed `db` with the populated eval fixture and return its `EvalFixture` handle.

    `projects`/`topics` default to the full 12 projects and 6 topics; a caller may pass
    a restricted subset (e.g. one topic in one project) for a smaller, faster build.
    """
    jitter = itertools.cycle(_JITTER)
    remaining = dict(_ARCHETYPE_COUNTS)
    manifest: list[tuple[str, str, str, int, int, int, str | None]] = []
    ages: dict[str, int] = {}
    votes: dict[str, int] = {}
    spine_names: dict[tuple[int, str], str] = {}
    marker_names: dict[str, str] = {}

    # Every topic's spine draws happen before any project's fill draws, project-grouped
    # table order notwithstanding: a fallback-less spine role (e.g. "decoy"/fresh-task)
    # has just enough budget for all 6 topics combined, but not enough to also survive an
    # earlier-processed project's fill loop competing for the same archetype first.
    spine_entities: dict[str, list[dict[str, object]]] = {}
    for topic_index, project, term in topics:
        spine_entities.setdefault(project, []).extend(
            _seed_spine_entities(topic_index, term, jitter, remaining, ages, votes, spine_names)
        )

    for project, size, _topic_count in projects:
        hosted = [t for t in topics if t[1] == project]
        entities = list(spine_entities.get(project, []))
        entities += _seed_structural_entities(project, jitter, remaining, ages, votes)

        fill_needed = size - len(hosted) * len(_SPINE) - len(_STRUCTURAL_SEEDS.get(project, ()))
        entities += _seed_fill_entities(project, fill_needed, jitter, remaining, ages, votes, marker_names)

        db.entities.create(project, entities)
        # Built from the entities just passed to `create_entities`, in that exact order,
        # so manifest row n is entity id n (ascending ids are the exact-score tiebreak).
        manifest += _manifest_rows(project, entities, ages, votes)

    _apply_votes(db, votes)

    entities_by_project: dict[str, list[str]] = {}
    for project, name, *_rest in manifest:
        entities_by_project.setdefault(project, []).append(name)

    relevant_names: set[str] = set()
    query_count = 0
    for topic_index, project, term in topics:
        added, relevant = _seed_topic_queries(db, topic_index, term, project, spine_names)
        query_count += added
        relevant_names.update(relevant)

    built_topics = {topic_index for topic_index, _, _ in topics}
    cross_added, cross_relevant = _seed_cross_project_queries(db, topics, spine_names, built_topics)
    query_count += cross_added
    relevant_names.update(cross_relevant)

    scope_added, scope_relevant = _seed_scope_queries(db, marker_names, entities_by_project)
    query_count += scope_added
    relevant_names.update(scope_relevant)

    _pin_timestamps(db, ages)

    return EvalFixture(
        db=db,
        path=db.connection.path,
        now=_FIXTURE_NOW,
        k=_K,
        project_names=tuple(p for p, _, _ in projects),
        entity_names=tuple(name for _, name, *_ in manifest),
        manifest=tuple(manifest),
        query_count=query_count,
        relevant_names=frozenset(relevant_names),
        expected_baseline=evaluate(db, k=_K, now=_FIXTURE_NOW),
        archetype_supply=dict(remaining),
        _spine_names=spine_names,
    )
