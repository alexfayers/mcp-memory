"""The measured ranking baseline: recall, precision, MRR, nDCG and success at k.

Owns the ranking section's shape, its serialisation and the stored precision, within the shared
`tests/eval/baseline.json` artefact - see `size_baseline` for the size section and
`regen_baseline` for the single entry point that measures and writes both. The artefact is a
measurement of the current code, written by `just baseline`; `compare_baseline` diffs two of them.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mcp_memory.eval import EvalReport

BASELINE_PATH = Path(__file__).with_name("baseline.json")

RANKING_METRICS = (
    "mean_precision_at_k",
    "mrr",
    "mean_recall_at_k",
    "mean_ndcg_at_k",
    "mean_success_at_k",
)

# `_DP` is the precision every stored value is rounded to on construction, which is what makes
# rendering byte-stable rather than repr-dependent.
_DP = 4


@dataclass(frozen=True)
class Baseline:
    """A measured eval baseline: the metric values plus the shape they were measured at."""

    k: int
    query_count: int
    metrics: dict[str, float]

    @classmethod
    def from_report(cls, report: EvalReport) -> Baseline:
        """Build a canonical baseline from `report`, rounding every metric to `_DP`."""
        return cls(
            k=report.k,
            query_count=report.query_count,
            metrics={metric: round(getattr(report, metric), _DP) for metric in RANKING_METRICS},
        )


def section(baseline: Baseline) -> dict[str, object]:
    """Return the ranking section's payload: fixed key order, `_DP` places."""
    return {
        "k": baseline.k,
        "query_count": baseline.query_count,
        "metrics": {metric: baseline.metrics[metric] for metric in RANKING_METRICS},
    }
