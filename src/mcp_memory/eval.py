"""Ranking-quality metrics over recorded retrieval telemetry.

Pure, dependency-free scoring functions plus helpers that turn the ``surfaced_entities``
table (populated by the implicit-usefulness observer) into labelled queries: the relevance
label for a surfaced entity is simply "it was used afterwards" (``used_at IS NOT NULL``).
This makes search accuracy a measured number - mean precision@k and MRR over real queries -
rather than a guess, and gives phase D a regression gate it must not degrade.
"""

from __future__ import annotations

import math
import sqlite3
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from .database import _parse_date

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


def success_at_k(ranked: Sequence[str], relevant: set[str], k: int) -> float:
    """Whether any relevant item appears in the top-k ranked items (binary hit-rate).

    Unlike precision@k, this is not capped by a small relevant set: a query with only one
    relevant item can still score 1.0. Returns 0.0 when there is no hit in the top-k.
    """
    return 1.0 if precision_at_k(ranked, relevant, k) > 0 else 0.0


def recall_at_k(ranked: Sequence[str], relevant: set[str], k: int) -> float:
    """Fraction of the relevant items that appear in the top-k ranked items.

    The denominator is the size of the relevant set, so a short result list is penalised
    for missing relevant items. Returns 0.0 when there are no relevant items.
    """
    if not relevant:
        return 0.0
    hits = sum(1 for name in ranked[:k] if name in relevant)
    return hits / len(relevant)


def ndcg_at_k(ranked: Sequence[str], relevant: set[str], k: int) -> float:
    """Normalised discounted cumulative gain over the top-k ranked items (binary relevance).

    Each relevant item contributes ``1 / log2(rank + 1)`` at its 1-based rank; the sum is
    normalised by the ideal DCG (every relevant item ranked first). Returns 0.0 when there
    are no relevant items or no ideal gain.
    """
    if not relevant:
        return 0.0
    dcg = sum(
        1.0 / math.log2(index + 1)
        for index, name in enumerate(ranked[:k], start=1)
        if name in relevant
    )
    ideal_hits = min(len(relevant), k)
    idcg = sum(1.0 / math.log2(index + 1) for index in range(1, ideal_hits + 1))
    return dcg / idcg if idcg else 0.0


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
    mean_recall_at_k: float
    mean_ndcg_at_k: float
    mean_success_at_k: float
    k: int


def iter_labelled_queries(
    db: DatabaseManager, since: str | None = None, min_content_tokens: int = 0
) -> Iterator[LabelledQuery]:
    """Reconstruct each recorded retrieval from ``surfaced_entities`` as a labelled query.

    Hits are grouped by ``retrieval_id`` and ordered by their stored rank; the relevance
    label is the set of surfaced entities that were subsequently used (``used_at`` set). A
    cross-project search re-runs against all projects, so its query scope is ``None``. When
    ``since`` is given (a relative '7d'/'2w'/'3m' or ISO date string), only retrievals
    surfaced on or after that instant are included. When ``min_content_tokens`` is given,
    queries with fewer whitespace-separated tokens than that (e.g. the single word "task")
    are excluded, since a degenerate query is unrankable regardless of ranking quality.
    """
    sql = (
        "SELECT retrieval_id, project, query, tool, entity_name, rank, used_at "
        "FROM surfaced_entities "
    )
    params: list[str] = []
    if since is not None:
        sql += "WHERE surfaced_at >= ? "
        params.append(_parse_date(since))
    sql += "ORDER BY retrieval_id, rank"
    rows = db._db.execute(sql, params).fetchall()

    grouped: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        grouped.setdefault(row["retrieval_id"], []).append(row)

    for hits in grouped.values():
        first = hits[0]
        if len(first["query"].split()) < min_content_tokens:
            continue
        project = None if first["tool"] == _ALL_PROJECTS_TOOL else first["project"]
        yield LabelledQuery(
            project=project,
            query=first["query"],
            ranked=[hit["entity_name"] for hit in hits],
            relevant={hit["entity_name"] for hit in hits if hit["used_at"] is not None},
        )


def evaluate(
    db: DatabaseManager, k: int = 10, since: str | None = None, min_content_tokens: int = 0
) -> EvalReport:
    """Score current ranking quality by replaying each labelled query against the live graph.

    For every recorded retrieval that has at least one used (relevant) entity, the query is
    re-run on the current graph and scored with precision@k, reciprocal rank, recall@k, and
    nDCG@k against that used set. Returns the mean of each metric over those queries; a graph
    with no usable labels yields an all-zero report. When ``since`` is given, only retrievals
    surfaced on or after that instant are scored. When ``min_content_tokens`` is given,
    degenerate short queries are excluded from the labelled set (see ``iter_labelled_queries``).
    """
    precisions: list[float] = []
    reciprocal_ranks: list[float] = []
    recalls: list[float] = []
    ndcgs: list[float] = []
    successes: list[float] = []
    for labelled in iter_labelled_queries(db, since, min_content_tokens):
        if not labelled.relevant:
            continue
        result = db.search_nodes(
            labelled.project, labelled.query, limit=max(k, len(labelled.ranked))
        )
        ranked_now = [entity.name for entity in cast("list[Entity]", result["entities"])]
        precisions.append(precision_at_k(ranked_now, labelled.relevant, k))
        reciprocal_ranks.append(reciprocal_rank(ranked_now, labelled.relevant))
        recalls.append(recall_at_k(ranked_now, labelled.relevant, k))
        ndcgs.append(ndcg_at_k(ranked_now, labelled.relevant, k))
        successes.append(success_at_k(ranked_now, labelled.relevant, k))

    count = len(precisions)
    if count == 0:
        return EvalReport(
            query_count=0,
            mean_precision_at_k=0.0,
            mrr=0.0,
            mean_recall_at_k=0.0,
            mean_ndcg_at_k=0.0,
            mean_success_at_k=0.0,
            k=k,
        )
    return EvalReport(
        query_count=count,
        mean_precision_at_k=sum(precisions) / count,
        mrr=sum(reciprocal_ranks) / count,
        mean_recall_at_k=sum(recalls) / count,
        mean_ndcg_at_k=sum(ndcgs) / count,
        mean_success_at_k=sum(successes) / count,
        k=k,
    )
