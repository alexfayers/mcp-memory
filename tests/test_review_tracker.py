"""Tests for the cumulative memory-write review tracker."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from mcp_memory.hooks import review_tracker


@pytest.fixture(autouse=True)
def _sandbox_state(tmp_path: Path) -> None:
    """Point the tracker's state file at a temp directory."""
    with patch.object(review_tracker, "get_data_dir", return_value=tmp_path):
        yield


class TestRecordWrite:
    def test_starts_at_one(self) -> None:
        assert review_tracker.record_write() == 1

    def test_increments_sequentially(self) -> None:
        for expected in range(1, 6):
            assert review_tracker.record_write() == expected


class TestShouldNudge:
    def test_below_threshold_no_nudge(self) -> None:
        for _ in range(review_tracker._REVIEW_NUDGE_THRESHOLD - 1):
            review_tracker.record_write()
        assert review_tracker.should_nudge() is False

    def test_at_threshold_fires(self) -> None:
        for _ in range(review_tracker._REVIEW_NUDGE_THRESHOLD):
            review_tracker.record_write()
        assert review_tracker.should_nudge() is True

    def test_above_threshold_fires(self) -> None:
        for _ in range(review_tracker._REVIEW_NUDGE_THRESHOLD + 5):
            review_tracker.record_write()
        assert review_tracker.should_nudge() is True


class TestReset:
    def test_reset_clears_count(self) -> None:
        for _ in range(5):
            review_tracker.record_write()
        review_tracker.reset()
        assert review_tracker.should_nudge(threshold=1) is False
        assert review_tracker.record_write() == 1


class TestCorruptState:
    def test_missing_file_treated_as_zero(self) -> None:
        assert review_tracker.should_nudge(threshold=1) is False

    def test_corrupt_file_treated_as_zero(self, tmp_path: Path) -> None:
        (tmp_path / "memory-review-state.json").write_text("not json")
        assert review_tracker.record_write() == 1
