"""Tests for the count of nudges the hooks showed."""

from __future__ import annotations

from mcp_memory import nudge_count
from mcp_memory.nudge_count import NudgeKind


def test_nudges_are_counted_by_kind_and_the_reported_ones_removed() -> None:
    assert nudge_count.read() == {}

    nudge_count.record(NudgeKind.REMINDER)
    nudge_count.record(NudgeKind.REMINDER)
    nudge_count.record(NudgeKind.COMPACT)
    reported = nudge_count.read()
    nudge_count.record(NudgeKind.REMINDER)
    nudge_count.subtract(reported)

    assert nudge_count.read() == {"reminder": 1}
