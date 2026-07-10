"""Tests for the memory-agent recall server."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from mcp_memory import agent, cli

_MODEL = "global.anthropic.claude-haiku-4-5-20251001-v1:0"


def _acoro(value: object) -> object:
    """Return an async no-arg callable that resolves to value (for monkeypatching)."""

    async def _call() -> object:
        return value

    return _call


def _recall_payload(result: str, *, model: str = _MODEL, is_error: bool = False) -> str:
    return json.dumps(
        {
            "type": "result",
            "is_error": is_error,
            "result": result,
            "modelUsage": {model: {"inputTokens": 100, "outputTokens": 50}},
        }
    )


class TestBuildMcpConfig:
    def test_points_only_at_memory_server(self) -> None:
        config = agent.build_mcp_config("http://localhost:3000/mcp")
        assert config == {
            "mcpServers": {"memory": {"type": "http", "url": "http://localhost:3000/mcp"}}
        }


class TestBuildRecallCommand:
    def test_denies_every_mutating_and_builtin_tool(self) -> None:
        command = agent.build_recall_command(
            "who owns X",
            claude_bin="/usr/bin/claude",
            model=_MODEL,
            mcp_config_path="/tmp/cfg.json",
        )
        deny_index = command.index("--disallowedTools")
        denied = set(command[deny_index + 1 :])
        assert "mcp__memory__create_entities" in denied
        assert "mcp__memory__vote_entity" in denied
        assert "Bash" in denied

    def test_allows_memory_read_tools_by_not_denying_them(self) -> None:
        command = agent.build_recall_command(
            "q", claude_bin="claude", model=_MODEL, mcp_config_path="/tmp/cfg.json"
        )
        assert "mcp__memory__search_nodes" not in command
        assert "mcp__memory__get_entity_with_relations" not in command

    def test_denies_filesystem_and_web_read_builtins(self) -> None:
        command = agent.build_recall_command(
            "q", claude_bin="claude", model=_MODEL, mcp_config_path="/tmp/cfg.json"
        )
        deny_index = command.index("--disallowedTools")
        denied = set(command[deny_index + 1 :])
        for tool in ("Read", "Grep", "Glob", "WebFetch", "WebSearch"):
            assert tool in denied

    def test_isolates_and_pins_the_spawn(self) -> None:
        command = agent.build_recall_command(
            "q", claude_bin="claude", model=_MODEL, mcp_config_path="/tmp/cfg.json"
        )
        assert "--strict-mcp-config" in command
        assert command[command.index("--model") + 1] == _MODEL
        assert command[command.index("--mcp-config") + 1] == "/tmp/cfg.json"

    def test_embeds_query_and_slug_ritual_in_prompt(self) -> None:
        command = agent.build_recall_command(
            "who owns billing", claude_bin="claude", model=_MODEL, mcp_config_path="/c.json"
        )
        prompt = command[command.index("-p") + 1]
        assert "who owns billing" in prompt
        assert "[project/entity-name]" in prompt

    def test_prompt_steers_for_specific_facts(self) -> None:
        command = agent.build_recall_command(
            "q", claude_bin="claude", model=_MODEL, mcp_config_path="/c.json"
        )
        prompt = command[command.index("-p") + 1].lower()
        assert "still connecting" in prompt
        assert "[project/entity-name]" in prompt
        assert "all of its observations" in prompt
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


class TestDreamTick:
    @staticmethod
    def _wire(
        monkeypatch: pytest.MonkeyPatch,
        *,
        idle: float | None,
        now: float,
        passes: list[str],
        outcome: str | Exception = "audit",
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


class TestIdleWatchLoop:
    def test_cancels_cleanly(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(agent, "get_dream_poll_seconds", lambda: 0.0)
        monkeypatch.setattr(agent, "get_dream_idle_seconds", lambda: 7200.0)
        ticks: list[float] = []

        async def fake_tick(last_pass: float) -> float:
            ticks.append(last_pass)
            return last_pass

        monkeypatch.setattr(agent, "_dream_tick", fake_tick)

        async def drive() -> None:
            task = asyncio.create_task(agent._idle_watch_loop())
            await asyncio.sleep(0.05)
            task.cancel()
            await task

        with pytest.raises(asyncio.CancelledError):
            asyncio.run(drive())
        assert ticks  # the loop ran at least one tick before cancellation


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
        assert result == "- Billing owned by team [bre/feature-billing]"

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
