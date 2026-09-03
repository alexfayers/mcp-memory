"""Deterministic before/after measurement of a change's effect on search-ranking quality.

Pins `k` and `now` across both evaluations so a comparison reflects only the mutation under
test, never wall-clock drift or a caller accidentally scoring the two sides differently.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from mcp_memory.eval import evaluate
from tests.eval_baseline import RANKING_METRICS, bands, load_baseline

if TYPE_CHECKING:
    import pytest

    from mcp_memory.database import DatabaseManager
    from mcp_memory.eval import EvalReport

_FIXTURE_NOW = datetime(2026, 1, 1, tzinfo=UTC)
_FIXTURE_LATER = datetime(2026, 6, 1, tzinfo=UTC)

_GATE_METRICS = ("mean_recall_at_k", "mean_success_at_k")

# Precision@10 sits above the naive mean|relevant|/k=0.1417 ceiling because `precision_at_k`
# divides by `min(k, len(ranked))`, and the fixture's small project scopes shrink that
# denominator below k - so absolute precision is not comparable across different fixtures,
# only deltas on this one are.
BASELINE = load_baseline()
FLOOR: dict[str, tuple[float, float]] = bands(BASELINE)

_LABEL_W = 20
_COL_W = 10


def _pinned(days: float) -> str:
    """Return the SQLite timestamp `days` days before the pinned fixture instant."""
    return (_FIXTURE_NOW - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")


def _row(label: str, before: str, after: str, delta: str) -> str:
    """Format one fixed-width row of a before/after/delta table."""
    return f"{label:<{_LABEL_W}}{before:>{_COL_W}}{after:>{_COL_W}}{delta:>{_COL_W}}"


def mark_used(db: DatabaseManager, retrieval_id: str, *names: str) -> None:
    """Mark `names` used within `retrieval_id`, keyed by the (retrieval_id, name) pair."""
    used_at = _pinned(0)
    db._db.executemany(
        "UPDATE surfaced_entities SET used_at = ? WHERE retrieval_id = ? AND entity_name = ?",
        [(used_at, retrieval_id, name) for name in names],
    )
    db._db.commit()


def _build_eval_fixture(db: DatabaseManager) -> None:
    """Seed a labelled-query fixture whose every timestamp is pinned to a fixed instant.

    Two entities share the query terms with identical FTS documents (same token counts, so
    identical BM25) and differ only in type half-life and age, so their order depends purely
    on the injected clock. A third entity carries a second, unambiguous query.
    """
    db.create_entities(
        "proj",
        [
            {"name": "task/fresh", "entityType": "task", "observations": ["backoff retry"]},
            {"name": "pattern/slow", "entityType": "pattern", "observations": ["backoff retry"]},
            {"name": "task/unrelated", "entityType": "task", "observations": ["cache eviction"]},
        ],
    )
    pinned = _pinned(0)
    db._db.execute("UPDATE entities SET created_at = ?, updated_at = ?", (pinned, pinned))
    db._db.execute(
        "UPDATE entities SET created_at = ?, updated_at = ? WHERE name = 'pattern/slow'",
        (_pinned(184), _pinned(184)),
    )
    db.record_surfaced(
        "search_nodes",
        "backoff retry",
        "rid-1",
        [("proj", "task/fresh", 1), ("proj", "pattern/slow", 2)],
    )
    db.record_surfaced("search_nodes", "cache eviction", "rid-2", [("proj", "task/unrelated", 1)])
    db._db.execute("UPDATE surfaced_entities SET surfaced_at = ?", (pinned,))
    db._db.commit()
    mark_used(db, "rid-1", "pattern/slow")
    mark_used(db, "rid-2", "task/unrelated")


MutateDb = Callable[["DatabaseManager"], None]


@dataclass(frozen=True)
class MeasuredChange:
    """A before/after pair of `EvalReport`s produced by `measure_change`."""

    before: EvalReport
    after: EvalReport

    @property
    def deltas(self) -> dict[str, float]:
        """Per-metric change (after minus before) over the five ranking metrics."""
        return {
            metric: getattr(self.after, metric) - getattr(self.before, metric)
            for metric in RANKING_METRICS
        }

    def format(self) -> str:
        """Render a fixed-width before/after/delta table, including k and query_count."""
        lines = [
            _row("metric", "before", "after", "delta"),
            _row("k", str(self.before.k), str(self.after.k), ""),
            _row("query_count", str(self.before.query_count), str(self.after.query_count), ""),
        ]
        for metric, delta in self.deltas.items():
            before_value = getattr(self.before, metric)
            after_value = getattr(self.after, metric)
            row = _row(metric, f"{before_value:.4f}", f"{after_value:.4f}", f"{delta:.4f}")
            lines.append(row)
        return "\n".join(lines)


def _require_comparable(result: MeasuredChange) -> None:
    """Fail unless before/after are means over the same set of labelled queries."""
    if result.before.query_count == 0:
        raise AssertionError(
            f"no labelled queries were evaluated (before.query_count == 0)\n{result.format()}"
        )
    if result.after.query_count != result.before.query_count:
        raise AssertionError(
            "before/after query counts are not comparable: "
            f"{result.before.query_count} vs {result.after.query_count}\n{result.format()}"
        )


def measure_change(
    db: DatabaseManager,
    mutate: MutateDb,
    *,
    k: int = 10,
    now: datetime,
    require_mutation: bool = True,
) -> MeasuredChange:
    """Evaluate `db`, apply `mutate`, then re-evaluate with the same `k` and `now`.

    `now` is keyword-only with no default, so the two evaluations cannot silently drift apart
    by measuring at different instants. When `require_mutation` is true (the default), this
    snapshots `db._db.total_changes` immediately either side of `mutate` and fails if it did
    not move; this only observes writes made through `db`'s own connection, so a mutation that
    opens its own connection needs `require_mutation=False`.
    """
    before = evaluate(db, k=k, now=now)
    changes_before = db._db.total_changes
    mutate(db)
    changes_after = db._db.total_changes
    after = evaluate(db, k=k, now=now)
    result = MeasuredChange(before=before, after=after)
    if require_mutation and changes_after == changes_before:
        raise AssertionError(f"mutate() made no changes on db's own connection\n{result.format()}")
    _require_comparable(result)
    return result


def attach_eval_report(request: pytest.FixtureRequest, result: MeasuredChange) -> None:
    """Attach `result`'s formatted table as a report section on `request`'s test node."""
    request.node.add_report_section("call", "eval report", result.format())


def assert_no_regression(
    result: MeasuredChange,
    request: pytest.FixtureRequest,
    *,
    metrics: tuple[str, ...] = _GATE_METRICS,
) -> None:
    """Fail if any of `metrics` got worse from before to after."""
    attach_eval_report(request, result)
    deltas = result.deltas
    regressed = {metric: deltas[metric] for metric in metrics if deltas[metric] < 0}
    if regressed:
        raise AssertionError(f"regression in {regressed}\n{result.format()}")


def assert_improves(
    result: MeasuredChange,
    request: pytest.FixtureRequest,
    metric: str,
    *,
    without_regressing: tuple[str, ...] = _GATE_METRICS,
) -> None:
    """Fail unless `metric` strictly improved and none of `without_regressing` got worse."""
    delta = result.deltas[metric]
    if delta <= 0:
        attach_eval_report(request, result)
        raise AssertionError(f"{metric} did not improve (delta={delta})\n{result.format()}")
    assert_no_regression(result, request, metrics=without_regressing)


def assert_within_floor(
    report: EvalReport,
    request: pytest.FixtureRequest,
    *,
    floor: dict[str, tuple[float, float]] = FLOOR,
) -> None:
    """Fail if any metric in `floor` falls outside its (floor, ceiling) bounds."""
    lines = [_row("metric", "value", "floor", "ceiling")]
    out_of_bounds = []
    for metric, (low, high) in floor.items():
        value = getattr(report, metric)
        lines.append(_row(metric, f"{value:.4f}", f"{low:.4f}", f"{high:.4f}"))
        if not low <= value <= high:
            out_of_bounds.append(metric)
    text = "\n".join(lines)
    request.node.add_report_section("call", "eval report", text)
    if out_of_bounds:
        raise AssertionError(f"out of floor/ceiling bounds: {out_of_bounds}\n{text}")
