"""Ranking evaluation harness.

Encodes the intended search-ranking behaviour as golden cases over a seeded, synthetic
database so the recency half-lives and vote constants can be tuned by measurement rather
than guesswork. Coupled to no live state: every entity, age, and vote is set explicitly.
"""

from __future__ import annotations

import asyncio
import math
import sqlite3
from typing import TYPE_CHECKING

import pytest

from mcp_memory import cli
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


class TestRecallAtK:
    def test_all_relevant_retrieved_in_top_k(self) -> None:
        assert ranking_eval.recall_at_k(["a", "b", "c"], {"a", "c"}, 3) == 1.0

    def test_partial_relevant_retrieved(self) -> None:
        assert ranking_eval.recall_at_k(["a", "x", "y"], {"a", "b"}, 3) == 0.5

    def test_relevant_below_cutoff_not_counted(self) -> None:
        assert ranking_eval.recall_at_k(["x", "y", "a"], {"a"}, 2) == 0.0

    def test_empty_relevant_is_zero(self) -> None:
        assert ranking_eval.recall_at_k(["a", "b"], set(), 5) == 0.0

    def test_no_relevant_retrieved_is_zero(self) -> None:
        assert ranking_eval.recall_at_k(["x", "y"], {"a", "b"}, 5) == 0.0

    def test_k_larger_than_list_uses_full_list(self) -> None:
        assert ranking_eval.recall_at_k(["a", "b"], {"a", "b"}, 10) == 1.0


class TestSuccessAtK:
    def test_hit_in_top_k_scores_one(self) -> None:
        assert ranking_eval.success_at_k(["a", "b", "c"], {"c"}, 3) == 1.0

    def test_no_hit_scores_zero(self) -> None:
        assert ranking_eval.success_at_k(["a", "b"], {"z"}, 2) == 0.0

    def test_empty_ranked_scores_zero(self) -> None:
        assert ranking_eval.success_at_k([], {"a"}, 5) == 0.0


class TestNdcgAtK:
    def test_perfect_ranking_scores_one(self) -> None:
        assert ranking_eval.ndcg_at_k(["a", "b", "c"], {"a", "b"}, 3) == pytest.approx(1.0)

    def test_empty_relevant_is_zero(self) -> None:
        assert ranking_eval.ndcg_at_k(["a", "b"], set(), 5) == 0.0

    def test_no_relevant_retrieved_is_zero(self) -> None:
        assert ranking_eval.ndcg_at_k(["x", "y"], {"a"}, 5) == 0.0

    def test_relevant_lower_rank_scores_less_than_perfect(self) -> None:
        # single relevant item at rank 2 -> dcg = 1/log2(3), idcg = 1/log2(2) = 1.0
        expected = (1.0 / math.log2(3)) / 1.0
        assert ranking_eval.ndcg_at_k(["x", "a"], {"a"}, 5) == pytest.approx(expected)

    def test_relevant_beyond_k_excluded(self) -> None:
        # relevant item only at rank 3 but k=2 -> dcg=0
        assert ranking_eval.ndcg_at_k(["x", "y", "a"], {"a"}, 2) == 0.0


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

    def test_min_content_tokens_filters_out_short_queries(self, db: DatabaseManager) -> None:
        db.record_surfaced("search_nodes", "task", "rid-short", [("proj", "task/a", 1)])
        db.record_surfaced(
            "search_nodes", "deploy notification", "rid-long", [("proj", "task/b", 1)]
        )

        queries = list(ranking_eval.iter_labelled_queries(db, min_content_tokens=2))

        assert {q.query for q in queries} == {"deploy notification"}

    def test_since_filters_out_older_retrievals(self, db: DatabaseManager) -> None:
        db.record_surfaced("search_nodes", "old", "rid-old", [("proj", "task/a", 1)])
        db.record_surfaced("search_nodes", "recent", "rid-recent", [("proj", "task/b", 1)])
        db._db.execute(
            "UPDATE surfaced_entities SET surfaced_at = datetime('now', '-10 days') "
            "WHERE retrieval_id = 'rid-old'"
        )
        db._db.commit()

        queries = list(ranking_eval.iter_labelled_queries(db, since="7d"))

        assert {q.query for q in queries} == {"recent"}


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
        assert report.mean_recall_at_k == 1.0
        assert report.mean_ndcg_at_k == pytest.approx(1.0)
        assert report.mean_success_at_k == 1.0

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
        # At k=1 the relevant item sits below the cutoff, so recall@1 and nDCG@1 are also 0.
        assert report.mean_recall_at_k == 0.0
        assert report.mean_ndcg_at_k == 0.0

    def test_queries_with_no_relevant_labels_are_skipped(self, db: DatabaseManager) -> None:
        db.create_entities(
            "proj", [{"name": "task/a", "entityType": "task", "observations": ["needle"]}]
        )
        db.record_surfaced("search_nodes", "needle", "rid-1", [("proj", "task/a", 1)])

        report = ranking_eval.evaluate(db, k=5)

        assert report.query_count == 0
        assert report.mean_precision_at_k == 0.0
        assert report.mrr == 0.0
        assert report.mean_recall_at_k == 0.0
        assert report.mean_ndcg_at_k == 0.0

    def test_since_scopes_the_query_window(self, db: DatabaseManager) -> None:
        db.create_entities(
            "proj", [{"name": "task/a", "entityType": "task", "observations": ["needle"]}]
        )
        db.record_surfaced("search_nodes", "needle", "rid-old", [("proj", "task/a", 1)])
        db.record_surfaced("search_nodes", "needle", "rid-recent", [("proj", "task/a", 1)])
        db._db.execute("UPDATE surfaced_entities SET used_at = CURRENT_TIMESTAMP")
        db._db.execute(
            "UPDATE surfaced_entities SET surfaced_at = datetime('now', '-10 days') "
            "WHERE retrieval_id = 'rid-old'"
        )
        db._db.commit()

        assert ranking_eval.evaluate(db, k=5).query_count == 2
        assert ranking_eval.evaluate(db, k=5, since="7d").query_count == 1

    def test_min_content_tokens_scopes_the_query_window(self, db: DatabaseManager) -> None:
        db.create_entities(
            "proj",
            [
                {"name": "task/a", "entityType": "task", "observations": ["needle"]},
                {"name": "task/b", "entityType": "task", "observations": ["needle"]},
            ],
        )
        db.record_surfaced("search_nodes", "task", "rid-short", [("proj", "task/a", 1)])
        db.record_surfaced(
            "search_nodes", "deploy notification", "rid-long", [("proj", "task/b", 1)]
        )
        db._db.execute("UPDATE surfaced_entities SET used_at = CURRENT_TIMESTAMP")
        db._db.commit()

        assert ranking_eval.evaluate(db, k=5).query_count == 2
        assert ranking_eval.evaluate(db, k=5, min_content_tokens=2).query_count == 1


class TestEvaluateCached:
    def test_cache_hit_does_not_recompute(
        self, db: DatabaseManager, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ranking_eval.clear_cache()
        calls = 0
        real_evaluate = ranking_eval.evaluate

        def counting_evaluate(*args: object, **kwargs: object) -> ranking_eval.EvalReport:
            nonlocal calls
            calls += 1
            return real_evaluate(*args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(ranking_eval, "evaluate", counting_evaluate)

        ranking_eval.evaluate_cached(db, k=5)
        ranking_eval.evaluate_cached(db, k=5)

        assert calls == 1

    def test_different_key_recomputes(
        self, db: DatabaseManager, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ranking_eval.clear_cache()
        calls = 0
        real_evaluate = ranking_eval.evaluate

        def counting_evaluate(*args: object, **kwargs: object) -> ranking_eval.EvalReport:
            nonlocal calls
            calls += 1
            return real_evaluate(*args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(ranking_eval, "evaluate", counting_evaluate)

        ranking_eval.evaluate_cached(db, k=5)
        ranking_eval.evaluate_cached(db, k=10)

        assert calls == 2

    def test_ttl_expiry_recomputes(
        self, db: DatabaseManager, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ranking_eval.clear_cache()
        calls = 0
        real_evaluate = ranking_eval.evaluate

        def counting_evaluate(*args: object, **kwargs: object) -> ranking_eval.EvalReport:
            nonlocal calls
            calls += 1
            return real_evaluate(*args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(ranking_eval, "evaluate", counting_evaluate)
        monkeypatch.setattr(ranking_eval, "get_eval_cache_ttl_seconds", lambda: 60)

        monkeypatch.setattr(ranking_eval.time, "monotonic", lambda: 1000.0)
        ranking_eval.evaluate_cached(db, k=5)
        monkeypatch.setattr(ranking_eval.time, "monotonic", lambda: 1061.0)
        ranking_eval.evaluate_cached(db, k=5)

        assert calls == 2


class TestEvaluateCachedAsync:
    @pytest.mark.anyio
    async def test_cache_hit_does_not_recompute(
        self, db: DatabaseManager, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ranking_eval.clear_cache()
        calls = 0
        real_evaluate_readonly = ranking_eval._evaluate_readonly

        def counting_evaluate_readonly(*args: object, **kwargs: object) -> ranking_eval.EvalReport:
            nonlocal calls
            calls += 1
            return real_evaluate_readonly(*args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(ranking_eval, "_evaluate_readonly", counting_evaluate_readonly)

        await ranking_eval.evaluate_cached_async(db, k=5)
        await ranking_eval.evaluate_cached_async(db, k=5)

        assert calls == 1

    @pytest.mark.anyio
    async def test_concurrent_misses_single_flight(
        self, db: DatabaseManager, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ranking_eval.clear_cache()
        calls = 0
        real_evaluate_readonly = ranking_eval._evaluate_readonly

        def counting_evaluate_readonly(*args: object, **kwargs: object) -> ranking_eval.EvalReport:
            nonlocal calls
            calls += 1
            return real_evaluate_readonly(*args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(ranking_eval, "_evaluate_readonly", counting_evaluate_readonly)

        results = await asyncio.gather(
            ranking_eval.evaluate_cached_async(db, k=5),
            ranking_eval.evaluate_cached_async(db, k=5),
            ranking_eval.evaluate_cached_async(db, k=5),
        )

        assert calls == 1
        assert results[0] == results[1] == results[2]

    @pytest.mark.anyio
    async def test_ttl_expiry_recomputes(
        self, db: DatabaseManager, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ranking_eval.clear_cache()
        calls = 0
        real_evaluate_readonly = ranking_eval._evaluate_readonly

        def counting_evaluate_readonly(*args: object, **kwargs: object) -> ranking_eval.EvalReport:
            nonlocal calls
            calls += 1
            return real_evaluate_readonly(*args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(ranking_eval, "_evaluate_readonly", counting_evaluate_readonly)
        monkeypatch.setattr(ranking_eval, "get_eval_cache_ttl_seconds", lambda: 60)

        monkeypatch.setattr(ranking_eval.time, "monotonic", lambda: 1000.0)
        await ranking_eval.evaluate_cached_async(db, k=5)
        monkeypatch.setattr(ranking_eval.time, "monotonic", lambda: 1061.0)
        await ranking_eval.evaluate_cached_async(db, k=5)

        assert calls == 2

    def test_clear_cache_resets_locks(self) -> None:
        ranking_eval._get_lock((5, None, 0))
        assert ranking_eval._locks

        ranking_eval.clear_cache()

        assert not ranking_eval._locks


class TestBudgetingIsOrthogonalToRanking:
    """Permanent guard: observation budgeting must never change ranking or eval metrics.

    Budgeting shapes observations in _build_entity, which runs after BM25/recency/vote scoring
    has already selected and ordered rows. This locks that orthogonality in forever: the same
    search returns identical entity names and order at every budget value, and evaluate()'s own
    internal (config-default-budget) search produces non-trivial metrics on a meaningful fixture.
    """

    def test_search_ranking_identical_across_budget_values(self, db: DatabaseManager) -> None:
        long_obs = "cache eviction ttl strategy for the distributed layer " * 6
        db.create_entities(
            "proj",
            [
                {"name": f"task/cache-{i}", "entityType": "task", "observations": [long_obs]}
                for i in range(5)
            ],
        )
        k = 5

        default = db.search_nodes("proj", "cache", limit=k)["entities"]
        small = db.search_nodes("proj", "cache", limit=k, max_observation_chars=50)["entities"]
        unlimited = db.search_nodes("proj", "cache", limit=k, max_observation_chars=-1)["entities"]

        names_default = [e.name for e in default]
        names_small = [e.name for e in small]
        names_unlimited = [e.name for e in unlimited]

        assert names_default == names_small == names_unlimited
        assert len(names_default) == 5

    def test_evaluate_yields_nontrivial_metrics_on_meaningful_fixture(
        self, db: DatabaseManager
    ) -> None:
        db.create_entities(
            "proj", [{"name": "task/a", "entityType": "task", "observations": ["needle haystack"]}]
        )
        db.record_surfaced("search_nodes", "needle haystack", "rid-1", [("proj", "task/a", 1)])
        db._db.execute("UPDATE surfaced_entities SET used_at = CURRENT_TIMESTAMP")
        db._db.commit()

        report = ranking_eval.evaluate(db, k=5)

        assert report.query_count > 0
        assert report.mean_precision_at_k > 0
        assert report.mrr > 0
        assert report.mean_recall_at_k > 0
        assert report.mean_ndcg_at_k > 0
        assert report.mean_success_at_k > 0


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


class TestEvalCommand:
    def test_eval_command_reports_ranking_quality(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        db_path = tmp_path / "cli-eval.db"
        seed = DatabaseManager(db_path)
        seed.create_entities(
            "proj", [{"name": "task/a", "entityType": "task", "observations": ["needle"]}]
        )
        seed.record_surfaced("search_nodes", "needle", "rid-1", [("proj", "task/a", 1)])
        seed._db.execute("UPDATE surfaced_entities SET used_at = CURRENT_TIMESTAMP")
        seed._db.commit()
        seed.close()

        monkeypatch.setenv("MCP_MEMORY_DB_PATH", str(db_path))
        monkeypatch.setattr("sys.argv", ["mcp-memory", "eval", "--k", "5"])
        cli.main()

        out = capsys.readouterr().out
        assert "1 labelled queries (k=5)" in out
        assert "mean precision@5: 1.000" in out
        assert "MRR: 1.000" in out
        assert "mean recall@5: 1.000" in out
        assert "mean nDCG@5: 1.000" in out
        assert "fraction of all relevant items" in out
        assert "normalised to the true relevant-set size" in out
        assert "mean success@5: 1.000" in out
        assert "not subject to the precision@k ceiling" in out

    def test_eval_command_since_scopes_window_and_header(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        db_path = tmp_path / "cli-eval-since.db"
        seed = DatabaseManager(db_path)
        seed.create_entities(
            "proj", [{"name": "task/a", "entityType": "task", "observations": ["needle"]}]
        )
        seed.record_surfaced("search_nodes", "needle", "rid-old", [("proj", "task/a", 1)])
        seed.record_surfaced("search_nodes", "needle", "rid-recent", [("proj", "task/a", 1)])
        seed._db.execute("UPDATE surfaced_entities SET used_at = CURRENT_TIMESTAMP")
        seed._db.execute(
            "UPDATE surfaced_entities SET surfaced_at = datetime('now', '-10 days') "
            "WHERE retrieval_id = 'rid-old'"
        )
        seed._db.commit()
        seed.close()

        monkeypatch.setenv("MCP_MEMORY_DB_PATH", str(db_path))
        monkeypatch.setattr("sys.argv", ["mcp-memory", "eval", "--k", "5", "--since", "7d"])
        cli.main()

        out = capsys.readouterr().out
        assert "1 labelled queries (k=5, since 7d)" in out

    def test_eval_command_min_content_tokens_scopes_window_and_header(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        db_path = tmp_path / "cli-eval-min-tokens.db"
        seed = DatabaseManager(db_path)
        seed.create_entities(
            "proj",
            [
                {"name": "task/a", "entityType": "task", "observations": ["needle"]},
                {"name": "task/b", "entityType": "task", "observations": ["needle"]},
            ],
        )
        seed.record_surfaced("search_nodes", "task", "rid-short", [("proj", "task/a", 1)])
        seed.record_surfaced(
            "search_nodes", "deploy notification", "rid-long", [("proj", "task/b", 1)]
        )
        seed._db.execute("UPDATE surfaced_entities SET used_at = CURRENT_TIMESTAMP")
        seed._db.commit()
        seed.close()

        monkeypatch.setenv("MCP_MEMORY_DB_PATH", str(db_path))
        monkeypatch.setattr(
            "sys.argv", ["mcp-memory", "eval", "--k", "5", "--min-content-tokens", "2"]
        )
        cli.main()

        out = capsys.readouterr().out
        assert "1 labelled queries (k=5, min_content_tokens=2)" in out


class TestEvaluateReadonly:
    def test_matches_evaluate_on_shared_connection(
        self, db: DatabaseManager, tmp_path: Path
    ) -> None:
        db.create_entities(
            "proj", [{"name": "task/a", "entityType": "task", "observations": ["needle"]}]
        )
        db.record_surfaced("search_nodes", "needle", "rid-1", [("proj", "task/a", 1)])
        db._db.execute("UPDATE surfaced_entities SET used_at = CURRENT_TIMESTAMP")
        db._db.commit()

        expected = ranking_eval.evaluate(db, k=5)

        actual = ranking_eval._evaluate_readonly(db.path, 5, None, 0)

        assert actual == expected

    def test_closes_its_connection(
        self, db: DatabaseManager, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        db.create_entities(
            "proj", [{"name": "task/a", "entityType": "task", "observations": ["needle"]}]
        )

        opened: list[DatabaseManager] = []
        real_connect_readonly = DatabaseManager.connect_readonly

        def spying_connect_readonly(path: Path) -> DatabaseManager:
            instance = real_connect_readonly(path)
            opened.append(instance)
            return instance

        monkeypatch.setattr(DatabaseManager, "connect_readonly", spying_connect_readonly)

        ranking_eval._evaluate_readonly(db.path, 5, None, 0)

        assert len(opened) == 1
        with pytest.raises(sqlite3.ProgrammingError):
            opened[0]._db.execute("SELECT 1")
