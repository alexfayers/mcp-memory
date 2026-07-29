"""Tests for the memory tool-call tracker."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import pytest

from mcp_memory.hooks import tracker


@pytest.fixture(autouse=True)
def _sandbox_state(tmp_path: Path) -> None:
    """Point the tracker's state file at a temp directory."""
    with patch.object(tracker, "get_data_dir", return_value=tmp_path):
        yield


class TestIncrement:
    def test_default_amount_adds_one(self) -> None:
        assert tracker.increment("t1") == 1
        assert tracker.increment("t1") == 2

    def test_fractional_amount_accumulates(self) -> None:
        assert tracker.increment("t1", 0.25) == 0.25
        assert tracker.increment("t1", 0.25) == 0.5

    def test_concurrent_increments_do_not_lose_updates(self) -> None:
        call_count = 50
        with ThreadPoolExecutor(max_workers=10) as executor:
            list(executor.map(lambda _: tracker.increment("t1"), range(call_count)))
        assert tracker.should_block("t1", threshold=call_count) is True


class TestShouldBlock:
    def test_below_threshold_does_not_block(self) -> None:
        for _ in range(tracker._MEMORY_BLOCK_THRESHOLD - 1):
            tracker.increment("t1")
        assert tracker.should_block("t1") is False

    def test_fractional_counter_blocks_at_threshold(self) -> None:
        for _ in range(tracker._MEMORY_BLOCK_THRESHOLD * 4):
            tracker.increment("t1", 0.25)
        assert tracker.should_block("t1") is True

    def test_fractional_counter_below_threshold_does_not_block(self) -> None:
        for _ in range(tracker._MEMORY_BLOCK_THRESHOLD * 4 - 1):
            tracker.increment("t1", 0.25)
        assert tracker.should_block("t1") is False


class TestReset:
    def test_reset_zeroes_counter(self) -> None:
        for _ in range(tracker._MEMORY_BLOCK_THRESHOLD):
            tracker.increment("t1")
        tracker.reset("t1")
        assert tracker.should_block("t1") is False
