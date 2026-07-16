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
    from pathlib import Path

_MODEL = "global.anthropic.claude-haiku-4-5-20251001-v1:0"


@pytest.fixture(autouse=True)
def _isolate_markers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate the status markers on disk so recording never touches the real data dir."""
    monkeypatch.setenv("MCP_MEMORY_DB_PATH", str(tmp_path / "memory.db"))
    dream_status.clear()
    recall_status.clear()


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
        assert "mcp__memory__restore_entity" in denied
        assert "mcp__memory__merge_entities" in denied
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


class TestTierTick:
    @staticmethod
    def _spec(
        monkeypatch: pytest.MonkeyPatch,
        *,
        idle: float | None,
        now: float,
        idle_gate: float,
        interval_gate: float,
        passes: list[str],
        recorded: list[tuple[str, bool, str]],
    ) -> agent.TierSpec:
        monkeypatch.setattr(agent, "fetch_idle_seconds", _acoro(idle))
        monkeypatch.setattr(agent.time, "monotonic", lambda: now)

        async def fake_pass() -> str:
            passes.append("ran")
            return "[p/task/x] - merged into task/y"

        def fake_record(audit_text: str, *, ok: bool, tier: str = "light") -> None:
            recorded.append((audit_text, ok, tier))

        monkeypatch.setattr(agent.dream_status, "record_pass", fake_record)
        return agent.TierSpec(
            name="heavy",
            enabled_getter=lambda: True,
            idle_getter=lambda: idle_gate,
            interval_getter=lambda: interval_gate,
            poll_getter=lambda: 1.0,
            run_pass=fake_pass,
        )

    def test_runs_pass_and_tags_tier_when_both_gates_pass(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        passes: list[str] = []
        recorded: list[tuple[str, bool, str]] = []
        spec = self._spec(
            monkeypatch,
            idle=8000.0,
            now=100_000.0,
            idle_gate=7200.0,
            interval_gate=86400.0,
            passes=passes,
            recorded=recorded,
        )
        new_last_pass = asyncio.run(agent._tier_tick(0.0, spec))
        assert passes == ["ran"]
        assert new_last_pass == 100_000.0
        assert recorded == [("[p/task/x] - merged into task/y", True, "heavy")]

    def test_skips_when_idle_gate_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        passes: list[str] = []
        recorded: list[tuple[str, bool, str]] = []
        spec = self._spec(
            monkeypatch,
            idle=100.0,
            now=100_000.0,
            idle_gate=7200.0,
            interval_gate=1.0,
            passes=passes,
            recorded=recorded,
        )
        assert asyncio.run(agent._tier_tick(42.0, spec)) == 42.0
        assert passes == []

    def test_skips_when_interval_gate_fails_though_idle(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Idle window is wide open, but not enough time has passed since this
        # tier's own last pass - the interval gate holds it off.
        passes: list[str] = []
        recorded: list[tuple[str, bool, str]] = []
        spec = self._spec(
            monkeypatch,
            idle=999_999.0,
            now=100_000.0,
            idle_gate=7200.0,
            interval_gate=86400.0,
            passes=passes,
            recorded=recorded,
        )
        assert asyncio.run(agent._tier_tick(99_000.0, spec)) == 99_000.0
        assert passes == []


class TestDreamTick:
    @staticmethod
    def _wire(
        monkeypatch: pytest.MonkeyPatch,
        *,
        idle: float | None,
        now: float,
        passes: list[str],
        outcome: str | Exception = "audit",
        recorded: list[tuple[str, bool, str]] | None = None,
    ) -> None:
        monkeypatch.setattr(agent, "get_dream_idle_seconds", lambda: 7200.0)
        monkeypatch.setattr(agent, "fetch_idle_seconds", _acoro(idle))
        monkeypatch.setattr(agent.time, "monotonic", lambda: now)

        async def fake_pass() -> str:
            passes.append("ran")
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        monkeypatch.setattr(agent, "run_dream_pass", fake_pass)

        def fake_record(audit_text: str, *, ok: bool, tier: str = "light") -> None:
            if recorded is not None:
                recorded.append((audit_text, ok, tier))

        monkeypatch.setattr(agent.dream_status, "record_pass", fake_record)

    def test_runs_pass_when_idle_exceeds_threshold(self, monkeypatch: pytest.MonkeyPatch) -> None:
        passes: list[str] = []
        self._wire(monkeypatch, idle=8000.0, now=100_000.0, passes=passes)
        new_last_pass = asyncio.run(agent._dream_tick(0.0))
        assert passes == ["ran"]
        assert new_last_pass == 100_000.0

    def test_skips_when_below_threshold(self, monkeypatch: pytest.MonkeyPatch) -> None:
        passes: list[str] = []
        self._wire(monkeypatch, idle=100.0, now=100_000.0, passes=passes)
        new_last_pass = asyncio.run(agent._dream_tick(42.0))
        assert passes == []
        assert new_last_pass == 42.0

    def test_skips_when_idle_unavailable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        passes: list[str] = []
        self._wire(monkeypatch, idle=None, now=100_000.0, passes=passes)
        assert asyncio.run(agent._dream_tick(42.0)) == 42.0
        assert passes == []

    def test_anti_spin_holds_off_a_second_pass_too_soon(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        passes: list[str] = []
        self._wire(monkeypatch, idle=8000.0, now=100_000.0, passes=passes)
        after_first = asyncio.run(agent._dream_tick(0.0))
        assert passes == ["ran"]
        second = asyncio.run(agent._dream_tick(after_first))
        assert passes == ["ran"]
        assert second == after_first

    def test_survives_a_failing_pass(self, monkeypatch: pytest.MonkeyPatch) -> None:
        passes: list[str] = []
        self._wire(
            monkeypatch,
            idle=8000.0,
            now=100_000.0,
            passes=passes,
            outcome=RuntimeError("boom"),
        )
        new_last_pass = asyncio.run(agent._dream_tick(0.0))
        assert passes == ["ran"]
        assert new_last_pass == 100_000.0

    def test_records_a_successful_pass(self, monkeypatch: pytest.MonkeyPatch) -> None:
        recorded: list[tuple[str, bool, str]] = []
        self._wire(
            monkeypatch,
            idle=8000.0,
            now=100_000.0,
            passes=[],
            outcome="[p/task/x] - stale",
            recorded=recorded,
        )
        asyncio.run(agent._dream_tick(0.0))
        assert recorded == [("[p/task/x] - stale", True, "light")]

    def test_records_a_handled_failure_as_not_ok(self, monkeypatch: pytest.MonkeyPatch) -> None:
        recorded: list[tuple[str, bool, str]] = []
        self._wire(
            monkeypatch,
            idle=8000.0,
            now=100_000.0,
            passes=[],
            outcome="recall unavailable: claude CLI not found",
            recorded=recorded,
        )
        asyncio.run(agent._dream_tick(0.0))
        assert recorded == [("recall unavailable: claude CLI not found", False, "light")]

    def test_records_a_raising_pass_as_not_ok(self, monkeypatch: pytest.MonkeyPatch) -> None:
        recorded: list[tuple[str, bool, str]] = []
        self._wire(
            monkeypatch,
            idle=8000.0,
            now=100_000.0,
            passes=[],
            outcome=RuntimeError("boom"),
            recorded=recorded,
        )
        asyncio.run(agent._dream_tick(0.0))
        assert len(recorded) == 1
        assert recorded[0][1] is False


class TestIdleWatchLoop:
    def test_cancels_cleanly(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(agent, "get_dream_poll_seconds", lambda: 0.0)
        monkeypatch.setattr(agent, "get_dream_idle_seconds", lambda: 7200.0)
        monkeypatch.setattr(agent, "get_dream_enabled", lambda: True)
        starts: list[bool] = []
        monkeypatch.setattr(
            agent.dream_status,
            "record_startup",
            lambda **_kwargs: starts.append(True),
        )
        ticks: list[agent.TierSpec] = []

        async def fake_tick(last_pass: float, spec: agent.TierSpec) -> float:
            ticks.append(spec)
            return last_pass

        monkeypatch.setattr(agent, "_tier_tick", fake_tick)

        async def drive() -> None:
            task = asyncio.create_task(agent._idle_watch_loop())
            await asyncio.sleep(0.05)
            task.cancel()
            await task

        with pytest.raises(asyncio.CancelledError):
            asyncio.run(drive())
        assert ticks  # the loop ran at least one tick before cancellation
        assert ticks[0].name == "light"  # the light tier drives this loop
        assert starts == [True]  # config snapshotted once before the loop

    def test_heavy_loop_records_its_own_startup(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(agent, "get_dream_heavy_poll_seconds", lambda: 3600.0)
        monkeypatch.setattr(agent, "get_dream_heavy_idle_seconds", lambda: 7200.0)
        monkeypatch.setattr(agent, "get_dream_heavy_interval_seconds", lambda: 86400.0)
        monkeypatch.setattr(agent, "get_dream_heavy_enabled", lambda: True)
        recorded: list[dict[str, object]] = []
        monkeypatch.setattr(
            agent.dream_status, "record_startup", lambda **kwargs: recorded.append(kwargs)
        )

        async def fake_tick(last_pass: float, spec: agent.TierSpec) -> float:
            return last_pass

        monkeypatch.setattr(agent, "_tier_tick", fake_tick)

        async def drive() -> None:
            task = asyncio.create_task(agent._heavy_watch_loop())
            await asyncio.sleep(0.05)
            task.cancel()
            await task

        with pytest.raises(asyncio.CancelledError):
            asyncio.run(drive())
        assert recorded == [
            {
                "tier": "heavy",
                "enabled": True,
                "idle_threshold_seconds": 7200.0,
                "interval_seconds": 86400.0,
                "poll_seconds": 3600.0,
            }
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
    def test_starts_watcher_when_dream_enabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(agent, "get_dream_enabled", lambda: True)
        monkeypatch.setattr(agent.shutil, "which", lambda _: "/usr/bin/claude")
        started: list[str] = []

        async def fake_loop() -> None:
            started.append("watching")
            await asyncio.sleep(3600)

        async def fake_serve_http() -> None:
            await asyncio.sleep(0.02)

        monkeypatch.setattr(agent, "_idle_watch_loop", fake_loop)
        monkeypatch.setattr(agent.mcp, "run_streamable_http_async", fake_serve_http)
        asyncio.run(agent._serve())
        assert started == ["watching"]

    def test_no_watcher_when_dream_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(agent, "get_dream_enabled", lambda: False)
        started: list[str] = []

        async def fake_loop() -> None:
            started.append("watching")

        async def fake_serve_http() -> None:
            await asyncio.sleep(0.02)

        monkeypatch.setattr(agent, "_idle_watch_loop", fake_loop)
        monkeypatch.setattr(agent.mcp, "run_streamable_http_async", fake_serve_http)
        asyncio.run(agent._serve())
        assert started == []

    def test_no_watcher_when_claude_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(agent, "get_dream_enabled", lambda: True)
        monkeypatch.setattr(agent.shutil, "which", lambda _: None)
        started: list[str] = []

        async def fake_loop() -> None:
            started.append("watching")

        async def fake_serve_http() -> None:
            await asyncio.sleep(0.02)

        monkeypatch.setattr(agent, "_idle_watch_loop", fake_loop)
        monkeypatch.setattr(agent.mcp, "run_streamable_http_async", fake_serve_http)
        asyncio.run(agent._serve())
        assert started == []

    def test_resets_recall_active_at_boot(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(agent, "get_dream_enabled", lambda: False)
        recall_status.record_start()  # a stale in-flight count a crash could leave

        async def fake_serve_http() -> None:
            await asyncio.sleep(0.01)

        monkeypatch.setattr(agent.mcp, "run_streamable_http_async", fake_serve_http)
        asyncio.run(agent._serve())
        status = recall_status.read_status()
        assert status is not None
        assert status["active"] == 0

    @staticmethod
    def _wire_watchers(monkeypatch: pytest.MonkeyPatch, started: list[str]) -> None:
        async def fake_light() -> None:
            started.append("light")
            await asyncio.sleep(3600)

        async def fake_heavy() -> None:
            started.append("heavy")
            await asyncio.sleep(3600)

        async def fake_serve_http() -> None:
            await asyncio.sleep(0.02)

        monkeypatch.setattr(agent, "_idle_watch_loop", fake_light)
        monkeypatch.setattr(agent, "_heavy_watch_loop", fake_heavy)
        monkeypatch.setattr(agent.mcp, "run_streamable_http_async", fake_serve_http)

    def test_starts_heavy_watcher_when_enabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(agent, "get_dream_enabled", lambda: False)
        monkeypatch.setattr(agent, "get_dream_heavy_enabled", lambda: True)
        monkeypatch.setattr(agent.shutil, "which", lambda _: "/usr/bin/claude")
        started: list[str] = []
        self._wire_watchers(monkeypatch, started)
        asyncio.run(agent._serve())
        assert started == ["heavy"]

    def test_no_heavy_watcher_when_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(agent, "get_dream_enabled", lambda: False)
        monkeypatch.setattr(agent, "get_dream_heavy_enabled", lambda: False)
        monkeypatch.setattr(agent.shutil, "which", lambda _: "/usr/bin/claude")
        started: list[str] = []
        self._wire_watchers(monkeypatch, started)
        asyncio.run(agent._serve())
        assert started == []

    def test_no_heavy_watcher_when_claude_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(agent, "get_dream_enabled", lambda: False)
        monkeypatch.setattr(agent, "get_dream_heavy_enabled", lambda: True)
        monkeypatch.setattr(agent.shutil, "which", lambda _: None)
        started: list[str] = []
        self._wire_watchers(monkeypatch, started)
        asyncio.run(agent._serve())
        assert started == []

    def test_runs_both_watchers_when_both_enabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(agent, "get_dream_enabled", lambda: True)
        monkeypatch.setattr(agent, "get_dream_heavy_enabled", lambda: True)
        monkeypatch.setattr(agent.shutil, "which", lambda _: "/usr/bin/claude")
        started: list[str] = []
        self._wire_watchers(monkeypatch, started)
        asyncio.run(agent._serve())
        assert sorted(started) == ["heavy", "light"]


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
