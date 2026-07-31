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
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict

import anyio
import httpx
from mcp.server.fastmcp import FastMCP
from starlette.responses import JSONResponse

from . import dream_status, recall_status
from .cli import _agent_spec, _setup_service_from_spec
from .config import (
    get_agent_port,
    get_dream_enabled,
    get_dream_heavy_enabled,
    get_dream_heavy_idle_seconds,
    get_dream_heavy_max_ops,
    get_dream_heavy_model,
    get_dream_heavy_poll_seconds,
    get_dream_heavy_timeout,
    get_dream_idle_seconds,
    get_dream_max_votes,
    get_dream_model,
    get_dream_poll_seconds,
    get_dream_timeout,
    get_memory_url,
    get_preflight_command,
    get_recall_max_turns,
    get_recall_model,
)
from .database import _GC_DOWNVOTE_FLOOR

__all__ = ["anyio", "dream_status", "shutil", "time"]

logger = logging.getLogger("memory-agent")

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from starlette.requests import Request


class SpawnResult(TypedDict):
    """The outcome of an isolated agent spawn: findings plus the reported metrics.

    ``ok`` distinguishes a real result from a handled failure (whose message is
    carried in ``text``). Metrics come from the ``claude -p`` JSON payload and are
    None when the spawn failed or the payload omitted them.
    """

    text: str
    ok: bool
    duration_ms: int | None
    num_turns: int | None
    cost_usd: float | None


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
    "merge_entities",
    "merge_observations",
    "restore_entity",
    "vote_entity",
    "vote_observation",
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

# The dream curation pass may vote entities and observations (its only mutations),
# so vote_entity and vote_observation are the memory tools it is NOT denied; every
# other mutation and every built-in stays denied, keeping grooming to demote-never-delete.
_LIGHT_TIER_ALLOWED = {"vote_entity", "vote_observation"}
DREAM_DISALLOWED_TOOLS: tuple[str, ...] = (
    *(f"mcp__memory__{tool}" for tool in _MUTATING_MEMORY_TOOLS if tool not in _LIGHT_TIER_ALLOWED),
    *_DISALLOWED_BUILTINS,
)

# The heavy tier may also merge duplicates (entities and observations), so vote_entity,
# vote_observation, merge_entities, and merge_observations are the memory tools it is NOT
# denied; every delete_* (and restore_entity) stays denied, so removal is never hard -
# merge only soft-deletes the folded-away source entity (observation merges hard-delete
# the source observation, consistent with delete_observations).
_HEAVY_TIER_ALLOWED = {"vote_entity", "vote_observation", "merge_entities", "merge_observations"}
HEAVY_DREAM_DISALLOWED_TOOLS: tuple[str, ...] = (
    *(f"mcp__memory__{tool}" for tool in _MUTATING_MEMORY_TOOLS if tool not in _HEAVY_TIER_ALLOWED),
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
    "Run one or two targeted searches (search_nodes / search_all_projects), then "
    "traverse only the most promising hits with get_entity_with_relations / "
    "search_related_nodes to gather linked context - do not exhaustively open every "
    "match. "
    "When an entity is relevant, read the observations that answer the query and "
    "pull out the SPECIFIC facts - concrete numbers, dates, names, and decisions - "
    "rather than only the high-level theme. If two observations conflict, prefer the "
    "newer one. "
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
    "Your ONLY permitted mutations are vote_entity with a vote of -1, to DEMOTE "
    "entities in ranking, and vote_observation with a vote of -1, to DEMOTE a single "
    "stale observation within an entity. You must NEVER delete anything and NEVER cast "
    "a positive vote. Deletion and promotion are not your job. "
    "Search broadly ACROSS ALL PROJECTS (search_all_projects, then search_nodes "
    "and get_entity_with_relations / search_related_nodes to confirm) for entities "
    "that are stale, superseded, or duplicated by a better entity. Before voting, "
    "inspect each candidate's current vote_score: do NOT downvote an entity that is "
    "already strongly negative (a score at or below {gc_floor}), because the ranking "
    "penalty has already saturated and further votes are wasted. "
    "To demote an individual observation, read the content_hash field off that "
    "observation object in the tool results and pass it to vote_observation "
    "(content_hash=...) - you do NOT need to paste the observation's content. "
    "Demote at most {max_votes} entities or observations this pass - be conservative "
    "and prefer clear noise over borderline calls. "
    "When you vote, pass the entity's exact project and name slug VERBATIM from the "
    "tool results. "
    "Finish with a terse audit summary: exactly one line per demotion. For an entity "
    "use '[project/entity-name] - reason'; for an observation include the word "
    "'observation' in the reason and the entity's content-hash in the slug, e.g. "
    "'[project/entity-name#a1b2c3d4] - observation demoted: stale'. Emit the single "
    "line 'nothing demoted' if you found no clear candidates."
)

HEAVY_DREAM_RITUAL = (
    "You are a memory curation agent doing structural grooming of a knowledge graph "
    "while it is idle, with access to the mcp__memory__* tools. "
    "The mcp__memory server may report as 'still connecting' on your very first "
    "turn; ignore that and CALL a memory tool anyway - the connection completes "
    "before the call runs. "
    "Your ONLY permitted mutations are: merge_entities (to fold a duplicate entity "
    "into its canonical twin), merge_observations (to fold a duplicate observation "
    "into another WITHIN THE SAME ENTITY, by content_hash), vote_entity with a vote "
    "of -1 (to DEMOTE a stale entity in ranking), and vote_observation with a vote of "
    "-1 (to DEMOTE a single stale observation). You must NEVER delete anything, NEVER "
    "create entities, and NEVER cast a positive vote. "
    "Search broadly ACROSS ALL PROJECTS (search_all_projects, then search_nodes and "
    "get_entity_with_relations / search_related_nodes to confirm). "
    "When you find two entities in the same project that describe the same thing, "
    "merge them: merge_entities(project, source, target) where source is the worse "
    "duplicate to fold away and target is the canonical keeper. Only merge entities "
    "you are confident are genuine duplicates, and only within a single project - "
    "never merge across projects. "
    "Each observation in read output carries a content_hash; use it to address "
    "observations cheaply. When one entity holds two observations saying the same "
    "thing, fold the worse into the better with "
    "merge_observations(project, entity, sourceHash, targetHash). "
    "Also demote (vote_entity, -1) entities, and demote (vote_observation, -1, by "
    "content_hash) individual observations, that are clearly stale or superseded but "
    "not duplicates, skipping any already at or below a score of {gc_floor} (the ranking "
    "penalty has saturated there). "
    "Do at most {max_ops} operations (merges plus demotions) this pass - be "
    "conservative and prefer clear cases over borderline calls. "
    "When you act, pass each entity's exact project and name slug VERBATIM from the "
    "tool results. "
    "Finish with a terse audit summary: exactly one line per operation in the form "
    "'[project/entity-name] - action reason' (the affected slug in square brackets, "
    "then a hyphen, then the action and a few words of why, e.g. "
    "'[proj/task/old] - merged into task/new: duplicate'). For an observation-level "
    "operation, include the word 'observation' in the reason and the entity's "
    "content-hash in the slug, e.g. '[proj/task/a#a1b2c3d4] - observation demoted: "
    "stale' or '[proj/task/a#a1b2c3d4] - merged observation into #deadbeef: "
    "duplicate'. Emit the single line 'nothing changed' if you found no clear "
    "candidates."
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
    max_turns: int,
) -> list[str]:
    """Build the headless ``claude -p`` argv for a read-only recall spawn.

    ``max_turns`` caps the tool-calling loop that dominates recall latency, keeping
    a spawn inside the calling client's timeout window.
    """
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
        "--max-turns",
        str(max_turns),
        "--disallowedTools",
        *DISALLOWED_TOOLS,
        "--output-format",
        "json",
    ]


def _build_curation_command(
    *,
    claude_bin: str,
    model: str,
    mcp_config_path: str,
    prompt: str,
    disallowed: tuple[str, ...],
) -> list[str]:
    """Build the headless ``claude -p`` argv shared by both dream curation tiers."""
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
        *disallowed,
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
    return _build_curation_command(
        claude_bin=claude_bin,
        model=model,
        mcp_config_path=mcp_config_path,
        prompt=DREAM_RITUAL.format(max_votes=max_votes, gc_floor=_GC_DOWNVOTE_FLOOR),
        disallowed=DREAM_DISALLOWED_TOOLS,
    )


def build_heavy_dream_command(
    *,
    claude_bin: str,
    model: str,
    mcp_config_path: str,
    max_ops: int,
) -> list[str]:
    """Build the headless ``claude -p`` argv for a merge-and-downvote heavy curation spawn."""
    return _build_curation_command(
        claude_bin=claude_bin,
        model=model,
        mcp_config_path=mcp_config_path,
        prompt=HEAVY_DREAM_RITUAL.format(max_ops=max_ops, gc_floor=_GC_DOWNVOTE_FLOOR),
        disallowed=HEAVY_DREAM_DISALLOWED_TOOLS,
    )


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


def parse_recall_result(stdout: str, *, expected_model: str) -> SpawnResult:
    """Extract the distilled result and metrics from a ``claude -p`` JSON payload.

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

    return {
        "text": result,
        "ok": True,
        "duration_ms": _as_int(payload.get("duration_ms")),
        "num_turns": _as_int(payload.get("num_turns")),
        "cost_usd": _as_float(payload.get("total_cost_usd")),
    }


def _as_int(value: object) -> int | None:
    """Coerce a numeric payload field to int, or None if absent or non-numeric."""
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _as_float(value: object) -> float | None:
    """Coerce a numeric payload field to float, or None if absent or non-numeric."""
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


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


def _failed_spawn(message: str) -> SpawnResult:
    """Build a failed SpawnResult carrying the handled-failure message in ``text``."""
    return {"text": message, "ok": False, "duration_ms": None, "num_turns": None, "cost_usd": None}


async def _run_isolated_agent(
    build_command: Callable[[str, str], list[str]],
    *,
    model: str,
    timeout: float,
) -> SpawnResult:
    """Spawn a hermetic headless agent and return its parsed result.

    Resolves the claude CLI, runs the pre-flight gate, then spawns in a throwaway
    tempdir holding the mcp-config and an isolated CLAUDE_CONFIG_DIR (no user
    hooks). ``build_command`` receives the resolved claude binary and mcp-config
    path. All failures surface as an ``ok=False`` result whose message is in
    ``text``; the tempdir is always cleaned up.
    """
    claude_bin = _claude_bin()
    if not claude_bin:
        return _failed_spawn("recall unavailable: claude CLI not found")

    try:
        await run_preflight(get_preflight_command())
    except RuntimeError as exc:
        return _failed_spawn(str(exc))

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
        return _failed_spawn(str(exc))
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


async def recall(query: str) -> str:
    """Run a heavy memory recall in a throwaway agent and return distilled findings.

    Records the recall to the shared status marker for the visualiser: an in-flight
    count while it runs and a finished-history entry after. The finish is in a
    ``finally`` so the in-flight count never leaks on timeout or error.
    """
    model = get_recall_model()
    max_turns = get_recall_max_turns()
    recall_status.record_start()
    result: SpawnResult | None = None
    try:
        result = await _run_isolated_agent(
            lambda claude_bin, config_path: build_recall_command(
                query,
                claude_bin=claude_bin,
                model=model,
                mcp_config_path=config_path,
                max_turns=max_turns,
            ),
            model=model,
            timeout=_RECALL_TIMEOUT_SECONDS,
        )
        return result["text"]
    finally:
        recall_status.record_finish(
            query,
            ok=result["ok"] if result else False,
            duration_ms=result["duration_ms"] if result else None,
            num_turns=result["num_turns"] if result else None,
            cost_usd=result["cost_usd"] if result else None,
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
    result = await _run_isolated_agent(
        lambda claude_bin, config_path: build_dream_command(
            claude_bin=claude_bin,
            model=model,
            mcp_config_path=config_path,
            max_votes=get_dream_max_votes(),
        ),
        model=model,
        timeout=get_dream_timeout(),
    )
    return result["text"]


async def run_heavy_dream_pass() -> str:
    """Run one merge-and-downvote heavy curation pass in a throwaway agent, returning its audit."""
    model = get_dream_heavy_model()
    result = await _run_isolated_agent(
        lambda claude_bin, config_path: build_heavy_dream_command(
            claude_bin=claude_bin,
            model=model,
            mcp_config_path=config_path,
            max_ops=get_dream_heavy_max_ops(),
        ),
        model=model,
        timeout=get_dream_heavy_timeout(),
    )
    return result["text"]


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


@dataclass(frozen=True)
class TierSpec:
    """What varies between the light and heavy dream tiers.

    Each getter is a thunk so it resolves the underlying config at call time,
    which keeps the idle threshold live-configurable and keeps tests able to
    monkeypatch the config functions the thunks call.
    """

    name: str
    enabled_getter: Callable[[], bool]
    idle_getter: Callable[[], float]
    poll_getter: Callable[[], float]
    run_pass: Callable[[], Awaitable[str]]


def _pass_succeeded(audit: str) -> bool:
    """Return whether a dream audit describes a real pass rather than a handled failure.

    Every handled-failure return string from the spawn stack begins with "recall ",
    which a genuine audit (demotion lines or "nothing demoted") never does.
    """
    return not audit.startswith("recall ")


# Global single-flight guard: at most one dream (light OR heavy, scheduled OR
# manual) runs at a time. A plain bool suffices because every tier watcher and
# every manual trigger runs in the one memory-agent event loop, so a check-and-set
# with no await in between is atomic - and unlike a module-level asyncio.Lock it
# does not bind to a single event loop (tests each run on a fresh loop).
_dream_running = False
# Manual triggers spawn a background task; hold a reference so it is not garbage
# collected mid-run (asyncio only holds a weak reference to running tasks).
_manual_tasks: set[asyncio.Task[None]] = set()

# Slack (seconds) allowed between a dream pass's own marker reset and when we
# observe it, before an idle-marker touch counts as real user activity. Tied to
# activity._MARKER_THROTTLE_SECONDS (60.0): a self-caused reset can surface up to
# ~60s stale due to the throttled marker write, plus fetch latency. Err large -
# too small a margin misreads the dream's own delayed reset as the user returning,
# re-arming the session early and re-creating the sawtooth that starves the heavy tier.
_SESSION_END_MARGIN_SECONDS = 60.0


@dataclass
class _SessionState:
    """Mutable state of the current idle session, shared across coordinator ticks.

    ``anchor`` is the monotonic instant the current idle session began (None when
    no session is active). ``fired`` names the tiers already fired this session.
    ``last_pass_end`` is the monotonic instant our last pass finished, used to
    attribute a marker reset to the dream itself rather than to the user.
    """

    anchor: float | None = None
    fired: set[str] = field(default_factory=set)
    last_pass_end: float = 0.0


# Single coordinator session state; reset in tests via ``agent._session = _SessionState()``.
_session = _SessionState()


def _claim_dream() -> bool:
    """Claim the single-flight slot, returning False if a dream is already running."""
    global _dream_running  # noqa: PLW0603
    if _dream_running:
        return False
    _dream_running = True
    return True


async def _run_claimed(spec: TierSpec) -> None:
    """Run one already-claimed pass: record start, run, record outcome, release the slot.

    The caller MUST have claimed the slot via ``_claim_dream`` first; this always
    releases it. ``record_pass_start`` / ``record_pass`` bracket the run so the
    visualiser can show the pass as in flight. Stamping ``_session.last_pass_end``
    here - the single chokepoint shared by the scheduled and manual paths - is what
    lets the coordinator tell the dream's own marker reset from real user activity.
    """
    global _dream_running  # noqa: PLW0603
    dream_status.record_pass_start(spec.name)
    try:
        audit = await spec.run_pass()
    except Exception as exc:
        logger.exception("%s dream pass failed", spec.name)
        dream_status.record_pass(str(exc), ok=False, tier=spec.name)
    else:
        logger.info("%s dream: %s", spec.name, audit)
        dream_status.record_pass(audit, ok=_pass_succeeded(audit), tier=spec.name)
    finally:
        _dream_running = False
        _session.last_pass_end = time.monotonic()


async def _run_guarded(spec: TierSpec) -> bool:
    """Run one pass under the single-flight guard, returning whether it actually ran."""
    if not _claim_dream():
        return False
    await _run_claimed(spec)
    return True


def _due_tiers(
    state: _SessionState, now: float, idle: float, tiers: list[TierSpec]
) -> list[TierSpec]:
    """Observe one (now, idle) sample: update the session and return the tiers due to fire.

    A pure, synchronous decision function - the tricky session-detection logic lives
    here, driven only by explicit inputs, so it is trivially unit-testable.

    ``idle`` is a wall-clock duration; ``now`` and ``state.last_pass_end`` are
    monotonic instants, so ``implied_activity = now - idle`` is a monotonic instant -
    only monotonic-vs-monotonic comparisons, no wall/monotonic mix. ``tiers`` must be
    the enabled tiers; the smallest idle threshold establishes the session.
    """
    implied_activity = now - idle
    session_ended = implied_activity > state.last_pass_end + _SESSION_END_MARGIN_SECONDS
    if state.anchor is not None and session_ended:
        state.anchor = None
        state.fired.clear()
    if state.anchor is None:
        if not tiers or idle < min(spec.idle_getter() for spec in tiers):
            return []
        state.anchor = now - idle
    genuine_idle = now - state.anchor
    return sorted(
        (
            spec
            for spec in tiers
            if spec.name not in state.fired and genuine_idle >= spec.idle_getter()
        ),
        key=lambda spec: spec.idle_getter(),
    )


async def _coordinator_tick() -> None:
    """Fetch idle once, then run every tier the session says is due, at most once each.

    A tier is added to ``_session.fired`` only after a successful guard claim, so a
    tick blocked by the single-flight guard simply retries the tier next poll.
    """
    idle = await fetch_idle_seconds()
    if idle is None:
        return
    now = time.monotonic()
    for spec in _due_tiers(_session, now, idle, _enabled_tiers()):
        if await _run_guarded(spec):
            _session.fired.add(spec.name)


# Every field is wrapped in a lambda (not passed as a direct reference) so the
# module-level name resolves at call time. A direct reference would freeze the
# original function object, defeating the runtime config the getters read and the
# monkeypatching the tests rely on.
_LIGHT_TIER = TierSpec(
    name="light",
    enabled_getter=lambda: get_dream_enabled(),  # noqa: PLW0108
    idle_getter=lambda: get_dream_idle_seconds(),  # noqa: PLW0108
    poll_getter=lambda: get_dream_poll_seconds(),  # noqa: PLW0108
    run_pass=lambda: run_dream_pass(),  # noqa: PLW0108
)

# The heavy tier fires once per idle session at a longer genuine-idle threshold,
# measured from the session's true start. Because the light tier now fires only
# once per session, its own marker reset no longer caps the idle clock, so the
# clock climbs freely to the heavy threshold.
_HEAVY_TIER = TierSpec(
    name="heavy",
    enabled_getter=lambda: get_dream_heavy_enabled(),  # noqa: PLW0108
    idle_getter=lambda: get_dream_heavy_idle_seconds(),  # noqa: PLW0108
    poll_getter=lambda: get_dream_heavy_poll_seconds(),  # noqa: PLW0108
    run_pass=lambda: run_heavy_dream_pass(),  # noqa: PLW0108
)

_ALL_TIERS = (_LIGHT_TIER, _HEAVY_TIER)


def _enabled_tiers() -> list[TierSpec]:
    """Return the dream tiers whose enabled flag is currently set."""
    return [spec for spec in _ALL_TIERS if spec.enabled_getter()]


_TIERS_BY_NAME = {_LIGHT_TIER.name: _LIGHT_TIER, _HEAVY_TIER.name: _HEAVY_TIER}


def trigger_dream(tier: str) -> dict[str, object]:
    """Kick off a manual dream pass of ``tier`` as a background task.

    Runs regardless of the tier's enabled flag (that flag only gates the autonomous
    scheduler), but still requires the claude CLI and respects the single-flight
    guard. Returns ``{"started": bool, "reason": str}`` immediately - the pass itself
    runs in the background and the visualiser reflects it via the running indicator.
    """
    spec = _TIERS_BY_NAME.get(tier)
    if spec is None:
        return {"started": False, "reason": "unknown tier"}
    if _claude_bin() is None:
        return {"started": False, "reason": "claude CLI not found"}
    if not _claim_dream():
        return {"started": False, "reason": "already running"}
    task = asyncio.create_task(_run_claimed(spec))
    _manual_tasks.add(task)
    task.add_done_callback(_manual_tasks.discard)
    return {"started": True}


@mcp.custom_route("/api/dream/trigger", methods=["POST"], include_in_schema=False)  # type: ignore[untyped-decorator]
async def _dream_trigger_route(request: Request) -> JSONResponse:
    """Trigger a manual dream pass. Body: ``{"tier": "light"|"heavy"}``."""
    try:
        body = await request.json()
    except (ValueError, TypeError):
        return JSONResponse({"started": False, "reason": "invalid JSON body"}, status_code=400)
    tier = body.get("tier") if isinstance(body, dict) else None
    if not isinstance(tier, str):
        return JSONResponse({"started": False, "reason": "tier is required"}, status_code=400)
    result = trigger_dream(tier)
    status = 200 if result["started"] else 409
    return JSONResponse(result, status_code=status)


async def _coordinator_loop() -> None:
    """Snapshot each enabled tier's config, then poll and coordinate their passes.

    One loop drives both tiers, so it polls at the finest enabled cadence and the
    per-session decision (which tier is due, when the session ended) is made by
    ``_due_tiers`` each tick.
    """
    tiers = _enabled_tiers()
    for spec in tiers:
        dream_status.record_startup(
            tier=spec.name,
            enabled=spec.enabled_getter(),
            idle_threshold_seconds=spec.idle_getter(),
            poll_seconds=spec.poll_getter(),
        )
    poll = min(spec.poll_getter() for spec in tiers)
    while True:
        await asyncio.sleep(poll)
        await _coordinator_tick()


async def _serve() -> None:
    """Serve over streamable HTTP, running the single dream coordinator alongside.

    The coordinator starts only when the claude CLI it would spawn is present AND at
    least one tier is enabled; otherwise it would poll forever with every pass
    no-opping. It is an explicit background task rather than a FastMCP ``lifespan``:
    in stateless_http mode the low-level server runs per request, so a lifespan task
    would be spawned and cancelled on every request. Cancelled cleanly on shutdown.
    """
    recall_status.record_startup()
    run_coordinator = _claude_bin() is not None and bool(_enabled_tiers())
    coordinator = asyncio.create_task(_coordinator_loop()) if run_coordinator else None
    try:
        await mcp.run_streamable_http_async()
    finally:
        if coordinator is not None:
            coordinator.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await coordinator


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
