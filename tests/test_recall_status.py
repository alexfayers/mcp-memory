"""Tests for the persisted recall-status marker."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from mcp_memory import recall_status

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def _clear_recall_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Reset the process-global recall state and isolate the marker file on disk."""
    monkeypatch.setenv("MCP_MEMORY_DB_PATH", str(tmp_path / "memory.db"))
    recall_status.clear()


def _finish(
    query: str = "who owns billing",
    *,
    ok: bool = True,
    duration_ms: int | None = 15200,
    num_turns: int | None = 6,
    cost_usd: float | None = 0.09,
) -> None:
    recall_status.record_finish(
        query, ok=ok, duration_ms=duration_ms, num_turns=num_turns, cost_usd=cost_usd
    )


class TestRecordAndRead:
    def test_absent_status_is_none(self) -> None:
        assert recall_status.read_status() is None

    def test_record_start_marks_one_active(self) -> None:
        recall_status.record_start()
        status = recall_status.read_status()
        assert status is not None
        assert status["active"] == 1
        assert status["recent"] == []

    def test_two_starts_count_two_active(self) -> None:
        recall_status.record_start()
        recall_status.record_start()
        status = recall_status.read_status()
        assert status is not None
        assert status["active"] == 2

    def test_record_finish_decrements_and_appends(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(recall_status.time, "time", lambda: 5000.0)
        recall_status.record_start()
        _finish()
        status = recall_status.read_status()
        assert status is not None
        assert status["active"] == 0
        assert len(status["recent"]) == 1
        record = status["recent"][0]
        assert record["ts"] == 5000.0
        assert record["query"] == "who owns billing"
        assert record["ok"] is True
        assert record["duration_ms"] == 15200
        assert record["num_turns"] == 6
        assert record["cost_usd"] == 0.09

    def test_query_truncated_to_max_chars(self) -> None:
        long_query = "x" * 200
        _finish(long_query)
        status = recall_status.read_status()
        assert status is not None
        assert len(status["recent"][0]["query"]) == recall_status._MAX_QUERY_CHARS

    def test_finish_clamps_active_at_zero(self) -> None:
        _finish()
        status = recall_status.read_status()
        assert status is not None
        assert status["active"] == 0

    def test_failed_recall_records_ok_false_with_none_metrics(self) -> None:
        recall_status.record_start()
        _finish(ok=False, duration_ms=None, num_turns=None, cost_usd=None)
        status = recall_status.read_status()
        assert status is not None
        record = status["recent"][-1]
        assert record["ok"] is False
        assert record["duration_ms"] is None
        assert record["num_turns"] is None
        assert record["cost_usd"] is None

    def test_history_is_bounded(self) -> None:
        for i in range(recall_status._MAX_RECENT + 5):
            _finish(f"query {i}")
        status = recall_status.read_status()
        assert status is not None
        assert len(status["recent"]) == recall_status._MAX_RECENT
        assert status["recent"][-1]["query"] == f"query {recall_status._MAX_RECENT + 4}"

    def test_startup_resets_active_and_preserves_recent(self) -> None:
        _finish("[recorded]")
        recall_status.record_start()  # a phantom in-flight left by a crash
        recall_status.clear()  # simulate a restart: in-memory state lost, disk retained
        recall_status.record_startup()
        status = recall_status.read_status()
        assert status is not None
        assert status["active"] == 0
        assert status["recent"][-1]["query"] == "[recorded]"

    def test_corrupt_file_reads_as_none(self) -> None:
        path = recall_status._status_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json", encoding="utf-8")
        assert recall_status.read_status() is None

    def test_write_leaves_no_temp_file(self) -> None:
        recall_status.record_start()
        _finish()
        leftovers = list(recall_status._status_path().parent.glob("*.tmp"))
        assert leftovers == []

    def test_clear_resets_in_memory_state(self) -> None:
        recall_status.record_start()
        _finish()
        recall_status.clear()
        assert recall_status._active == 0
        assert len(recall_status._recent) == 0
