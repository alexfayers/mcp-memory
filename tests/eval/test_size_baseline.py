"""Tests for the committed size baseline artefact and its size-section loader/builder."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from mcp_memory.storage import open_writable
from tests.eval.eval_fixture import _build_populated_fixture
from tests.eval.size_baseline import (
    BASELINE_PATH,
    TOOLS,
    SizeBaseline,
    format_moves,
    load_baseline,
    measure,
    section,
)

if TYPE_CHECKING:
    from pathlib import Path

    from tests.eval.eval_fixture import EvalFixture


def _baseline(**overrides: int) -> SizeBaseline:
    """Build a valid `SizeBaseline`, overridable per tool's total_bytes."""
    probe_counts = {"search_nodes": 6, "read_graph": 12, "get_entity_with_relations": 6}
    total_bytes = {"search_nodes": 4000, "read_graph": 9000, "get_entity_with_relations": 2500}
    total_bytes.update(overrides)
    return SizeBaseline(entity_count=130, probe_counts=probe_counts, total_bytes=total_bytes)


class TestLoadBaseline:
    """A baseline that cannot be trusted must fail loudly, never load as a weaker floor."""

    def test_the_committed_artefact_covers_exactly_the_tools(self) -> None:
        baseline = load_baseline()
        assert set(baseline.total_bytes) == set(TOOLS)
        assert set(baseline.probe_counts) == set(TOOLS)
        assert all(count > 0 for count in baseline.probe_counts.values())
        assert baseline.entity_count > 0

    def test_a_missing_artefact_names_the_regeneration_command(self, tmp_path: Path) -> None:
        with pytest.raises(TypeError, match="--rebaseline"):
            load_baseline(tmp_path / "absent.json")

    def test_unparseable_json_names_the_regeneration_command(self, tmp_path: Path) -> None:
        path = tmp_path / "baseline.json"
        path.write_text("{")
        with pytest.raises(TypeError, match="--rebaseline"):
            load_baseline(path)

    def test_a_missing_size_section_is_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "baseline.json"
        path.write_text(json.dumps({"ranking": {}}))
        with pytest.raises(TypeError, match="--rebaseline"):
            load_baseline(path)

    def test_a_non_dict_size_section_is_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "baseline.json"
        path.write_text(json.dumps({"size": "not a dict"}))
        with pytest.raises(TypeError, match="--rebaseline"):
            load_baseline(path)

    def test_a_missing_tool_is_rejected(self, tmp_path: Path) -> None:
        baseline = _baseline()
        tools = {
            tool: {"probes": baseline.probe_counts[tool], "total_bytes": baseline.total_bytes[tool]}
            for tool in TOOLS
            if tool != "read_graph"
        }
        path = tmp_path / "baseline.json"
        path.write_text(json.dumps({"size": {"entity_count": baseline.entity_count, "tools": tools}}))
        with pytest.raises(TypeError, match="--rebaseline"):
            load_baseline(path)

    @pytest.mark.parametrize("value", [0, -1])
    def test_a_non_positive_total_bytes_is_rejected(self, tmp_path: Path, value: int) -> None:
        path = tmp_path / "baseline.json"
        path.write_text(json.dumps({"size": section(_baseline(read_graph=value))}))
        with pytest.raises(TypeError, match="--rebaseline"):
            load_baseline(path)

    @pytest.mark.parametrize("value", [0, -1])
    def test_a_non_positive_entity_count_is_rejected(self, tmp_path: Path, value: int) -> None:
        baseline = _baseline()
        malformed = SizeBaseline(
            entity_count=value, probe_counts=baseline.probe_counts, total_bytes=baseline.total_bytes
        )
        path = tmp_path / "baseline.json"
        path.write_text(json.dumps({"size": section(malformed)}))
        with pytest.raises(TypeError, match="--rebaseline"):
            load_baseline(path)


class TestSection:
    """The section payload is deterministic, so a no-op regeneration produces no diff."""

    def test_the_committed_artefact_s_size_section_matches_the_rebuilt_payload(self) -> None:
        committed = json.loads(BASELINE_PATH.read_text())
        assert section(load_baseline()) == committed["size"]

    def test_section_is_idempotent_under_reload(self, tmp_path: Path) -> None:
        baseline = _baseline()
        path = tmp_path / "baseline.json"
        path.write_text(json.dumps({"size": section(baseline)}))
        assert section(load_baseline(path)) == section(baseline)


class TestSizeGate:
    """The gate: the fixture's measured output size must exactly match the committed baseline."""

    @pytest.fixture(scope="module")
    def fixture(self, tmp_path_factory: pytest.TempPathFactory) -> EvalFixture:
        """Build the full 12-project, 6-topic fixture once for every test in this module."""
        db_path = tmp_path_factory.mktemp("size-fixture") / "full.db"
        db = open_writable(db_path)
        return _build_populated_fixture(db)

    def test_measured_output_size_matches_the_committed_baseline(
        self, fixture: EvalFixture, request: pytest.FixtureRequest
    ) -> None:
        baseline = load_baseline()
        measured = measure(fixture)
        lines = [f"{'tool':<28}{'measured':>10}{'committed':>10}"]
        for tool in TOOLS:
            lines.append(f"{tool:<28}{measured.total_bytes[tool]:>10}{baseline.total_bytes[tool]:>10}")
        request.node.add_report_section("call", "size report", "\n".join(lines))
        assert measured == baseline, format_moves(baseline, measured)
