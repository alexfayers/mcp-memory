"""Tests for the latency baseline measurement and its section builder."""

from __future__ import annotations

from typing import TYPE_CHECKING

from mcp_memory.storage import open_writable
from tests.eval.latency_baseline import LatencyBaseline, measure, section
from tests.eval.size_baseline import TOOLS

from .eval_fixture import _build_populated_fixture

if TYPE_CHECKING:
    from pathlib import Path


class TestSection:
    def test_section_lists_every_tool_in_probe_order_with_its_median(self) -> None:
        baseline = LatencyBaseline(median_ms=dict.fromkeys(TOOLS, 1.5) | {"read_graph": 2.25})

        payload = section(baseline)

        assert list(payload["tools"]) == list(TOOLS)
        assert payload["tools"]["read_graph"] == {"median_ms": 2.25}


class TestMeasure:
    def test_every_probed_tool_has_a_positive_median_per_call(self, tmp_path: Path) -> None:
        fixture = _build_populated_fixture(open_writable(tmp_path / "latency.db"))

        baseline = measure(fixture, repeats=2)

        assert set(baseline.median_ms) == set(TOOLS)
        assert all(value > 0 for value in baseline.median_ms.values())
