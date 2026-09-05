"""The committed measured baseline that `eval_harness.FLOOR` is derived from.

Owns the artefact path, its (de)serialisation and the band width. Regenerating it is a
separate, explicit act (`just baseline --rebaseline`) - never something a test run does, since a
reference value recomputed from the code under test can never fail.

A corrupt artefact cannot be repaired by `regen_eval_baseline`, which imports this module
transitively; recover it with `git checkout tests/eval_baseline.json`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mcp_memory.eval import EvalReport

BASELINE_PATH = Path(__file__).with_name("eval_baseline.json")

RANKING_METRICS = (
    "mean_precision_at_k",
    "mrr",
    "mean_recall_at_k",
    "mean_ndcg_at_k",
    "mean_success_at_k",
)

# Bands are the measured value +/- `_BAND`, deliberately narrower than the fixture's own
# smallest resolvable delta (about 0.0016 on precision, 0.0078 on MRR, 0.0052 on recall), so
# any real ranking change breaches them while floating-point jitter does not. Do not widen or
# tighten. `_DP` is the precision every stored value is rounded to on construction, which is
# what makes rendering byte-stable rather than repr-dependent.
_BAND = 0.001
_DP = 4

_REBASELINE_HINT = "run `just baseline --rebaseline` to regenerate it"


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


def load_baseline(path: Path = BASELINE_PATH) -> Baseline:
    """Read and validate the committed baseline artefact at `path`."""
    if not path.exists():
        raise AssertionError(f"no eval baseline at {path} - {_REBASELINE_HINT}")
    try:
        payload = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise AssertionError(f"eval baseline at {path} is not valid JSON - {_REBASELINE_HINT}") from exc
    metrics = payload["metrics"]
    if set(metrics) != set(RANKING_METRICS):
        raise AssertionError(
            f"eval baseline at {path} covers {sorted(metrics)}, expected {sorted(RANKING_METRICS)} - {_REBASELINE_HINT}"
        )
    out_of_range = sorted(name for name, value in metrics.items() if not 0.0 < value < 1.0)
    if out_of_range:
        raise AssertionError(f"eval baseline at {path} has metrics outside (0, 1): {out_of_range} - {_REBASELINE_HINT}")
    k, query_count = payload["k"], payload["query_count"]
    if k <= 0 or query_count <= 0:
        raise AssertionError(
            f"eval baseline at {path} was measured at k={k} query_count={query_count} - {_REBASELINE_HINT}"
        )
    return Baseline(k=k, query_count=query_count, metrics=metrics)


def render(baseline: Baseline) -> str:
    """Serialise `baseline` deterministically: fixed key order, `_DP` places, trailing newline."""
    payload = {
        "k": baseline.k,
        "query_count": baseline.query_count,
        "metrics": {metric: baseline.metrics[metric] for metric in RANKING_METRICS},
    }
    return json.dumps(payload, indent=2) + "\n"


def bands(baseline: Baseline, *, band: float = _BAND) -> dict[str, tuple[float, float]]:
    """Return each measured value's (floor, ceiling) as value -/+ `band`, in metric order."""
    return {metric: (baseline.metrics[metric] - band, baseline.metrics[metric] + band) for metric in RANKING_METRICS}
