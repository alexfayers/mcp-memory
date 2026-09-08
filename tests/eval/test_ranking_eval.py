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

from mcp_memory import (
    cli,
    eval as ranking_eval,
)
from mcp_memory.storage import open_readonly, open_writable
from tests import backdate_store, rank_of
from tests.eval.eval_harness import (
    _FIXTURE_LATER,
    _FIXTURE_NOW,
    _build_eval_fixture,
    _pinned,
    assert_no_regression,
    attach_eval_report,
    mark_used,
    measure_change,
)

if TYPE_CHECKING:
    from pathlib import Path

    from mcp_memory.storage import Storage


class TestTypeAwareDecay:
    def test_durable_pattern_not_buried_below_fresh_task(self, store: Storage) -> None:
        store.entities.create(
            "proj",
            [
                {"name": "pattern/retry", "entityType": "pattern", "observations": ["backoff"]},
                {"name": "task/retry", "entityType": "task", "observations": ["backoff"]},
            ],
        )
        backdate_store(store, "pattern/retry", 120)
        backdate_store(store, "task/retry", 20)

        entities = store.reads.search("proj", "backoff")["entities"]
        assert rank_of("pattern/retry", entities) < rank_of("task/retry", entities)


class TestInjectableClock:
    def test_ranking_uses_the_injected_now_not_the_wall_clock(self, store: Storage) -> None:
        _build_eval_fixture(store)

        early = store.reads.search("proj", "backoff retry", now=_FIXTURE_NOW)["entities"]
        # task/fresh (14d half-life) decays past pattern/slow (365d half-life) well before
        # _FIXTURE_LATER, so the same two entities swap order depending only on the clock.
        later = store.reads.search("proj", "backoff retry", now=_FIXTURE_LATER)["entities"]

        assert [e.name for e in early] == ["task/fresh", "pattern/slow"]
        assert [e.name for e in later] == ["pattern/slow", "task/fresh"]


class TestDeterministicTiebreak:
    """Permanent guard, not a red/green slice: FTS5's rowid-ordered doclist already agrees
    with ascending entity id for this fixture, so old and new code produce the same order.
    """

    def test_exact_score_tie_orders_by_entity_id(self, store: Storage) -> None:
        store.entities.create(
            "proj",
            [
                {"name": "task/aa1", "entityType": "task", "observations": ["identical text"]},
                {"name": "task/aa2", "entityType": "task", "observations": ["identical text"]},
            ],
        )
        pinned = _pinned(0)
        with store.connection.transaction():
            store.connection.write("UPDATE entities SET created_at = ?, updated_at = ?", (pinned, pinned))

        entities = store.reads.search("proj", "identical text")["entities"]

        assert [e.name for e in entities] == ["task/aa1", "task/aa2"]


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
    def test_groups_hits_by_retrieval_id_ordered_by_rank(self, store: Storage) -> None:
        store.telemetry.record_surfaced(
            "search_nodes",
            "cache",
            "rid-1",
            [("proj", "task/b", 2), ("proj", "task/a", 1)],
        )

        queries = list(ranking_eval.iter_labelled_queries(store))

        assert len(queries) == 1
        assert queries[0].query == "cache"
        assert queries[0].project == "proj"
        assert queries[0].ranked == ["task/a", "task/b"]

    def test_relevant_set_is_the_used_entities(self, store: Storage) -> None:
        store.telemetry.record_surfaced("search_nodes", "q", "rid-1", [("proj", "task/a", 1), ("proj", "task/b", 2)])
        mark_used(store, "rid-1", "task/a")

        query = next(iter(ranking_eval.iter_labelled_queries(store)))

        assert query.relevant == {"task/a"}

    def test_separate_retrievals_are_separate_queries(self, store: Storage) -> None:
        store.telemetry.record_surfaced("search_nodes", "q1", "rid-1", [("proj", "task/a", 1)])
        store.telemetry.record_surfaced("search_nodes", "q2", "rid-2", [("proj", "task/b", 1)])

        queries = list(ranking_eval.iter_labelled_queries(store))

        assert {q.query for q in queries} == {"q1", "q2"}

    def test_min_content_tokens_filters_out_short_queries(self, store: Storage) -> None:
        store.telemetry.record_surfaced("search_nodes", "task", "rid-short", [("proj", "task/a", 1)])
        store.telemetry.record_surfaced("search_nodes", "deploy notification", "rid-long", [("proj", "task/b", 1)])

        queries = list(ranking_eval.iter_labelled_queries(store, min_content_tokens=2))

        assert {q.query for q in queries} == {"deploy notification"}

    def test_since_filters_out_older_retrievals(self, store: Storage) -> None:
        store.telemetry.record_surfaced("search_nodes", "old", "rid-old", [("proj", "task/a", 1)])
        store.telemetry.record_surfaced("search_nodes", "recent", "rid-recent", [("proj", "task/b", 1)])
        with store.connection.transaction():
            store.connection.write(
                "UPDATE surfaced_entities SET surfaced_at = datetime('now', '-10 days') WHERE retrieval_id = 'rid-old'"
            )

        queries = list(ranking_eval.iter_labelled_queries(store, since="7d"))

        assert {q.query for q in queries} == {"recent"}


class TestEvaluate:
    def test_perfect_ranking_scores_one(self, store: Storage) -> None:
        store.entities.create("proj", [{"name": "task/a", "entityType": "task", "observations": ["needle"]}])
        store.telemetry.record_surfaced("search_nodes", "needle", "rid-1", [("proj", "task/a", 1)])
        mark_used(store, "rid-1", "task/a")

        report = ranking_eval.evaluate(store, k=5)

        assert report.query_count == 1
        assert report.mean_precision_at_k == 1.0
        assert report.mrr == 1.0
        assert report.mean_recall_at_k == 1.0
        assert report.mean_ndcg_at_k == pytest.approx(1.0)
        assert report.mean_success_at_k == 1.0

    def test_used_entity_ranked_below_noise_lowers_scores(self, store: Storage) -> None:
        store.entities.create(
            "proj",
            [
                {"name": "task/noise", "entityType": "task", "observations": ["shared term"]},
                {"name": "task/used", "entityType": "task", "observations": ["shared term"]},
            ],
        )
        # The used (relevant) entity is heavily downvoted, so the live search ranks it last.
        for _ in range(10):
            store.entities.vote("proj", "task/used", -1)
        store.telemetry.record_surfaced(
            "search_nodes",
            "shared term",
            "rid-1",
            [("proj", "task/used", 1), ("proj", "task/noise", 2)],
        )
        mark_used(store, "rid-1", "task/used")

        report = ranking_eval.evaluate(store, k=1)

        # Only 'task/used' is relevant, but it is now ranked #2, so precision@1 and RR drop.
        assert report.mean_precision_at_k == 0.0
        assert report.mrr == pytest.approx(0.5)
        # At k=1 the relevant item sits below the cutoff, so recall@1 and nDCG@1 are also 0.
        assert report.mean_recall_at_k == 0.0
        assert report.mean_ndcg_at_k == 0.0

    def test_queries_with_no_relevant_labels_are_skipped(self, store: Storage) -> None:
        store.entities.create("proj", [{"name": "task/a", "entityType": "task", "observations": ["needle"]}])
        store.telemetry.record_surfaced("search_nodes", "needle", "rid-1", [("proj", "task/a", 1)])

        report = ranking_eval.evaluate(store, k=5)

        assert report.query_count == 0
        assert report.mean_precision_at_k == 0.0
        assert report.mrr == 0.0
        assert report.mean_recall_at_k == 0.0
        assert report.mean_ndcg_at_k == 0.0

    def test_since_scopes_the_query_window(self, store: Storage) -> None:
        store.entities.create("proj", [{"name": "task/a", "entityType": "task", "observations": ["needle"]}])
        store.telemetry.record_surfaced("search_nodes", "needle", "rid-old", [("proj", "task/a", 1)])
        store.telemetry.record_surfaced("search_nodes", "needle", "rid-recent", [("proj", "task/a", 1)])
        mark_used(store, "rid-old", "task/a")
        mark_used(store, "rid-recent", "task/a")
        with store.connection.transaction():
            store.connection.write(
                "UPDATE surfaced_entities SET surfaced_at = datetime('now', '-10 days') WHERE retrieval_id = 'rid-old'"
            )

        assert ranking_eval.evaluate(store, k=5).query_count == 2
        assert ranking_eval.evaluate(store, k=5, since="7d").query_count == 1

    def test_min_content_tokens_scopes_the_query_window(self, store: Storage) -> None:
        store.entities.create(
            "proj",
            [
                {"name": "task/a", "entityType": "task", "observations": ["needle"]},
                {"name": "task/b", "entityType": "task", "observations": ["needle"]},
            ],
        )
        store.telemetry.record_surfaced("search_nodes", "task", "rid-short", [("proj", "task/a", 1)])
        store.telemetry.record_surfaced("search_nodes", "deploy notification", "rid-long", [("proj", "task/b", 1)])
        mark_used(store, "rid-short", "task/a")
        mark_used(store, "rid-long", "task/b")

        assert ranking_eval.evaluate(store, k=5).query_count == 2
        assert ranking_eval.evaluate(store, k=5, min_content_tokens=2).query_count == 1


class TestEvaluateCachedAsync:
    @pytest.mark.anyio
    async def test_cache_hit_does_not_recompute(self, store: Storage, monkeypatch: pytest.MonkeyPatch) -> None:
        ranking_eval.clear_cache()
        calls = 0
        real_evaluate_readonly = ranking_eval._evaluate_readonly

        def counting_evaluate_readonly(*args: object, **kwargs: object) -> ranking_eval.EvalReport:
            nonlocal calls
            calls += 1
            return real_evaluate_readonly(*args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(ranking_eval, "_evaluate_readonly", counting_evaluate_readonly)

        await ranking_eval.evaluate_cached_async(store, k=5)
        await ranking_eval.evaluate_cached_async(store, k=5)

        assert calls == 1

    @pytest.mark.anyio
    async def test_different_key_recomputes(self, store: Storage, monkeypatch: pytest.MonkeyPatch) -> None:
        ranking_eval.clear_cache()
        calls = 0
        real_evaluate_readonly = ranking_eval._evaluate_readonly

        def counting_evaluate_readonly(*args: object, **kwargs: object) -> ranking_eval.EvalReport:
            nonlocal calls
            calls += 1
            return real_evaluate_readonly(*args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(ranking_eval, "_evaluate_readonly", counting_evaluate_readonly)

        await ranking_eval.evaluate_cached_async(store, k=5)
        await ranking_eval.evaluate_cached_async(store, k=10)

        assert calls == 2

    @pytest.mark.anyio
    async def test_concurrent_misses_single_flight(self, store: Storage, monkeypatch: pytest.MonkeyPatch) -> None:
        ranking_eval.clear_cache()
        calls = 0
        real_evaluate_readonly = ranking_eval._evaluate_readonly

        def counting_evaluate_readonly(*args: object, **kwargs: object) -> ranking_eval.EvalReport:
            nonlocal calls
            calls += 1
            return real_evaluate_readonly(*args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(ranking_eval, "_evaluate_readonly", counting_evaluate_readonly)

        results = await asyncio.gather(
            ranking_eval.evaluate_cached_async(store, k=5),
            ranking_eval.evaluate_cached_async(store, k=5),
            ranking_eval.evaluate_cached_async(store, k=5),
        )

        assert calls == 1
        assert results[0] == results[1] == results[2]

    @pytest.mark.anyio
    async def test_ttl_expiry_recomputes(self, store: Storage, monkeypatch: pytest.MonkeyPatch) -> None:
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
        await ranking_eval.evaluate_cached_async(store, k=5)
        monkeypatch.setattr(ranking_eval.time, "monotonic", lambda: 1061.0)
        await ranking_eval.evaluate_cached_async(store, k=5)

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

    def test_search_ranking_identical_across_budget_values(self, store: Storage) -> None:
        long_obs = "cache eviction ttl strategy for the distributed layer " * 6
        store.entities.create(
            "proj",
            [{"name": f"task/cache-{i}", "entityType": "task", "observations": [long_obs]} for i in range(5)],
        )
        k = 5

        default = store.reads.search("proj", "cache", limit=k)["entities"]
        small = store.reads.search("proj", "cache", limit=k, max_observation_chars=50)["entities"]
        unlimited = store.reads.search("proj", "cache", limit=k, max_observation_chars=-1)["entities"]

        names_default = [e.name for e in default]
        names_small = [e.name for e in small]
        names_unlimited = [e.name for e in unlimited]

        assert names_default == names_small == names_unlimited
        assert len(names_default) == 5

    def test_evaluate_yields_nontrivial_metrics_on_meaningful_fixture(self, store: Storage) -> None:
        store.entities.create("proj", [{"name": "task/a", "entityType": "task", "observations": ["needle haystack"]}])
        store.telemetry.record_surfaced("search_nodes", "needle haystack", "rid-1", [("proj", "task/a", 1)])
        mark_used(store, "rid-1", "task/a")

        report = ranking_eval.evaluate(store, k=5)

        assert report.query_count > 0
        assert report.mean_precision_at_k > 0
        assert report.mrr > 0
        assert report.mean_recall_at_k > 0
        assert report.mean_ndcg_at_k > 0
        assert report.mean_success_at_k > 0


class TestVoteInfluence:
    def test_upvoted_outranks_equal_unvoted(self, store: Storage) -> None:
        store.entities.create(
            "proj",
            [
                {"name": "task/a", "entityType": "task", "observations": ["deploy"]},
                {"name": "task/b", "entityType": "task", "observations": ["deploy"]},
            ],
        )
        store.entities.vote("proj", "task/b", 1)

        entities = store.reads.search("proj", "deploy")["entities"]
        assert rank_of("task/b", entities) < rank_of("task/a", entities)

    def test_heavily_downvoted_still_returned_but_last(self, store: Storage) -> None:
        store.entities.create(
            "proj",
            [
                {"name": "task/good", "entityType": "task", "observations": ["cache"]},
                {"name": "task/bad", "entityType": "task", "observations": ["cache"]},
            ],
        )
        for _ in range(10):
            store.entities.vote("proj", "task/bad", -1)

        entities = store.reads.search("proj", "cache")["entities"]
        assert rank_of("task/bad", entities) != -1
        assert rank_of("task/bad", entities) > rank_of("task/good", entities)


class TestEvalCommand:
    def test_eval_command_reports_ranking_quality(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        db_path = tmp_path / "cli-eval.db"
        seed = open_writable(db_path)
        seed.entities.create("proj", [{"name": "task/a", "entityType": "task", "observations": ["needle"]}])
        seed.telemetry.record_surfaced("search_nodes", "needle", "rid-1", [("proj", "task/a", 1)])
        mark_used(seed, "rid-1", "task/a")
        seed.connection.close()

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
        seed = open_writable(db_path)
        seed.entities.create("proj", [{"name": "task/a", "entityType": "task", "observations": ["needle"]}])
        seed.telemetry.record_surfaced("search_nodes", "needle", "rid-old", [("proj", "task/a", 1)])
        seed.telemetry.record_surfaced("search_nodes", "needle", "rid-recent", [("proj", "task/a", 1)])
        mark_used(seed, "rid-old", "task/a")
        mark_used(seed, "rid-recent", "task/a")
        with seed.connection.transaction():
            seed.connection.write(
                "UPDATE surfaced_entities SET surfaced_at = datetime('now', '-10 days') WHERE retrieval_id = 'rid-old'"
            )
        seed.connection.close()

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
        seed = open_writable(db_path)
        seed.entities.create(
            "proj",
            [
                {"name": "task/a", "entityType": "task", "observations": ["needle"]},
                {"name": "task/b", "entityType": "task", "observations": ["needle"]},
            ],
        )
        seed.telemetry.record_surfaced("search_nodes", "task", "rid-short", [("proj", "task/a", 1)])
        seed.telemetry.record_surfaced("search_nodes", "deploy notification", "rid-long", [("proj", "task/b", 1)])
        mark_used(seed, "rid-short", "task/a")
        mark_used(seed, "rid-long", "task/b")
        seed.connection.close()

        monkeypatch.setenv("MCP_MEMORY_DB_PATH", str(db_path))
        monkeypatch.setattr("sys.argv", ["mcp-memory", "eval", "--k", "5", "--min-content-tokens", "2"])
        cli.main()

        out = capsys.readouterr().out
        assert "1 labelled queries (k=5, min_content_tokens=2)" in out

    def test_eval_command_does_not_prune_surfaced_labels(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        db_path = tmp_path / "cli-eval-retention.db"
        seed = open_writable(db_path)
        seed.entities.create("proj", [{"name": "task/a", "entityType": "task", "observations": ["needle"]}])
        seed.telemetry.record_surfaced("search_nodes", "needle", "rid-1", [("proj", "task/a", 1)])
        mark_used(seed, "rid-1", "task/a")
        with seed.connection.transaction():
            seed.connection.write("UPDATE surfaced_entities SET surfaced_at = datetime('now', '-10 days')")
        seed.connection.close()

        monkeypatch.setenv("MCP_MEMORY_DB_PATH", str(db_path))
        monkeypatch.setenv("MCP_MEMORY_SURFACED_RETENTION_DAYS", "1")
        monkeypatch.setattr("sys.argv", ["mcp-memory", "eval", "--k", "5"])
        cli.main()

        out = capsys.readouterr().out
        assert "1 labelled queries (k=5)" in out

        reopened = open_readonly(db_path)
        row = reopened.connection.query_one("SELECT COUNT(*) AS n FROM surfaced_entities")
        assert row["n"] == 1
        reopened.connection.close()

    def test_eval_command_errors_when_the_database_is_missing(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        db_path = tmp_path / "does-not-exist.db"
        monkeypatch.setenv("MCP_MEMORY_DB_PATH", str(db_path))
        monkeypatch.setattr("sys.argv", ["mcp-memory", "eval"])

        with pytest.raises(SystemExit) as excinfo:
            cli.main()

        assert excinfo.value.code == 1
        assert str(db_path) in capsys.readouterr().err


class TestEvaluateReadonly:
    def test_matches_evaluate_on_shared_connection(self, store: Storage, tmp_path: Path) -> None:
        store.entities.create("proj", [{"name": "task/a", "entityType": "task", "observations": ["needle"]}])
        store.telemetry.record_surfaced("search_nodes", "needle", "rid-1", [("proj", "task/a", 1)])
        mark_used(store, "rid-1", "task/a")

        expected = ranking_eval.evaluate(store, k=5)

        actual = ranking_eval._evaluate_readonly(store.connection.path, 5, None, 0)

        assert actual == expected

    def test_closes_its_connection(self, store: Storage, monkeypatch: pytest.MonkeyPatch) -> None:
        store.entities.create("proj", [{"name": "task/a", "entityType": "task", "observations": ["needle"]}])

        opened: list[Storage] = []
        real_open_readonly = ranking_eval.open_readonly

        def spying_open_readonly(path: Path) -> Storage:
            instance = real_open_readonly(path)
            opened.append(instance)
            return instance

        monkeypatch.setattr(ranking_eval, "open_readonly", spying_open_readonly)

        ranking_eval._evaluate_readonly(store.connection.path, 5, None, 0)

        assert len(opened) == 1
        with pytest.raises(sqlite3.ProgrammingError):
            opened[0].connection.query_one("SELECT 1")


class TestEvalDeterminism:
    def test_repeated_runs_on_the_pinned_fixture_are_identical(self, store: Storage) -> None:
        _build_eval_fixture(store)

        first = ranking_eval.evaluate(store, k=10, now=_FIXTURE_NOW)
        second = ranking_eval.evaluate(store, k=10, now=_FIXTURE_NOW)

        assert first == second
        assert first.query_count == 2

    def test_pinned_fixture_metrics_are_the_baseline(self, store: Storage) -> None:
        _build_eval_fixture(store)

        report = ranking_eval.evaluate(store, k=10, now=_FIXTURE_NOW)

        assert report.query_count == 2
        assert report.mean_precision_at_k == pytest.approx(0.75)
        assert report.mrr == pytest.approx(0.75)
        assert report.mean_recall_at_k == pytest.approx(1.0)
        assert report.mean_ndcg_at_k == pytest.approx((1 / math.log2(3) + 1.0) / 2)
        assert report.mean_success_at_k == pytest.approx(1.0)

    def test_metrics_follow_the_injected_now(self, store: Storage) -> None:
        _build_eval_fixture(store)

        assert ranking_eval.evaluate(store, k=10, now=_FIXTURE_NOW).mrr == pytest.approx(0.75)
        assert ranking_eval.evaluate(store, k=10, now=_FIXTURE_LATER).mrr == pytest.approx(1.0)


class TestArchivingEffectOnEval:
    """Measures the archive tier's ranking effect deterministically against the pinned
    fixture and its own `now`, rather than by eyeballing wall-clock CLI drift.
    """

    def test_archiving_irrelevant_entities_does_not_reduce_metrics(
        self, store: Storage, request: pytest.FixtureRequest
    ) -> None:
        _build_eval_fixture(store)
        relevant = {name for q in ranking_eval.iter_labelled_queries(store) for name in q.relevant}

        def archive_irrelevant(db: Storage) -> None:
            names = ("task/fresh", "pattern/slow", "task/unrelated")
            for name in names:
                if name not in relevant:
                    db.entities.set_status("proj", name, "archived")

        result = measure_change(store, archive_irrelevant, now=_FIXTURE_NOW)

        assert_no_regression(result, request)

    def test_archiving_a_relevant_entity_reduces_recall(self, store: Storage, request: pytest.FixtureRequest) -> None:
        """The one failure mode archiving can cause: a relevant entity drops out of results.

        This is the entire justification for `evaluate()` having no `include_archived`
        escape hatch - it must see exactly what an agent sees, so this loss is visible.
        """
        _build_eval_fixture(store)

        def archive_relevant(db: Storage) -> None:
            db.entities.set_status("proj", "pattern/slow", "archived")

        result = measure_change(store, archive_relevant, now=_FIXTURE_NOW)
        attach_eval_report(request, result)

        assert result.deltas["mean_recall_at_k"] < 0

    def test_archive_stale_entities_spares_every_labelled_relevant_entity(self, store: Storage) -> None:
        """Pins the never-evict exclusion against the eval's own ground truth."""
        _build_eval_fixture(store)
        relevant = {name for q in ranking_eval.iter_labelled_queries(store) for name in q.relevant}

        for name in ("task/fresh", "pattern/slow", "task/unrelated"):
            store.entities.set_status("proj", name, "resolved")
            backdate_store(store, name, 60)

        store.maintenance._archive_stale_entities()

        for name in relevant:
            assert store.reads.get_entity("proj", name).status != "archived"
        # task/fresh is not labelled-relevant, so it is fair game for the sweep - confirms
        # the sweep actually ran rather than trivially sparing everything.
        assert store.reads.get_entity("proj", "task/fresh").status == "archived"
