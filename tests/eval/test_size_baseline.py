"""Tests for the size baseline measurement and its size-section builder."""

from __future__ import annotations

import pytest

from mcp_memory.storage import open_writable
from tests.eval.eval_fixture import _TOPICS, _build_populated_fixture
from tests.eval.size_baseline import (
    TOOLS,
    SizeBaseline,
    measure,
    move_row,
    section,
)


def _baseline(**overrides: int) -> SizeBaseline:
    """Build a valid `SizeBaseline`, overridable per tool's total_bytes."""
    probe_counts = dict.fromkeys(TOOLS, 6)
    total_bytes = dict.fromkeys(TOOLS, 2500) | {"search_nodes": 4000, "read_graph": 9000}
    total_bytes.update(overrides)
    return SizeBaseline(entity_count=130, probe_counts=probe_counts, total_bytes=total_bytes)


class TestSection:
    """The section payload has a fixed shape and key order, so a measurement renders byte-stably."""

    def test_section_lists_every_tool_in_probe_order_with_its_counts(self) -> None:
        payload = section(_baseline(read_graph=1234))
        assert payload["entity_count"] == 130
        assert list(payload["tools"]) == list(TOOLS)
        assert payload["tools"]["read_graph"] == {"probes": 6, "total_bytes": 1234}


class TestMoveRow:
    def test_growth_reports_signed_bytes_and_percent(self) -> None:
        assert move_row(1000, 1200) == (200, 20.0)

    def test_shrink_reports_negative_values(self) -> None:
        assert move_row(1000, 600) == (-400, -40.0)


class TestProbes:
    @pytest.fixture(scope="class")
    def measured(self, tmp_path_factory: pytest.TempPathFactory) -> SizeBaseline:
        db = open_writable(tmp_path_factory.mktemp("probe-fixture") / "full.db")
        return measure(_build_populated_fixture(db))

    def test_every_probe_measures_at_least_one_payload(self, measured: SizeBaseline) -> None:
        assert set(measured.probe_counts) == set(TOOLS)
        assert all(count > 0 for count in measured.probe_counts.values())

    def test_tool_list_is_measured_once(self, measured: SizeBaseline) -> None:
        assert measured.probe_counts["tools/list"] == 1

    def test_cross_project_search_is_measured_per_topic_term(self, measured: SizeBaseline) -> None:
        assert measured.probe_counts["search_all_projects"] == len(_TOPICS)

    @pytest.mark.parametrize("tool", ["search_nodes", "read_graph", "get_entity_with_relations", "search_all_projects"])
    def test_compact_output_is_smaller_than_full(self, measured: SizeBaseline, tool: str) -> None:
        assert measured.total_bytes[f"{tool}[compact]"] < measured.total_bytes[tool]

    def test_unlimited_entity_read_is_larger_than_the_default_budget(self, measured: SizeBaseline) -> None:
        totals = measured.total_bytes
        assert totals["get_entity_with_relations[full]"] >= totals["get_entity_with_relations"]
