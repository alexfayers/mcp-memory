"""FastMCP server exposing an LLM recall layer in front of mcp-memory.

`recall` spawns an ephemeral headless ``claude -p`` agent locked to the
mcp-memory read tools, absorbs the heavy graph traversal in that throwaway
context, and returns distilled findings so the caller's own context stays clean.
The data store (mcp-memory) is untouched by this server.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
import re
import shutil
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING

import anyio
import httpx
from mcp.server.fastmcp import FastMCP

from .cli import _agent_spec, _setup_service_from_spec
from .config import (
    get_agent_port,
    get_dream_enabled,
    get_dream_idle_seconds,
    get_dream_max_votes,
    get_dream_model,
    get_dream_poll_seconds,
    get_dream_timeout,
    get_memory_url,
    get_preflight_command,
    get_recall_model,
)

logger = logging.getLogger("memory-agent")

if TYPE_CHECKING:
    from collections.abc import Callable

# Mutating mcp-memory tools plus vote_entity: recall is strictly read-only, so
# every one of these is denied. Deny rules override the inherited
# permissions.allow wildcard, so this is the hard safety guarantee (an
# allow-list cannot subtract from an inherited allow).
_MUTATING_MEMORY_TOOLS = (
    "create_entities",
    "create_relations",
    "delete_entity",
    "delete_relation",
    "delete_project",
    "add_observations",
    "delete_observations",
    "set_entity_status",
    "set_project_paths",
    "move_project_entities",
    "vote_entity",
)
# --strict-mcp-config isolates MCP servers but not built-ins. Deny the write/exec
# ones so the agent cannot touch the filesystem or spawn processes, and the
# read/web ones (Read/Grep/Glob/WebFetch/WebSearch) so it is forced onto the
# mcp__memory__* graph tools - otherwise it answers from files on disk and cites
# file paths instead of the [project/entity] slugs the recall contract requires.
_DISALLOWED_BUILTINS = (
    "Bash",
    "Write",
    "Edit",
    "NotebookEdit",
    "Agent",
    "Read",
    "Grep",
    "Glob",
    "WebFetch",
    "WebSearch",
)

DISALLOWED_TOOLS: tuple[str, ...] = (
    *(f"mcp__memory__{tool}" for tool in _MUTATING_MEMORY_TOOLS),
    *_DISALLOWED_BUILTINS,
)

# The dream curation pass may vote (its sole mutation), so vote_entity is the one
# memory tool it is NOT denied; every other mutation and every built-in stays
# denied, keeping grooming to demote-never-delete.
DREAM_DISALLOWED_TOOLS: tuple[str, ...] = (
    *(f"mcp__memory__{tool}" for tool in _MUTATING_MEMORY_TOOLS if tool != "vote_entity"),
    *_DISALLOWED_BUILTINS,
)

_RECALL_TIMEOUT_SECONDS = int(os.environ.get("MCP_RECALL_TIMEOUT", "180"))
_PREFLIGHT_TIMEOUT_SECONDS = 30
_IDLE_FETCH_TIMEOUT_SECONDS = 5.0
# Startup wait for the spawned agent's mcp-memory HTTP connection (ms). Without
# it, claude begins its first turn mid-handshake and the model reports the graph
# unreachable.
_MCP_STARTUP_TIMEOUT_MS = "30000"
# The CLI defers MCP tools behind a ToolSearch discovery step by default. A small
# recall model then wastes turns "loading schemas" and can wrongly report the
# graph empty, so force every mcp__memory__* tool to load upfront. CLI-internal
# env var (no public flag); an unrecognised value is ignored, reverting to default.
_ENABLE_TOOL_SEARCH = "false"

# The spawned agent runs against an isolated CLAUDE_CONFIG_DIR so it never reads
# the user's ~/.claude hooks or SessionStart skills - those inject reminders that
# derail the small recall model into thinking the graph is unreachable. These
# minimal settings pre-approve the memory tools (the deny-list still subtracts the
# mutating ones) with no hooks of any kind.
AGENT_SETTINGS: dict[str, object] = {"permissions": {"allow": ["mcp__memory__*"]}}

RECALL_RITUAL = (
    "You are a memory recall agent with read-only access to a knowledge graph "
    "via the mcp__memory__* tools. "
    "The mcp__memory server may report as 'still connecting' on your very first "
    "turn; ignore that and CALL a memory tool anyway - the connection completes "
    "before the call runs. NEVER conclude the tools are unavailable without "
    "actually attempting at least one search_nodes or search_all_projects call. "
    "Search from several angles - vary your search terms and do not stop at the "
    "first entity you find. Start with search_nodes / search_all_projects, then "
    "traverse promising hits with get_entity_with_relations / search_related_nodes "
    "to gather linked context. "
    "When an entity is relevant, read through ALL of its observations, not just "
    "the first few, and pull out the SPECIFIC facts that answer the query - "
    "concrete numbers, dates, names, and decisions - rather than only the "
    "high-level theme. If two observations conflict, prefer the newer one, and do "
    "not drop a detail that bears on the query just because the entity is long. "
    "Return a compact bulleted list of the findings that answer the query - "
    "answer it completely, but stay on topic and do not dump entire entities. "
    "Tag every claim inline with its source entity slug as [project/entity-name], "
    "copied VERBATIM from the tool results - never paraphrase or invent a slug. "
    "If nothing relevant exists, say so plainly. Do not attempt to write, vote, "
    "or otherwise mutate anything."
)

DREAM_RITUAL = (
    "You are a memory curation agent grooming a knowledge graph while it is idle, "
    "with access to the mcp__memory__* tools. "
    "The mcp__memory server may report as 'still connecting' on your very first "
    "turn; ignore that and CALL a memory tool anyway - the connection completes "
    "before the call runs. "
    "Your ONLY permitted mutation is vote_entity with a vote of -1, to DEMOTE "
    "entities in ranking. You must NEVER delete anything and NEVER cast a positive "
    "vote. Deletion and promotion are not your job. "
    "Search broadly ACROSS ALL PROJECTS (search_all_projects, then search_nodes "
    "and get_entity_with_relations / search_related_nodes to confirm) for entities "
    "that are stale, superseded, or duplicated by a better entity. Before voting, "
    "inspect each candidate's current vote_score: do NOT downvote an entity that is "
    "already strongly negative (a score at or below -10), because the ranking "
    "penalty has already saturated and further votes are wasted. "
    "Demote at most {max_votes} entities this pass - be conservative and prefer "
    "clear noise over borderline calls. "
    "When you vote, pass the entity's exact project and name slug VERBATIM from the "
    "tool results. "
    "Finish with a terse audit summary: one line per demoted entity as "
    "[project/entity-name] and a few words of why, or 'nothing demoted' if you "
    "found no clear candidates."
)

RECALL_DESC = (
    "Delegate a HEAVY, multi-step memory recall to a throwaway agent and get back "
    "distilled findings, each tagged with its source [project/entity] slug so you "
    "can traverse or vote without re-searching. The agent reads the graph in its "
    "own context, keeping yours clean. Each call spawns a billed model invocation "
    "(~15-30s), so reserve it for broad questions that need several searches and "
    "graph traversals - a single lookup you could do with one search_nodes call "
    "must NOT go through recall."
)

mcp = FastMCP(
    "memory-agent",
    stateless_http=True,
    json_response=True,
    port=get_agent_port(),
)


def _claude_bin() -> str | None:
    """Return the path to the claude CLI, or None if it is not on PATH."""
    return shutil.which("claude")


def build_mcp_config(memory_url: str) -> dict[str, object]:
    """Build the --mcp-config payload pointing the spawned agent at mcp-memory only."""
    return {"mcpServers": {"memory": {"type": "http", "url": memory_url}}}


def build_recall_command(
    query: str,
    *,
    claude_bin: str,
    model: str,
    mcp_config_path: str,
) -> list[str]:
    """Build the headless ``claude -p`` argv for a read-only recall spawn."""
    prompt = f"{RECALL_RITUAL}\n\nQuery: {query}"
    return [
        claude_bin,
        "-p",
        prompt,
        "--model",
        model,
        "--mcp-config",
        mcp_config_path,
        "--strict-mcp-config",
        "--disallowedTools",
        *DISALLOWED_TOOLS,
        "--output-format",
        "json",
    ]


def build_dream_command(
    *,
    claude_bin: str,
    model: str,
    mcp_config_path: str,
    max_votes: int,
) -> list[str]:
    """Build the headless ``claude -p`` argv for a downvote-only dream curation spawn."""
    return [
        claude_bin,
        "-p",
        DREAM_RITUAL.format(max_votes=max_votes),
        "--model",
        model,
        "--mcp-config",
        mcp_config_path,
        "--strict-mcp-config",
        "--disallowedTools",
        *DREAM_DISALLOWED_TOOLS,
        "--output-format",
        "json",
    ]


def build_spawn_env(config_dir: str, *, base_env: dict[str, str] | None = None) -> dict[str, str]:
    """Return the subprocess env for a recall or dream spawn.

    Inherits the parent environment so AWS/Bedrock credentials still resolve, and
    overrides three keys: CLAUDE_CONFIG_DIR isolates the spawn from the user's
    hooks; MCP_TIMEOUT blocks the first turn until the mcp-memory connection is
    established (avoiding a spurious "graph unreachable"); and ENABLE_TOOL_SEARCH
    loads the memory tools upfront instead of behind a discovery step.
    """
    env = dict(os.environ if base_env is None else base_env)
    env["CLAUDE_CONFIG_DIR"] = config_dir
    env["MCP_TIMEOUT"] = _MCP_STARTUP_TIMEOUT_MS
    env["ENABLE_TOOL_SEARCH"] = _ENABLE_TOOL_SEARCH
    return env


def _model_token(model_id: str) -> str:
    """Reduce a model id to a stable family token (e.g. claude-haiku-4-5)."""
    match = re.search(r"claude-[a-z]+-\d+-\d+", model_id.lower())
    return match.group(0) if match else model_id.lower()


def _model_matches(model_id: str, usage_keys: list[str]) -> bool:
    """Return True if any reported model-usage key is the expected model family."""
    token = _model_token(model_id)
    return any(token in key.lower() for key in usage_keys)


def parse_recall_result(stdout: str, *, expected_model: str) -> str:
    """Extract the distilled result from a ``claude -p --output-format json`` payload.

    Raises:
        RuntimeError: if the payload is malformed, reports an error, or the model
            that actually ran is not the expected one (guards a silent Opus remap).
    """
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("recall agent returned malformed output") from exc

    if not isinstance(payload, dict) or payload.get("is_error"):
        raise RuntimeError("recall agent reported an error")

    result = payload.get("result")
    if not isinstance(result, str) or not result.strip():
        raise RuntimeError("recall agent returned no findings")

    usage = payload.get("modelUsage")
    usage_keys = list(usage) if isinstance(usage, dict) else []
    if not _model_matches(expected_model, usage_keys):
        raise RuntimeError("recall ran on an unexpected model")

    return result


async def run_preflight(
    command: str | None,
    *,
    timeout: float = _PREFLIGHT_TIMEOUT_SECONDS,
) -> None:
    """Run the optional pre-flight readiness check before a spawn.

    Raises:
        RuntimeError: if the command exits non-zero or times out. The message is
            generic; callers must not leak the command or its output.
    """
    if not command:
        return
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        returncode = await asyncio.wait_for(proc.wait(), timeout=timeout)
    except (TimeoutError, OSError) as exc:
        raise RuntimeError("recall unavailable: pre-flight check failed") from exc
    if returncode != 0:
        raise RuntimeError("recall unavailable: pre-flight check failed")


async def _spawn_recall(
    command: list[str],
    *,
    env: dict[str, str] | None = None,
    timeout: float = _RECALL_TIMEOUT_SECONDS,
) -> str:
    """Spawn the recall agent and return its stdout, killing it on timeout."""
    proc = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError as exc:
        proc.kill()
        await proc.wait()
        raise RuntimeError("recall timed out") from exc
    if proc.returncode != 0:
        raise RuntimeError("recall agent exited with an error")
    return stdout.decode()


async def _run_isolated_agent(
    build_command: Callable[[str, str], list[str]],
    *,
    model: str,
    timeout: float,
) -> str:
    """Spawn a hermetic headless agent and return its parsed result.

    Resolves the claude CLI, runs the pre-flight gate, then spawns in a throwaway
    tempdir holding the mcp-config and an isolated CLAUDE_CONFIG_DIR (no user
    hooks). ``build_command`` receives the resolved claude binary and mcp-config
    path. All failures surface as their plain message string; the tempdir is
    always cleaned up.
    """
    claude_bin = _claude_bin()
    if not claude_bin:
        return "recall unavailable: claude CLI not found"

    try:
        await run_preflight(get_preflight_command())
    except RuntimeError as exc:
        return str(exc)

    work_dir = tempfile.mkdtemp(prefix="memory-agent-")
    work_path = Path(work_dir)
    config_path = work_path / "mcp-config.json"
    isolated_config_dir = work_path / "config"
    try:
        config_path.write_text(json.dumps(build_mcp_config(get_memory_url())), encoding="utf-8")
        isolated_config_dir.mkdir()
        (isolated_config_dir / "settings.json").write_text(
            json.dumps(AGENT_SETTINGS), encoding="utf-8"
        )
        command = build_command(claude_bin, str(config_path))
        stdout = await _spawn_recall(
            command, env=build_spawn_env(str(isolated_config_dir)), timeout=timeout
        )
        return parse_recall_result(stdout, expected_model=model)
    except RuntimeError as exc:
        return str(exc)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


async def recall(query: str) -> str:
    """Run a heavy memory recall in a throwaway agent and return distilled findings."""
    model = get_recall_model()
    return await _run_isolated_agent(
        lambda claude_bin, config_path: build_recall_command(
            query, claude_bin=claude_bin, model=model, mcp_config_path=config_path
        ),
        model=model,
        timeout=_RECALL_TIMEOUT_SECONDS,
    )


def _register_recall(server: FastMCP) -> None:
    """Register the recall tool, but only when the claude CLI it depends on is present.

    Without claude every recall would fail at spawn time, so the tool is hidden from
    ``tools/list`` rather than advertised and then failing on every call.
    """
    if _claude_bin():
        server.add_tool(recall, description=RECALL_DESC)


_register_recall(mcp)


async def run_dream_pass() -> str:
    """Run one downvote-only curation pass in a throwaway agent, returning its audit."""
    model = get_dream_model()
    return await _run_isolated_agent(
        lambda claude_bin, config_path: build_dream_command(
            claude_bin=claude_bin,
            model=model,
            mcp_config_path=config_path,
            max_votes=get_dream_max_votes(),
        ),
        model=model,
        timeout=get_dream_timeout(),
    )


async def fetch_idle_seconds() -> float | None:
    """Return the mcp-memory server's idle seconds, or None if it cannot be reached.

    Queries the server's ``/api/idle`` route, which is mounted at the app root -
    not under the ``/mcp`` path that get_memory_url() returns - so the suffix is
    stripped. A plain GET does not count as memory activity, so polling here does
    not reset the idle timer.
    """
    url = get_memory_url().removesuffix("/mcp") + "/api/idle"
    try:
        async with httpx.AsyncClient(timeout=_IDLE_FETCH_TIMEOUT_SECONDS) as client:
            response = await client.get(url)
            response.raise_for_status()
            idle = response.json()["idle_seconds"]
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return None
    return float(idle) if isinstance(idle, (int, float)) else None


async def _dream_tick(last_pass: float) -> float:
    """Run a dream pass if memory has been idle long enough, returning the new last-pass time.

    Requires BOTH the reported memory idle window AND the time since the previous
    pass to exceed the threshold. The second guard prevents a degenerate pass that
    makes no tool calls (so it never resets the idle marker) from re-firing every
    poll. Returns ``last_pass`` unchanged when no pass runs.
    """
    threshold = get_dream_idle_seconds()
    idle = await fetch_idle_seconds()
    if idle is None or idle < threshold:
        return last_pass
    now = time.monotonic()
    if now - last_pass < threshold:
        return last_pass
    try:
        logger.info("dream: %s", await run_dream_pass())
    except Exception:
        logger.exception("dream pass failed")
    return now


async def _idle_watch_loop() -> None:
    """Poll for a memory-idle window and run a dream curation pass when one opens."""
    last_pass = 0.0
    while True:
        await asyncio.sleep(get_dream_poll_seconds())
        last_pass = await _dream_tick(last_pass)


async def _serve() -> None:
    """Serve over streamable HTTP, running the idle-watcher alongside if enabled.

    The watcher starts only when the dream is enabled AND the claude CLI it would
    spawn is present; otherwise it would poll forever with every pass no-opping. It
    is an explicit background task rather than a FastMCP ``lifespan``: in
    stateless_http mode the low-level server runs per request, so a lifespan task
    would be spawned and cancelled on every request. Cancelled cleanly on shutdown.
    """
    watcher = (
        asyncio.create_task(_idle_watch_loop()) if get_dream_enabled() and _claude_bin() else None
    )
    try:
        await mcp.run_streamable_http_async()
    finally:
        if watcher is not None:
            watcher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await watcher


def main(argv: list[str] | None = None) -> None:
    """Run the memory-agent server, or install it as a service.

    Bare invocation serves over streamable HTTP (with the idle-triggered dream
    curation pass when enabled); ``setup-service`` installs a persistent
    background service.
    """
    parser = argparse.ArgumentParser(prog="memory-agent", description="Memory recall agent server")
    parser.add_subparsers(dest="command").add_parser(
        "setup-service", help="Install as a persistent background service"
    )
    args = parser.parse_args(argv)

    if args.command == "setup-service":
        _setup_service_from_spec(_agent_spec(str(get_agent_port())))
    else:
        anyio.run(_serve)


if __name__ == "__main__":
    main()
