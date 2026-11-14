"""Tests for the idle-time dream curation."""

from __future__ import annotations

import asyncio
import json
import sys
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from mcp_memory import dream, dream_status, server, tool_names
from mcp_memory.config import get_db_path
from mcp_memory.storage import open_writable

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from pathlib import Path

_MODEL = "global.anthropic.claude-haiku-4-5-20251001-v1:0"


@pytest.fixture(autouse=True)
def _no_pi(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hide the pi CLI so the claude backend is selected unless a test installs pi."""
    monkeypatch.setattr(dream, "_pi_bin", lambda: None)


@pytest.fixture(autouse=True)
def _reset_coordinator_state() -> None:
    """Reset the dream status marker and the coordinator state per test."""
    dream_status.clear()
    dream._dream_running = False
    dream._manual_tasks.clear()
    dream._session = dream._SessionState()


def _acoro(value: object) -> object:
    """Return an async no-arg callable that resolves to value (for monkeypatching)."""

    async def _call() -> object:
        return value

    return _call


def _dream_payload(
    result: str,
    *,
    model: str = _MODEL,
    is_error: bool = False,
    metrics: bool = False,
) -> str:
    payload: dict[str, object] = {
        "type": "result",
        "is_error": is_error,
        "result": result,
        "modelUsage": {model: {"inputTokens": 100, "outputTokens": 50}},
    }
    if metrics:
        payload |= {
            "duration_ms": 15200,
            "num_turns": 6,
            "total_cost_usd": 0.09,
            "usage": {
                "input_tokens": 120,
                "output_tokens": 340,
                "cache_read_input_tokens": 9000,
                "cache_creation_input_tokens": 800,
            },
        }
    return json.dumps(payload)


class TestBuildMcpConfig:
    def test_points_only_at_memory_server(self) -> None:
        config = dream.build_mcp_config("http://localhost:3000/mcp")
        assert config == {"mcpServers": {"memory": {"type": "http", "url": "http://localhost:3000/mcp"}}}


class TestLoadDreamRules:
    def test_joins_installed_rules_and_skill_without_frontmatter(self, tmp_path: Path) -> None:
        (tmp_path / "rules").mkdir()
        (tmp_path / "skills" / "memory-review").mkdir(parents=True)
        (tmp_path / "rules" / "memory.md").write_text("WRITE RULE\n")
        (tmp_path / "rules" / "memory-read.md").write_text("READ RULE\n")
        (tmp_path / "skills" / "memory-review" / "SKILL.md").write_text("---\nname: memory-review\n---\nREVIEW SKILL\n")
        rules = dream.load_dream_rules(tmp_path)
        assert rules.endswith("WRITE RULE\n\nREAD RULE\n\nREVIEW SKILL")
        assert "name: memory-review" not in rules

    def test_skips_files_that_are_not_installed(self, tmp_path: Path) -> None:
        (tmp_path / "rules").mkdir()
        (tmp_path / "rules" / "memory.md").write_text("WRITE RULE")
        assert dream.load_dream_rules(tmp_path).endswith("WRITE RULE")
        assert dream.load_dream_rules(tmp_path / "missing") == ""


class TestMemoryToolClassification:
    def test_classification_partitions_every_registered_tool(self) -> None:
        mutating = set(tool_names.MUTATING_TOOLS)
        read_only = set(tool_names.READ_ONLY_TOOLS)
        assert not mutating & read_only
        assert mutating | read_only == {tool.name for tool in asyncio.run(server.mcp.list_tools())}


class TestBuildDreamCommand:
    def test_allows_vote_but_denies_other_mutations(self) -> None:
        command = dream.build_dream_command(
            claude_bin="claude", model=_MODEL, mcp_config_path="/tmp/cfg.json", max_votes=15
        )
        deny_index = command.index("--disallowedTools")
        denied = set(command[deny_index + 1 :])
        assert "mcp__memory__vote" not in denied
        assert "mcp__memory__delete_entity" in denied
        assert "mcp__memory__create_entities" in denied

    def test_still_denies_filesystem_and_web_read_builtins(self) -> None:
        command = dream.build_dream_command(
            claude_bin="claude", model=_MODEL, mcp_config_path="/tmp/cfg.json", max_votes=15
        )
        deny_index = command.index("--disallowedTools")
        denied = set(command[deny_index + 1 :])
        for tool in ("Bash", "Write", "Read", "Grep", "Glob", "WebFetch", "WebSearch"):
            assert tool in denied

    def test_isolates_and_pins_the_spawn(self) -> None:
        command = dream.build_dream_command(
            claude_bin="claude", model=_MODEL, mcp_config_path="/tmp/cfg.json", max_votes=15
        )
        assert "--strict-mcp-config" in command
        assert command[command.index("--model") + 1] == _MODEL
        assert command[command.index("--mcp-config") + 1] == "/tmp/cfg.json"
        assert command[command.index("--output-format") + 1] == "stream-json"
        assert "--verbose" in command

    def test_bare_spawn_drops_builtins_and_passes_memory_permissions(self) -> None:
        command = dream.build_dream_command(claude_bin="claude", model=_MODEL, mcp_config_path="/c.json", max_votes=7)
        assert "--bare" in command
        assert command[command.index("--tools") + 1] == ""
        assert json.loads(command[command.index("--settings") + 1]) == dream.AGENT_SETTINGS

    def test_appends_rules_only_when_present(self) -> None:
        with_rules = dream.build_heavy_dream_command(
            claude_bin="claude", model=_MODEL, mcp_config_path="/c.json", max_ops=5, rules="RULES"
        )
        without = dream.build_heavy_dream_command(
            claude_bin="claude", model=_MODEL, mcp_config_path="/c.json", max_ops=5
        )
        assert with_rules[with_rules.index("--append-system-prompt") + 1] == "RULES"
        assert "--append-system-prompt" not in without

    def test_prompt_is_downvote_only_and_embeds_the_cap(self) -> None:
        command = dream.build_dream_command(claude_bin="claude", model=_MODEL, mcp_config_path="/c.json", max_votes=7)
        prompt = command[command.index("-p") + 1]
        assert "7" in prompt
        assert "vote" in prompt
        assert "-1" in prompt

    def test_prompt_interpolates_the_gc_downvote_floor(self) -> None:
        command = dream.build_dream_command(claude_bin="claude", model=_MODEL, mcp_config_path="/c.json", max_votes=7)
        prompt = command[command.index("-p") + 1]
        assert "-10" in prompt

    def test_prompt_specifies_a_parseable_audit_format(self) -> None:
        command = dream.build_dream_command(claude_bin="claude", model=_MODEL, mcp_config_path="/c.json", max_votes=7)
        prompt = command[command.index("-p") + 1]
        assert "[project/entity-name] - reason" in prompt
        assert "nothing demoted" in prompt

    def test_ritual_example_line_round_trips_through_parse_operations(self) -> None:
        operations = dream_status.parse_operations("[myproj/task/old-thing] - superseded")
        assert operations == [
            {
                "project": "myproj",
                "name": "task/old-thing",
                "reason": "superseded",
                "action": "demote",
            }
        ]

    def test_light_denies_merge_entities(self) -> None:
        command = dream.build_dream_command(
            claude_bin="claude", model=_MODEL, mcp_config_path="/tmp/cfg.json", max_votes=15
        )
        deny_index = command.index("--disallowedTools")
        denied = set(command[deny_index + 1 :])
        assert "mcp__memory__merge_entities" in denied

    def test_light_allows_vote_but_denies_merge_observations(self) -> None:
        command = dream.build_dream_command(
            claude_bin="claude", model=_MODEL, mcp_config_path="/tmp/cfg.json", max_votes=15
        )
        deny_index = command.index("--disallowedTools")
        denied = set(command[deny_index + 1 :])
        assert "mcp__memory__vote" not in denied
        assert "mcp__memory__merge_observations" in denied

    def test_prompt_permits_observation_demotion(self) -> None:
        command = dream.build_dream_command(claude_bin="claude", model=_MODEL, mcp_config_path="/c.json", max_votes=7)
        prompt = command[command.index("-p") + 1]
        assert "vote" in prompt
        assert "content_hash" in prompt

    def test_ritual_observation_line_round_trips_to_obs_demote(self) -> None:
        operations = dream_status.parse_operations("[myproj/task/a#a1b2c3d4] - observation demoted: stale")
        assert operations[0]["action"] == "obs-demote"
        assert operations[0]["hash"] == "a1b2c3d4"


class TestBuildHeavyDreamCommand:
    def test_allows_vote_and_merge_but_denies_other_mutations(self) -> None:
        command = dream.build_heavy_dream_command(
            claude_bin="claude", model=_MODEL, mcp_config_path="/tmp/cfg.json", max_ops=10
        )
        deny_index = command.index("--disallowedTools")
        denied = set(command[deny_index + 1 :])
        assert "mcp__memory__vote" not in denied
        assert "mcp__memory__merge_entities" not in denied
        assert "mcp__memory__delete_entity" in denied
        assert "mcp__memory__create_entities" in denied
        assert "mcp__memory__restore_entity" in denied

    def test_still_denies_filesystem_and_web_read_builtins(self) -> None:
        command = dream.build_heavy_dream_command(
            claude_bin="claude", model=_MODEL, mcp_config_path="/tmp/cfg.json", max_ops=10
        )
        deny_index = command.index("--disallowedTools")
        denied = set(command[deny_index + 1 :])
        for tool in ("Bash", "Write", "Read", "Grep", "Glob", "WebFetch", "WebSearch"):
            assert tool in denied

    def test_isolates_and_pins_the_spawn(self) -> None:
        command = dream.build_heavy_dream_command(
            claude_bin="claude", model=_MODEL, mcp_config_path="/tmp/cfg.json", max_ops=10
        )
        assert "--strict-mcp-config" in command
        assert command[command.index("--model") + 1] == _MODEL
        assert command[command.index("--mcp-config") + 1] == "/tmp/cfg.json"
        assert command[command.index("--output-format") + 1] == "stream-json"
        assert "--verbose" in command

    def test_prompt_covers_merge_and_downvote_and_embeds_the_cap(self) -> None:
        command = dream.build_heavy_dream_command(
            claude_bin="claude", model=_MODEL, mcp_config_path="/c.json", max_ops=4
        )
        prompt = command[command.index("-p") + 1]
        assert "4" in prompt
        assert "merge_entities" in prompt
        assert "same project" in prompt
        assert "vote" in prompt

    def test_prompt_interpolates_the_gc_downvote_floor(self) -> None:
        command = dream.build_heavy_dream_command(
            claude_bin="claude", model=_MODEL, mcp_config_path="/c.json", max_ops=4
        )
        prompt = command[command.index("-p") + 1]
        assert "-10" in prompt

    def test_prompt_specifies_a_parseable_audit_format(self) -> None:
        command = dream.build_heavy_dream_command(
            claude_bin="claude", model=_MODEL, mcp_config_path="/c.json", max_ops=4
        )
        prompt = command[command.index("-p") + 1]
        assert "[project/entity-name] - action reason" in prompt
        assert "nothing changed" in prompt

    def test_merge_audit_line_round_trips_through_parse_operations(self) -> None:
        operations = dream_status.parse_operations("[myproj/task/old] - merged into task/new")
        assert operations == [
            {
                "project": "myproj",
                "name": "task/old",
                "reason": "merged into task/new",
                "action": "merge",
            }
        ]

    def test_heavy_allows_observation_vote_and_merge(self) -> None:
        command = dream.build_heavy_dream_command(
            claude_bin="claude", model=_MODEL, mcp_config_path="/tmp/cfg.json", max_ops=10
        )
        deny_index = command.index("--disallowedTools")
        denied = set(command[deny_index + 1 :])
        assert "mcp__memory__vote" not in denied
        assert "mcp__memory__merge_observations" not in denied

    def test_prompt_permits_observation_ops(self) -> None:
        command = dream.build_heavy_dream_command(
            claude_bin="claude", model=_MODEL, mcp_config_path="/c.json", max_ops=4
        )
        prompt = command[command.index("-p") + 1]
        assert "vote" in prompt
        assert "merge_observations" in prompt
        assert "content_hash" in prompt

    def test_obs_merge_audit_line_round_trips(self) -> None:
        operations = dream_status.parse_operations(
            "[myproj/task/a#a1b2c3d4] - merged observation into #deadbeef: duplicate"
        )
        assert operations[0]["action"] == "obs-merge"
        assert operations[0]["hash"] == "a1b2c3d4"


def _threshold_spec(name: str, threshold: float) -> dream.TierSpec:
    """Build a TierSpec whose only meaningful field is its idle threshold."""
    return dream.TierSpec(
        name=name,
        enabled_getter=lambda: True,
        idle_getter=lambda: threshold,
        poll_getter=lambda: 1.0,
        run_pass=_acoro("audit"),  # type: ignore[arg-type]
    )


class TestDueTiers:
    """The pure decision function: which tiers should fire for a (now, idle) sample."""

    _LIGHT = _threshold_spec("light", 1800.0)
    _HEAVY = _threshold_spec("heavy", 5400.0)

    def test_no_tier_is_due_below_the_smallest_threshold(self) -> None:
        state = dream._SessionState()
        due = dream._due_tiers(state, now=100_000.0, idle=100.0, tiers=[self._LIGHT, self._HEAVY])
        assert due == []
        assert state.anchor is None  # no session established while still active

    def test_establishes_session_and_fires_light_at_its_threshold(self) -> None:
        state = dream._SessionState()
        due = dream._due_tiers(state, now=100_000.0, idle=1800.0, tiers=[self._LIGHT, self._HEAVY])
        assert [spec.name for spec in due] == ["light"]
        assert state.anchor == 100_000.0 - 1800.0  # back-dated to the true session start

    def test_does_not_refire_a_tier_already_fired_this_session(self) -> None:
        state = dream._SessionState(anchor=98_200.0, fired={"light"}, last_pass_end=100_000.0)
        due = dream._due_tiers(state, now=101_800.0, idle=3600.0, tiers=[self._LIGHT, self._HEAVY])
        assert due == []  # light fired; heavy not yet due (genuine idle 3600 < 5400)

    def test_heavy_fires_later_in_the_same_session(self) -> None:
        # Light already fired; the idle clock has climbed to the heavy threshold.
        state = dream._SessionState(anchor=98_200.0, fired={"light"}, last_pass_end=100_000.0)
        due = dream._due_tiers(state, now=103_600.0, idle=5400.0, tiers=[self._LIGHT, self._HEAVY])
        assert [spec.name for spec in due] == ["heavy"]

    def test_returns_due_tiers_ascending_by_threshold(self) -> None:
        state = dream._SessionState()
        due = dream._due_tiers(state, now=100_000.0, idle=9000.0, tiers=[self._HEAVY, self._LIGHT])
        assert [spec.name for spec in due] == ["light", "heavy"]

    def test_user_activity_ends_the_session_and_rearms(self) -> None:
        # Both tiers fired; then a marker touch well after our last pass = the user
        # returned. The session resets, so a fresh idle window can fire them again.
        state = dream._SessionState(anchor=98_200.0, fired={"light", "heavy"}, last_pass_end=100_000.0)
        due = dream._due_tiers(state, now=200_000.0, idle=50.0, tiers=[self._LIGHT, self._HEAVY])
        assert due == []  # just-active, nothing due yet
        assert state.anchor is None
        assert state.fired == set()
        # The clock climbs again in the new session: light fires once more.
        due = dream._due_tiers(state, now=205_000.0, idle=1800.0, tiers=[self._LIGHT, self._HEAVY])
        assert [spec.name for spec in due] == ["light"]

    def test_a_self_caused_marker_reset_does_not_end_the_session(self) -> None:
        # The dream's own tool calls reset the marker, but its implied activity is at
        # or before our last pass, so it must NOT be read as the user returning.
        state = dream._SessionState(anchor=98_200.0, fired={"light"}, last_pass_end=100_050.0)
        due = dream._due_tiers(state, now=103_600.0, idle=5400.0, tiers=[self._LIGHT, self._HEAVY])
        assert state.anchor == 98_200.0  # session intact
        assert [spec.name for spec in due] == ["heavy"]


class TestCoordinatorTick:
    @staticmethod
    def _wire(
        monkeypatch: pytest.MonkeyPatch,
        *,
        idle: float | None,
        now: float,
        tiers: list[dream.TierSpec],
    ) -> None:
        monkeypatch.setattr(dream.activity, "idle_seconds", lambda: idle)
        monkeypatch.setattr(dream, "time", SimpleNamespace(monotonic=lambda: now))
        monkeypatch.setattr(dream, "_enabled_tiers", lambda: tiers)
        monkeypatch.setattr(dream.dream_status, "record_pass_start", lambda _tier: None)
        monkeypatch.setattr(dream.dream_status, "record_pass", lambda *_a, **_k: None)

    @staticmethod
    def _run_spec(name: str, threshold: float, runs: list[str]) -> dream.TierSpec:
        async def fake_pass() -> str:
            runs.append(name)
            return "nothing demoted"

        return dream.TierSpec(
            name=name,
            enabled_getter=lambda: True,
            idle_getter=lambda: threshold,
            poll_getter=lambda: 1.0,
            run_pass=fake_pass,
        )

    def test_light_fires_once_then_not_again_while_idle(self, monkeypatch: pytest.MonkeyPatch) -> None:
        runs: list[str] = []
        light = self._run_spec("light", 1800.0, runs)
        self._wire(monkeypatch, idle=1800.0, now=100_000.0, tiers=[light])
        asyncio.run(dream._coordinator_tick())
        assert runs == ["light"]
        # Still idle a poll later - the tier has fired this session, so it holds off.
        monkeypatch.setattr(dream.activity, "idle_seconds", lambda: 2100.0)
        monkeypatch.setattr(dream, "time", SimpleNamespace(monotonic=lambda: 100_300.0))
        asyncio.run(dream._coordinator_tick())
        assert runs == ["light"]

    def test_heavy_fires_once_at_its_threshold(self, monkeypatch: pytest.MonkeyPatch) -> None:
        runs: list[str] = []
        heavy = self._run_spec("heavy", 5400.0, runs)
        self._wire(monkeypatch, idle=5400.0, now=100_000.0, tiers=[heavy])
        asyncio.run(dream._coordinator_tick())
        assert runs == ["heavy"]

    def test_light_then_heavy_fire_in_the_same_idle_session(self, monkeypatch: pytest.MonkeyPatch) -> None:
        runs: list[str] = []
        light = self._run_spec("light", 1800.0, runs)
        heavy = self._run_spec("heavy", 5400.0, runs)
        # Light fires first at 30 min idle.
        self._wire(monkeypatch, idle=1800.0, now=100_000.0, tiers=[light, heavy])
        asyncio.run(dream._coordinator_tick())
        # The clock climbs to 90 min in the SAME session; heavy fires, light does not re-fire.
        monkeypatch.setattr(dream.activity, "idle_seconds", lambda: 5400.0)
        monkeypatch.setattr(dream, "time", SimpleNamespace(monotonic=lambda: 103_600.0))
        asyncio.run(dream._coordinator_tick())
        assert runs == ["light", "heavy"]

    def test_does_not_fire_when_the_guard_is_held(self, monkeypatch: pytest.MonkeyPatch) -> None:
        runs: list[str] = []
        light = self._run_spec("light", 1800.0, runs)
        self._wire(monkeypatch, idle=1800.0, now=100_000.0, tiers=[light])
        monkeypatch.setattr(dream, "_dream_running", True)
        asyncio.run(dream._coordinator_tick())
        assert runs == []
        assert "light" not in dream._session.fired  # unclaimed tier retries next poll

    def test_user_activity_rearms_a_fired_tier(self, monkeypatch: pytest.MonkeyPatch) -> None:
        runs: list[str] = []
        light = self._run_spec("light", 1800.0, runs)
        self._wire(monkeypatch, idle=1800.0, now=100_000.0, tiers=[light])
        asyncio.run(dream._coordinator_tick())
        assert runs == ["light"]
        # The user returns (marker touched well after our pass), then goes idle again.
        monkeypatch.setattr(dream.activity, "idle_seconds", lambda: 50.0)
        monkeypatch.setattr(dream, "time", SimpleNamespace(monotonic=lambda: 200_000.0))
        asyncio.run(dream._coordinator_tick())
        monkeypatch.setattr(dream.activity, "idle_seconds", lambda: 1800.0)
        monkeypatch.setattr(dream, "time", SimpleNamespace(monotonic=lambda: 205_000.0))
        asyncio.run(dream._coordinator_tick())
        assert runs == ["light", "light"]


def _tier_spec(name: str, run_pass: Callable[[], Awaitable[str]]) -> dream.TierSpec:
    """Build a minimal TierSpec whose gates are irrelevant (guard tests call it directly)."""
    return dream.TierSpec(
        name=name,
        enabled_getter=lambda: True,
        idle_getter=lambda: 0.0,
        poll_getter=lambda: 0.0,
        run_pass=run_pass,
    )


class TestRunGuarded:
    def test_runs_and_records_when_free(self, monkeypatch: pytest.MonkeyPatch) -> None:
        recorded: list[tuple[str, bool, str]] = []
        monkeypatch.setattr(
            dream.dream_status,
            "record_pass",
            lambda audit, *, ok, tier="light", **_: recorded.append((audit, ok, tier)),
        )
        spec = _tier_spec("light", _acoro("[p/task/x] - stale"))  # type: ignore[arg-type]
        assert asyncio.run(dream._run_guarded(spec)) is True
        assert recorded == [("[p/task/x] - stale", True, "light")]
        assert dream._dream_running is False  # slot released after the run

    def test_skips_when_already_running(self, monkeypatch: pytest.MonkeyPatch) -> None:
        passes: list[str] = []

        async def fake_pass() -> str:
            passes.append("ran")
            return "audit"

        monkeypatch.setattr(dream, "_dream_running", True)
        spec = _tier_spec("light", fake_pass)
        assert asyncio.run(dream._run_guarded(spec)) is False
        assert passes == []

    def test_releases_the_slot_even_when_the_pass_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(dream.dream_status, "record_pass", lambda *_args, **_kwargs: None)

        async def boom() -> str:
            raise RuntimeError("boom")

        spec = _tier_spec("heavy", boom)
        assert asyncio.run(dream._run_guarded(spec)) is True
        assert dream._dream_running is False

    def test_stamps_last_pass_end_on_the_session(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # last_pass_end is what lets the coordinator attribute a marker reset to the
        # dream itself; it must be stamped at the end of every claimed pass.
        monkeypatch.setattr(dream.dream_status, "record_pass", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(dream, "time", SimpleNamespace(monotonic=lambda: 123_456.0))
        spec = _tier_spec("light", _acoro("nothing demoted"))  # type: ignore[arg-type]
        asyncio.run(dream._run_guarded(spec))
        assert dream._session.last_pass_end == 123_456.0


class TestTriggerDream:
    def test_unknown_tier_is_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(dream, "_claude_bin", lambda: "/usr/bin/claude")
        result = dream.trigger_dream("bogus")
        assert result == {"started": False, "reason": "unknown tier"}

    def test_missing_claude_is_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(dream, "_claude_bin", lambda: None)
        result = dream.trigger_dream("light")
        assert result == {"started": False, "reason": "pi CLI not found and claude CLI not found"}

    def test_already_running_is_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(dream, "_claude_bin", lambda: "/usr/bin/claude")
        monkeypatch.setattr(dream, "_dream_running", True)
        result = dream.trigger_dream("light")
        assert result == {"started": False, "reason": "already running"}

    def test_starts_a_background_pass_regardless_of_enabled_flag(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(dream, "_claude_bin", lambda: "/usr/bin/claude")
        monkeypatch.setattr(dream, "get_dream_enabled", lambda: False)
        passes: list[str] = []

        async def fake_pass() -> str:
            passes.append("ran")
            return "nothing demoted"

        monkeypatch.setattr(dream, "run_dream_pass", fake_pass)
        monkeypatch.setattr(dream.dream_status, "record_pass", lambda *_a, **_k: None)
        monkeypatch.setattr(dream.dream_status, "record_pass_start", lambda _tier: None)

        async def drive() -> dict[str, object]:
            result = dream.trigger_dream("light")
            await asyncio.gather(*dream._manual_tasks)
            return result

        result = asyncio.run(drive())
        assert result == {"started": True}
        assert passes == ["ran"]


class TestCoordinatorLoop:
    def test_cancels_cleanly_and_ticks(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(dream, "get_dream_enabled", lambda: True)
        monkeypatch.setattr(dream, "get_dream_heavy_enabled", lambda: False)
        monkeypatch.setattr(dream, "get_dream_poll_seconds", lambda: 0.0)
        monkeypatch.setattr(dream.dream_status, "record_startup", lambda **_kwargs: None)
        ticks: list[bool] = []

        async def fake_tick() -> None:
            ticks.append(True)

        monkeypatch.setattr(dream, "_coordinator_tick", fake_tick)

        async def drive() -> None:
            task = asyncio.create_task(dream._coordinator_loop())
            await asyncio.sleep(0.05)
            task.cancel()
            await task

        with pytest.raises(asyncio.CancelledError):
            asyncio.run(drive())
        assert ticks  # the loop ran at least one tick before cancellation

    def test_records_no_startup_snapshot(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(dream, "get_dream_enabled", lambda: True)
        monkeypatch.setattr(dream, "get_dream_heavy_enabled", lambda: True)
        recorded: list[dict[str, object]] = []
        monkeypatch.setattr(dream.dream_status, "record_startup", lambda **kwargs: recorded.append(kwargs))

        async def fake_tick() -> None:
            pass

        monkeypatch.setattr(dream, "_coordinator_tick", fake_tick)

        async def drive() -> None:
            task = asyncio.create_task(dream._coordinator_loop())
            await asyncio.sleep(0.05)
            task.cancel()
            await task

        with pytest.raises(asyncio.CancelledError):
            asyncio.run(drive())
        assert recorded == []


class TestStartCoordinator:
    @staticmethod
    def _start(monkeypatch: pytest.MonkeyPatch, *, light: bool, heavy: bool, claude: bool) -> list[str]:
        started: list[str] = []

        async def fake_loop() -> None:
            started.append("coordinating")
            await asyncio.sleep(3600)

        monkeypatch.setattr(dream, "get_dream_enabled", lambda: light)
        monkeypatch.setattr(dream, "get_dream_heavy_enabled", lambda: heavy)
        monkeypatch.setattr(dream.shutil, "which", lambda _: "/usr/bin/claude" if claude else None)
        monkeypatch.setattr(dream, "_coordinator_loop", fake_loop)

        async def drive() -> None:
            task = dream.start_coordinator()
            await asyncio.sleep(0.02)
            if task is not None:
                task.cancel()

        asyncio.run(drive())
        return started

    def test_starts_coordinator_when_a_tier_is_enabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assert self._start(monkeypatch, light=True, heavy=False, claude=True) == ["coordinating"]

    def test_starts_coordinator_when_only_heavy_enabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assert self._start(monkeypatch, light=False, heavy=True, claude=True) == ["coordinating"]

    def test_no_coordinator_when_all_tiers_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assert self._start(monkeypatch, light=False, heavy=False, claude=True) == []

    def test_no_coordinator_when_claude_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assert self._start(monkeypatch, light=True, heavy=True, claude=False) == []

    def test_records_dream_startup_for_both_tiers(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(dream, "get_dream_enabled", lambda: True)
        monkeypatch.setattr(dream, "get_dream_heavy_enabled", lambda: False)
        monkeypatch.setattr(dream, "get_dream_idle_seconds", lambda: 1800.0)
        monkeypatch.setattr(dream, "get_dream_poll_seconds", lambda: 300.0)
        monkeypatch.setattr(dream, "get_dream_heavy_idle_seconds", lambda: 5400.0)
        monkeypatch.setattr(dream, "get_dream_heavy_poll_seconds", lambda: 900.0)
        monkeypatch.setattr(dream.shutil, "which", lambda _: None)
        recorded: list[dict[str, object]] = []
        monkeypatch.setattr(dream.dream_status, "record_startup", lambda **kwargs: recorded.append(kwargs))
        assert dream.start_coordinator() is None
        assert recorded == [
            {
                "tier": "light",
                "enabled": True,
                "idle_threshold_seconds": 1800.0,
                "poll_seconds": 300.0,
            },
            {
                "tier": "heavy",
                "enabled": False,
                "idle_threshold_seconds": 5400.0,
                "poll_seconds": 900.0,
            },
        ]


class TestSpawnEnv:
    def test_isolates_config_dir_from_user_hooks(self) -> None:
        env = dream.build_spawn_env("/tmp/iso", base_env={"PATH": "/bin", "AWS_PROFILE": "x"})
        assert env["CLAUDE_CONFIG_DIR"] == "/tmp/iso"
        assert env["MCP_TIMEOUT"] == "30000"
        assert env["PATH"] == "/bin"
        assert env["AWS_PROFILE"] == "x"

    def test_disables_tool_search(self) -> None:
        env = dream.build_spawn_env("/tmp/iso", base_env={"PATH": "/bin"})
        assert env["ENABLE_TOOL_SEARCH"] == "false"

    def test_isolated_settings_preapprove_memory_without_hooks(self) -> None:
        settings = dream.AGENT_SETTINGS
        permissions = cast("dict[str, list[str]]", settings["permissions"])
        assert "mcp__memory__*" in permissions["allow"]
        assert "hooks" not in settings


class TestParseDreamResult:
    def test_returns_distilled_findings(self) -> None:
        stdout = _dream_payload("- Billing owned by team [bre/feature-billing]")
        result = dream.parse_dream_result(stdout, expected_model=_MODEL)
        assert result["text"] == "- Billing owned by team [bre/feature-billing]"
        assert result["ok"] is True

    def test_extracts_metrics_when_present(self) -> None:
        stdout = _dream_payload("- finding", metrics=True)
        result = dream.parse_dream_result(stdout, expected_model=_MODEL)
        assert result["duration_ms"] == 15200
        assert result["num_turns"] == 6
        assert result["cost_usd"] == 0.09
        assert result["input_tokens"] == 120
        assert result["output_tokens"] == 340
        assert result["cache_read_tokens"] == 9000
        assert result["cache_creation_tokens"] == 800

    def test_metrics_default_to_none_when_absent(self) -> None:
        stdout = _dream_payload("- finding")
        result = dream.parse_dream_result(stdout, expected_model=_MODEL)
        assert result["duration_ms"] is None
        assert result["num_turns"] is None
        assert result["cost_usd"] is None
        assert result["input_tokens"] is None

    def test_rejects_unexpected_model(self) -> None:
        stdout = _dream_payload("- finding", model="global.anthropic.claude-opus-4-8-v1:0")
        with pytest.raises(RuntimeError, match="unexpected model"):
            dream.parse_dream_result(stdout, expected_model=_MODEL)

    def test_rejects_error_payload(self) -> None:
        stdout = _dream_payload("boom", is_error=True)
        with pytest.raises(RuntimeError, match="error"):
            dream.parse_dream_result(stdout, expected_model=_MODEL)

    def test_rejects_malformed_output(self) -> None:
        with pytest.raises(RuntimeError, match="malformed"):
            dream.parse_dream_result("not json", expected_model=_MODEL)

    def test_rejects_empty_result(self) -> None:
        with pytest.raises(RuntimeError, match="no findings"):
            dream.parse_dream_result(_dream_payload("   "), expected_model=_MODEL)


def _assistant_tool_call(name: str, tool_input: dict[str, object]) -> str:
    block = {"type": "tool_use", "name": name, "input": tool_input}
    return json.dumps({"type": "assistant", "message": {"content": [block]}})


class TestStreamParsing:
    def test_result_is_taken_from_the_last_line_of_the_stream(self) -> None:
        stream = "\n".join([
            json.dumps({"type": "system"}),
            _assistant_tool_call("mcp__memory__vote", {}),
            _dream_payload("- done", metrics=True),
        ])
        result = dream.parse_dream_result(stream, expected_model=_MODEL)
        assert result["text"] == "- done"
        assert result["num_turns"] == 6

    def test_stream_without_a_result_is_malformed(self) -> None:
        with pytest.raises(RuntimeError, match="malformed"):
            dream.parse_dream_result(json.dumps({"type": "system"}), expected_model=_MODEL)


class TestSpawnProgress:
    @staticmethod
    def _spawn(lines: list[str], *, timeout: float = 10) -> str:
        script = "import sys\nfor line in sys.argv[1:]: print(line, flush=True)"
        return asyncio.run(dream._spawn_agent([sys.executable, "-c", script, *lines], timeout=timeout))

    def test_mutating_memory_calls_are_recorded_with_short_args(self) -> None:
        dream_status.clear()
        dream_status.record_pass_start("heavy")
        self._spawn([
            _assistant_tool_call("mcp__memory__merge_entities", {"project": "p", "source": "a", "target": "b"}),
            _assistant_tool_call("mcp__memory__vote", {"project": "p", "name": "x" * 80, "value": -1}),
        ])
        status = dream_status.read_status()
        assert status is not None
        calls = status["progress"]["calls"]
        assert calls[0] == "merge_entities p a b"
        assert calls[1].startswith("vote p xxx")
        assert len(calls[1]) < 80

    def test_observation_text_is_never_recorded(self) -> None:
        dream_status.clear()
        dream_status.record_pass_start("heavy")
        self._spawn([
            _assistant_tool_call(
                "mcp__memory__vote", {"project": "p", "name": "e", "vote": -1, "observation": "secret observation text"}
            ),
            _assistant_tool_call(
                "mcp__memory__edit_observation",
                {
                    "project": "p",
                    "entityName": "e",
                    "observationHash": "ab12",
                    "newText": "secret new",
                    "oldText": "secret old",
                },
            ),
        ])
        status = dream_status.read_status()
        assert status is not None
        assert status["progress"]["calls"] == ["vote p e -1", "edit_observation p e ab12"]

    def test_reads_are_counted_and_foreign_tools_ignored(self) -> None:
        dream_status.clear()
        dream_status.record_pass_start("heavy")
        self._spawn([
            _assistant_tool_call("mcp__memory__search_nodes", {"query": "q"}),
            _assistant_tool_call("Bash", {"command": "ls"}),
            "not json",
        ])
        status = dream_status.read_status()
        assert status is not None
        assert status["progress"] == {"reads": 1, "calls": []}

    def test_times_out_a_silent_agent(self) -> None:
        script = "import time; time.sleep(30)"
        with pytest.raises(RuntimeError, match="timed out"):
            asyncio.run(dream._spawn_agent([sys.executable, "-c", script], timeout=0.5))


class TestRunDreamPass:
    def test_returns_audit_summary_end_to_end(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(dream.shutil, "which", lambda _: "/usr/bin/claude")
        monkeypatch.setattr(dream, "get_dream_model", lambda: _MODEL)

        captured: dict[str, object] = {}

        async def fake_spawn(
            command: list[str],
            *,
            env: dict[str, str] | None = None,
            timeout: float = 0,
        ) -> str:
            captured["command"] = command
            return _dream_payload("- demoted [mcp-memory/task/old]: superseded")

        monkeypatch.setattr(dream, "_spawn_agent", fake_spawn)
        result = asyncio.run(dream.run_dream_pass())
        assert result == "- demoted [mcp-memory/task/old]: superseded"
        command = captured["command"]
        assert isinstance(command, list)
        assert "mcp__memory__vote" not in command

    def test_retries_without_bare_when_authentication_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(dream.shutil, "which", lambda _: "/usr/bin/claude")
        monkeypatch.setattr(dream, "get_dream_model", lambda: _MODEL)
        commands: list[list[str]] = []

        async def fake_spawn(
            command: list[str],
            *,
            env: dict[str, str] | None = None,
            timeout: float = 0,
        ) -> str:
            commands.append(command)
            if "--bare" in command:
                raise dream.DreamAuthError("dream agent failed to authenticate")
            return _dream_payload("- demoted [mcp-memory/task/old]: superseded")

        monkeypatch.setattr(dream, "_spawn_agent", fake_spawn)
        result = asyncio.run(dream.run_dream_pass())
        assert result == "- demoted [mcp-memory/task/old]: superseded"
        assert len(commands) == 2
        assert "--bare" not in commands[1]
        assert commands[1][commands[1].index("--tools") + 1] == ""

    def test_reports_missing_claude_cli(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(dream.shutil, "which", lambda _: None)
        result = asyncio.run(dream.run_dream_pass())
        assert "claude CLI not found" in result

    def test_rejects_unexpected_model(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(dream.shutil, "which", lambda _: "/usr/bin/claude")
        monkeypatch.setattr(dream, "get_dream_model", lambda: _MODEL)

        async def fake_spawn(
            command: list[str],
            *,
            env: dict[str, str] | None = None,
            timeout: float = 0,
        ) -> str:
            return _dream_payload("- x", model="global.anthropic.claude-opus-4-8-v1:0")

        monkeypatch.setattr(dream, "_spawn_agent", fake_spawn)
        result = asyncio.run(dream.run_dream_pass())
        assert "unexpected model" in result


class TestFailedAgentLogging:
    def test_logs_stderr_but_keeps_result_generic(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        fake_bin = tmp_path / "claude"
        fake_bin.write_text("#!/bin/sh\necho boom-detail >&2\nexit 1\n")
        fake_bin.chmod(0o755)
        monkeypatch.setattr(dream.shutil, "which", lambda _: str(fake_bin))
        monkeypatch.setattr(dream, "get_dream_model", lambda: _MODEL)
        with caplog.at_level("ERROR", logger="mcp-memory"):
            result = asyncio.run(dream.run_dream_pass())
        assert result == "dream agent exited with an error"
        assert "boom-detail" in caplog.text
        assert "exited 1" in caplog.text

    def test_classifies_a_failed_login_as_an_authentication_failure(self, tmp_path: Path) -> None:
        fake_bin = tmp_path / "claude"
        fake_bin.write_text("#!/bin/sh\necho 'Not logged in' >&2\nexit 1\n")
        fake_bin.chmod(0o755)
        with pytest.raises(dream.DreamAuthError):
            asyncio.run(dream._spawn_agent([str(fake_bin)], timeout=10))

    def test_agent_text_mentioning_a_login_is_not_an_authentication_failure(self, tmp_path: Path) -> None:
        fake_bin = tmp_path / "claude"
        fake_bin.write_text('#!/bin/sh\necho \'{"type":"assistant","text":"Not logged in"}\'\nexit 1\n')
        fake_bin.chmod(0o755)
        with pytest.raises(RuntimeError) as raised:
            asyncio.run(dream._spawn_agent([str(fake_bin)], timeout=10))
        assert not isinstance(raised.value, dream.DreamAuthError)

    def test_logs_agent_output_when_the_agent_reports_an_error_after_a_clean_exit(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setattr(dream.shutil, "which", lambda _: "/usr/bin/claude")
        monkeypatch.setattr(dream, "get_dream_model", lambda: _MODEL)

        async def fake_spawn(
            command: list[str],
            *,
            env: dict[str, str] | None = None,
            timeout: float = 0,
        ) -> str:
            return _dream_payload("agent-failure-detail", is_error=True)

        monkeypatch.setattr(dream, "_spawn_agent", fake_spawn)
        with caplog.at_level("ERROR", logger="mcp-memory"):
            result = asyncio.run(dream.run_dream_pass())
        assert result == "dream agent reported an error"
        assert "agent-failure-detail" in caplog.text


class TestRunHeavyDreamPass:
    @pytest.fixture(autouse=True)
    def _database(self) -> None:
        open_writable(get_db_path()).connection.close()

    def test_returns_audit_summary_end_to_end(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(dream.shutil, "which", lambda _: "/usr/bin/claude")
        monkeypatch.setattr(dream, "get_dream_heavy_model", lambda: _MODEL)

        captured: dict[str, object] = {}

        async def fake_spawn(
            command: list[str],
            *,
            env: dict[str, str] | None = None,
            timeout: float = 0,
        ) -> str:
            captured["command"] = command
            return _dream_payload("[mcp-memory/task/old] - merged into task/new")

        monkeypatch.setattr(dream, "_spawn_agent", fake_spawn)
        result = asyncio.run(dream.run_heavy_dream_pass())
        assert result == "[mcp-memory/task/old] - merged into task/new"
        command = captured["command"]
        assert isinstance(command, list)
        assert "mcp__memory__merge_entities" not in command
        assert "mcp__memory__vote" not in command

    def test_backs_up_the_database_before_the_pass(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(dream.shutil, "which", lambda _: "/usr/bin/claude")

        async def fake_spawn(
            command: list[str],
            *,
            env: dict[str, str] | None = None,
            timeout: float = 0,
        ) -> str:
            assert len(list((get_db_path().parent / "backups").glob("memory-*.db"))) == 1
            return _dream_payload("[p/task/x] - merged")

        monkeypatch.setattr(dream, "_spawn_agent", fake_spawn)
        monkeypatch.setattr(dream, "get_dream_heavy_model", lambda: _MODEL)
        asyncio.run(dream.run_heavy_dream_pass())

    def test_skips_the_backup_when_backups_are_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(dream.shutil, "which", lambda _: None)
        monkeypatch.setenv("MCP_MEMORY_BACKUP_ENABLED", "false")
        asyncio.run(dream.run_heavy_dream_pass())
        assert not (get_db_path().parent / "backups").exists()

    def test_aborts_the_pass_when_the_backup_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(dream.shutil, "which", lambda _: "/usr/bin/claude")

        def fail() -> None:
            raise OSError("disk full")

        async def must_not_spawn(*_: object, **__: object) -> str:
            raise AssertionError("spawned without a backup")

        monkeypatch.setattr(dream.backup, "backup_database", fail)
        monkeypatch.setattr(dream, "_spawn_agent", must_not_spawn)
        assert asyncio.run(dream.run_heavy_dream_pass()).startswith("dream ")

    def test_reports_missing_claude_cli(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(dream.shutil, "which", lambda _: None)
        result = asyncio.run(dream.run_heavy_dream_pass())
        assert "claude CLI not found" in result

    def test_rejects_unexpected_model(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(dream.shutil, "which", lambda _: "/usr/bin/claude")
        monkeypatch.setattr(dream, "get_dream_heavy_model", lambda: _MODEL)

        async def fake_spawn(
            command: list[str],
            *,
            env: dict[str, str] | None = None,
            timeout: float = 0,
        ) -> str:
            return _dream_payload("[p/task/x] - merged", model="global.anthropic.claude-opus-4-8-v1:0")

        monkeypatch.setattr(dream, "_spawn_agent", fake_spawn)
        result = asyncio.run(dream.run_heavy_dream_pass())
        assert "unexpected model" in result


def _pi_message(text: str, *, stop_reason: str = "stop", cost: float = 0.01) -> str:
    message = {
        "role": "assistant",
        "model": "m",
        "stopReason": stop_reason,
        "content": [{"type": "text", "text": text}],
        "usage": {"input": 5, "output": 7, "cacheRead": 100, "cacheWrite": 20, "cost": {"total": cost}},
    }
    return json.dumps({"type": "message_end", "message": message})


class TestPiBackend:
    @pytest.fixture
    def pi_dirs(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
        agent_dir, user_dir = tmp_path / "dream-agent", tmp_path / "pi"
        user_dir.mkdir()
        monkeypatch.setattr(dream, "get_dream_agent_dir", lambda: agent_dir)
        monkeypatch.setattr(dream, "get_pi_agent_dir", lambda: user_dir)
        monkeypatch.delenv("MCP_DREAM_PI_MODEL_LIGHT", raising=False)
        monkeypatch.delenv("MCP_DREAM_PI_MODEL_HEAVY", raising=False)
        return agent_dir, user_dir

    def test_prefers_pi_over_claude_and_returns_summed_telemetry(
        self, monkeypatch: pytest.MonkeyPatch, pi_dirs: tuple[Path, Path]
    ) -> None:
        _, user_dir = pi_dirs
        (user_dir / "settings.json").write_text(json.dumps({"defaultProvider": "prov", "defaultModel": "m1"}))
        monkeypatch.setattr(dream, "_pi_bin", lambda: "/usr/bin/pi")
        monkeypatch.setattr(dream, "_claude_bin", lambda: "/usr/bin/claude")
        captured: dict[str, object] = {}

        async def fake_spawn(command: list[str], *, env: dict[str, str] | None = None, timeout: float = 0) -> str:
            captured["command"], captured["env"] = command, env
            return "\n".join([_pi_message("working", cost=0.01), _pi_message("[p/e] - demoted", cost=0.02)])

        monkeypatch.setattr(dream, "_spawn_agent", fake_spawn)
        assert asyncio.run(dream.run_dream_pass()) == "[p/e] - demoted"
        command, env = cast("list[str]", captured["command"]), cast("dict[str, str]", captured["env"])
        assert command[0] == "/usr/bin/pi"
        assert command[command.index("--model") + 1] == "prov/m1"
        assert env["PI_CODING_AGENT_DIR"] == str(pi_dirs[0])
        metrics = dream._pass_metrics
        assert metrics is not None
        assert (metrics["num_turns"], metrics["input_tokens"], metrics["output_tokens"]) == (2, 10, 14)
        assert (metrics["cache_read_tokens"], metrics["cache_creation_tokens"]) == (200, 40)
        assert metrics["cost_usd"] == pytest.approx(0.03)

    def test_model_comes_from_env_then_agent_settings_then_user_default(
        self, monkeypatch: pytest.MonkeyPatch, pi_dirs: tuple[Path, Path]
    ) -> None:
        agent_dir, user_dir = pi_dirs
        agent_dir.mkdir()
        (user_dir / "settings.json").write_text(json.dumps({"defaultModel": "user-default"}))
        resolve = lambda heavy: dream._resolve_pi_model(heavy=heavy, agent_dir=agent_dir, user_dir=user_dir)  # ruff: ignore[lambda-assignment]
        assert resolve(False) == "user-default"
        (agent_dir / "settings.json").write_text(json.dumps({"dreamModelLight": "from-settings"}))
        assert resolve(False) == "from-settings"
        assert resolve(True) == "user-default"
        monkeypatch.setenv("MCP_DREAM_PI_MODEL_LIGHT", "from-env")
        assert resolve(False) == "from-env"

    def test_command_loads_only_memory_tools_and_denies_mutations(self) -> None:
        light = dream.PassSpec(heavy=False, limit=7, timeout=1)
        command = dream.build_pi_command(
            pi_bin="pi", model="p/m", prompt="go", disallowed=light.disallowed, extensions=("/ext.ts",), rules="r"
        )
        assert command[-1] == "go"
        assert command[command.index("--tools") + 1] == "mcp__memory__*"
        assert command[command.index("--append-system-prompt") + 1] == "r"
        assert command[command.index("-e") : command.index("-e") + 4] == ["-e", "/ext.ts", "-e", "builtin:mcp"]
        excluded = command[command.index("--exclude-tools") + 1].split(",")
        assert "mcp__memory__merge_entities" in excluded
        assert "mcp__memory__vote" not in excluded
        assert "Bash" not in excluded

    def test_agent_dir_is_created_linked_and_keeps_user_edits(self, tmp_path: Path) -> None:
        agent_dir, user_dir = tmp_path / "dream-agent", tmp_path / "pi"
        user_dir.mkdir()
        (user_dir / "auth.json").write_text("{}")
        dream._prepare_pi_agent_dir(agent_dir, user_dir, "http://h/mcp")
        assert json.loads((agent_dir / "mcp.json").read_text())["mcpServers"]["memory"]["exposure"] == "direct"
        assert (agent_dir / "auth.json").resolve() == (user_dir / "auth.json").resolve()
        assert not (agent_dir / "models.json").exists()
        moved = tmp_path / "moved"
        moved.mkdir()
        (moved / "auth.json").write_text("{}")
        dream._prepare_pi_agent_dir(agent_dir, moved, "http://h/mcp")
        assert (agent_dir / "auth.json").resolve() == (moved / "auth.json").resolve()
        (agent_dir / "settings.json").write_text('{"dreamModelHeavy": "mine"}')
        (agent_dir / "mcp.json").write_text(
            '{"mcpServers": {"memory": {"type": "stdio", "command": "x", "timeout": 5}, "other": {"command": "y"}}}'
        )
        dream._prepare_pi_agent_dir(agent_dir, user_dir, "http://h/mcp")
        assert json.loads((agent_dir / "settings.json").read_text()) == {"dreamModelHeavy": "mine"}
        servers = json.loads((agent_dir / "mcp.json").read_text())["mcpServers"]
        assert servers == {"memory": {"type": "http", "url": "http://h/mcp", "exposure": "direct", "timeout": 5}}

    def test_error_stop_reason_fails_the_pass(self) -> None:
        stdout = "\n".join([_pi_message("x"), _pi_message("", stop_reason="error")])
        with pytest.raises(RuntimeError, match="reported an error"):
            dream.parse_pi_result(stdout, duration_ms=1)

    def test_a_truncated_last_message_fails_the_pass(self) -> None:
        with pytest.raises(RuntimeError, match="reported an error"):
            dream.parse_pi_result(_pi_message("cut off", stop_reason="length"), duration_ms=1)

    def test_completed_compaction_usage_is_added_to_the_totals(self) -> None:
        compaction = {"type": "compaction_end", "result": {"usage": {"input": 1, "cost": {"total": 0.5}}}}
        aborted = {"type": "compaction_end", "aborted": True}
        stdout = "\n".join([_pi_message("ok", cost=0.25), json.dumps(compaction), json.dumps(aborted)])
        result = dream.parse_pi_result(stdout, duration_ms=1)
        assert (result["num_turns"], result["input_tokens"]) == (1, 6)
        assert result["cost_usd"] == pytest.approx(0.75)

    def test_an_error_that_pi_retried_past_is_not_a_failure(self) -> None:
        stdout = "\n".join([_pi_message("", stop_reason="error"), _pi_message("[p/e] - demoted")])
        assert dream.parse_pi_result(stdout, duration_ms=1)["text"] == "[p/e] - demoted"

    def test_memory_tool_calls_are_recorded_from_pi_events(self) -> None:
        dream_status.clear()
        dream_status.record_pass_start("light")
        event = {"type": "tool_execution_start", "toolName": "mcp__memory__vote", "args": {"project": "p", "name": "e"}}
        TestSpawnProgress._spawn([json.dumps(event)])
        status = dream_status.read_status()
        assert status is not None
        assert status["progress"]["calls"] == ["vote p e"]
