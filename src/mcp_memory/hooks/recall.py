"""Recall triggers: surface the observations whose triggers match a hook event."""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from fnmatch import fnmatchcase
import json
from pathlib import Path, PurePosixPath
import re
import time
from typing import TYPE_CHECKING

from cline_hooks.core.parameters import FileEditParameters, McpToolUse, ReadParameters
from cline_hooks.core.protocol import get_protocol
from cline_hooks.core.vocabulary import FILE_EDIT_TOOLS, FILE_READ_TOOLS, CanonicalTool

from mcp_memory import api_client
from mcp_memory.atomic_write import write_json_atomic
from mcp_memory.config import get_data_dir
from mcp_memory.hooks.tracker import take_unseen

if TYPE_CHECKING:
    from collections.abc import Mapping

TriggerRow = dict[str, object]

_SERVER_TIMEOUT_SECONDS = 1.0
_REFRESH_SECONDS = 5.0
_MAX_MATCHED_CHARS = 20_000
_MAX_RECALLED = 2
_RECALL_BLOCK = (
    "RECALLED MEMORY: this call matches a recall trigger. Check the call against these observations,"
    " then run it again if it still holds. The repeat call runs:\n{observations}"
)
_RECALL_NOTE = "RECALLED MEMORY: these observations match what you are working on:\n{observations}"


@dataclass(frozen=True)
class Facts:
    """What a hook event exposes to triggers: the tool, its input JSON, the files it touches and the turn text."""

    tool: str = ""
    input_json: str = ""
    paths: tuple[str, ...] = ()
    text: str = ""


def facts(event: Mapping[str, object]) -> Facts:
    """Normalise a hook event's keyword arguments into the facts triggers match against."""
    tool = str(event.get("tool_name", ""))
    raw = event.get("parameters")
    parameters = raw if isinstance(raw, dict) else {}
    paths: tuple[str, ...] = ()
    if tool in FILE_EDIT_TOOLS:
        paths = (FileEditParameters.build(parameters).path,)
    elif tool in FILE_READ_TOOLS:
        paths = (ReadParameters.build(parameters).path,)
    elif tool == CanonicalTool.MCP:
        tool = McpToolUse.build(parameters).tool_name
    return Facts(
        tool=tool,
        input_json=json.dumps(parameters, ensure_ascii=False, default=str),
        paths=tuple(path for path in paths if path),
        text=_turn_text(event),
    )


def _turn_text(event: Mapping[str, object]) -> str:
    """Return the assistant's turn text for a turn-ending event, which names a transcript but no tool."""
    transcript_path = event.get("transcript_path")
    if event.get("tool_name") or not isinstance(transcript_path, str) or not transcript_path:
        return ""
    return get_protocol().transcript.turn_assistant_text(transcript_path)


def _path_matches(path: str, glob: str) -> bool:
    """Return True if a repo-relative glob matches the path or any trailing part of it."""
    parts = PurePosixPath(path).parts
    return any(fnmatchcase("/".join(parts[start:]), glob) for start in range(len(parts)))


def _row_matches(row: TriggerRow, found: Facts) -> bool:
    value = str(row["value"])
    if row["kind"] == "path":
        return any(_path_matches(path, value) for path in found.paths)
    try:
        if row["kind"] == "mention":
            return re.search(value, found.text[:_MAX_MATCHED_CHARS]) is not None
        return row["tool"] in {"", found.tool} and re.search(value, found.input_json[:_MAX_MATCHED_CHARS]) is not None
    except re.error:
        return False


def matches(rows: list[TriggerRow], found: Facts, kind: str) -> list[TriggerRow]:
    """Return the rows of one trigger kind that match the facts, one per observation, in the rows' order."""
    matched: dict[object, TriggerRow] = {}
    for row in rows:
        if row["kind"] == kind and row["content_hash"] not in matched and _row_matches(row, found):
            matched[row["content_hash"]] = row
    return list(matched.values())


def _cache_path() -> Path:
    """Return the path to the local copy of the server's trigger rows, next to the memory database."""
    return get_data_dir() / "recall-triggers.json"


def trigger_rows() -> list[TriggerRow]:
    """Return every trigger row, refreshing the local copy only when the server's etag changes.

    The server is asked at most once per _REFRESH_SECONDS. An unreachable server or an unreadable copy
    leaves the last known rows, or none.
    """
    path = _cache_path()
    try:
        cached = json.loads(path.read_text())
        fresh = time.time() - path.stat().st_mtime < _REFRESH_SECONDS
    except (OSError, ValueError):
        cached, fresh = {}, False
    if not isinstance(cached, dict):
        cached = {}
    if not fresh:
        try:
            reply = api_client.call("recall-triggers", {"etag": cached.get("etag")}, timeout=_SERVER_TIMEOUT_SECONDS)
        except api_client.ApiError:
            reply = {}
        if isinstance(reply, dict) and "rows" in reply:
            cached = reply
        with contextlib.suppress(OSError):
            write_json_atomic(path, cached)
    rows = cached.get("rows", [])
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def recall(task_id: str, event: Mapping[str, object], kind: str) -> str | None:
    """Return the observations of this trigger kind that match the event and the task has not yet seen.

    At most _MAX_RECALLED observations are returned, best-voted first, and each is recorded as seen.
    """
    try:
        return _recall(task_id, event, kind)
    except (KeyError, TypeError, ValueError, OSError):
        return None


def _recall(task_id: str, event: Mapping[str, object], kind: str) -> str | None:
    rows = trigger_rows()
    if not any(row.get("kind") == kind for row in rows):
        return None
    matched = matches(rows, facts(event), kind)
    by_hash = {str(row["content_hash"]): row for row in matched}
    taken = take_unseen(task_id, f"recalled_{kind}", list(by_hash), _MAX_RECALLED)
    if not taken:
        return None
    shown = [by_hash[key] for key in taken]
    observations = "\n".join(f"- `{row['entity']}` ({row['project']}): {row['content']}" for row in shown)
    template = _RECALL_BLOCK if kind == "pattern" else _RECALL_NOTE
    return template.format(observations=observations)
