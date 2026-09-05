"""Tests for the committed eval baseline artefact, its loader and its regeneration guard."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from mcp_memory.eval import EvalReport
from tests.eval_baseline import (
    _BAND,
    BASELINE_PATH,
    RANKING_METRICS,
    Baseline,
    bands,
    load_baseline,
    render,
)
from tests.eval_fixture import _K
from tests.regen_eval_baseline import main

if TYPE_CHECKING:
    from pathlib import Path


def _baseline(**overrides: float) -> Baseline:
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


class TestLoadBaseline:
    """A baseline that cannot be trusted must fail loudly, never load as a weaker floor."""

    def test_the_committed_artefact_covers_exactly_the_ranking_metrics(self) -> None:
        baseline = load_baseline()
        assert set(baseline.metrics) == set(RANKING_METRICS)
        assert baseline.k == _K
        assert baseline.query_count > 0

    def test_a_missing_artefact_names_the_regeneration_command(self, tmp_path: Path) -> None:
        with pytest.raises(AssertionError, match="--rebaseline"):
            load_baseline(tmp_path / "absent.json")

    def test_unparseable_json_names_the_regeneration_command(self, tmp_path: Path) -> None:
        path = tmp_path / "baseline.json"
        path.write_text("{")
        with pytest.raises(AssertionError, match="--rebaseline"):
            load_baseline(path)

    def test_a_missing_metric_is_rejected(self, tmp_path: Path) -> None:
        """A truncated artefact must not load as a smaller, still-passing floor."""
        baseline = _baseline()
        del baseline.metrics["mrr"]
        path = tmp_path / "baseline.json"
        path.write_text(json.dumps({"k": 10, "query_count": 96, "metrics": baseline.metrics}))
        with pytest.raises(AssertionError, match="--rebaseline"):
            load_baseline(path)

    def test_an_unknown_metric_is_rejected(self, tmp_path: Path) -> None:
        metrics = _baseline().metrics | {"extra": 0.5}
        path = tmp_path / "baseline.json"
        path.write_text(json.dumps({"k": 10, "query_count": 96, "metrics": metrics}))
        with pytest.raises(AssertionError, match="--rebaseline"):
            load_baseline(path)

    @pytest.mark.parametrize("value", [0.0, 1.0, 1.5, -0.1])
    def test_an_out_of_range_metric_is_rejected(self, tmp_path: Path, value: float) -> None:
        path = tmp_path / "baseline.json"
        path.write_text(render(_baseline(mrr=value)))
        with pytest.raises(AssertionError, match="--rebaseline"):
            load_baseline(path)

    @pytest.mark.parametrize("shape", [{"k": 0}, {"k": -1}, {"query_count": 0}])
    def test_an_impossible_measurement_shape_is_rejected(self, tmp_path: Path, shape: dict[str, int]) -> None:
        payload = {"k": 10, "query_count": 96, "metrics": _baseline().metrics} | shape
        path = tmp_path / "baseline.json"
        path.write_text(json.dumps(payload))
        with pytest.raises(AssertionError, match="--rebaseline"):
            load_baseline(path)


class TestFromReport:
    """Rounding on construction is what makes rendering byte-stable rather than repr-dependent."""

    def test_a_measured_report_is_rounded_to_the_stored_precision(self) -> None:
        report = EvalReport(
            query_count=96,
            mean_precision_at_k=0.20994999,
            mrr=0.56612345,
            mean_recall_at_k=0.835,
            mean_ndcg_at_k=0.60605001,
            mean_success_at_k=0.84375,
            k=10,
        )
        baseline = Baseline.from_report(report)
        assert (baseline.k, baseline.query_count) == (10, 96)
        assert baseline.metrics == {
            "mean_precision_at_k": 0.2099,
            "mrr": 0.5661,
            "mean_recall_at_k": 0.835,
            "mean_ndcg_at_k": 0.6061,
            "mean_success_at_k": 0.8438,
        }


class TestBands:
    """Bounds are derived from one named band, so widening them is a visible one-line diff."""

    def test_each_band_is_the_measured_value_plus_or_minus_one_constant(self) -> None:
        baseline = _baseline()
        for metric, (low, high) in bands(baseline).items():
            assert (low, high) == pytest.approx((baseline.metrics[metric] - _BAND, baseline.metrics[metric] + _BAND))

    def test_band_order_matches_the_ranking_metric_order(self) -> None:
        assert tuple(bands(_baseline())) == RANKING_METRICS


class TestRender:
    """Serialisation is deterministic, so a no-op regeneration produces no diff."""

    def test_rendering_the_committed_artefact_reproduces_it_byte_for_byte(self) -> None:
        assert render(load_baseline()) == BASELINE_PATH.read_text()

    def test_rendering_is_idempotent_under_reload(self, tmp_path: Path) -> None:
        path = tmp_path / "baseline.json"
        path.write_text(render(_baseline()))
        assert render(load_baseline(path)) == path.read_text()


class TestRegenGuard:
    """Check mode is the default; only `--rebaseline` may rewrite the committed artefact."""

    def test_check_mode_reports_no_drift_and_exits_zero(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        path = tmp_path / "baseline.json"
        path.write_text(render(_baseline()))
        assert main([], path=path, measure=_baseline) == 0
        assert "baseline unchanged" in capsys.readouterr().out

    def test_check_mode_exits_one_on_drift_and_names_the_flag(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        path = tmp_path / "baseline.json"
        path.write_text(render(_baseline()))
        before = path.read_text()
        assert main([], path=path, measure=lambda: _baseline(mrr=0.6)) == 1
        assert "--rebaseline" in capsys.readouterr().out
        assert path.read_text() == before

    def test_rebaseline_writes_the_measured_values(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        path = tmp_path / "baseline.json"
        path.write_text(render(_baseline()))
        improved = _baseline(mrr=0.6)
        assert main(["--rebaseline"], path=path, measure=lambda: improved) == 0
        assert "wrote" in capsys.readouterr().out
        assert load_baseline(path) == improved

    def test_rebaseline_announces_a_lowered_metric(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        path = tmp_path / "baseline.json"
        path.write_text(render(_baseline()))
        assert main(["--rebaseline"], path=path, measure=lambda: _baseline(mrr=0.5)) == 0
        out = capsys.readouterr().out
        assert "LOWERED" in out
        assert "mrr" in out

    def test_rebaseline_generates_a_missing_artefact(self, tmp_path: Path) -> None:
        """The tool must be able to produce the very first artefact, before one is committed."""
        path = tmp_path / "baseline.json"
        assert main(["--rebaseline"], path=path, measure=_baseline) == 0
        assert load_baseline(path) == _baseline()

    def test_check_mode_on_a_missing_artefact_exits_one(self, tmp_path: Path) -> None:
        with pytest.raises(AssertionError, match="--rebaseline"):
            main([], path=tmp_path / "baseline.json", measure=_baseline)

    def test_rebaseline_does_not_write_when_nothing_moved(self, tmp_path: Path) -> None:
        path = tmp_path / "baseline.json"
        path.write_text(render(_baseline()))
        mtime = path.stat().st_mtime_ns
        assert main(["--rebaseline"], path=path, measure=_baseline) == 0
        assert path.stat().st_mtime_ns == mtime
