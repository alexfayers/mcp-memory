"""Regenerate the committed shared baseline artefact (tests/eval/baseline.json) from one build.

Builds the shared fixture once via `_build_populated_fixture`, measures both the ranking
metrics and the read-tool output sizes against it, and writes both sections of the artefact
in a single pass via each domain module's own `section()` builder. This is the sole entry
point for baseline regeneration - `just baseline` (and its `--rebaseline` flag) always go
through this module.

Run with no arguments to CHECK the committed artefact against the current code; run with
`--rebaseline` to overwrite it. Overwriting is deliberately a separate act: the gate is only
meaningful while the artefact predates the run being judged.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile
from typing import TYPE_CHECKING

from mcp_memory.storage import open_writable
from tests.eval.eval_baseline import (
    BASELINE_PATH,
    Baseline,
    format_moves as format_ranking_moves,
    load_baseline as load_ranking,
    moved as ranking_moved,
    section as section_ranking,
)
from tests.eval.eval_fixture import _build_populated_fixture
from tests.eval.size_baseline import (
    SizeBaseline,
    format_moves as format_size_moves,
    load_baseline as load_size,
    measure as measure_size,
    moved as size_moved,
    section as section_size,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence


def _measure_both() -> tuple[Baseline, SizeBaseline]:
    """Build the shared fixture once and derive both the ranking and size baselines from it."""
    with tempfile.TemporaryDirectory() as tmp:
        db = open_writable(Path(tmp) / "baseline.db")
        try:
            fixture = _build_populated_fixture(db)
            return Baseline.from_report(fixture.expected_baseline), measure_size(fixture)
        finally:
            db.connection.close()


def _write_combined(path: Path, ranking: Baseline, size: SizeBaseline) -> None:
    """Write both sections to `path` in one pass, each section built by its own module."""
    combined = {"ranking": section_ranking(ranking), "size": section_size(size)}
    path.write_text(json.dumps(combined, indent=2) + "\n", encoding="utf-8")


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
    measure: Callable[[], tuple[Baseline, SizeBaseline]] = _measure_both,
) -> int:
    """Check both sections of the baseline at `path` against a fresh measurement, or rewrite it.

    `path` is keyword-only with no default so a caller cannot reach the committed artefact by
    omission - a test that rewrote it with synthetic values would silently make the gate
    decorative. Both sections are always measured and, if written, written together, so
    neither can be regenerated without the other.
    """
    args = build_parser().parse_args(argv)
    bootstrap = args.rebaseline and not path.exists()
    committed_ranking = None if bootstrap else load_ranking(path)
    committed_size = None if bootstrap else load_size(path)
    measured_ranking, measured_size = measure()
    if committed_ranking is None or committed_size is None:
        _write_combined(path, measured_ranking, measured_size)
        print(f"wrote {path} (first baseline)")
        return 0
    ranking_drift = ranking_moved(committed_ranking, measured_ranking)
    size_drift = size_moved(committed_size, measured_size)
    if ranking_drift:
        print(format_ranking_moves(committed_ranking, measured_ranking))
    else:
        print("ranking baseline unchanged")
    if size_drift:
        print(format_size_moves(committed_size, measured_size))
    else:
        print("size baseline unchanged")
    if not ranking_drift and not size_drift:
        return 0
    if not args.rebaseline:
        sections = [("ranking", ranking_drift), ("size", size_drift)]
        drifted = [name for name, drift in sections if drift]
        print(f"baseline drift in {drifted}; run `just baseline --rebaseline` to update it")
        return 1
    lowered = sorted(metric for metric, (old, new) in ranking_drift.items() if new < old)
    if lowered:
        print(f"LOWERED: {lowered} - a lowered bound weakens the gate; justify it in the commit")
    increased = sorted(tool for tool, (old, new) in size_drift.items() if new > old)
    if increased:
        print(f"INCREASED: {increased} - a larger output costs more context on every call; justify it in the commit")
    _write_combined(path, measured_ranking, measured_size)
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(path=BASELINE_PATH))
