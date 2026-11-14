"""Measure the shared baseline artefact (tests/eval/baseline.json) from one build.

Builds the shared fixture once via `_build_populated_fixture`, measures the ranking metrics,
the read-tool output sizes and the read-tool latency against it, and writes every section of
the artefact in a single pass via each domain module's own `section()` builder. This is the sole entry
point for measurement - `just baseline` always goes through this module. Comparing two
artefacts happens in `compare_baseline` (CI, and `just bench-diff` locally).
"""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
from typing import TYPE_CHECKING

from mcp_memory.storage import open_writable
from tests.eval.eval_baseline import (
    BASELINE_PATH,
    Baseline,
    section as section_ranking,
)
from tests.eval.latency_baseline import (
    LatencyBaseline,
    measure as measure_latency,
    section as section_latency,
)
from tests.eval.ranking_replay import measure as measure_ranking
from tests.eval.size_baseline import (
    SizeBaseline,
    measure as measure_size,
    section as section_size,
)

from .eval_fixture import _build_populated_fixture

if TYPE_CHECKING:
    from collections.abc import Callable


def _measure_all() -> tuple[Baseline, SizeBaseline, LatencyBaseline]:
    """Build the shared fixture once and derive the ranking, size and latency baselines from it."""
    with tempfile.TemporaryDirectory() as tmp:
        db = open_writable(Path(tmp) / "baseline.db")
        try:
            fixture = _build_populated_fixture(db)
            return measure_ranking(fixture), measure_size(fixture), measure_latency(fixture)
        finally:
            db.connection.close()


def _write_combined(path: Path, ranking: Baseline, size: SizeBaseline, latency: LatencyBaseline) -> None:
    """Write every section to `path` in one pass, each section built by its own module."""
    combined = {
        "ranking": section_ranking(ranking),
        "size": section_size(size),
        "latency": section_latency(latency),
    }
    path.write_text(json.dumps(combined, indent=2) + "\n", encoding="utf-8")


def main(*, path: Path, measure: Callable[[], tuple[Baseline, SizeBaseline, LatencyBaseline]] = _measure_all) -> int:
    """Measure every section and write them together to `path`."""
    ranking, size, latency = measure()
    _write_combined(path, ranking, size, latency)
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(path=BASELINE_PATH))
