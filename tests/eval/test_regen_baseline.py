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
    probe_counts = {"search_nodes": 6, "read_graph": 12, "get_entity_with_relations": 6}
    total_bytes = {"search_nodes": 4000, "read_graph": 9000, "get_entity_with_relations": 2500}
    total_bytes.update(overrides)
    return SizeBaseline(entity_count=130, probe_counts=probe_counts, total_bytes=total_bytes)


def _write_committed(path: Path, ranking: Baseline, size: SizeBaseline) -> None:
    """Write a synthetic shared artefact directly, bypassing `main` entirely."""
    combined = {"ranking": section_ranking(ranking), "size": section_size(size)}
    path.write_text(json.dumps(combined, indent=2) + "\n", encoding="utf-8")


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


class TestRegenGuard:
    """Check mode is the default; only `--rebaseline` may rewrite the committed artefact."""

    def test_check_mode_reports_both_sections_unchanged(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        path = tmp_path / "baseline.json"
        _write_committed(path, _ranking(), _size())
        assert main([], path=path, measure=lambda: (_ranking(), _size())) == 0
        out = capsys.readouterr().out
        assert "ranking baseline unchanged" in out
        assert "size baseline unchanged" in out

    def test_ranking_drift_alone_fails_the_gate_and_reports_size_unchanged(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        path = tmp_path / "baseline.json"
        _write_committed(path, _ranking(), _size())
        before = path.read_text()
        assert main([], path=path, measure=lambda: (_ranking(mrr=0.6), _size())) == 1
        out = capsys.readouterr().out
        assert "baseline drift in ['ranking']" in out
        assert "size baseline unchanged" in out
        assert path.read_text() == before

    def test_size_drift_alone_fails_the_gate_and_reports_ranking_unchanged(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        path = tmp_path / "baseline.json"
        _write_committed(path, _ranking(), _size())
        before = path.read_text()
        assert main([], path=path, measure=lambda: (_ranking(), _size(read_graph=9530))) == 1
        out = capsys.readouterr().out
        assert "ranking baseline unchanged" in out
        assert "baseline drift in ['size']" in out
        assert path.read_text() == before

    def test_rebaseline_writes_both_sections_in_one_pass_when_only_one_drifted(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        path = tmp_path / "baseline.json"
        _write_committed(path, _ranking(), _size())

        def measured() -> tuple[Baseline, SizeBaseline]:
            return _ranking(mrr=0.6), _size()

        assert main(["--rebaseline"], path=path, measure=measured) == 0
        assert "wrote" in capsys.readouterr().out
        payload = json.loads(path.read_text())
        assert set(payload) == {"ranking", "size"}
        assert payload["ranking"]["metrics"]["mrr"] == pytest.approx(0.6)
        assert payload["size"]["tools"]["read_graph"]["total_bytes"] == 9000

    def test_rebaseline_announces_a_lowered_ranking_metric(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        path = tmp_path / "baseline.json"
        _write_committed(path, _ranking(), _size())
        assert main(["--rebaseline"], path=path, measure=lambda: (_ranking(mrr=0.5), _size())) == 0
        out = capsys.readouterr().out
        assert "LOWERED" in out
        assert "mrr" in out

    def test_rebaseline_announces_an_increased_size_tool(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        path = tmp_path / "baseline.json"
        _write_committed(path, _ranking(), _size())

        def measured() -> tuple[Baseline, SizeBaseline]:
            return _ranking(), _size(read_graph=9530)

        assert main(["--rebaseline"], path=path, measure=measured) == 0
        out = capsys.readouterr().out
        assert "INCREASED" in out
        assert "read_graph" in out

    def test_rebaseline_does_not_write_when_nothing_moved(self, tmp_path: Path) -> None:
        path = tmp_path / "baseline.json"
        _write_committed(path, _ranking(), _size())
        mtime = path.stat().st_mtime_ns
        assert main(["--rebaseline"], path=path, measure=lambda: (_ranking(), _size())) == 0
        assert path.stat().st_mtime_ns == mtime

    def test_rebaseline_generates_a_missing_artefact_with_both_sections(self, tmp_path: Path) -> None:
        path = tmp_path / "baseline.json"
        assert main(["--rebaseline"], path=path, measure=lambda: (_ranking(), _size())) == 0
        payload = json.loads(path.read_text())
        assert set(payload) == {"ranking", "size"}

    def test_check_mode_on_a_missing_artefact_exits_one(self, tmp_path: Path) -> None:
        with pytest.raises(TypeError, match="--rebaseline"):
            main([], path=tmp_path / "absent.json", measure=lambda: (_ranking(), _size()))
