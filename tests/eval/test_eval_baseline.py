"""Tests for the ranking baseline measurement and its ranking-section builder."""

from __future__ import annotations

from mcp_memory.eval import EvalReport
from tests.eval.eval_baseline import (
    RANKING_METRICS,
    Baseline,
    section,
)


def _baseline(**overrides: float) -> Baseline:
    """Build a valid `Baseline`, overridable per metric."""
    metrics = {
        "mean_precision_at_k": 0.2099,
        "mrr": 0.5661,
        "mean_recall_at_k": 0.835,
        "mean_ndcg_at_k": 0.6061,
        "mean_success_at_k": 0.8438,
    }
    metrics.update(overrides)
    return Baseline(k=10, query_count=96, metrics=metrics)


class TestFromReport:
    """Rounding on construction is what makes rendering byte-stable rather than repr-dependent."""

    def test_a_measured_report_is_rounded_to_the_stored_precision(self) -> None:
        report = EvalReport(
            query_count=96,
            mean_precision_at_k=0.20994999,
            mrr=0.56612345,
            mean_recall_at_k=0.835,
            mean_ndcg_at_k=0.60605001,
            mean_success_at_k=0.84375,
            k=10,
        )
        baseline = Baseline.from_report(report)
        assert (baseline.k, baseline.query_count) == (10, 96)
        assert baseline.metrics == {
            "mean_precision_at_k": 0.2099,
            "mrr": 0.5661,
            "mean_recall_at_k": 0.835,
            "mean_ndcg_at_k": 0.6061,
            "mean_success_at_k": 0.8438,
        }


class TestSection:
    """The section payload has a fixed key order, so a measurement renders byte-stably."""

    def test_section_lists_the_metrics_in_ranking_order(self) -> None:
        payload = section(_baseline())
        assert (payload["k"], payload["query_count"]) == (10, 96)
        assert list(payload["metrics"]) == list(RANKING_METRICS)
