"""Tests for the link-reachability eval cases."""

from __future__ import annotations

from typing import TYPE_CHECKING

from tests.eval.eval_harness import assert_improves
from tests.eval.link_reachability import build_link_fixture, measure_link_reachability

if TYPE_CHECKING:
    import pytest

    from mcp_memory.storage import Storage


class TestLinkReachability:
    def test_links_make_the_target_reachable_when_hits_are_followed(
        self, store: Storage, request: pytest.FixtureRequest
    ) -> None:
        build_link_fixture(store)
        result = measure_link_reachability(store, follow_links=True)
        assert result.before.mean_success_at_k == 0
        assert result.after.mean_success_at_k == 1
        assert_improves(result, request, "mean_recall_at_k")

    def test_links_do_not_change_plain_search_ranking(self, store: Storage, request: pytest.FixtureRequest) -> None:
        build_link_fixture(store)
        result = measure_link_reachability(store, follow_links=False)
        assert result.before.mean_success_at_k == 0
        assert not any(result.deltas.values()), result.format()
