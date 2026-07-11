"""Ranking evaluation harness.

Encodes the intended search-ranking behaviour as golden cases over a seeded, synthetic
database so the recency half-lives and vote constants can be tuned by measurement rather
than guesswork. Coupled to no live state: every entity, age, and vote is set explicitly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from mcp_memory import eval as ranking_eval
from mcp_memory.database import DatabaseManager

if TYPE_CHECKING:
    from pathlib import Path

    from mcp_memory.models import Entity


@pytest.fixture
def db(tmp_path: Path) -> DatabaseManager:
    """Create a fresh database for each test."""
    return DatabaseManager(tmp_path / "eval.db")


def _backdate(db: DatabaseManager, name: str, days: int) -> None:
    """Age an entity by rewriting its created_at/updated_at to `days` ago."""
    db._db.execute(
        "UPDATE entities SET created_at = datetime('now', ?), updated_at = datetime('now', ?) "
        "WHERE name = ?",
        (f"-{days} days", f"-{days} days", name),
    )
    db._db.commit()


def _rank_of(name: str, entities: list[Entity]) -> int:
    """Return the 0-based rank of an entity in a result list, or -1 if absent."""
    for index, entity in enumerate(entities):
        if entity.name == name:
            return index
    return -1


class TestTypeAwareDecay:
    def test_durable_pattern_not_buried_below_fresh_task(self, db: DatabaseManager) -> None:
        db.create_entities(
            "proj",
            [
                {"name": "pattern/retry", "entityType": "pattern", "observations": ["backoff"]},
                {"name": "task/retry", "entityType": "task", "observations": ["backoff"]},
            ],
        )
        _backdate(db, "pattern/retry", 120)
        _backdate(db, "task/retry", 20)

        entities = db.search_nodes("proj", "backoff")["entities"]
        assert _rank_of("pattern/retry", entities) < _rank_of("task/retry", entities)


class TestMetricFunctions:
    def test_precision_at_k_counts_relevant_in_top_k(self) -> None:
        ranked = ["a", "b", "c", "d"]
        relevant = {"a", "c"}
        assert ranking_eval.precision_at_k(ranked, relevant, 4) == 0.5
        assert ranking_eval.precision_at_k(ranked, relevant, 2) == 0.5
        assert ranking_eval.precision_at_k(ranked, relevant, 1) == 1.0

    def test_precision_at_k_with_no_relevant_is_zero(self) -> None:
        assert ranking_eval.precision_at_k(["a", "b"], set(), 2) == 0.0

    def test_precision_at_k_k_larger_than_list_uses_list_length(self) -> None:
        assert ranking_eval.precision_at_k(["a", "b"], {"a"}, 10) == 0.5

    def test_precision_at_k_empty_ranked_is_zero(self) -> None:
        assert ranking_eval.precision_at_k([], {"a"}, 5) == 0.0

    def test_reciprocal_rank_uses_first_relevant_position(self) -> None:
        assert ranking_eval.reciprocal_rank(["a", "b", "c"], {"b"}) == pytest.approx(0.5)
        assert ranking_eval.reciprocal_rank(["a", "b", "c"], {"a", "c"}) == 1.0

    def test_reciprocal_rank_no_relevant_is_zero(self) -> None:
        assert ranking_eval.reciprocal_rank(["a", "b"], {"z"}) == 0.0


class TestIterLabelledQueries:
    def test_groups_hits_by_retrieval_id_ordered_by_rank(self, db: DatabaseManager) -> None:
        db.record_surfaced(
            "search_nodes",
            "cache",
            "rid-1",
            [("proj", "task/b", 2), ("proj", "task/a", 1)],
        )

        queries = list(ranking_eval.iter_labelled_queries(db))

        assert len(queries) == 1
        assert queries[0].query == "cache"
        assert queries[0].project == "proj"
        assert queries[0].ranked == ["task/a", "task/b"]

    def test_relevant_set_is_the_used_entities(self, db: DatabaseManager) -> None:
        db.record_surfaced(
            "search_nodes", "q", "rid-1", [("proj", "task/a", 1), ("proj", "task/b", 2)]
        )
        db._db.execute(
            "UPDATE surfaced_entities SET used_at = CURRENT_TIMESTAMP WHERE entity_name = 'task/a'"
        )
        db._db.commit()

        query = next(iter(ranking_eval.iter_labelled_queries(db)))

        assert query.relevant == {"task/a"}

    def test_separate_retrievals_are_separate_queries(self, db: DatabaseManager) -> None:
        db.record_surfaced("search_nodes", "q1", "rid-1", [("proj", "task/a", 1)])
        db.record_surfaced("search_nodes", "q2", "rid-2", [("proj", "task/b", 1)])

        queries = list(ranking_eval.iter_labelled_queries(db))

        assert {q.query for q in queries} == {"q1", "q2"}


class TestEvaluate:
    def test_perfect_ranking_scores_one(self, db: DatabaseManager) -> None:
        db.create_entities(
            "proj", [{"name": "task/a", "entityType": "task", "observations": ["needle"]}]
        )
        db.record_surfaced("search_nodes", "needle", "rid-1", [("proj", "task/a", 1)])
        db._db.execute("UPDATE surfaced_entities SET used_at = CURRENT_TIMESTAMP")
        db._db.commit()

        report = ranking_eval.evaluate(db, k=5)

        assert report.query_count == 1
        assert report.mean_precision_at_k == 1.0
        assert report.mrr == 1.0

    def test_used_entity_ranked_below_noise_lowers_scores(self, db: DatabaseManager) -> None:
        db.create_entities(
            "proj",
            [
                {"name": "task/noise", "entityType": "task", "observations": ["shared term"]},
                {"name": "task/used", "entityType": "task", "observations": ["shared term"]},
            ],
        )
        # The used (relevant) entity is heavily downvoted, so the live search ranks it last.
        for _ in range(10):
            db.vote_entity("proj", "task/used", -1)
        db.record_surfaced(
            "search_nodes",
            "shared term",
            "rid-1",
            [("proj", "task/used", 1), ("proj", "task/noise", 2)],
        )
        db._db.execute(
            "UPDATE surfaced_entities SET used_at = CURRENT_TIMESTAMP "
            "WHERE entity_name = 'task/used'"
        )
        db._db.commit()

        report = ranking_eval.evaluate(db, k=1)

        # Only 'task/used' is relevant, but it is now ranked #2, so precision@1 and RR drop.
        assert report.mean_precision_at_k == 0.0
        assert report.mrr == pytest.approx(0.5)

    def test_queries_with_no_relevant_labels_are_skipped(self, db: DatabaseManager) -> None:
        db.create_entities(
            "proj", [{"name": "task/a", "entityType": "task", "observations": ["needle"]}]
        )
        db.record_surfaced("search_nodes", "needle", "rid-1", [("proj", "task/a", 1)])

        report = ranking_eval.evaluate(db, k=5)

        assert report.query_count == 0
        assert report.mean_precision_at_k == 0.0
        assert report.mrr == 0.0


class TestVoteInfluence:
    def test_upvoted_outranks_equal_unvoted(self, db: DatabaseManager) -> None:
        db.create_entities(
            "proj",
            [
                {"name": "task/a", "entityType": "task", "observations": ["deploy"]},
                {"name": "task/b", "entityType": "task", "observations": ["deploy"]},
            ],
        )
        db.vote_entity("proj", "task/b", 1)

        entities = db.search_nodes("proj", "deploy")["entities"]
        assert _rank_of("task/b", entities) < _rank_of("task/a", entities)

    def test_heavily_downvoted_still_returned_but_last(self, db: DatabaseManager) -> None:
        db.create_entities(
            "proj",
            [
                {"name": "task/good", "entityType": "task", "observations": ["cache"]},
                {"name": "task/bad", "entityType": "task", "observations": ["cache"]},
            ],
        )
        for _ in range(10):
            db.vote_entity("proj", "task/bad", -1)

        entities = db.search_nodes("proj", "cache")["entities"]
        assert _rank_of("task/bad", entities) != -1
        assert _rank_of("task/bad", entities) > _rank_of("task/good", entities)
