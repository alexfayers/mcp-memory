"""Ranking-quality metrics over recorded retrieval telemetry.

Pure, dependency-free scoring functions plus helpers that turn the ``surfaced_entities``
table (populated by the implicit-usefulness observer) into labelled queries: the relevance
label for a surfaced entity is simply "it was used afterwards" (``used_at IS NOT NULL``).
This makes search accuracy a measured number - mean precision@k and MRR over real queries -
rather than a guess, and gives phase D a regression gate it must not degrade.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .database import DatabaseManager
    from .models import Entity

# Tool name for a cross-project search, whose labelled query re-runs against all projects.
_ALL_PROJECTS_TOOL = "search_all_projects"


def precision_at_k(ranked: Sequence[str], relevant: set[str], k: int) -> float:
    """Fraction of the top-k ranked items that are relevant.

    The denominator is ``min(k, len(ranked))`` so a short result list is not penalised for
    having fewer than k slots. Returns 0.0 when there are no items to score.
    """
    cutoff = min(k, len(ranked))
    if cutoff <= 0:
        return 0.0
    hits = sum(1 for name in ranked[:cutoff] if name in relevant)
    return hits / cutoff


def reciprocal_rank(ranked: Sequence[str], relevant: set[str]) -> float:
    """Reciprocal of the 1-based position of the first relevant item, or 0.0 if none."""
    for index, name in enumerate(ranked, start=1):
        if name in relevant:
            return 1.0 / index
    return 0.0


@dataclass(frozen=True)
class LabelledQuery:
    """One recorded retrieval: the query, its ranked entity names, and the used (relevant) set."""

    project: str | None
    query: str
    ranked: list[str]
    relevant: set[str]


@dataclass(frozen=True)
class EvalReport:
    """Aggregate ranking quality over the labelled queries with at least one relevant hit."""

    query_count: int
    mean_precision_at_k: float
    mrr: float
    k: int


def iter_labelled_queries(db: DatabaseManager) -> Iterator[LabelledQuery]:
    """Reconstruct each recorded retrieval from ``surfaced_entities`` as a labelled query.

    Hits are grouped by ``retrieval_id`` and ordered by their stored rank; the relevance
    label is the set of surfaced entities that were subsequently used (``used_at`` set). A
    cross-project search re-runs against all projects, so its query scope is ``None``.
    """
    rows = db._db.execute(
        "SELECT retrieval_id, project, query, tool, entity_name, rank, used_at "
        "FROM surfaced_entities ORDER BY retrieval_id, rank"
    ).fetchall()

    grouped: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        grouped.setdefault(row["retrieval_id"], []).append(row)

    for hits in grouped.values():
        first = hits[0]
        project = None if first["tool"] == _ALL_PROJECTS_TOOL else first["project"]
        yield LabelledQuery(
            project=project,
            query=first["query"],
            ranked=[hit["entity_name"] for hit in hits],
            relevant={hit["entity_name"] for hit in hits if hit["used_at"] is not None},
        )


def evaluate(db: DatabaseManager, k: int = 10) -> EvalReport:
    """Score current ranking quality by replaying each labelled query against the live graph.

    For every recorded retrieval that has at least one used (relevant) entity, the query is
    re-run on the current graph and scored with precision@k and reciprocal rank against that
    used set. Returns the mean precision@k and MRR over those queries; a graph with no usable
    labels yields an all-zero report.
    """
    precisions: list[float] = []
    reciprocal_ranks: list[float] = []
    for labelled in iter_labelled_queries(db):
        if not labelled.relevant:
            continue
        result = db.search_nodes(
            labelled.project, labelled.query, limit=max(k, len(labelled.ranked))
        )
        ranked_now = [entity.name for entity in cast("list[Entity]", result["entities"])]
        precisions.append(precision_at_k(ranked_now, labelled.relevant, k))
        reciprocal_ranks.append(reciprocal_rank(ranked_now, labelled.relevant))

    count = len(precisions)
    if count == 0:
        return EvalReport(query_count=0, mean_precision_at_k=0.0, mrr=0.0, k=k)
    return EvalReport(
        query_count=count,
        mean_precision_at_k=sum(precisions) / count,
        mrr=sum(reciprocal_ranks) / count,
        k=k,
    )
