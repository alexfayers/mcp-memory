"""Tests for the unified regeneration entry point that owns the shared baseline artefact."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from tests.eval import regen_baseline
from tests.eval.eval_baseline import (
    Baseline,
    section as section_ranking,
)
from tests.eval.regen_baseline import _measure_both, main
from tests.eval.size_baseline import (
    TOOLS,
    SizeBaseline,
    section as section_size,
)

if TYPE_CHECKING:
    from pathlib import Path

    from mcp_memory.storage import Storage
    from tests.eval.eval_fixture import EvalFixture


def _ranking(**overrides: float) -> Baseline:
    """Build a valid `Baseline`, overridable per metric."""
    metrics = {
        "mean_precision_at_k": 0.2099,
        "mrr": 0.5661,
        "mean_recall_at_k": 0.835,
        "mean_ndcg_at_k": 0.6061,
        "mean_success_at_k": 0.8438,
    }
    metrics.update(overrides)
    return Baseline(k=10, query_count=96, metrics=metrics)


def _size(**overrides: int) -> SizeBaseline:
    """Build a valid `SizeBaseline`, overridable per tool's total_bytes."""
    probe_counts = dict.fromkeys(TOOLS, 6)
    total_bytes = dict.fromkeys(TOOLS, 2500) | {"search_nodes": 4000, "read_graph": 9000}
    total_bytes.update(overrides)
    return SizeBaseline(entity_count=130, probe_counts=probe_counts, total_bytes=total_bytes)


class TestMeasureBoth:
    """The shared fixture must be built exactly once per regeneration, not once per section."""

    def test_builds_the_fixture_exactly_once(self, monkeypatch: pytest.MonkeyPatch) -> None:
        original = regen_baseline._build_populated_fixture
        calls: list[Storage] = []

        def counting(db: Storage) -> EvalFixture:
            calls.append(db)
            return original(db)

        monkeypatch.setattr(regen_baseline, "_build_populated_fixture", counting)
        ranking, size = _measure_both()
        assert len(calls) == 1
        assert isinstance(ranking, Baseline)
        assert isinstance(size, SizeBaseline)
        assert size.entity_count > 0
        assert ranking.query_count > 0


class TestWrite:
    """`main` measures once and writes both sections, whatever the file held before."""

    def test_writes_both_sections_to_a_missing_path(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        path = tmp_path / "baseline.json"
        assert main(path=path, measure=lambda: (_ranking(), _size())) == 0
        assert f"wrote {path}" in capsys.readouterr().out
        payload = json.loads(path.read_text())
        assert payload == {"ranking": section_ranking(_ranking()), "size": section_size(_size())}

    def test_overwrites_an_existing_artefact_with_the_measurement(self, tmp_path: Path) -> None:
        path = tmp_path / "baseline.json"
        path.write_text("not json", encoding="utf-8")
        assert main(path=path, measure=lambda: (_ranking(mrr=0.6), _size(read_graph=9530))) == 0
        payload = json.loads(path.read_text())
        assert payload["ranking"]["metrics"]["mrr"] == pytest.approx(0.6)
        assert payload["size"]["tools"]["read_graph"]["total_bytes"] == 9530
