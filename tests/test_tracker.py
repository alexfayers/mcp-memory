"""Tests for the memory tool-call tracker."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from cline_hooks.core.state import set_hook_context
import pytest

from mcp_memory.hooks import tracker


@pytest.fixture(autouse=True)
def _sandbox_state(tmp_path: Path) -> Iterator[None]:
    """Point the tracker's state file at a temp directory."""
    with patch.object(tracker, "get_data_dir", return_value=tmp_path):
        yield


def _rewound(record: Callable[[], object]) -> None:
    """Record state at one point in the context, then rewind past that point."""
    set_hook_context("t1", "a1", ["a1"])
    record()
    set_hook_context("t1", "a2", ["a2"])


class TestContextAnchors:
    @pytest.mark.parametrize(
        ("record", "recorded"),
        [
            (lambda: tracker.increment("t1", 10), lambda: tracker.should_block("t1")),
            (lambda: tracker.tick_turn("t1"), lambda: tracker.tick_turn("t1", 0) > 0),
            (lambda: tracker.mark_flag("t1", "wide"), lambda: tracker.has_flag("t1", "wide")),
            (
                lambda: tracker.mark_scope_blocked("t1", "repo-a"),
                lambda: tracker.has_scope_blocked("t1", "repo-a"),
            ),
            (
                lambda: tracker.take_unseen("t1", "recalled", ["h1"], 1),
                lambda: not tracker.take_unseen("t1", "recalled", ["h1"], 1),
            ),
        ],
        ids=["count", "turn", "flag", "scope_blocked", "seen"],
    )
    def test_context_state_is_forgotten_once_its_anchor_leaves_the_context(
        self, record: Callable[[], object], recorded: Callable[[], bool]
    ) -> None:
        _rewound(record)
        assert not recorded()

    def test_the_window_changes_once_its_anchor_leaves_the_context(self) -> None:
        set_hook_context("t1", "a1", ["a1"])
        window = tracker.get_window("t1")
        assert tracker.get_window("t1") == window
        set_hook_context("t1", "a2", ["a2"])
        assert tracker.get_window("t1") != window

    @pytest.mark.parametrize(
        ("record", "recorded"),
        [
            (lambda: tracker.set_scope("t1", "repo-a"), lambda: tracker.get_scope("t1") == "repo-a"),
            (lambda: tracker.mark_flag("t1", "wrote", per_session=True), lambda: tracker.has_flag("t1", "wrote")),
            (lambda: tracker.mark_edited("t1"), lambda: tracker.has_edited("t1")),
            (lambda: tracker.claim_edit_path("t1", "a.py"), lambda: not tracker.claim_edit_path("t1", "b.py")),
        ],
        ids=["scope", "session_flag", "edited", "edit_path"],
    )
    def test_session_state_survives_a_rewind_past_it(
        self, record: Callable[[], object], recorded: Callable[[], bool]
    ) -> None:
        _rewound(record)
        assert recorded()


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


class TestShouldWarn:
    def test_warns_from_the_soft_threshold_but_not_before(self) -> None:
        assert tracker._MEMORY_WARN_THRESHOLD < tracker._MEMORY_BLOCK_THRESHOLD
        for _ in range(int(tracker._MEMORY_WARN_THRESHOLD) - 1):
            tracker.increment("t1")
        assert tracker.should_warn("t1") is False
        tracker.increment("t1")
        assert tracker.should_warn("t1") is True


class TestReset:
    def test_reset_zeroes_counter(self) -> None:
        for _ in range(tracker._MEMORY_BLOCK_THRESHOLD):
            tracker.increment("t1")
        tracker.reset("t1")
        assert tracker.should_block("t1") is False


class TestScope:
    def test_scope_round_trips_per_task(self) -> None:
        tracker.set_scope("t1", "repo-a")
        assert tracker.get_scope("t1") == "repo-a"
        assert tracker.get_scope("t2") is None


class TestClear:
    def test_clear_removes_scope_blocked_entry(self) -> None:
        tracker.mark_scope_blocked("t1", "some-project")
        tracker.clear("t1")
        assert tracker.has_scope_blocked("t1", "some-project") is False

    def test_clear_removes_agent_scoped_entries(self) -> None:
        tracker.mark_scope_blocked("t1:agent-a", "some-project")
        tracker.clear("t1")
        assert tracker.has_scope_blocked("t1:agent-a", "some-project") is False

    def test_clear_removes_scope(self) -> None:
        tracker.set_scope("t1", "repo-a")
        tracker.clear("t1")
        assert tracker.get_scope("t1") is None
