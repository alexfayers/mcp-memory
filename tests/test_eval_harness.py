"""Tests for the eval harness helpers shared by ranking-change regression tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from mcp_memory.database import DatabaseManager
from mcp_memory.eval import EvalReport
from tests.eval_harness import (
    _FIXTURE_NOW,
    FLOOR,
    MeasuredChange,
    _pinned,
    assert_improves,
    assert_no_regression,
    assert_within_floor,
    attach_eval_report,
    mark_used,
    measure_change,
)
from tests.test_ranking_eval import _build_eval_fixture

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def db(tmp_path: Path) -> DatabaseManager:
    """Create a fresh database for each test."""
    return DatabaseManager(tmp_path / "eval-harness.db")


def _report(**overrides: float) -> EvalReport:
    """Build a non-trivial `EvalReport`, overridable per test."""
    fields: dict[str, int | float] = {
        "query_count": 2,
        "mean_precision_at_k": 0.5,
        "mrr": 0.5,
        "mean_recall_at_k": 0.5,
        "mean_ndcg_at_k": 0.5,
        "mean_success_at_k": 0.5,
        "k": 10,
    }
    fields.update(overrides)
    return EvalReport(**fields)  # type: ignore[arg-type]


def _floor_midpoints() -> dict[str, float]:
    """Return the midpoint of every `FLOOR` band, keyed by metric name."""
    return {metric: (low + high) / 2 for metric, (low, high) in FLOOR.items()}


def _touch_every_entity(db: DatabaseManager) -> None:
    """Rewrite every entity's `updated_at` to its own value: a real write, no ranking effect."""
    db._db.execute("UPDATE entities SET updated_at = updated_at")
    db._db.commit()


def _noop(db: DatabaseManager) -> None:
    """Deliberately make no database changes."""


def _create_one_entity(db: DatabaseManager) -> None:
    """Insert a single unrelated entity, a real write with no labelled queries either side."""
    db.create_entities("proj", [{"name": "task/x", "entityType": "task", "observations": ["x"]}])


def _add_a_third_labelled_query(db: DatabaseManager) -> None:
    """Add a new labelled query, changing query_count between before and after."""
    db.create_entities(
        "proj", [{"name": "task/extra", "entityType": "task", "observations": ["needle"]}]
    )
    db.record_surfaced("search_nodes", "needle", "rid-extra", [("proj", "task/extra", 1)])
    mark_used(db, "rid-extra", "task/extra")


class TestPinned:
    def test_zero_days_is_the_fixture_instant(self) -> None:
        assert _pinned(0) == "2026-01-01 00:00:00"

    def test_184_days_before_the_fixture_instant(self) -> None:
        assert _pinned(184) == "2025-07-01 00:00:00"


class TestMeasureChange:
    def test_evaluates_before_and_after_with_the_same_k_and_now(self, db: DatabaseManager) -> None:
        _build_eval_fixture(db)

        result = measure_change(db, _touch_every_entity, now=_FIXTURE_NOW)

        assert result.before.query_count == 2
        assert result.before.mean_precision_at_k == pytest.approx(0.75)
        assert result.after == result.before

    def test_deltas_are_zero_when_the_mutation_has_no_ranking_effect(
        self, db: DatabaseManager
    ) -> None:
        _build_eval_fixture(db)

        result = measure_change(db, _touch_every_entity, now=_FIXTURE_NOW)

        assert result.deltas == pytest.approx(
            {
                "mean_precision_at_k": 0.0,
                "mrr": 0.0,
                "mean_recall_at_k": 0.0,
                "mean_ndcg_at_k": 0.0,
                "mean_success_at_k": 0.0,
            }
        )

    def test_format_includes_k_and_query_count(self, db: DatabaseManager) -> None:
        _build_eval_fixture(db)

        result = measure_change(db, _touch_every_entity, now=_FIXTURE_NOW)
        text = result.format()

        assert "k" in text
        assert "query_count" in text
        assert "mean_recall_at_k" in text

    def test_requires_mutation_by_default(self, db: DatabaseManager) -> None:
        _build_eval_fixture(db)

        with pytest.raises(AssertionError, match="no changes"):
            measure_change(db, _noop, now=_FIXTURE_NOW)

    def test_require_mutation_false_permits_a_no_op(self, db: DatabaseManager) -> None:
        _build_eval_fixture(db)

        result = measure_change(db, _noop, now=_FIXTURE_NOW, require_mutation=False)

        assert result.before == result.after

    def test_rejects_an_empty_before_query_set(self, db: DatabaseManager) -> None:
        with pytest.raises(AssertionError, match="no labelled queries"):
            measure_change(db, _create_one_entity, now=_FIXTURE_NOW)

    def test_rejects_differing_before_and_after_query_counts(self, db: DatabaseManager) -> None:
        _build_eval_fixture(db)

        with pytest.raises(AssertionError, match="not comparable"):
            measure_change(db, _add_a_third_labelled_query, now=_FIXTURE_NOW)


class TestAttachEvalReport:
    def test_adds_a_report_section_with_the_formatted_table(
        self, request: pytest.FixtureRequest
    ) -> None:
        result = MeasuredChange(before=_report(), after=_report())

        attach_eval_report(request, result)

        assert request.node._report_sections[-1] == ("call", "eval report", result.format())


class TestAssertNoRegression:
    def test_passes_when_the_gate_metrics_hold(self, request: pytest.FixtureRequest) -> None:
        result = MeasuredChange(before=_report(), after=_report(mean_precision_at_k=0.25))

        assert_no_regression(result, request)

    def test_fails_when_a_gate_metric_drops(self, request: pytest.FixtureRequest) -> None:
        result = MeasuredChange(before=_report(), after=_report(mean_recall_at_k=0.25))

        with pytest.raises(AssertionError, match="regression"):
            assert_no_regression(result, request)


class TestAssertImproves:
    def test_passes_on_a_strict_gain_without_regressing_gates(
        self, request: pytest.FixtureRequest
    ) -> None:
        result = MeasuredChange(before=_report(), after=_report(mrr=0.75))

        assert_improves(result, request, "mrr")

    def test_fails_on_a_zero_delta_rather_than_passing_vacuously(
        self, request: pytest.FixtureRequest
    ) -> None:
        result = MeasuredChange(before=_report(), after=_report())

        with pytest.raises(AssertionError, match="did not improve"):
            assert_improves(result, request, "mean_recall_at_k")

    def test_fails_when_a_gate_metric_regresses_alongside_the_gain(
        self, request: pytest.FixtureRequest
    ) -> None:
        result = MeasuredChange(before=_report(), after=_report(mrr=0.75, mean_recall_at_k=0.25))

        with pytest.raises(AssertionError, match="regression"):
            assert_improves(result, request, "mrr")


class TestAssertWithinFloor:
    def test_passes_when_all_metrics_are_in_bounds(self, request: pytest.FixtureRequest) -> None:
        report = _report(mean_recall_at_k=0.8, mean_success_at_k=0.9)
        floor = {"mean_recall_at_k": (0.5, 1.0), "mean_success_at_k": (0.5, 1.0)}

        assert_within_floor(report, request, floor=floor)

    def test_fails_below_the_floor(self, request: pytest.FixtureRequest) -> None:
        report = _report(mean_recall_at_k=0.2)
        floor = {"mean_recall_at_k": (0.5, 1.0)}

        with pytest.raises(AssertionError, match="out of floor/ceiling bounds"):
            assert_within_floor(report, request, floor=floor)

    def test_fails_above_the_ceiling(self, request: pytest.FixtureRequest) -> None:
        report = _report(mean_recall_at_k=0.99)
        floor = {"mean_recall_at_k": (0.5, 0.9)}

        with pytest.raises(AssertionError, match="out of floor/ceiling bounds"):
            assert_within_floor(report, request, floor=floor)

    def test_defaults_to_the_module_level_floor(self, request: pytest.FixtureRequest) -> None:
        report = _report(**_floor_midpoints())

        assert_within_floor(report, request)

    def test_defaults_to_the_module_level_floor_and_rejects_an_out_of_bounds_metric(
        self, request: pytest.FixtureRequest
    ) -> None:
        metric, (low, _high) = next(iter(FLOOR.items()))
        fields = _floor_midpoints()
        fields[metric] = low - 1.0
        report = _report(**fields)

        with pytest.raises(AssertionError, match="out of floor/ceiling bounds"):
            assert_within_floor(report, request)


class TestMarkUsed:
    def test_marks_only_the_named_entity_within_the_given_retrieval(
        self, db: DatabaseManager
    ) -> None:
        db.create_entities(
            "proj", [{"name": "task/shared", "entityType": "task", "observations": ["needle"]}]
        )
        db.record_surfaced("search_nodes", "needle", "rid-a", [("proj", "task/shared", 1)])
        db.record_surfaced("search_nodes", "needle", "rid-b", [("proj", "task/shared", 1)])

        mark_used(db, "rid-a", "task/shared")

        rows = db._db.execute(
            "SELECT retrieval_id, used_at FROM surfaced_entities "
            "WHERE entity_name = 'task/shared' ORDER BY retrieval_id"
        ).fetchall()
        assert rows[0]["used_at"] is not None
        assert rows[1]["used_at"] is None
