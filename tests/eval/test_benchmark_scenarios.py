"""Benchmark scenarios for search ranking quality.

Each scenario seeds a small synthetic database and asserts a concrete, intended ranking
or metric outcome - recall on a targeted query, resistance to a near-miss distractor,
preference for fresher/higher-voted content over stale duplicates, and correct abstention
(empty results, zero metrics) when nothing relevant exists.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from mcp_memory import eval as ranking_eval
from tests import SeedEntity, rank_of, seed_store

if TYPE_CHECKING:
    from mcp_memory.storage import Storage


class TestTargetedRecall:
    def _seed_and_search(self, store: Storage) -> tuple[list[str], set[str]]:
        seed_store(
            store,
            "bench",
            [
                SeedEntity("task/oauth-token-refresh", ["oauth token refresh rotation"]),
                SeedEntity("task/oauth-scope-config", ["oauth scope configuration"]),
                SeedEntity("task/pytest-fixtures", ["pytest fixture teardown"]),
                SeedEntity("task/css-grid", ["css grid layout responsive"]),
                SeedEntity("task/docker-cache", ["docker layer cache prune"]),
            ],
        )
        ranked = [e.name for e in store.reads.search("bench", "oauth", limit=10)["entities"]]
        relevant = {"task/oauth-token-refresh", "task/oauth-scope-config"}
        return ranked, relevant

    def test_recall_at_k_is_one_when_all_relevant_surfaced(self, store: Storage) -> None:
        ranked, relevant = self._seed_and_search(store)

        assert ranking_eval.recall_at_k(ranked, relevant, 10) == 1.0
        assert ranking_eval.precision_at_k(ranked, relevant, 2) == 1.0
        assert ranking_eval.reciprocal_rank(ranked, relevant) == 1.0

    def test_ndcg_at_k_is_one_when_relevant_rank_first(self, store: Storage) -> None:
        ranked, relevant = self._seed_and_search(store)

        assert ranking_eval.ndcg_at_k(ranked, relevant, 10) == pytest.approx(1.0)


class TestDistractorResistance:
    def test_relevant_outranks_near_miss_distractor(self, store: Storage) -> None:
        seed_store(
            store,
            "bench",
            [
                SeedEntity(
                    "task/cache-invalidation-redis",
                    ["redis cache invalidation ttl eviction"],
                ),
                SeedEntity(
                    "task/browser-cache-headers",
                    ["browser cache http headers"],
                ),
            ],
        )

        entities = store.reads.search("bench", "redis cache invalidation", limit=10)["entities"]

        relevant_rank = rank_of("task/cache-invalidation-redis", entities)
        distractor_rank = rank_of("task/browser-cache-headers", entities)
        assert relevant_rank != -1
        assert distractor_rank != -1
        assert relevant_rank < distractor_rank


class TestSupersession:
    def test_fresher_entity_outranks_stale(self, store: Storage) -> None:
        seed_store(
            store,
            "bench",
            [
                SeedEntity(
                    "task/deploy-runbook-v2",
                    ["deploy runbook rollback steps"],
                    age_days=1,
                ),
                SeedEntity(
                    "task/deploy-runbook-v1",
                    ["deploy runbook rollback steps"],
                    age_days=60,
                ),
            ],
        )

        entities = store.reads.search("bench", "deploy runbook rollback", limit=10)["entities"]

        fresh_rank = rank_of("task/deploy-runbook-v2", entities)
        stale_rank = rank_of("task/deploy-runbook-v1", entities)
        assert fresh_rank != -1
        assert stale_rank != -1
        assert fresh_rank < stale_rank

    def test_higher_voted_entity_outranks_equal(self, store: Storage) -> None:
        seed_store(
            store,
            "bench",
            [
                SeedEntity(
                    "knowledge/api-auth-current",
                    ["api auth bearer token"],
                    entity_type="knowledge",
                    votes=5,
                ),
                SeedEntity(
                    "knowledge/api-auth-stale",
                    ["api auth bearer token"],
                    entity_type="knowledge",
                ),
            ],
        )

        entities = store.reads.search("bench", "api auth bearer", limit=10)["entities"]

        current_rank = rank_of("knowledge/api-auth-current", entities)
        stale_rank = rank_of("knowledge/api-auth-stale", entities)
        assert current_rank != -1
        assert stale_rank != -1
        assert current_rank < stale_rank


class TestAbstentionControl:
    def _seed_noise_and_search(self, store: Storage) -> list[str]:
        seed_store(
            store,
            "bench",
            [
                SeedEntity("task/pytest-fixtures", ["pytest fixture teardown"]),
                SeedEntity("task/css-grid", ["css grid layout responsive"]),
                SeedEntity("task/docker-cache", ["docker layer cache prune"]),
            ],
        )
        return [e.name for e in store.reads.search("bench", "quantum cryptography lattice", limit=10)["entities"]]

    def test_no_relevant_entity_yields_zero_metrics(self, store: Storage) -> None:
        ranked = self._seed_noise_and_search(store)
        relevant = {"task/does-not-exist"}

        assert ranking_eval.precision_at_k(ranked, relevant, 5) == 0.0
        assert ranking_eval.recall_at_k(ranked, relevant, 5) == 0.0
        assert ranking_eval.ndcg_at_k(ranked, relevant, 5) == 0.0

    def test_query_with_no_token_overlap_returns_empty(self, store: Storage) -> None:
        ranked = self._seed_noise_and_search(store)

        assert ranked == []
