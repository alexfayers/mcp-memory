"""The measured baseline for MCP read-tool latency, in milliseconds per call.

Owns the latency section's shape and serialisation within the shared `tests/eval/baseline.json`
artefact, and times the same probes as `size_baseline`, so each figure covers the tool call and
the sizing of its payload. See `regen_baseline` for the entry point that measures and writes every
section. Timings vary between runs and machines, so `compare_baseline` reports them without gating.
"""

from __future__ import annotations

from dataclasses import dataclass
import statistics
import time
from typing import TYPE_CHECKING

from tests.eval.size_baseline import _PROBES, TOOLS

if TYPE_CHECKING:
    from .eval_fixture import EvalFixture

_REPEATS = 5
_MS_PER_SECOND = 1000
_DP = 3


@dataclass(frozen=True)
class LatencyBaseline:
    """A measured latency baseline: the median milliseconds per call, per tool."""

    median_ms: dict[str, float]


def measure(fixture: EvalFixture, *, repeats: int = _REPEATS) -> LatencyBaseline:
    """Time every probe in `_PROBES` against `fixture` and return its median per-call milliseconds."""
    medians: dict[str, float] = {}
    for tool, probe in _PROBES.items():
        per_call_ms: list[float] = []
        for _ in range(repeats):
            started = time.perf_counter()
            calls = len(probe(fixture))
            per_call_ms.append((time.perf_counter() - started) * _MS_PER_SECOND / calls)
        medians[tool] = round(statistics.median(per_call_ms), _DP)
    return LatencyBaseline(median_ms=medians)


def section(baseline: LatencyBaseline) -> dict[str, object]:
    """Return the latency section's payload: fixed key order."""
    return {"tools": {tool: {"median_ms": baseline.median_ms[tool]} for tool in TOOLS}}
