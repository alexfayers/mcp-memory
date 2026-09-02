"""Regenerate the committed eval baseline artefact from a fresh fixture build.

Run with no arguments to CHECK the committed artefact against the current code; run with
`--rebaseline` to overwrite it. Overwriting is deliberately a separate act: the gate is only
meaningful while the artefact predates the run being judged.
"""

from __future__ import annotations

import argparse
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from mcp_memory.database import DatabaseManager
from tests.eval_baseline import (
    _DP,
    BASELINE_PATH,
    RANKING_METRICS,
    Baseline,
    load_baseline,
    render,
)
from tests.eval_fixture import _build_populated_fixture

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

_LABEL_W = 20
_COL_W = 12


def measure_baseline() -> Baseline:
    """Build the populated fixture in a temp directory and return its measured baseline."""
    with tempfile.TemporaryDirectory() as tmp:
        db = DatabaseManager(Path(tmp) / "baseline.db")
        try:
            return Baseline.from_report(_build_populated_fixture(db).expected_baseline)
        finally:
            db.close()


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


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the regeneration command."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--rebaseline",
        action="store_true",
        help="overwrite the committed baseline with the measured values",
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    path: Path,
    measure: Callable[[], Baseline] = measure_baseline,
) -> int:
    """Check the baseline at `path` against a fresh measurement, or rewrite it.

    `path` is keyword-only with no default so a caller cannot reach the committed artefact by
    omission - a test that rewrote it with synthetic values would silently make the gate
    decorative.
    """
    args = build_parser().parse_args(argv)
    bootstrap = args.rebaseline and not path.exists()
    committed = None if bootstrap else load_baseline(path)
    measured = measure()
    if committed is None:
        path.write_text(render(measured))
        print(f"wrote {path} (first baseline)")
        return 0
    print(format_moves(committed, measured))
    drift = moved(committed, measured)
    if not drift:
        print("baseline unchanged")
        return 0
    if not args.rebaseline:
        print(f"baseline drift in {sorted(drift)}; run `just baseline --rebaseline` to update it")
        return 1
    lowered = sorted(metric for metric, (old, new) in drift.items() if new < old)
    if lowered:
        print(f"LOWERED: {lowered} - a lowered bound weakens the gate; justify it in the commit")
    path.write_text(render(measured))
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(path=BASELINE_PATH))
