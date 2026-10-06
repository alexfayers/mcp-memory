"""Measure the shared baseline artefact (tests/eval/baseline.json) from one build.

Builds the shared fixture once via `_build_populated_fixture`, measures both the ranking
metrics and the read-tool output sizes against it, and writes both sections of the artefact
in a single pass via each domain module's own `section()` builder. This is the sole entry
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
from tests.eval.eval_fixture import _build_populated_fixture
from tests.eval.ranking_replay import measure as measure_ranking
from tests.eval.size_baseline import (
    SizeBaseline,
    measure as measure_size,
    section as section_size,
)

if TYPE_CHECKING:
    from collections.abc import Callable


def _measure_both() -> tuple[Baseline, SizeBaseline]:
    """Build the shared fixture once and derive both the ranking and size baselines from it."""
    with tempfile.TemporaryDirectory() as tmp:
        db = open_writable(Path(tmp) / "baseline.db")
        try:
            fixture = _build_populated_fixture(db)
            return measure_ranking(fixture), measure_size(fixture)
        finally:
            db.connection.close()


def _write_combined(path: Path, ranking: Baseline, size: SizeBaseline) -> None:
    """Write both sections to `path` in one pass, each section built by its own module."""
    combined = {"ranking": section_ranking(ranking), "size": section_size(size)}
    path.write_text(json.dumps(combined, indent=2) + "\n", encoding="utf-8")


def main(*, path: Path, measure: Callable[[], tuple[Baseline, SizeBaseline]] = _measure_both) -> int:
    """Measure both sections and write them together to `path`."""
    ranking, size = measure()
    _write_combined(path, ranking, size)
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(path=BASELINE_PATH))
