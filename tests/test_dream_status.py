"""Tests for the persisted dream-status marker."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from mcp_memory import dream_status

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def _clear_dream_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Reset the process-global dream state and isolate the marker file on disk."""
    monkeypatch.setenv("MCP_MEMORY_DB_PATH", str(tmp_path / "memory.db"))
    dream_status.clear()


class TestParseDemotions:
    def test_extracts_project_name_and_reason(self) -> None:
        result = dream_status.parse_demotions("- [mcp-memory/task/old-x]: superseded by task/new-x")
        assert result == [
            {"project": "mcp-memory", "name": "task/old-x", "reason": "superseded by task/new-x"}
        ]

    def test_parses_multiple_lines(self) -> None:
        text = "[global/pattern/foo] - stale\n[platform/task/bar] duplicate of task/baz"
        result = dream_status.parse_demotions(text)
        assert [d["name"] for d in result] == ["pattern/foo", "task/bar"]
        assert result[0]["reason"] == "stale"
        assert result[1]["reason"] == "duplicate of task/baz"

    def test_nothing_demoted_yields_empty(self) -> None:
        assert dream_status.parse_demotions("nothing demoted") == []

    def test_ignores_unparseable_lines(self) -> None:
        result = dream_status.parse_demotions("I reviewed the graph.\n[p/task/x] - noise\nDone.")
        assert len(result) == 1
        assert result[0]["name"] == "task/x"

    def test_deep_name_keeps_type_prefix(self) -> None:
        assert dream_status.parse_demotions("[a/b/c/d]") == [
            {"project": "a", "name": "b/c/d", "reason": ""}
        ]


class TestRecordAndRead:
    def test_absent_status_is_none(self) -> None:
        assert dream_status.read_status() is None

    def test_startup_records_config(self) -> None:
        dream_status.record_startup(
            enabled=True, idle_threshold_seconds=7200.0, poll_seconds=1800.0
        )
        status = dream_status.read_status()
        assert status is not None
        assert status["config"] == {
            "enabled": True,
            "idle_threshold_seconds": 7200.0,
            "poll_seconds": 1800.0,
        }
        assert status["last_pass"] is None

    def test_record_pass_sets_last_pass_with_demotions(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(dream_status.time, "time", lambda: 5000.0)
        dream_status.record_startup(
            enabled=True, idle_threshold_seconds=7200.0, poll_seconds=1800.0
        )
        dream_status.record_pass("[scratch/task/old] - stale", ok=True)
        status = dream_status.read_status()
        assert status is not None
        last = status["last_pass"]
        assert last is not None
        assert last["ts"] == 5000.0
        assert last["ok"] is True
        assert last["audit_text"] == "[scratch/task/old] - stale"
        assert last["demotions"] == [{"project": "scratch", "name": "task/old", "reason": "stale"}]

    def test_record_pass_retains_raw_text_when_unparseable(self) -> None:
        dream_status.record_startup(enabled=True, idle_threshold_seconds=1.0, poll_seconds=1.0)
        dream_status.record_pass("nothing demoted", ok=True)
        status = dream_status.read_status()
        assert status is not None
        assert status["last_pass"] is not None
        assert status["last_pass"]["audit_text"] == "nothing demoted"
        assert status["last_pass"]["demotions"] == []

    def test_failed_pass_records_ok_false(self) -> None:
        dream_status.record_startup(enabled=True, idle_threshold_seconds=1.0, poll_seconds=1.0)
        dream_status.record_pass("recall timed out", ok=False)
        status = dream_status.read_status()
        assert status is not None
        assert status["last_pass"] is not None
        assert status["last_pass"]["ok"] is False

    def test_startup_preserves_prior_last_pass_across_restart(self) -> None:
        dream_status.record_startup(enabled=True, idle_threshold_seconds=1.0, poll_seconds=1.0)
        dream_status.record_pass("[p/task/x] - gone", ok=True)
        dream_status.clear()  # simulate a restart: in-memory state lost, disk retained
        dream_status.record_startup(enabled=False, idle_threshold_seconds=2.0, poll_seconds=2.0)
        status = dream_status.read_status()
        assert status is not None
        assert status["config"] is not None
        assert status["config"]["enabled"] is False
        assert status["last_pass"] is not None
        assert status["last_pass"]["demotions"][0]["name"] == "task/x"

    def test_corrupt_file_reads_as_none(self) -> None:
        path = dream_status._status_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json", encoding="utf-8")
        assert dream_status.read_status() is None

    def test_clear_resets_in_memory_state(self) -> None:
        dream_status.record_startup(enabled=True, idle_threshold_seconds=1.0, poll_seconds=1.0)
        dream_status.clear()
        assert dream_status._config is None
        assert dream_status._last_pass is None
