"""Tests for the memory-agent recall server."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

import httpx
import pytest
from mcp.server.fastmcp import FastMCP

from mcp_memory import agent, cli, dream_status, recall_status

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from pathlib import Path

_MODEL = "global.anthropic.claude-haiku-4-5-20251001-v1:0"


@pytest.fixture(autouse=True)
def _isolate_markers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate the status markers on disk and reset the coordinator state per test."""
    monkeypatch.setenv("MCP_MEMORY_DB_PATH", str(tmp_path / "memory.db"))
    dream_status.clear()
    recall_status.clear()
    agent._dream_running = False
    agent._manual_tasks.clear()
    agent._session = agent._SessionState()


def _acoro(value: object) -> object:
    """Return an async no-arg callable that resolves to value (for monkeypatching)."""

    async def _call() -> object:
        return value

    return _call


def _recall_payload(
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
        payload |= {"duration_ms": 15200, "num_turns": 6, "total_cost_usd": 0.09}
    return json.dumps(payload)


class TestBuildMcpConfig:
    def test_points_only_at_memory_server(self) -> None:
        config = agent.build_mcp_config("http://localhost:3000/mcp")
        assert config == {
            "mcpServers": {"memory": {"type": "http", "url": "http://localhost:3000/mcp"}}
        }


class TestRegisterRecall:
    @staticmethod
    def _tool_names(server: FastMCP) -> set[str]:
        return {tool.name for tool in asyncio.run(server.list_tools())}

    def test_registers_recall_when_claude_present(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(agent.shutil, "which", lambda _: "/usr/bin/claude")
        server = FastMCP("test")
        agent._register_recall(server)
        assert "recall" in self._tool_names(server)

    def test_hides_recall_when_claude_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(agent.shutil, "which", lambda _: None)
        server = FastMCP("test")
        agent._register_recall(server)
        assert "recall" not in self._tool_names(server)


class TestBuildRecallCommand:
    def test_denies_every_mutating_and_builtin_tool(self) -> None:
        command = agent.build_recall_command(
            "who owns X",
            claude_bin="/usr/bin/claude",
            model=_MODEL,
            mcp_config_path="/tmp/cfg.json",
            max_turns=12,
        )
        deny_index = command.index("--disallowedTools")
        denied = set(command[deny_index + 1 :])
        assert "mcp__memory__create_entities" in denied
        assert "mcp__memory__vote_entity" in denied
        assert "mcp__memory__vote_observation" in denied
        assert "mcp__memory__restore_entity" in denied
        assert "mcp__memory__merge_entities" in denied
        assert "mcp__memory__merge_observations" in denied
        assert "Bash" in denied

    def test_allows_memory_read_tools_by_not_denying_them(self) -> None:
        command = agent.build_recall_command(
            "q", claude_bin="claude", model=_MODEL, mcp_config_path="/tmp/cfg.json", max_turns=12
        )
        assert "mcp__memory__search_nodes" not in command
        assert "mcp__memory__get_entity_with_relations" not in command

    def test_denies_filesystem_and_web_read_builtins(self) -> None:
        command = agent.build_recall_command(
            "q", claude_bin="claude", model=_MODEL, mcp_config_path="/tmp/cfg.json", max_turns=12
        )
        deny_index = command.index("--disallowedTools")
        denied = set(command[deny_index + 1 :])
        for tool in ("Read", "Grep", "Glob", "WebFetch", "WebSearch"):
            assert tool in denied

    def test_isolates_and_pins_the_spawn(self) -> None:
        command = agent.build_recall_command(
            "q", claude_bin="claude", model=_MODEL, mcp_config_path="/tmp/cfg.json", max_turns=12
        )
        assert "--strict-mcp-config" in command
        assert command[command.index("--model") + 1] == _MODEL
        assert command[command.index("--mcp-config") + 1] == "/tmp/cfg.json"

    def test_caps_tool_calling_turns(self) -> None:
        command = agent.build_recall_command(
            "q", claude_bin="claude", model=_MODEL, mcp_config_path="/c.json", max_turns=8
        )
        assert command[command.index("--max-turns") + 1] == "8"

    def test_embeds_query_and_slug_ritual_in_prompt(self) -> None:
        command = agent.build_recall_command(
            "who owns billing",
            claude_bin="claude",
            model=_MODEL,
            mcp_config_path="/c.json",
            max_turns=12,
        )
        prompt = command[command.index("-p") + 1]
        assert "who owns billing" in prompt
        assert "[project/entity-name]" in prompt

    def test_prompt_steers_for_specific_facts(self) -> None:
        command = agent.build_recall_command(
            "q", claude_bin="claude", model=_MODEL, mcp_config_path="/c.json", max_turns=12
        )
        prompt = command[command.index("-p") + 1].lower()
        assert "still connecting" in prompt
        assert "[project/entity-name]" in prompt
        assert "observations that answer the query" in prompt
        assert "newer" in prompt


class TestBuildDreamCommand:
    def test_allows_vote_entity_but_denies_other_mutations(self) -> None:
        command = agent.build_dream_command(
            claude_bin="claude", model=_MODEL, mcp_config_path="/tmp/cfg.json", max_votes=15
        )
        deny_index = command.index("--disallowedTools")
        denied = set(command[deny_index + 1 :])
        assert "mcp__memory__vote_entity" not in denied
        assert "mcp__memory__delete_entity" in denied
        assert "mcp__memory__create_entities" in denied

    def test_still_denies_filesystem_and_web_read_builtins(self) -> None:
        command = agent.build_dream_command(
            claude_bin="claude", model=_MODEL, mcp_config_path="/tmp/cfg.json", max_votes=15
        )
        deny_index = command.index("--disallowedTools")
        denied = set(command[deny_index + 1 :])
        for tool in ("Bash", "Write", "Read", "Grep", "Glob", "WebFetch", "WebSearch"):
            assert tool in denied

    def test_isolates_and_pins_the_spawn(self) -> None:
        command = agent.build_dream_command(
            claude_bin="claude", model=_MODEL, mcp_config_path="/tmp/cfg.json", max_votes=15
        )
        assert "--strict-mcp-config" in command
        assert command[command.index("--model") + 1] == _MODEL
        assert command[command.index("--mcp-config") + 1] == "/tmp/cfg.json"
        assert command[command.index("--output-format") + 1] == "json"

    def test_prompt_is_downvote_only_and_embeds_the_cap(self) -> None:
        command = agent.build_dream_command(
            claude_bin="claude", model=_MODEL, mcp_config_path="/c.json", max_votes=7
        )
        prompt = command[command.index("-p") + 1]
        assert "7" in prompt
        assert "vote_entity" in prompt
        assert "-1" in prompt

    def test_prompt_interpolates_the_gc_downvote_floor(self) -> None:
        command = agent.build_dream_command(
            claude_bin="claude", model=_MODEL, mcp_config_path="/c.json", max_votes=7
        )
        prompt = command[command.index("-p") + 1]
        assert "-10" in prompt

    def test_prompt_specifies_a_parseable_audit_format(self) -> None:
        command = agent.build_dream_command(
            claude_bin="claude", model=_MODEL, mcp_config_path="/c.json", max_votes=7
        )
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
        command = agent.build_dream_command(
            claude_bin="claude", model=_MODEL, mcp_config_path="/tmp/cfg.json", max_votes=15
        )
        deny_index = command.index("--disallowedTools")
        denied = set(command[deny_index + 1 :])
        assert "mcp__memory__merge_entities" in denied

    def test_light_allows_vote_observation_but_denies_merge_observations(self) -> None:
        command = agent.build_dream_command(
            claude_bin="claude", model=_MODEL, mcp_config_path="/tmp/cfg.json", max_votes=15
        )
        deny_index = command.index("--disallowedTools")
        denied = set(command[deny_index + 1 :])
        assert "mcp__memory__vote_observation" not in denied
        assert "mcp__memory__merge_observations" in denied

    def test_prompt_permits_observation_demotion(self) -> None:
        command = agent.build_dream_command(
            claude_bin="claude", model=_MODEL, mcp_config_path="/c.json", max_votes=7
        )
        prompt = command[command.index("-p") + 1]
        assert "vote_observation" in prompt
        assert "content_hash" in prompt

    def test_ritual_observation_line_round_trips_to_obs_demote(self) -> None:
        operations = dream_status.parse_operations(
            "[myproj/task/a#a1b2c3d4] - observation demoted: stale"
        )
        assert operations[0]["action"] == "obs-demote"
        assert operations[0]["hash"] == "a1b2c3d4"


class TestBuildHeavyDreamCommand:
    def test_allows_vote_and_merge_but_denies_other_mutations(self) -> None:
        command = agent.build_heavy_dream_command(
            claude_bin="claude", model=_MODEL, mcp_config_path="/tmp/cfg.json", max_ops=10
        )
        deny_index = command.index("--disallowedTools")
        denied = set(command[deny_index + 1 :])
        assert "mcp__memory__vote_entity" not in denied
        assert "mcp__memory__merge_entities" not in denied
        assert "mcp__memory__delete_entity" in denied
        assert "mcp__memory__create_entities" in denied
        assert "mcp__memory__restore_entity" in denied

    def test_still_denies_filesystem_and_web_read_builtins(self) -> None:
        command = agent.build_heavy_dream_command(
            claude_bin="claude", model=_MODEL, mcp_config_path="/tmp/cfg.json", max_ops=10
        )
        deny_index = command.index("--disallowedTools")
        denied = set(command[deny_index + 1 :])
        for tool in ("Bash", "Write", "Read", "Grep", "Glob", "WebFetch", "WebSearch"):
            assert tool in denied

    def test_isolates_and_pins_the_spawn(self) -> None:
        command = agent.build_heavy_dream_command(
            claude_bin="claude", model=_MODEL, mcp_config_path="/tmp/cfg.json", max_ops=10
        )
        assert "--strict-mcp-config" in command
        assert command[command.index("--model") + 1] == _MODEL
        assert command[command.index("--mcp-config") + 1] == "/tmp/cfg.json"
        assert command[command.index("--output-format") + 1] == "json"

    def test_prompt_covers_merge_and_downvote_and_embeds_the_cap(self) -> None:
        command = agent.build_heavy_dream_command(
            claude_bin="claude", model=_MODEL, mcp_config_path="/c.json", max_ops=4
        )
        prompt = command[command.index("-p") + 1]
        assert "4" in prompt
        assert "merge_entities" in prompt
        assert "same project" in prompt
        assert "vote_entity" in prompt

    def test_prompt_interpolates_the_gc_downvote_floor(self) -> None:
        command = agent.build_heavy_dream_command(
            claude_bin="claude", model=_MODEL, mcp_config_path="/c.json", max_ops=4
        )
        prompt = command[command.index("-p") + 1]
        assert "-10" in prompt

    def test_prompt_specifies_a_parseable_audit_format(self) -> None:
        command = agent.build_heavy_dream_command(
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
        command = agent.build_heavy_dream_command(
            claude_bin="claude", model=_MODEL, mcp_config_path="/tmp/cfg.json", max_ops=10
        )
        deny_index = command.index("--disallowedTools")
        denied = set(command[deny_index + 1 :])
        assert "mcp__memory__vote_observation" not in denied
        assert "mcp__memory__merge_observations" not in denied

    def test_prompt_permits_observation_ops(self) -> None:
        command = agent.build_heavy_dream_command(
            claude_bin="claude", model=_MODEL, mcp_config_path="/c.json", max_ops=4
        )
        prompt = command[command.index("-p") + 1]
        assert "vote_observation" in prompt
        assert "merge_observations" in prompt
        assert "content_hash" in prompt

    def test_obs_merge_audit_line_round_trips(self) -> None:
        operations = dream_status.parse_operations(
            "[myproj/task/a#a1b2c3d4] - merged observation into #deadbeef: duplicate"
        )
        assert operations[0]["action"] == "obs-merge"
        assert operations[0]["hash"] == "a1b2c3d4"


class TestFetchIdleSeconds:
    @staticmethod
    def _patch_transport(monkeypatch: pytest.MonkeyPatch, handler: object) -> None:
        real_client = httpx.AsyncClient
        monkeypatch.setattr(agent, "get_memory_url", lambda: "http://localhost:3000/mcp")
        monkeypatch.setattr(
            httpx,
            "AsyncClient",
            lambda **_kw: real_client(transport=httpx.MockTransport(handler)),
        )

    def test_queries_api_idle_stripping_the_mcp_suffix(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        requested: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            requested["url"] = str(request.url)
            return httpx.Response(200, json={"last_activity": 100.0, "idle_seconds": 42.0})

        self._patch_transport(monkeypatch, handler)
        idle = asyncio.run(agent.fetch_idle_seconds())
        assert idle == 42.0
        assert requested["url"] == "http://localhost:3000/api/idle"

    def test_returns_none_on_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._patch_transport(monkeypatch, lambda _request: httpx.Response(500))
        assert asyncio.run(agent.fetch_idle_seconds()) is None


def _threshold_spec(name: str, threshold: float) -> agent.TierSpec:
    """Build a TierSpec whose only meaningful field is its idle threshold."""
    return agent.TierSpec(
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
        state = agent._SessionState()
        due = agent._due_tiers(state, now=100_000.0, idle=100.0, tiers=[self._LIGHT, self._HEAVY])
        assert due == []
        assert state.anchor is None  # no session established while still active

    def test_establishes_session_and_fires_light_at_its_threshold(self) -> None:
        state = agent._SessionState()
        due = agent._due_tiers(state, now=100_000.0, idle=1800.0, tiers=[self._LIGHT, self._HEAVY])
        assert [spec.name for spec in due] == ["light"]
        assert state.anchor == 100_000.0 - 1800.0  # back-dated to the true session start

    def test_does_not_refire_a_tier_already_fired_this_session(self) -> None:
        state = agent._SessionState(anchor=98_200.0, fired={"light"}, last_pass_end=100_000.0)
        due = agent._due_tiers(state, now=101_800.0, idle=3600.0, tiers=[self._LIGHT, self._HEAVY])
        assert due == []  # light fired; heavy not yet due (genuine idle 3600 < 5400)

    def test_heavy_fires_later_in_the_same_session(self) -> None:
        # Light already fired; the idle clock has climbed to the heavy threshold.
        state = agent._SessionState(anchor=98_200.0, fired={"light"}, last_pass_end=100_000.0)
        due = agent._due_tiers(state, now=103_600.0, idle=5400.0, tiers=[self._LIGHT, self._HEAVY])
        assert [spec.name for spec in due] == ["heavy"]

    def test_returns_due_tiers_ascending_by_threshold(self) -> None:
        state = agent._SessionState()
        due = agent._due_tiers(state, now=100_000.0, idle=9000.0, tiers=[self._HEAVY, self._LIGHT])
        assert [spec.name for spec in due] == ["light", "heavy"]

    def test_user_activity_ends_the_session_and_rearms(self) -> None:
        # Both tiers fired; then a marker touch well after our last pass = the user
        # returned. The session resets, so a fresh idle window can fire them again.
        state = agent._SessionState(
            anchor=98_200.0, fired={"light", "heavy"}, last_pass_end=100_000.0
        )
        due = agent._due_tiers(state, now=200_000.0, idle=50.0, tiers=[self._LIGHT, self._HEAVY])
        assert due == []  # just-active, nothing due yet
        assert state.anchor is None
        assert state.fired == set()
        # The clock climbs again in the new session: light fires once more.
        due = agent._due_tiers(state, now=205_000.0, idle=1800.0, tiers=[self._LIGHT, self._HEAVY])
        assert [spec.name for spec in due] == ["light"]

    def test_a_self_caused_marker_reset_does_not_end_the_session(self) -> None:
        # The dream's own tool calls reset the marker, but its implied activity is at
        # or before our last pass, so it must NOT be read as the user returning.
        state = agent._SessionState(anchor=98_200.0, fired={"light"}, last_pass_end=100_050.0)
        due = agent._due_tiers(state, now=103_600.0, idle=5400.0, tiers=[self._LIGHT, self._HEAVY])
        assert state.anchor == 98_200.0  # session intact
        assert [spec.name for spec in due] == ["heavy"]


class TestCoordinatorTick:
    @staticmethod
    def _wire(
        monkeypatch: pytest.MonkeyPatch,
        *,
        idle: float | None,
        now: float,
        tiers: list[agent.TierSpec],
    ) -> None:
        monkeypatch.setattr(agent, "fetch_idle_seconds", _acoro(idle))
        monkeypatch.setattr(agent.time, "monotonic", lambda: now)
        monkeypatch.setattr(agent, "_enabled_tiers", lambda: tiers)
        monkeypatch.setattr(agent.dream_status, "record_pass_start", lambda _tier: None)
        monkeypatch.setattr(agent.dream_status, "record_pass", lambda *_a, **_k: None)

    @staticmethod
    def _run_spec(name: str, threshold: float, runs: list[str]) -> agent.TierSpec:
        async def fake_pass() -> str:
            runs.append(name)
            return "nothing demoted"

        return agent.TierSpec(
            name=name,
            enabled_getter=lambda: True,
            idle_getter=lambda: threshold,
            poll_getter=lambda: 1.0,
            run_pass=fake_pass,
        )

    def test_light_fires_once_then_not_again_while_idle(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runs: list[str] = []
        light = self._run_spec("light", 1800.0, runs)
        self._wire(monkeypatch, idle=1800.0, now=100_000.0, tiers=[light])
        asyncio.run(agent._coordinator_tick())
        assert runs == ["light"]
        # Still idle a poll later - the tier has fired this session, so it holds off.
        monkeypatch.setattr(agent, "fetch_idle_seconds", _acoro(2100.0))
        monkeypatch.setattr(agent.time, "monotonic", lambda: 100_300.0)
        asyncio.run(agent._coordinator_tick())
        assert runs == ["light"]

    def test_heavy_fires_once_at_its_threshold(self, monkeypatch: pytest.MonkeyPatch) -> None:
        runs: list[str] = []
        heavy = self._run_spec("heavy", 5400.0, runs)
        self._wire(monkeypatch, idle=5400.0, now=100_000.0, tiers=[heavy])
        asyncio.run(agent._coordinator_tick())
        assert runs == ["heavy"]

    def test_light_then_heavy_fire_in_the_same_idle_session(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runs: list[str] = []
        light = self._run_spec("light", 1800.0, runs)
        heavy = self._run_spec("heavy", 5400.0, runs)
        # Light fires first at 30 min idle.
        self._wire(monkeypatch, idle=1800.0, now=100_000.0, tiers=[light, heavy])
        asyncio.run(agent._coordinator_tick())
        # The clock climbs to 90 min in the SAME session; heavy fires, light does not re-fire.
        monkeypatch.setattr(agent, "fetch_idle_seconds", _acoro(5400.0))
        monkeypatch.setattr(agent.time, "monotonic", lambda: 103_600.0)
        asyncio.run(agent._coordinator_tick())
        assert runs == ["light", "heavy"]

    def test_returns_without_firing_when_idle_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runs: list[str] = []
        light = self._run_spec("light", 1800.0, runs)
        self._wire(monkeypatch, idle=None, now=100_000.0, tiers=[light])
        asyncio.run(agent._coordinator_tick())
        assert runs == []
        assert agent._session.anchor is None  # a missing sample establishes nothing

    def test_does_not_fire_when_the_guard_is_held(self, monkeypatch: pytest.MonkeyPatch) -> None:
        runs: list[str] = []
        light = self._run_spec("light", 1800.0, runs)
        self._wire(monkeypatch, idle=1800.0, now=100_000.0, tiers=[light])
        monkeypatch.setattr(agent, "_dream_running", True)
        asyncio.run(agent._coordinator_tick())
        assert runs == []
        assert "light" not in agent._session.fired  # unclaimed tier retries next poll

    def test_user_activity_rearms_a_fired_tier(self, monkeypatch: pytest.MonkeyPatch) -> None:
        runs: list[str] = []
        light = self._run_spec("light", 1800.0, runs)
        self._wire(monkeypatch, idle=1800.0, now=100_000.0, tiers=[light])
        asyncio.run(agent._coordinator_tick())
        assert runs == ["light"]
        # The user returns (marker touched well after our pass), then goes idle again.
        monkeypatch.setattr(agent, "fetch_idle_seconds", _acoro(50.0))
        monkeypatch.setattr(agent.time, "monotonic", lambda: 200_000.0)
        asyncio.run(agent._coordinator_tick())
        monkeypatch.setattr(agent, "fetch_idle_seconds", _acoro(1800.0))
        monkeypatch.setattr(agent.time, "monotonic", lambda: 205_000.0)
        asyncio.run(agent._coordinator_tick())
        assert runs == ["light", "light"]


def _tier_spec(name: str, run_pass: Callable[[], Awaitable[str]]) -> agent.TierSpec:
    """Build a minimal TierSpec whose gates are irrelevant (guard tests call it directly)."""
    return agent.TierSpec(
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
            agent.dream_status,
            "record_pass",
            lambda audit, *, ok, tier="light": recorded.append((audit, ok, tier)),
        )
        spec = _tier_spec("light", _acoro("[p/task/x] - stale"))  # type: ignore[arg-type]
        assert asyncio.run(agent._run_guarded(spec)) is True
        assert recorded == [("[p/task/x] - stale", True, "light")]
        assert agent._dream_running is False  # slot released after the run

    def test_skips_when_already_running(self, monkeypatch: pytest.MonkeyPatch) -> None:
        passes: list[str] = []

        async def fake_pass() -> str:
            passes.append("ran")
            return "audit"

        monkeypatch.setattr(agent, "_dream_running", True)
        spec = _tier_spec("light", fake_pass)
        assert asyncio.run(agent._run_guarded(spec)) is False
        assert passes == []

    def test_releases_the_slot_even_when_the_pass_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(agent.dream_status, "record_pass", lambda *_args, **_kwargs: None)

        async def boom() -> str:
            raise RuntimeError("boom")

        spec = _tier_spec("heavy", boom)
        assert asyncio.run(agent._run_guarded(spec)) is True
        assert agent._dream_running is False

    def test_stamps_last_pass_end_on_the_session(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # last_pass_end is what lets the coordinator attribute a marker reset to the
        # dream itself; it must be stamped at the end of every claimed pass.
        monkeypatch.setattr(agent.dream_status, "record_pass", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(agent.time, "monotonic", lambda: 123_456.0)
        spec = _tier_spec("light", _acoro("nothing demoted"))  # type: ignore[arg-type]
        asyncio.run(agent._run_guarded(spec))
        assert agent._session.last_pass_end == 123_456.0


class TestTriggerDream:
    def test_unknown_tier_is_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(agent, "_claude_bin", lambda: "/usr/bin/claude")
        result = agent.trigger_dream("bogus")
        assert result == {"started": False, "reason": "unknown tier"}

    def test_missing_claude_is_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(agent, "_claude_bin", lambda: None)
        result = agent.trigger_dream("light")
        assert result == {"started": False, "reason": "claude CLI not found"}

    def test_already_running_is_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(agent, "_claude_bin", lambda: "/usr/bin/claude")
        monkeypatch.setattr(agent, "_dream_running", True)
        result = agent.trigger_dream("light")
        assert result == {"started": False, "reason": "already running"}

    def test_starts_a_background_pass_regardless_of_enabled_flag(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(agent, "_claude_bin", lambda: "/usr/bin/claude")
        monkeypatch.setattr(agent, "get_dream_enabled", lambda: False)
        passes: list[str] = []

        async def fake_pass() -> str:
            passes.append("ran")
            return "nothing demoted"

        monkeypatch.setattr(agent, "run_dream_pass", fake_pass)
        monkeypatch.setattr(agent.dream_status, "record_pass", lambda *_a, **_k: None)
        monkeypatch.setattr(agent.dream_status, "record_pass_start", lambda _tier: None)

        async def drive() -> dict[str, object]:
            result = agent.trigger_dream("light")
            await asyncio.gather(*agent._manual_tasks)
            return result

        result = asyncio.run(drive())
        assert result == {"started": True}
        assert passes == ["ran"]


class TestDreamTriggerRoute:
    @staticmethod
    def _client() -> httpx.AsyncClient:
        transport = httpx.ASGITransport(app=agent.mcp.streamable_http_app())
        return httpx.AsyncClient(transport=transport, base_url="http://test")

    @pytest.mark.anyio
    async def test_started_returns_200(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(agent, "trigger_dream", lambda _tier: {"started": True})
        async with self._client() as client:
            resp = await client.post("/api/dream/trigger", json={"tier": "light"})
        assert resp.status_code == 200
        assert resp.json() == {"started": True}

    @pytest.mark.anyio
    async def test_rejected_returns_409(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            agent, "trigger_dream", lambda _tier: {"started": False, "reason": "already running"}
        )
        async with self._client() as client:
            resp = await client.post("/api/dream/trigger", json={"tier": "light"})
        assert resp.status_code == 409
        assert resp.json()["reason"] == "already running"

    @pytest.mark.anyio
    async def test_missing_tier_returns_400(self) -> None:
        async with self._client() as client:
            resp = await client.post("/api/dream/trigger", json={})
        assert resp.status_code == 400
        assert resp.json()["started"] is False


class TestCoordinatorLoop:
    def test_cancels_cleanly_and_ticks(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(agent, "get_dream_enabled", lambda: True)
        monkeypatch.setattr(agent, "get_dream_heavy_enabled", lambda: False)
        monkeypatch.setattr(agent, "get_dream_poll_seconds", lambda: 0.0)
        monkeypatch.setattr(agent.dream_status, "record_startup", lambda **_kwargs: None)
        ticks: list[bool] = []

        async def fake_tick() -> None:
            ticks.append(True)

        monkeypatch.setattr(agent, "_coordinator_tick", fake_tick)

        async def drive() -> None:
            task = asyncio.create_task(agent._coordinator_loop())
            await asyncio.sleep(0.05)
            task.cancel()
            await task

        with pytest.raises(asyncio.CancelledError):
            asyncio.run(drive())
        assert ticks  # the loop ran at least one tick before cancellation

    def test_records_startup_for_each_enabled_tier(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(agent, "get_dream_enabled", lambda: True)
        monkeypatch.setattr(agent, "get_dream_heavy_enabled", lambda: True)
        monkeypatch.setattr(agent, "get_dream_idle_seconds", lambda: 1800.0)
        monkeypatch.setattr(agent, "get_dream_poll_seconds", lambda: 300.0)
        monkeypatch.setattr(agent, "get_dream_heavy_idle_seconds", lambda: 5400.0)
        monkeypatch.setattr(agent, "get_dream_heavy_poll_seconds", lambda: 900.0)
        recorded: list[dict[str, object]] = []
        monkeypatch.setattr(
            agent.dream_status, "record_startup", lambda **kwargs: recorded.append(kwargs)
        )

        async def fake_tick() -> None:
            pass

        monkeypatch.setattr(agent, "_coordinator_tick", fake_tick)

        async def drive() -> None:
            task = asyncio.create_task(agent._coordinator_loop())
            await asyncio.sleep(0.05)
            task.cancel()
            await task

        with pytest.raises(asyncio.CancelledError):
            asyncio.run(drive())
        assert recorded == [
            {
                "tier": "light",
                "enabled": True,
                "idle_threshold_seconds": 1800.0,
                "poll_seconds": 300.0,
            },
            {
                "tier": "heavy",
                "enabled": True,
                "idle_threshold_seconds": 5400.0,
                "poll_seconds": 900.0,
            },
        ]


class TestAgentCli:
    def test_setup_service_installs_agent_spec(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, object] = {}
        monkeypatch.setattr(
            agent, "_setup_service_from_spec", lambda spec: captured.update(spec=spec)
        )
        agent.main(["setup-service"])
        spec = captured["spec"]
        assert isinstance(spec, cli._ServiceSpec)
        assert spec.binary_name == "memory-agent"

    def test_bare_invocation_runs_server(self, monkeypatch: pytest.MonkeyPatch) -> None:
        ran: list[bool] = []
        monkeypatch.setattr(agent, "_serve", _acoro(None))
        monkeypatch.setattr(agent.anyio, "run", lambda fn: ran.append(fn is agent._serve))
        agent.main([])
        assert ran == [True]


class TestServe:
    @staticmethod
    def _wire_coordinator(monkeypatch: pytest.MonkeyPatch, started: list[str]) -> None:
        async def fake_loop() -> None:
            started.append("coordinating")
            await asyncio.sleep(3600)

        async def fake_serve_http() -> None:
            await asyncio.sleep(0.02)

        monkeypatch.setattr(agent, "_coordinator_loop", fake_loop)
        monkeypatch.setattr(agent.mcp, "run_streamable_http_async", fake_serve_http)

    def test_starts_coordinator_when_a_tier_is_enabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(agent, "get_dream_enabled", lambda: True)
        monkeypatch.setattr(agent, "get_dream_heavy_enabled", lambda: False)
        monkeypatch.setattr(agent.shutil, "which", lambda _: "/usr/bin/claude")
        started: list[str] = []
        self._wire_coordinator(monkeypatch, started)
        asyncio.run(agent._serve())
        assert started == ["coordinating"]

    def test_starts_coordinator_when_only_heavy_enabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(agent, "get_dream_enabled", lambda: False)
        monkeypatch.setattr(agent, "get_dream_heavy_enabled", lambda: True)
        monkeypatch.setattr(agent.shutil, "which", lambda _: "/usr/bin/claude")
        started: list[str] = []
        self._wire_coordinator(monkeypatch, started)
        asyncio.run(agent._serve())
        assert started == ["coordinating"]

    def test_no_coordinator_when_all_tiers_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(agent, "get_dream_enabled", lambda: False)
        monkeypatch.setattr(agent, "get_dream_heavy_enabled", lambda: False)
        monkeypatch.setattr(agent.shutil, "which", lambda _: "/usr/bin/claude")
        started: list[str] = []
        self._wire_coordinator(monkeypatch, started)
        asyncio.run(agent._serve())
        assert started == []

    def test_no_coordinator_when_claude_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(agent, "get_dream_enabled", lambda: True)
        monkeypatch.setattr(agent, "get_dream_heavy_enabled", lambda: True)
        monkeypatch.setattr(agent.shutil, "which", lambda _: None)
        started: list[str] = []
        self._wire_coordinator(monkeypatch, started)
        asyncio.run(agent._serve())
        assert started == []

    def test_resets_recall_active_at_boot(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(agent, "get_dream_enabled", lambda: False)
        monkeypatch.setattr(agent, "get_dream_heavy_enabled", lambda: False)
        recall_status.record_start()  # a stale in-flight count a crash could leave

        async def fake_serve_http() -> None:
            await asyncio.sleep(0.01)

        monkeypatch.setattr(agent.mcp, "run_streamable_http_async", fake_serve_http)
        asyncio.run(agent._serve())
        status = recall_status.read_status()
        assert status is not None
        assert status["active"] == 0


class TestSpawnEnv:
    def test_isolates_config_dir_from_user_hooks(self) -> None:
        env = agent.build_spawn_env("/tmp/iso", base_env={"PATH": "/bin", "AWS_PROFILE": "x"})
        assert env["CLAUDE_CONFIG_DIR"] == "/tmp/iso"
        assert env["MCP_TIMEOUT"] == "30000"
        assert env["PATH"] == "/bin"
        assert env["AWS_PROFILE"] == "x"

    def test_disables_tool_search(self) -> None:
        env = agent.build_spawn_env("/tmp/iso", base_env={"PATH": "/bin"})
        assert env["ENABLE_TOOL_SEARCH"] == "false"

    def test_isolated_settings_preapprove_memory_without_hooks(self) -> None:
        settings = agent.AGENT_SETTINGS
        assert "mcp__memory__*" in settings["permissions"]["allow"]
        assert "hooks" not in settings


class TestParseRecallResult:
    def test_returns_distilled_findings(self) -> None:
        stdout = _recall_payload("- Billing owned by team [bre/feature-billing]")
        result = agent.parse_recall_result(stdout, expected_model=_MODEL)
        assert result["text"] == "- Billing owned by team [bre/feature-billing]"
        assert result["ok"] is True

    def test_extracts_metrics_when_present(self) -> None:
        stdout = _recall_payload("- finding", metrics=True)
        result = agent.parse_recall_result(stdout, expected_model=_MODEL)
        assert result["duration_ms"] == 15200
        assert result["num_turns"] == 6
        assert result["cost_usd"] == 0.09

    def test_metrics_default_to_none_when_absent(self) -> None:
        stdout = _recall_payload("- finding")
        result = agent.parse_recall_result(stdout, expected_model=_MODEL)
        assert result["duration_ms"] is None
        assert result["num_turns"] is None
        assert result["cost_usd"] is None

    def test_rejects_unexpected_model(self) -> None:
        stdout = _recall_payload("- finding", model="global.anthropic.claude-opus-4-8-v1:0")
        with pytest.raises(RuntimeError, match="unexpected model"):
            agent.parse_recall_result(stdout, expected_model=_MODEL)

    def test_rejects_error_payload(self) -> None:
        stdout = _recall_payload("boom", is_error=True)
        with pytest.raises(RuntimeError, match="error"):
            agent.parse_recall_result(stdout, expected_model=_MODEL)

    def test_rejects_malformed_output(self) -> None:
        with pytest.raises(RuntimeError, match="malformed"):
            agent.parse_recall_result("not json", expected_model=_MODEL)

    def test_rejects_empty_result(self) -> None:
        with pytest.raises(RuntimeError, match="no findings"):
            agent.parse_recall_result(_recall_payload("   "), expected_model=_MODEL)


class TestRunPreflight:
    def test_noop_when_unconfigured(self) -> None:
        asyncio.run(agent.run_preflight(None))

    def test_passes_on_exit_zero(self) -> None:
        asyncio.run(agent.run_preflight("true"))

    def test_raises_generic_error_on_failure(self) -> None:
        with pytest.raises(RuntimeError, match="pre-flight check failed"):
            asyncio.run(agent.run_preflight("false"))

    def test_raises_on_timeout(self) -> None:
        with pytest.raises(RuntimeError, match="pre-flight check failed"):
            asyncio.run(agent.run_preflight("sleep 5", timeout=0.1))


class TestRecallTool:
    def test_returns_distilled_findings_end_to_end(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(agent.shutil, "which", lambda _: "/usr/bin/claude")
        monkeypatch.setattr(agent, "get_recall_model", lambda: _MODEL)
        monkeypatch.setattr(agent, "get_preflight_command", lambda: None)

        captured: dict[str, object] = {}

        async def fake_spawn(
            command: list[str],
            *,
            env: dict[str, str] | None = None,
            timeout: float = 0,
        ) -> str:
            captured["env"] = env
            return _recall_payload("- Owned by team [bre/x]")

        monkeypatch.setattr(agent, "_spawn_recall", fake_spawn)
        result = asyncio.run(agent.recall("who owns x"))
        assert result == "- Owned by team [bre/x]"
        spawn_env = captured["env"]
        assert isinstance(spawn_env, dict)
        assert "CLAUDE_CONFIG_DIR" in spawn_env

    def test_reports_missing_claude_cli(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(agent.shutil, "which", lambda _: None)
        result = asyncio.run(agent.recall("q"))
        assert "claude CLI not found" in result

    def test_preflight_failure_blocks_spawn(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(agent.shutil, "which", lambda _: "/usr/bin/claude")
        monkeypatch.setattr(agent, "get_preflight_command", lambda: "false")

        async def fail_if_called(
            command: list[str],
            *,
            env: dict[str, str] | None = None,
            timeout: float = 0,
        ) -> str:
            msg = "spawn must not run when pre-flight fails"
            raise AssertionError(msg)

        monkeypatch.setattr(agent, "_spawn_recall", fail_if_called)
        result = asyncio.run(agent.recall("q"))
        assert "pre-flight check failed" in result


class TestRunDreamPass:
    def test_returns_audit_summary_end_to_end(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(agent.shutil, "which", lambda _: "/usr/bin/claude")
        monkeypatch.setattr(agent, "get_dream_model", lambda: _MODEL)
        monkeypatch.setattr(agent, "get_preflight_command", lambda: None)

        captured: dict[str, object] = {}

        async def fake_spawn(
            command: list[str],
            *,
            env: dict[str, str] | None = None,
            timeout: float = 0,
        ) -> str:
            captured["command"] = command
            return _recall_payload("- demoted [mcp-memory/task/old]: superseded")

        monkeypatch.setattr(agent, "_spawn_recall", fake_spawn)
        result = asyncio.run(agent.run_dream_pass())
        assert result == "- demoted [mcp-memory/task/old]: superseded"
        command = captured["command"]
        assert isinstance(command, list)
        assert "mcp__memory__vote_entity" not in command

    def test_reports_missing_claude_cli(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(agent.shutil, "which", lambda _: None)
        result = asyncio.run(agent.run_dream_pass())
        assert "claude CLI not found" in result

    def test_preflight_failure_blocks_spawn(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(agent.shutil, "which", lambda _: "/usr/bin/claude")
        monkeypatch.setattr(agent, "get_preflight_command", lambda: "false")

        async def fail_if_called(
            command: list[str],
            *,
            env: dict[str, str] | None = None,
            timeout: float = 0,
        ) -> str:
            msg = "spawn must not run when pre-flight fails"
            raise AssertionError(msg)

        monkeypatch.setattr(agent, "_spawn_recall", fail_if_called)
        result = asyncio.run(agent.run_dream_pass())
        assert "pre-flight check failed" in result

    def test_rejects_unexpected_model(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(agent.shutil, "which", lambda _: "/usr/bin/claude")
        monkeypatch.setattr(agent, "get_dream_model", lambda: _MODEL)
        monkeypatch.setattr(agent, "get_preflight_command", lambda: None)

        async def fake_spawn(
            command: list[str],
            *,
            env: dict[str, str] | None = None,
            timeout: float = 0,
        ) -> str:
            return _recall_payload("- x", model="global.anthropic.claude-opus-4-8-v1:0")

        monkeypatch.setattr(agent, "_spawn_recall", fake_spawn)
        result = asyncio.run(agent.run_dream_pass())
        assert "unexpected model" in result


class TestRunHeavyDreamPass:
    def test_returns_audit_summary_end_to_end(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(agent.shutil, "which", lambda _: "/usr/bin/claude")
        monkeypatch.setattr(agent, "get_dream_heavy_model", lambda: _MODEL)
        monkeypatch.setattr(agent, "get_preflight_command", lambda: None)

        captured: dict[str, object] = {}

        async def fake_spawn(
            command: list[str],
            *,
            env: dict[str, str] | None = None,
            timeout: float = 0,
        ) -> str:
            captured["command"] = command
            return _recall_payload("[mcp-memory/task/old] - merged into task/new")

        monkeypatch.setattr(agent, "_spawn_recall", fake_spawn)
        result = asyncio.run(agent.run_heavy_dream_pass())
        assert result == "[mcp-memory/task/old] - merged into task/new"
        command = captured["command"]
        assert isinstance(command, list)
        assert "mcp__memory__merge_entities" not in command
        assert "mcp__memory__vote_entity" not in command

    def test_reports_missing_claude_cli(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(agent.shutil, "which", lambda _: None)
        result = asyncio.run(agent.run_heavy_dream_pass())
        assert "claude CLI not found" in result

    def test_preflight_failure_blocks_spawn(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(agent.shutil, "which", lambda _: "/usr/bin/claude")
        monkeypatch.setattr(agent, "get_preflight_command", lambda: "false")

        async def fail_if_called(
            command: list[str],
            *,
            env: dict[str, str] | None = None,
            timeout: float = 0,
        ) -> str:
            msg = "spawn must not run when pre-flight fails"
            raise AssertionError(msg)

        monkeypatch.setattr(agent, "_spawn_recall", fail_if_called)
        result = asyncio.run(agent.run_heavy_dream_pass())
        assert "pre-flight check failed" in result

    def test_rejects_unexpected_model(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(agent.shutil, "which", lambda _: "/usr/bin/claude")
        monkeypatch.setattr(agent, "get_dream_heavy_model", lambda: _MODEL)
        monkeypatch.setattr(agent, "get_preflight_command", lambda: None)

        async def fake_spawn(
            command: list[str],
            *,
            env: dict[str, str] | None = None,
            timeout: float = 0,
        ) -> str:
            return _recall_payload(
                "[p/task/x] - merged", model="global.anthropic.claude-opus-4-8-v1:0"
            )

        monkeypatch.setattr(agent, "_spawn_recall", fake_spawn)
        result = asyncio.run(agent.run_heavy_dream_pass())
        assert "unexpected model" in result


class TestRecallRecording:
    @staticmethod
    def _wire_spawn(monkeypatch: pytest.MonkeyPatch, payload: str) -> None:
        monkeypatch.setattr(agent.shutil, "which", lambda _: "/usr/bin/claude")
        monkeypatch.setattr(agent, "get_recall_model", lambda: _MODEL)
        monkeypatch.setattr(agent, "get_preflight_command", lambda: None)

        async def fake_spawn(
            command: list[str],
            *,
            env: dict[str, str] | None = None,
            timeout: float = 0,
        ) -> str:
            return payload

        monkeypatch.setattr(agent, "_spawn_recall", fake_spawn)

    def test_successful_recall_is_recorded_with_metrics(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._wire_spawn(monkeypatch, _recall_payload("- Owned by team [bre/x]", metrics=True))
        asyncio.run(agent.recall("who owns billing"))
        status = recall_status.read_status()
        assert status is not None
        assert status["active"] == 0
        assert len(status["recent"]) == 1
        record = status["recent"][0]
        assert record["query"] == "who owns billing"
        assert record["ok"] is True
        assert record["duration_ms"] == 15200
        assert record["num_turns"] == 6
        assert record["cost_usd"] == 0.09

    def test_recall_is_in_flight_during_spawn(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(agent.shutil, "which", lambda _: "/usr/bin/claude")
        monkeypatch.setattr(agent, "get_recall_model", lambda: _MODEL)
        monkeypatch.setattr(agent, "get_preflight_command", lambda: None)
        seen: list[int] = []

        async def fake_spawn(
            command: list[str],
            *,
            env: dict[str, str] | None = None,
            timeout: float = 0,
        ) -> str:
            status = recall_status.read_status()
            seen.append(status["active"] if status else -1)
            return _recall_payload("- x")

        monkeypatch.setattr(agent, "_spawn_recall", fake_spawn)
        asyncio.run(agent.recall("q"))
        assert seen == [1]  # active while the spawn ran
        after = recall_status.read_status()
        assert after is not None
        assert after["active"] == 0

    def test_failed_recall_is_recorded_ok_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(agent.shutil, "which", lambda _: None)
        asyncio.run(agent.recall("q"))
        status = recall_status.read_status()
        assert status is not None
        assert status["active"] == 0
        assert status["recent"][-1]["ok"] is False

    def test_dream_pass_records_no_recall_history(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(agent.shutil, "which", lambda _: "/usr/bin/claude")
        monkeypatch.setattr(agent, "get_dream_model", lambda: _MODEL)
        monkeypatch.setattr(agent, "get_preflight_command", lambda: None)

        async def fake_spawn(
            command: list[str],
            *,
            env: dict[str, str] | None = None,
            timeout: float = 0,
        ) -> str:
            return _recall_payload("[p/task/old] - stale")

        monkeypatch.setattr(agent, "_spawn_recall", fake_spawn)
        asyncio.run(agent.run_dream_pass())
        assert recall_status.read_status() is None
