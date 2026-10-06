"""Tests for the ranking replay that scores the fixture through the real search tool bodies."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from mcp_memory.eval import iter_labelled_queries
from mcp_memory.storage import open_writable
from mcp_memory.storage.operations import reads
from mcp_memory.storage.pure import ranking
from mcp_memory.storage.pure.ranking import score_row
from tests.eval.eval_fixture import _build_populated_fixture
from tests.eval.eval_harness import _GATE_METRICS
from tests.eval.ranking_replay import measure

if TYPE_CHECKING:
    from datetime import datetime
    from pathlib import Path

    from tests.eval.eval_fixture import EvalFixture


def _inverted(*, rank: float, updated_at: str, entity_type: str, vote_score: int, now: datetime) -> float:
    return -score_row(rank=rank, updated_at=updated_at, entity_type=entity_type, vote_score=vote_score, now=now)


_ABLATIONS: dict[str, tuple[tuple[object, str, object], ...]] = {
    "no-recency": ((ranking, "_RECENCY_FLOOR", 1.0),),
    "no-votes": ((ranking, "_VOTE_WEIGHT", 0.0),),
    "bm25-only": ((ranking, "_RECENCY_FLOOR", 1.0), (ranking, "_VOTE_WEIGHT", 0.0)),
    "inverted": ((reads, "score_row", _inverted),),
}


@pytest.fixture
def fixture(tmp_path: Path) -> EvalFixture:
    """Build the full populated fixture on a fresh database."""
    return _build_populated_fixture(open_writable(tmp_path / "replay.db"))


class TestMeasure:
    def test_measures_at_the_fixture_shape(self, fixture: EvalFixture) -> None:
        baseline = measure(fixture)

        assert baseline.k == fixture.k
        assert baseline.unreachable is not None
        assert baseline.unreachable.query_count == sum(
            fixture.is_unreachable(query) for query in iter_labelled_queries(fixture.db)
        )
        assert baseline.query_count + baseline.unreachable.query_count == fixture.query_count

    def test_records_no_surfacings(self, fixture: EvalFixture) -> None:
        count_sql = "SELECT COUNT(*) AS n FROM surfaced_entities"
        before = fixture.db.connection.query_one(count_sql)["n"]

        measure(fixture)

        assert fixture.db.connection.query_one(count_sql)["n"] == before

    def test_fails_when_the_search_ignores_the_pinned_clock(
        self, fixture: EvalFixture, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        search = reads.Reads.search

        def search_with_explicit_now(self: reads.Reads, *args: Any, **kwargs: Any) -> reads.NodeList:
            return search(self, *args, now=fixture.now, **kwargs)

        monkeypatch.setattr(reads.Reads, "search", search_with_explicit_now)

        with pytest.raises(RuntimeError, match="pinned clock"):
            measure(fixture)

    def test_a_ranking_regression_lowers_recall(self, fixture: EvalFixture) -> None:
        before = measure(fixture).metrics["mean_recall_at_k"]
        with fixture.db.connection.transaction():
            fixture.db.connection.write_many(
                "UPDATE entities SET vote_score = -5 WHERE name = ?",
                [(name,) for name in fixture.relevant_names],
            )

        assert measure(fixture).metrics["mean_recall_at_k"] < before


class TestSensitivity:
    @pytest.mark.parametrize("patches", list(_ABLATIONS.values()), ids=list(_ABLATIONS))
    def test_an_ablated_ranker_regresses_the_gate(
        self,
        fixture: EvalFixture,
        monkeypatch: pytest.MonkeyPatch,
        patches: tuple[tuple[object, str, object], ...],
    ) -> None:
        current = measure(fixture).metrics
        for target, attribute, value in patches:
            monkeypatch.setattr(target, attribute, value)

        ablated = measure(fixture).metrics

        assert all(ablated[metric] <= current[metric] for metric in _GATE_METRICS)
        assert any(ablated[metric] < current[metric] for metric in _GATE_METRICS)
