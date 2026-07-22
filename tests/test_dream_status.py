"""Tests for the persisted dream-status marker."""

from __future__ import annotations

import json
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


class TestParseOperations:
    def test_extracts_project_name_and_reason(self) -> None:
        result = dream_status.parse_operations(
            "- [mcp-memory/task/old-x]: superseded by task/new-x"
        )
        assert result == [
            {
                "project": "mcp-memory",
                "name": "task/old-x",
                "reason": "superseded by task/new-x",
                "action": "demote",
            }
        ]

    def test_parses_multiple_lines(self) -> None:
        text = "[global/pattern/foo] - stale\n[platform/task/bar] duplicate of task/baz"
        result = dream_status.parse_operations(text)
        assert [d["name"] for d in result] == ["pattern/foo", "task/bar"]
        assert result[0]["reason"] == "stale"
        assert result[1]["reason"] == "duplicate of task/baz"

    def test_nothing_demoted_yields_empty(self) -> None:
        assert dream_status.parse_operations("nothing demoted") == []

    def test_ignores_unparseable_lines(self) -> None:
        result = dream_status.parse_operations("I reviewed the graph.\n[p/task/x] - noise\nDone.")
        assert len(result) == 1
        assert result[0]["name"] == "task/x"

    def test_deep_name_keeps_type_prefix(self) -> None:
        assert dream_status.parse_operations("[a/b/c/d]") == [
            {"project": "a", "name": "b/c/d", "reason": "", "action": "demote"}
        ]

    def test_merged_reason_is_a_merge_action(self) -> None:
        result = dream_status.parse_operations("[p/task/old] - merged into task/new: dup")
        assert result[0]["action"] == "merge"
        assert result[0]["reason"] == "merged into task/new: dup"

    def test_non_merge_reason_is_a_demote_action(self) -> None:
        assert dream_status.parse_operations("[p/task/old] - superseded")[0]["action"] == "demote"

    def test_merge_classification_is_case_insensitive(self) -> None:
        result = dream_status.parse_operations("[p/task/old] - Merged into task/new")
        assert result[0]["action"] == "merge"

    def test_observation_demote_action(self) -> None:
        result = dream_status.parse_operations("[p/task/a#a1b2c3d4] - observation demoted: stale")
        assert result[0]["action"] == "obs-demote"
        assert result[0]["name"] == "task/a"
        assert result[0]["hash"] == "a1b2c3d4"

    def test_observation_merge_action(self) -> None:
        result = dream_status.parse_operations(
            "[p/task/a#a1b2c3d4] - merged observation into #deadbeef: duplicate"
        )
        assert result[0]["action"] == "obs-merge"
        assert result[0]["hash"] == "a1b2c3d4"

    def test_hash_suffix_excluded_from_name(self) -> None:
        result = dream_status.parse_operations("[global/pattern/foo#abc12345] - observation stale")
        assert result[0]["name"] == "pattern/foo"
        assert result[0]["hash"] == "abc12345"

    def test_entity_line_has_no_hash_key(self) -> None:
        result = dream_status.parse_operations("[p/task/x] - superseded")
        assert "hash" not in result[0]

    def test_obsolete_reason_is_not_observation_action(self) -> None:
        assert dream_status.parse_operations("[p/task/x] - obsolete")[0]["action"] == "demote"


class TestRecordAndRead:
    def test_absent_status_is_none(self) -> None:
        assert dream_status.read_status() is None

    def test_startup_records_light_config_by_default(self) -> None:
        dream_status.record_startup(
            enabled=True,
            idle_threshold_seconds=1800.0,
            poll_seconds=300.0,
        )
        status = dream_status.read_status()
        assert status is not None
        assert status["configs"] == {
            "light": {
                "enabled": True,
                "idle_threshold_seconds": 1800.0,
                "poll_seconds": 300.0,
            }
        }
        assert status["last_pass"] is None

    def test_startup_records_heavy_config_under_its_tier(self) -> None:
        dream_status.record_startup(
            tier="heavy",
            enabled=True,
            idle_threshold_seconds=5400.0,
            poll_seconds=900.0,
        )
        status = dream_status.read_status()
        assert status is not None
        assert status["configs"]["heavy"] == {
            "enabled": True,
            "idle_threshold_seconds": 5400.0,
            "poll_seconds": 900.0,
        }

    def test_both_tiers_coexist_in_the_marker(self) -> None:
        dream_status.record_startup(
            tier="light",
            enabled=True,
            idle_threshold_seconds=1800.0,
            poll_seconds=300.0,
        )
        dream_status.record_startup(
            tier="heavy",
            enabled=False,
            idle_threshold_seconds=5400.0,
            poll_seconds=900.0,
        )
        status = dream_status.read_status()
        assert status is not None
        assert set(status["configs"]) == {"light", "heavy"}
        assert status["configs"]["light"]["enabled"] is True
        assert status["configs"]["heavy"]["enabled"] is False

    def test_migrates_legacy_schema1_marker_on_read(self) -> None:
        path = dream_status._status_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "schema": 1,
                    "config": {
                        "enabled": True,
                        "idle_threshold_seconds": 3600.0,
                        "poll_seconds": 900.0,
                    },
                    "last_pass": {
                        "ts": 1.0,
                        "ok": True,
                        "tier": "light",
                        "audit_text": "[p/task/x] - stale",
                        "demotions": [{"project": "p", "name": "task/x", "reason": "stale"}],
                    },
                }
            ),
            encoding="utf-8",
        )
        status = dream_status.read_status()
        assert status is not None
        assert status["configs"]["light"] == {
            "enabled": True,
            "idle_threshold_seconds": 3600.0,
            "poll_seconds": 900.0,
        }
        assert status["last_pass"] is not None
        assert status["last_pass"]["operations"] == [
            {"project": "p", "name": "task/x", "reason": "stale", "action": "demote"}
        ]

    def test_drops_legacy_interval_seconds_from_a_config_on_read(self) -> None:
        path = dream_status._status_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "schema": 2,
                    "configs": {
                        "heavy": {
                            "enabled": True,
                            "idle_threshold_seconds": 1800.0,
                            "interval_seconds": 86400.0,
                            "poll_seconds": 900.0,
                        }
                    },
                    "last_pass": None,
                }
            ),
            encoding="utf-8",
        )
        status = dream_status.read_status()
        assert status is not None
        assert status["configs"]["heavy"] == {
            "enabled": True,
            "idle_threshold_seconds": 1800.0,
            "poll_seconds": 900.0,
        }

    def test_record_pass_sets_last_pass_with_operations(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(dream_status.time, "time", lambda: 5000.0)
        dream_status.record_startup(
            enabled=True,
            idle_threshold_seconds=1800.0,
            poll_seconds=300.0,
        )
        dream_status.record_pass("[scratch/task/old] - stale", ok=True)
        status = dream_status.read_status()
        assert status is not None
        last = status["last_pass"]
        assert last is not None
        assert last["ts"] == 5000.0
        assert last["ok"] is True
        assert last["audit_text"] == "[scratch/task/old] - stale"
        assert last["operations"] == [
            {"project": "scratch", "name": "task/old", "reason": "stale", "action": "demote"}
        ]

    def test_record_pass_retains_raw_text_when_unparseable(self) -> None:
        dream_status.record_startup(enabled=True, idle_threshold_seconds=1.0, poll_seconds=1.0)
        dream_status.record_pass("nothing demoted", ok=True)
        status = dream_status.read_status()
        assert status is not None
        assert status["last_pass"] is not None
        assert status["last_pass"]["audit_text"] == "nothing demoted"
        assert status["last_pass"]["operations"] == []

    def test_failed_pass_records_ok_false(self) -> None:
        dream_status.record_startup(enabled=True, idle_threshold_seconds=1.0, poll_seconds=1.0)
        dream_status.record_pass("recall timed out", ok=False)
        status = dream_status.read_status()
        assert status is not None
        assert status["last_pass"] is not None
        assert status["last_pass"]["ok"] is False

    def test_pass_defaults_to_light_tier(self) -> None:
        dream_status.record_startup(enabled=True, idle_threshold_seconds=1.0, poll_seconds=1.0)
        dream_status.record_pass("[p/task/x] - stale", ok=True)
        status = dream_status.read_status()
        assert status is not None
        assert status["last_pass"] is not None
        assert status["last_pass"]["tier"] == "light"

    def test_pass_records_heavy_tier(self) -> None:
        dream_status.record_startup(enabled=True, idle_threshold_seconds=1.0, poll_seconds=1.0)
        dream_status.record_pass("[p/task/old] - merged into task/new", ok=True, tier="heavy")
        status = dream_status.read_status()
        assert status is not None
        assert status["last_pass"] is not None
        assert status["last_pass"]["tier"] == "heavy"

    def test_tier_survives_restart(self) -> None:
        dream_status.record_startup(enabled=True, idle_threshold_seconds=1.0, poll_seconds=1.0)
        dream_status.record_pass("[p/task/old] - merged", ok=True, tier="heavy")
        dream_status.clear()  # simulate a restart: in-memory state lost, disk retained
        dream_status.record_startup(enabled=True, idle_threshold_seconds=1.0, poll_seconds=1.0)
        status = dream_status.read_status()
        assert status is not None
        assert status["last_pass"] is not None
        assert status["last_pass"]["tier"] == "heavy"

    def test_startup_preserves_prior_last_pass_across_restart(self) -> None:
        dream_status.record_startup(enabled=True, idle_threshold_seconds=1.0, poll_seconds=1.0)
        dream_status.record_pass("[p/task/x] - gone", ok=True)
        dream_status.clear()  # simulate a restart: in-memory state lost, disk retained
        dream_status.record_startup(enabled=False, idle_threshold_seconds=2.0, poll_seconds=2.0)
        status = dream_status.read_status()
        assert status is not None
        assert status["configs"]["light"]["enabled"] is False
        assert status["last_pass"] is not None
        assert status["last_pass"]["operations"][0]["name"] == "task/x"

    def test_corrupt_file_reads_as_none(self) -> None:
        path = dream_status._status_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json", encoding="utf-8")
        assert dream_status.read_status() is None

    def test_clear_resets_in_memory_state(self) -> None:
        dream_status.record_startup(enabled=True, idle_threshold_seconds=1.0, poll_seconds=1.0)
        dream_status.clear()
        assert dream_status._configs == {}
        assert dream_status._last_pass is None


class TestRunning:
    def test_absent_marker_has_no_running_tier(self) -> None:
        dream_status.record_startup(enabled=True, idle_threshold_seconds=1.0, poll_seconds=1.0)
        status = dream_status.read_status()
        assert status is not None
        assert status["running"] is None

    def test_pass_start_marks_the_running_tier(self) -> None:
        dream_status.record_pass_start("heavy")
        status = dream_status.read_status()
        assert status is not None
        assert status["running"] == "heavy"

    def test_pass_clears_the_running_tier(self) -> None:
        dream_status.record_pass_start("light")
        dream_status.record_pass("nothing demoted", ok=True)
        status = dream_status.read_status()
        assert status is not None
        assert status["running"] is None

    def test_startup_resets_a_stale_running_tier(self) -> None:
        dream_status.record_pass_start("light")  # a phantom left by a crash mid-pass
        dream_status.clear()  # simulate a restart: in-memory state lost, disk retained
        dream_status.record_startup(enabled=True, idle_threshold_seconds=1.0, poll_seconds=1.0)
        status = dream_status.read_status()
        assert status is not None
        assert status["running"] is None

    def test_legacy_marker_without_running_reads_as_none(self) -> None:
        path = dream_status._status_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"schema": 2, "configs": {}, "last_pass": None}), encoding="utf-8"
        )
        status = dream_status.read_status()
        assert status is not None
        assert status["running"] is None

    def test_write_leaves_no_temp_file(self) -> None:
        dream_status.record_pass_start("light")
        dream_status.record_pass("nothing demoted", ok=True)
        leftovers = list(dream_status._status_path().parent.glob("*.tmp"))
        assert leftovers == []

    def test_clear_resets_running(self) -> None:
        dream_status.record_pass_start("light")
        dream_status.clear()
        assert dream_status._running is None
