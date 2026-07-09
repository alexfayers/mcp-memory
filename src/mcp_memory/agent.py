"""FastMCP server exposing an LLM recall layer in front of mcp-memory.

`recall` spawns an ephemeral headless ``claude -p`` agent locked to the
mcp-memory read tools, absorbs the heavy graph traversal in that throwaway
context, and returns distilled findings so the caller's own context stays clean.
The data store (mcp-memory) is untouched by this server.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import tempfile
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from .cli import _agent_spec, _setup_service_from_spec
from .config import (
    get_agent_port,
    get_memory_url,
    get_preflight_command,
    get_recall_model,
)

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

_RECALL_TIMEOUT_SECONDS = int(os.environ.get("MCP_RECALL_TIMEOUT", "180"))
_PREFLIGHT_TIMEOUT_SECONDS = 30
# Startup wait for the spawned agent's mcp-memory HTTP connection (ms). Without
# it, claude begins its first turn mid-handshake and the model reports the graph
# unreachable.
_MCP_STARTUP_TIMEOUT_MS = "30000"

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
    "Search thoroughly and multi-step for the query below: start with "
    "search_nodes / search_all_projects, then traverse promising hits with "
    "get_entity_with_relations / search_related_nodes to gather linked context. "
    "Return a compact bulleted list of the findings that answer the query. "
    "Tag every claim inline with its source entity slug as [project/entity-name], "
    "copied VERBATIM from the tool results - never paraphrase or invent a slug. "
    "If nothing relevant exists, say so plainly. Do not attempt to write, vote, "
    "or otherwise mutate anything."
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


def build_spawn_env(config_dir: str, *, base_env: dict[str, str] | None = None) -> dict[str, str]:
    """Return the subprocess env for a recall spawn.

    Isolates CLAUDE_CONFIG_DIR from the user's hooks and sets MCP_TIMEOUT so the
    spawned agent blocks until the mcp-memory connection is established, rather
    than starting its first turn mid-handshake and reporting the graph
    unreachable. Inherits the parent environment so AWS/Bedrock credentials still
    resolve; only these two keys are overridden.
    """
    env = dict(os.environ if base_env is None else base_env)
    env["CLAUDE_CONFIG_DIR"] = config_dir
    env["MCP_TIMEOUT"] = _MCP_STARTUP_TIMEOUT_MS
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


@mcp.tool(description=RECALL_DESC)
async def recall(query: str) -> str:
    """Run a heavy memory recall in a throwaway agent and return distilled findings."""
    claude_bin = shutil.which("claude")
    if not claude_bin:
        return "recall unavailable: claude CLI not found"

    try:
        await run_preflight(get_preflight_command())
    except RuntimeError as exc:
        return str(exc)

    model = get_recall_model()
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
        command = build_recall_command(
            query,
            claude_bin=claude_bin,
            model=model,
            mcp_config_path=str(config_path),
        )
        stdout = await _spawn_recall(command, env=build_spawn_env(str(isolated_config_dir)))
        return parse_recall_result(stdout, expected_model=model)
    except RuntimeError as exc:
        return str(exc)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def main(argv: list[str] | None = None) -> None:
    """Run the memory-agent server, or install it as a service.

    Bare invocation serves over streamable HTTP; ``setup-service`` installs a
    persistent background service.
    """
    parser = argparse.ArgumentParser(prog="memory-agent", description="Memory recall agent server")
    parser.add_subparsers(dest="command").add_parser(
        "setup-service", help="Install as a persistent background service"
    )
    args = parser.parse_args(argv)

    if args.command == "setup-service":
        _setup_service_from_spec(_agent_spec(str(get_agent_port())))
    else:
        mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
