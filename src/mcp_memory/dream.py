"""Opt-in idle-time dream curation of the memory graph, run inside the mcp-memory server.

While the server is idle, the scheduler spawns an ephemeral headless ``claude -p``
agent that demotes (light tier) or merges and demotes (heavy tier) stale entities
through the mcp-memory tools. The heavy tier may also soft-delete wrong entities,
observations and relations, all restorable until a grace-window purge.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import importlib.resources
from itertools import zip_longest
import json
import logging
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
import time
from typing import TYPE_CHECKING, TypedDict, cast

from . import activity, backup, dream_status
from .audit import audit_graph, propose_plan
from .config import (
    get_backup_enabled,
    get_db_path,
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
)
from .plan_apply import apply_safe_steps, archive_stale
from .storage import open_readonly, open_writable
from .storage.operations.maintenance import GC_DOWNVOTE_FLOOR
from .tool_names import MUTATING_TOOLS

__all__ = ["dream_status", "shutil", "time"]

logger = logging.getLogger("mcp-memory")

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from mcp.server.mcpserver import Context


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
    input_tokens: int | None
    output_tokens: int | None
    cache_read_tokens: int | None
    cache_creation_tokens: int | None


# --strict-mcp-config isolates MCP servers but not built-ins. Deny the write/exec
# ones so the agent cannot touch the filesystem or spawn processes, and the
# read/web ones (Read/Grep/Glob/WebFetch/WebSearch) so it is forced onto the
# mcp__memory__* graph tools - otherwise it answers from files on disk and cites
# file paths instead of the [project/entity] slugs the dream audit format requires.
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

# Deny rules override the inherited permissions.allow wildcard; an allow-list cannot.
# The dream curation pass may vote entities and observations (its only mutation),
# so vote is the memory tool it is NOT denied; every other mutation and every
# built-in stays denied, keeping grooming to demote-never-delete.
_LIGHT_TIER_ALLOWED = {"vote"}
DREAM_DISALLOWED_TOOLS: tuple[str, ...] = (
    *(f"mcp__memory__{tool}" for tool in MUTATING_TOOLS if tool not in _LIGHT_TIER_ALLOWED),
    *_DISALLOWED_BUILTINS,
)

# The heavy tier is allowed only lossless or reversible tools: vote, merges, rename_entity,
# create_relations, the soft-deleting delete_* and trim tools, and restores. create_entities and
# add_observations exist only for splits. Edit, status, project, metadata and move tools stay denied.
_HEAVY_TIER_ALLOWED = {
    "vote",
    "merge_entities",
    "merge_observations",
    "rename_entity",
    "create_relations",
    "create_entities",
    "add_observations",
    "delete_entity",
    "delete_observations",
    "delete_relation",
    "trim_observations_to_outcome",
    "restore_entity",
    "restore_observations",
    "restore_relation",
}
HEAVY_DREAM_DISALLOWED_TOOLS: tuple[str, ...] = (
    *(f"mcp__memory__{tool}" for tool in MUTATING_TOOLS if tool not in _HEAVY_TIER_ALLOWED),
    *_DISALLOWED_BUILTINS,
)

# Request header marking calls from a spawned dream agent, so the server can apply dream-only guards.
DREAM_CALLER_HEADER = "x-mcp-memory-dream"

# Startup wait for the spawned agent's mcp-memory HTTP connection (ms). Without
# it, claude begins its first turn mid-handshake and the model reports the graph
# unreachable.
_MCP_STARTUP_TIMEOUT_MS = "30000"
# The CLI defers MCP tools behind a ToolSearch discovery step by default. A small
# dream model then wastes turns "loading schemas" and can wrongly report the
# graph empty, so force every mcp__memory__* tool to load upfront. CLI-internal
# env var (no public flag); an unrecognised value is ignored, reverting to default.
_ENABLE_TOOL_SEARCH = "false"

# The spawned agent runs against an isolated CLAUDE_CONFIG_DIR so it never reads
# the user's ~/.claude hooks or SessionStart skills - those inject reminders that
# derail the small dream model into thinking the graph is unreachable. These
# minimal settings pre-approve the memory tools (the deny-list still subtracts the
# mutating ones) with no hooks of any kind.
AGENT_SETTINGS: dict[str, object] = {"permissions": {"allow": ["mcp__memory__*"]}}

DREAM_RITUAL = (
    "You are a memory curation agent grooming a knowledge graph while it is idle, "
    "with access to the mcp__memory__* tools. "
    "The mcp__memory server may report as 'still connecting' on your very first "
    "turn; ignore that and CALL a memory tool anyway - the connection completes "
    "before the call runs. "
    "Your ONLY permitted mutations are vote with a vote of -1 on an entity, to DEMOTE "
    "entities in ranking, and vote with a vote of -1 on an observation, to DEMOTE a single "
    "stale observation within an entity. You must NEVER delete anything and NEVER cast "
    "a positive vote. Deletion and promotion are not your job. "
    "Search broadly ACROSS ALL PROJECTS (search_all_projects, then search_nodes "
    "and get_entity_with_relations to confirm) for entities "
    "that are stale, superseded, or duplicated by a better entity. Before voting, "
    "inspect each candidate's current vote_score: do NOT downvote an entity that is "
    "already strongly negative (a score at or below {gc_floor}), because the ranking "
    "penalty has already saturated and further votes are wasted. "
    "To demote an individual observation, read the content_hash field off that "
    "observation object in the tool results and pass it to vote "
    "(observationHash=...) - you do NOT need to paste the observation's content. "
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

_HEAVY_DREAM_PREAMBLE = (
    "You are a memory curation agent doing structural grooming of a knowledge graph "
    "while it is idle, with access to the mcp__memory__* tools. "
    "The mcp__memory server may report as 'still connecting' on your very first "
    "turn; ignore that and CALL a memory tool anyway - the connection completes "
    "before the call runs. "
    "Work through the memory-review checklist below. These headless rules OVERRIDE "
    "the checklist: there are no subagents, no user and no CLI, and the deterministic "
    "audit below is already done. Skip any step that needs a denied tool and never try "
    "to work around a denial. "
    "Your ONLY permitted mutations are the tools not denied to you. Code has already "
    "applied the safe audit steps (exact-duplicate merges, safe renames, feature relinks, "
    "stale archives) before this pass, and the audit below was taken after that. "
    "NEVER cast a positive vote. Delete only content that is outright wrong or misleading "
    "and that you read live. Prefer merge_entities, merge_observations or a -1 vote to a delete. "
    "Use rename_entity only for audit rename_entity steps with needs_review false. "
    "Use merge_observations (same entity, by content_hash) to fold duplicate observations. "
    "Do not archive or change status. Code archives stale resolved entities. The checklist's "
    "section 5 'archive or delete resolved tasks' does not apply: never delete a task for being "
    "old or resolved. Never delete an entity that has live depends-on or implements edges. "
    "Use trim_observations_to_outcome only on audit trim steps. Their must_keep_hashes are the "
    "minimum to keep, not a drop list. Read every observation live, and also keep each one that "
    "holds a distinct fact still true now. Observations recording a user correction, decision "
    "or lesson (Correction, User corrected, pushed back, User decided, User chose, Lesson) are "
    "never trimmed: the server keeps them. "
    "Demote or delete an observation only if its fact is false or stale now. A later observation "
    "that corrects its framing or scope is not a reason. "
    "Use create_relations only between entities you have read live. Add 'task implements feature' "
    "only when one feature clearly covers the task's change. Skip a task that spans several "
    "features or fits none, and skip star_graph_tasks entries that are not belongs-to. Delete a "
    "relation only to replace a task's belongs-to project edge with the implements edge you just "
    "added, or when the edge is outright wrong. "
    "Use create_entities and add_observations only to split an audit consider_split entity in the "
    "same project. Copy observation text verbatim, give each new entity a relation, then delete "
    "the moved observations from the source. Never write new facts. "
    "Use restore_* only to undo an operation you made in this pass. "
    "Re-read each target live before acting, and pass each entity's exact project and "
    "name slug VERBATIM. "
    "Spread operations across projects. "
    "Do at most {max_ops} operations this pass. Skip any entity already at or below a "
    "vote score of {gc_floor}. "
)

_HEAVY_DREAM_AUDIT_FORMAT = (
    "Finish with a terse audit summary: exactly one line per operation in the form "
    "'[project/entity-name] - action reason' (the affected slug in square brackets, "
    "then a hyphen, then the action and a few words of why, e.g. "
    "'[proj/task/old] - merged into task/new: duplicate'). Other actions: "
    "'renamed to <new>', 'linked <type> -> <target>', 'unlinked <type> -> <target>', "
    "'deleted: <why>', 'trimmed: <why>', 'created from split of <source>', 'restored: <why>'. For an "
    "observation-level operation, include the word 'observation' in the reason and the "
    "entity's content-hash in the slug, e.g. '[proj/task/a#a1b2c3d4] - observation "
    "demoted: stale' or '[proj/task/a#a1b2c3d4] - merged observation into #deadbeef: "
    "duplicate' or '[proj/task/a#a1b2c3d4] - observation deleted: wrong'. "
    "Emit the single line 'nothing changed' if you changed nothing. "
    "Emit only these lines: no counts, totals, headings or prose. This overrides the checklist's "
    "final report step."
)

_HEAVY_DREAM_AUDIT_MAX_BYTES = 200_000

_FRONTMATTER = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)


def _memory_review_checklist() -> str:
    """Return the memory-review skill text without its frontmatter."""
    skill = importlib.resources.files("mcp_memory").joinpath("prompts/shared/skills/memory-review/SKILL.md")
    return _FRONTMATTER.sub("", skill.read_text(encoding="utf-8"), count=1).strip()


def _project_of(item: object) -> object:
    """Return the project an audit finding or plan step belongs to."""
    if isinstance(item, dict):
        arguments = item.get("arguments")
        return item.get("project") or (arguments.get("project") if isinstance(arguments, dict) else None)
    return item


def _round_robin_by_project[T](items: list[T]) -> list[T]:
    """Interleave items one per project in turn, keeping each project's own order."""
    groups: dict[object, list[T]] = {}
    for item in items:
        groups.setdefault(_project_of(item), []).append(item)
    return [item for batch in zip_longest(*groups.values()) for item in batch if item is not None]


def _as_review_step(step: dict[str, object]) -> dict[str, object]:
    """Turn a trim step's keep_hashes into must_keep_hashes and force review, leaving other steps alone."""
    if step.get("tool") != "trim_observations_to_outcome":
        return step
    arguments = {**cast("dict[str, object]", step["arguments"])}
    arguments["must_keep_hashes"] = arguments.pop("keep_hashes")
    return {**step, "arguments": arguments, "needs_review": True}


def _heavy_dream_audit(max_ops: int) -> str:
    """Return the compact JSON audit report and proposed plan, each list capped at max_ops across projects."""
    db = open_readonly(get_db_path())
    try:
        report = audit_graph(db, None)
        steps = propose_plan(db, report)
    finally:
        db.connection.close()
    fair_report = {
        key: _round_robin_by_project(value) if isinstance(value, list) else value for key, value in report.items()
    }
    fair_steps = _round_robin_by_project([_as_review_step(step) for step in steps])
    while True:
        capped = {key: value[:max_ops] if isinstance(value, list) else value for key, value in fair_report.items()}
        audit = json.dumps({"report": capped, "steps": fair_steps[:max_ops]}, separators=(",", ":"))
        if len(audit.encode()) <= _HEAVY_DREAM_AUDIT_MAX_BYTES or max_ops <= 1:
            return audit
        max_ops = max_ops * 3 // 4


def _dream_prepass() -> list[str]:
    """Apply the safe audit steps and archive stale entities in code, returning one audit line per change."""
    db = open_writable(get_db_path())
    try:
        steps = propose_plan(db, audit_graph(db, None))
        lines = [*apply_safe_steps(db, steps), *archive_stale(db)]
    finally:
        db.connection.close()
    return [line for block in lines for line in block.splitlines()]


def _heavy_dream_prompt(max_ops: int, audit: str) -> str:
    """Assemble the heavy dream prompt: preamble, checklist, audit, audit-line format."""
    preamble = _HEAVY_DREAM_PREAMBLE.format(max_ops=max_ops, gc_floor=GC_DOWNVOTE_FLOOR)
    sections = [
        preamble,
        _memory_review_checklist(),
        f"Deterministic audit (JSON):\n{audit}",
        _HEAVY_DREAM_AUDIT_FORMAT,
    ]
    return "\n\n".join(sections)


def _claude_bin() -> str | None:
    """Return the path to the claude CLI, or None if it is not on PATH."""
    return shutil.which("claude")


def build_mcp_config(memory_url: str) -> dict[str, object]:
    """Build the --mcp-config payload pointing the spawned agent at mcp-memory only."""
    return {"mcpServers": {"memory": {"type": "http", "url": memory_url, "headers": {DREAM_CALLER_HEADER: "1"}}}}


def is_dream_request(ctx: Context | None) -> bool:
    """Return whether a tool call arrived from a spawned dream agent."""
    headers = None if ctx is None else ctx.headers
    return headers is not None and headers.get(DREAM_CALLER_HEADER) == "1"


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
        "stream-json",
        "--verbose",
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
        prompt=DREAM_RITUAL.format(max_votes=max_votes, gc_floor=GC_DOWNVOTE_FLOOR),
        disallowed=DREAM_DISALLOWED_TOOLS,
    )


def build_heavy_dream_command(
    *,
    claude_bin: str,
    model: str,
    mcp_config_path: str,
    max_ops: int,
    audit: str,
) -> list[str]:
    """Build the headless ``claude -p`` argv for a memory-review heavy curation spawn."""
    return _build_curation_command(
        claude_bin=claude_bin,
        model=model,
        mcp_config_path=mcp_config_path,
        prompt=_heavy_dream_prompt(max_ops, audit),
        disallowed=HEAVY_DREAM_DISALLOWED_TOOLS,
    )


def build_spawn_env(config_dir: str, *, base_env: dict[str, str] | None = None) -> dict[str, str]:
    """Return the subprocess env for a dream spawn.

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
    match = re.search(r"claude-[a-z]+-\d+(?:-\d{1,2})?(?!\d)", model_id.lower())
    return match.group(0) if match else model_id.lower()


def _model_matches(model_id: str, usage_keys: list[str]) -> bool:
    """Return True if any reported model-usage key is the expected model family."""
    token = _model_token(model_id)
    return any(_model_token(key) == token for key in usage_keys)


def parse_dream_result(stdout: str, *, expected_model: str) -> SpawnResult:
    """Extract the distilled result and metrics from a ``claude -p`` stream-json transcript.

    The result is the transcript's final ``type: result`` event.

    Raises:
        RuntimeError: if the transcript is malformed, reports an error, or the model
            that actually ran is not the expected one (guards a silent Opus remap).
    """
    payload = _final_result_event(stdout)
    if payload.get("is_error"):
        raise RuntimeError("dream agent reported an error")

    result = payload.get("result")
    if not isinstance(result, str) or not result.strip():
        raise RuntimeError("dream agent returned no findings")

    usage = payload.get("modelUsage")
    usage_keys = list(usage) if isinstance(usage, dict) else []
    if not _model_matches(expected_model, usage_keys):
        raise RuntimeError("dream ran on an unexpected model")

    raw_totals = payload.get("usage")
    totals = raw_totals if isinstance(raw_totals, dict) else {}
    return {
        "text": result,
        "ok": True,
        "duration_ms": _as_int(payload.get("duration_ms")),
        "num_turns": _as_int(payload.get("num_turns")),
        "cost_usd": _as_float(payload.get("total_cost_usd")),
        "input_tokens": _as_int(totals.get("input_tokens")),
        "output_tokens": _as_int(totals.get("output_tokens")),
        "cache_read_tokens": _as_int(totals.get("cache_read_input_tokens")),
        "cache_creation_tokens": _as_int(totals.get("cache_creation_input_tokens")),
    }


def _final_result_event(stdout: str) -> dict[str, object]:
    """Return the last ``type: result`` event of a stream-json transcript."""
    final: dict[str, object] | None = None
    for line in stdout.splitlines():
        event = _parse_event(line)
        if event is not None and event.get("type") == "result":
            final = event
    if final is None:
        raise RuntimeError("dream agent returned malformed output")
    return final


def _parse_event(line: str) -> dict[str, object] | None:
    """Parse one stream-json line into an event dict, or None for a blank or non-event line."""
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return None
    return event if isinstance(event, dict) else None


def _as_int(value: object) -> int | None:
    """Coerce a numeric payload field to int, or None if absent or non-numeric."""
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _as_float(value: object) -> float | None:
    """Coerce a numeric payload field to float, or None if absent or non-numeric."""
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


_LOGGED_OUTPUT_BYTES = 2048
_STREAM_LINE_LIMIT_BYTES = 16 * 1024 * 1024
_MEMORY_TOOL_PREFIX = "mcp__memory__"
_PROGRESS_ARG_CHARS = 40
_OBSERVATION_TEXT_ARGS = frozenset({"observation", "newText", "oldText"})


def _short_args(tool_input: object) -> str:
    """Render a tool call's arguments as a short line: scalars as-is, collections by size, observation text omitted."""
    if not isinstance(tool_input, dict):
        return ""
    parts = [
        f"[{len(value)}]" if isinstance(value, (list, dict)) else str(value)[:_PROGRESS_ARG_CHARS]
        for key, value in tool_input.items()
        if key not in _OBSERVATION_TEXT_ARGS
    ]
    return " ".join(parts)


def _record_progress(line: str) -> None:
    """Record every memory tool call in one stream-json line on the dream status."""
    event = _parse_event(line)
    message = event.get("message") if event else None
    content = message.get("content") if isinstance(message, dict) else None
    for block in content if isinstance(content, list) else []:
        name = block.get("name") if isinstance(block, dict) else None
        if isinstance(name, str) and name.startswith(_MEMORY_TOOL_PREFIX) and block.get("type") == "tool_use":
            tool = name.removeprefix(_MEMORY_TOOL_PREFIX)
            dream_status.record_progress(tool, _short_args(block.get("input")), mutating=tool in MUTATING_TOOLS)


async def _read_stream(proc: asyncio.subprocess.Process, lines: list[str]) -> tuple[str, bytes]:
    """Read the agent's stdout line by line into ``lines``, recording progress, and return stdout plus stderr."""
    assert proc.stdout is not None
    assert proc.stderr is not None
    stderr = asyncio.ensure_future(proc.stderr.read())
    try:
        async for raw in proc.stdout:
            line = raw.decode(errors="replace")
            lines.append(line)
            _record_progress(line)
        await proc.wait()
        return "".join(lines), await stderr
    finally:
        stderr.cancel()


async def _spawn_agent(
    command: list[str],
    *,
    env: dict[str, str] | None = None,
    timeout: float,
    transcript: list[str] | None = None,
) -> str:
    """Spawn the dream agent, recording its memory tool calls as it streams, and return its stdout.

    Kills the agent on timeout. Every stdout line is appended to ``transcript`` as it arrives,
    so the caller keeps the partial output of a failed run.
    """
    lines = transcript if transcript is not None else []
    proc = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
        limit=_STREAM_LINE_LIMIT_BYTES,
    )
    try:
        stdout, stderr = await asyncio.wait_for(_read_stream(proc, lines), timeout=timeout)
    except TimeoutError as exc:
        proc.kill()
        await proc.wait()
        raise RuntimeError("dream timed out") from exc
    if proc.returncode != 0:
        logger.error(
            "dream agent exited %s\nstderr: %s\nstdout: %s",
            proc.returncode,
            stderr.decode(errors="replace")[-_LOGGED_OUTPUT_BYTES:],
            stdout[-_LOGGED_OUTPUT_BYTES:],
        )
        raise RuntimeError("dream agent exited with an error")
    return stdout


def _failed_spawn(message: str) -> SpawnResult:
    """Build a failed SpawnResult carrying the handled-failure message in ``text``."""
    return {
        "text": message,
        "ok": False,
        "duration_ms": None,
        "num_turns": None,
        "cost_usd": None,
        "input_tokens": None,
        "output_tokens": None,
        "cache_read_tokens": None,
        "cache_creation_tokens": None,
    }


def _remember_metrics(result: SpawnResult) -> str:
    """Stash the spawn's metrics for the pass being run and return its audit text."""
    global _pass_metrics  # ruff: ignore[global-statement]
    _pass_metrics = cast("dream_status.PassMetrics", {key: result[key] for key in dream_status.PASS_METRIC_KEYS})  # type: ignore[literal-required]
    return result["text"]


async def _run_isolated_agent(
    build_command: Callable[[str, str], list[str]],
    *,
    tier: str,
    model: str,
    timeout: float,
) -> SpawnResult:
    """Spawn a hermetic headless agent and return its parsed result.

    Resolves the claude CLI, then spawns in a throwaway
    tempdir holding the mcp-config and an isolated CLAUDE_CONFIG_DIR (no user
    hooks). ``build_command`` receives the resolved claude binary and mcp-config
    path. All failures surface as an ``ok=False`` result whose message is in
    ``text``; the tempdir is always cleaned up. The prompt and the agent's output,
    partial on failure, are saved as a ``tier`` transcript when backups are enabled.
    """
    claude_bin = _claude_bin()
    if not claude_bin:
        return _failed_spawn("dream unavailable: claude CLI not found")

    work_dir = tempfile.mkdtemp(prefix="mcp-memory-dream-")
    work_path = Path(work_dir)
    config_path = work_path / "mcp-config.json"
    isolated_config_dir = work_path / "config"
    command: list[str] = []
    transcript: list[str] = []
    try:
        config_path.write_text(json.dumps(build_mcp_config(get_memory_url())), encoding="utf-8")
        isolated_config_dir.mkdir()
        (isolated_config_dir / "settings.json").write_text(json.dumps(AGENT_SETTINGS), encoding="utf-8")
        command = build_command(claude_bin, str(config_path))
        stdout = await _spawn_agent(
            command, env=build_spawn_env(str(isolated_config_dir)), timeout=timeout, transcript=transcript
        )
        return parse_dream_result(stdout, expected_model=model)
    except RuntimeError as exc:
        return _failed_spawn(str(exc))
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
        if command and get_backup_enabled():
            await _save_transcript(tier, command, transcript)


async def _save_transcript(tier: str, command: list[str], transcript: list[str]) -> None:
    """Save the spawn's prompt and output beside the backups, logging a failure instead of raising."""
    try:
        await asyncio.to_thread(backup.save_transcript, tier, command[command.index("-p") + 1], "".join(transcript))
    except OSError:
        logger.exception("dream transcript save failed")


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
        tier=_LIGHT_TIER.name,
        model=model,
        timeout=get_dream_timeout(),
    )
    return _remember_metrics(result)


def _with_prepass(text: str, prepass: list[str]) -> str:
    """Join the code pre-pass audit lines to the agent text: before it on success, after it on failure."""
    if not prepass:
        return text
    lines = [text, *prepass] if text.startswith("dream ") else [*prepass, text]
    return "\n".join(lines)


async def run_heavy_dream_pass() -> str:
    """Run one memory-review heavy curation pass in a throwaway agent, returning its audit."""
    model = get_dream_heavy_model()
    max_ops = get_dream_heavy_max_ops()
    try:
        if get_backup_enabled():
            await asyncio.to_thread(backup.backup_database)
    except (OSError, sqlite3.Error):
        logger.exception("heavy dream backup failed")
        return "dream backup failed"
    try:
        prepass = await asyncio.to_thread(_dream_prepass)
    except (OSError, sqlite3.Error):
        logger.exception("heavy dream prepass failed")
        return "dream prepass failed"
    try:
        audit = _heavy_dream_audit(max_ops)
        result = await _run_isolated_agent(
            lambda claude_bin, config_path: build_heavy_dream_command(
                claude_bin=claude_bin,
                model=model,
                mcp_config_path=config_path,
                max_ops=max_ops,
                audit=audit,
            ),
            tier=_HEAVY_TIER.name,
            model=model,
            timeout=get_dream_heavy_timeout(),
        )
    except Exception as exc:
        logger.exception("heavy dream failed")
        return _with_prepass(f"dream failed: {type(exc).__name__}", prepass)
    return _with_prepass(_remember_metrics(result), prepass)


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

    Every handled-failure return string from the spawn stack begins with "dream ",
    which a genuine audit (demotion lines or "nothing demoted") never does.
    """
    return not audit.startswith("dream ")


# Global single-flight guard: at most one dream (light OR heavy, scheduled OR
# manual) runs at a time. A plain bool suffices because every tier watcher and
# every manual trigger runs in the one mcp-memory event loop, so a check-and-set
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


# Single coordinator session state; reset in tests via ``dream._session = _SessionState()``.
_session = _SessionState()


def _claim_dream() -> bool:
    """Claim the single-flight slot, returning False if a dream is already running."""
    global _dream_running  # ruff: ignore[global-statement]
    if _dream_running:
        return False
    _dream_running = True
    return True


_pass_metrics: dream_status.PassMetrics = {}


async def _run_claimed(spec: TierSpec) -> None:
    """Run one already-claimed pass: record start, run, record outcome, release the slot.

    The caller MUST have claimed the slot via ``_claim_dream`` first; this always
    releases it. ``record_pass_start`` / ``record_pass`` bracket the run so the
    visualiser can show the pass as in flight. Stamping ``_session.last_pass_end``
    here - the single chokepoint shared by the scheduled and manual paths - is what
    lets the coordinator tell the dream's own marker reset from real user activity.
    """
    global _dream_running, _pass_metrics  # ruff: ignore[global-statement]
    dream_status.record_pass_start(spec.name)
    _pass_metrics = {}
    try:
        audit = await spec.run_pass()
    except Exception as exc:
        logger.exception("%s dream pass failed", spec.name)
        dream_status.record_pass(str(exc), ok=False, tier=spec.name, metrics=_pass_metrics)
    else:
        logger.info("%s dream: %s", spec.name, audit)
        dream_status.record_pass(audit, ok=_pass_succeeded(audit), tier=spec.name, metrics=_pass_metrics)
    finally:
        _dream_running = False
        _session.last_pass_end = time.monotonic()


async def _run_guarded(spec: TierSpec) -> bool:
    """Run one pass under the single-flight guard, returning whether it actually ran."""
    if not _claim_dream():
        return False
    await _run_claimed(spec)
    return True


def _due_tiers(state: _SessionState, now: float, idle: float, tiers: list[TierSpec]) -> list[TierSpec]:
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
        (spec for spec in tiers if spec.name not in state.fired and genuine_idle >= spec.idle_getter()),
        key=lambda spec: spec.idle_getter(),
    )


async def _coordinator_tick() -> None:
    """Fetch idle once, then run every tier the session says is due, at most once each.

    A tier is added to ``_session.fired`` only after a successful guard claim, so a
    tick blocked by the single-flight guard simply retries the tier next poll.
    """
    idle = activity.idle_seconds()
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
    enabled_getter=lambda: get_dream_enabled(),  # ruff: ignore[unnecessary-lambda]
    idle_getter=lambda: get_dream_idle_seconds(),  # ruff: ignore[unnecessary-lambda]
    poll_getter=lambda: get_dream_poll_seconds(),  # ruff: ignore[unnecessary-lambda]
    run_pass=lambda: run_dream_pass(),  # ruff: ignore[unnecessary-lambda]
)

# The heavy tier fires once per idle session at a longer genuine-idle threshold,
# measured from the session's true start. Because the light tier now fires only
# once per session, its own marker reset no longer caps the idle clock, so the
# clock climbs freely to the heavy threshold.
_HEAVY_TIER = TierSpec(
    name="heavy",
    enabled_getter=lambda: get_dream_heavy_enabled(),  # ruff: ignore[unnecessary-lambda]
    idle_getter=lambda: get_dream_heavy_idle_seconds(),  # ruff: ignore[unnecessary-lambda]
    poll_getter=lambda: get_dream_heavy_poll_seconds(),  # ruff: ignore[unnecessary-lambda]
    run_pass=lambda: run_heavy_dream_pass(),  # ruff: ignore[unnecessary-lambda]
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


async def _coordinator_loop() -> None:
    """Poll and coordinate dream passes for whichever tiers are enabled.

    One loop drives both tiers, so it polls at the finest enabled cadence and the
    per-session decision (which tier is due, when the session ended) is made by
    ``_due_tiers`` each tick.
    """
    tiers = _enabled_tiers()
    poll = min(spec.poll_getter() for spec in tiers)
    while True:
        await asyncio.sleep(poll)
        await _coordinator_tick()


def start_coordinator() -> asyncio.Task[None] | None:
    """Snapshot tier config for the visualiser and start the dream coordinator if it can run.

    The coordinator starts only when the claude CLI it would spawn is present AND at
    least one tier is enabled; otherwise it would poll forever with every pass
    no-opping, so None is returned. The caller owns the returned task and cancels it
    on shutdown.
    """
    # Snapshot tier config on every boot so the visualiser reflects current
    # enabled/disabled state even when autonomous dream scheduling is off.
    for spec in _ALL_TIERS:
        dream_status.record_startup(
            tier=spec.name,
            enabled=spec.enabled_getter(),
            idle_threshold_seconds=spec.idle_getter(),
            poll_seconds=spec.poll_getter(),
        )
    if _claude_bin() is None or not _enabled_tiers():
        return None
    return asyncio.create_task(_coordinator_loop())
