"""The committed measured ranking baseline that `eval_harness.FLOOR` is derived from.

Owns the ranking section's shape, its (de)serialisation and the band width, within the shared
`tests/eval/baseline.json` artefact - see `size_baseline` for the size section and
`regen_baseline` for the single entry point that regenerates both. Regenerating it is a
separate, explicit act (`just baseline --rebaseline`) - never something a test run does, since a
reference value recomputed from the code under test can never fail.

A corrupt artefact cannot be repaired by `regen_baseline`, which imports this module
transitively; recover it with `git checkout tests/eval/baseline.json`.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
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

# Bands are the measured value +/- `_BAND`, deliberately narrower than the fixture's own
# smallest resolvable delta (about 0.0016 on precision, 0.0078 on MRR, 0.0052 on recall), so
# any real ranking change breaches them while floating-point jitter does not. Do not widen or
# tighten. `_DP` is the precision every stored value is rounded to on construction, which is
# what makes rendering byte-stable rather than repr-dependent.
_BAND = 0.001
_DP = 4
_LABEL_W = 20
_COL_W = 12

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
    """Read and validate the ranking section of the committed baseline artefact at `path`."""
    if not path.exists():
        raise TypeError(f"no eval baseline at {path} - {_REBASELINE_HINT}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise TypeError(f"eval baseline at {path} is not valid JSON - {_REBASELINE_HINT}") from exc
    if "ranking" not in payload:
        raise TypeError(f"eval baseline at {path} has no 'ranking' section - {_REBASELINE_HINT}")
    section_payload = payload["ranking"]
    if not isinstance(section_payload, dict):
        raise TypeError(f"eval baseline at {path} has a non-dict 'ranking' section - {_REBASELINE_HINT}")
    metrics = section_payload["metrics"]
    if set(metrics) != set(RANKING_METRICS):
        raise TypeError(
            f"eval baseline at {path} covers {sorted(metrics)}, expected {sorted(RANKING_METRICS)} - {_REBASELINE_HINT}"
        )
    out_of_range = sorted(name for name, value in metrics.items() if not 0.0 < value < 1.0)
    if out_of_range:
        raise TypeError(f"eval baseline at {path} has metrics outside (0, 1): {out_of_range} - {_REBASELINE_HINT}")
    k, query_count = section_payload["k"], section_payload["query_count"]
    if k <= 0 or query_count <= 0:
        raise TypeError(f"eval baseline at {path} was measured at k={k} query_count={query_count} - {_REBASELINE_HINT}")
    return Baseline(k=k, query_count=query_count, metrics=metrics)


def section(baseline: Baseline) -> dict[str, object]:
    """Return the ranking section's payload: fixed key order, `_DP` places."""
    return {
        "k": baseline.k,
        "query_count": baseline.query_count,
        "metrics": {metric: baseline.metrics[metric] for metric in RANKING_METRICS},
    }


def bands(baseline: Baseline, *, band: float = _BAND) -> dict[str, tuple[float, float]]:
    """Return each measured value's (floor, ceiling) as value -/+ `band`, in metric order."""
    return {metric: (baseline.metrics[metric] - band, baseline.metrics[metric] + band) for metric in RANKING_METRICS}


def moved(committed: Baseline, measured: Baseline) -> dict[str, tuple[float, float]]:
    """Return {metric: (old, new)} for every metric whose stored value differs."""
    return {
        metric: (committed.metrics[metric], measured.metrics[metric])
        for metric in RANKING_METRICS
        if committed.metrics[metric] != measured.metrics[metric]
    }


def _row(label: str, old: str, new: str, delta: str) -> str:
    """Format one fixed-width row of an old/new/delta table."""
    return f"{label:<{_LABEL_W}}{old:>{_COL_W}}{new:>{_COL_W}}{delta:>{_COL_W}}"


def format_moves(committed: Baseline, measured: Baseline) -> str:
    """Render an old/new/delta table over every metric, plus the measurement shape."""
    lines = [_row("metric", "committed", "measured", "delta")]
    for metric in RANKING_METRICS:
        old, new = committed.metrics[metric], measured.metrics[metric]
        lines.append(_row(metric, f"{old:.{_DP}f}", f"{new:.{_DP}f}", f"{new - old:+.{_DP}f}"))
    lines.append(_row("k", str(committed.k), str(measured.k), ""))
    lines.append(_row("query_count", str(committed.query_count), str(measured.query_count), ""))
    return "\n".join(lines)
