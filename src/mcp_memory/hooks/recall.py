"""Recall triggers: surface the observations whose triggers match a hook event."""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, replace
from fnmatch import fnmatchcase
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
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
_GIT_TIMEOUT_SECONDS = 2.0
_BLAMED_COMMIT = re.compile(r"^([0-9a-f]{40,64}) \d+ \d+", re.MULTILINE)
_SEARCH_BLOCK = re.compile(r"------- SEARCH\n(.*?)\n=======", re.DOTALL)
_SURFACE_CANDIDATES = 10
_RECALL_BLOCK = (
    "RECALLED MEMORY: this call matches a recall trigger. Check the call against these observations,"
    " then run it again if it still holds. The repeat call runs:\n{observations}"
)
_RECALL_NOTE = "RECALLED MEMORY: these observations match what you are working on:\n{observations}"
_SURFACE_NOTE = "RELEVANT MEMORY: these entities match the user's message. Read them if they bear on it:\n{entities}"


@dataclass(frozen=True)
class Facts:
    """What a hook event exposes to triggers.

    That is the tool, its input JSON, the files it touches, the turn text and the patch-ids of the changes
    an edit replaces.
    """

    tool: str = ""
    input_json: str = ""
    paths: tuple[str, ...] = ()
    text: str = ""
    changes: frozenset[str] = frozenset()


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


def _git(directory: Path, *args: str, stdin: str | None = None) -> str:
    """Return a git command's output, or an empty string if it fails."""
    git = shutil.which("git")
    if git is None:
        return ""
    run = subprocess.run(
        [git, *args],
        check=False,
        cwd=directory,
        input=stdin,
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_SECONDS,
    )
    return run.stdout if run.returncode == 0 else ""


def _patch_ids_path() -> Path:
    """Return the path to the local map of commit sha to patch-id, next to the memory database."""
    return get_data_dir() / "recall-patch-ids.json"


def _patch_ids(directory: Path, shas: set[str]) -> frozenset[str]:
    """Return the `git patch-id --stable` of each commit, reading and adding to the local map."""
    path = _patch_ids_path()
    try:
        known = json.loads(path.read_text())
    except (OSError, ValueError):
        known = {}
    if not isinstance(known, dict):
        known = {}
    missing = sorted(shas - known.keys())
    if missing:
        shown = _git(directory, "show", "--no-color", *missing)
        for line in _git(directory, "patch-id", "--stable", stdin=shown).splitlines():
            patch_id, sha = line.split()
            known[sha] = patch_id
        with contextlib.suppress(OSError):
            write_json_atomic(path, known)
    return frozenset(str(known[sha]) for sha in shas if sha in known) - {""}


def _edited_changes(event: Mapping[str, object]) -> frozenset[str]:
    """Return the patch-ids of the commits that last changed the lines a file edit replaces.

    An edit that names no replaced text, such as a whole-file write, replaces every line of the file.
    An edit whose replaced text is not in the file replaces nothing.
    """
    raw = event.get("parameters")
    if event.get("tool_name") not in FILE_EDIT_TOOLS or not isinstance(raw, dict):
        return frozenset()
    edit = FileEditParameters.build(raw)
    roots = event.get("workspace_roots")
    path = Path(str(roots[0]) if isinstance(roots, list) and roots else "", edit.path)
    if not edit.path or not path.is_file():
        return frozenset()
    text = path.read_text(encoding="utf-8")
    searches = _SEARCH_BLOCK.findall(edit.diff)
    ranges: list[str] = []
    for search in searches:
        start = text.find(search)
        if start >= 0:
            ranges += ["-L", f"{text.count(chr(10), 0, start) + 1},+{search.count(chr(10)) + 1}"]
    if searches and not ranges:
        return frozenset()
    blame = _git(path.parent, "blame", "--porcelain", *ranges, "--", path.name)
    return _patch_ids(path.parent, {sha for sha in _BLAMED_COMMIT.findall(blame) if sha.strip("0")})


def _path_matches(path: str, glob: str) -> bool:
    """Return True if a repo-relative glob matches the path or any trailing part of it."""
    parts = PurePosixPath(path).parts
    return any(fnmatchcase("/".join(parts[start:]), glob) for start in range(len(parts)))


def _row_matches(row: TriggerRow, found: Facts) -> bool:
    value = str(row["value"])
    if row["kind"] == "change":
        return value in found.changes
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
    except (KeyError, TypeError, ValueError, OSError, subprocess.SubprocessError):
        return None


def surface(task_id: str, message: str, projects: list[str]) -> str | None:
    """Return a note naming the entities that strongly match the message and the task has not yet seen.

    At most _MAX_RECALLED entities are named, best match first, and each is recorded as seen.
    A blank message or an unreachable server surfaces nothing.
    """
    if not message.strip():
        return None
    args = {"projects": projects, "message": message, "limit": _SURFACE_CANDIDATES}
    try:
        matches = api_client.call("surface-relevant", args, timeout=_SERVER_TIMEOUT_SECONDS)
    except api_client.ApiError:
        return None
    by_key = {f"{match['project']}:{match['entity']}": match for match in matches}
    taken = take_unseen(task_id, "surfaced", list(by_key), _MAX_RECALLED)
    return _SURFACE_NOTE.format(entities=_lines([by_key[key] for key in taken])) if taken else None


def _lines(rows: list[TriggerRow]) -> str:
    """Return one line per row, naming its entity and project, then its content."""
    return "\n".join(f"- `{row['entity']}` ({row['project']}): {row['content']}" for row in rows)


def _recall(task_id: str, event: Mapping[str, object], kind: str) -> str | None:
    rows = trigger_rows()
    if not any(row.get("kind") == kind for row in rows):
        return None
    found = facts(event)
    if kind == "change":
        found = replace(found, changes=_edited_changes(event))
    matched = matches(rows, found, kind)
    by_hash = {str(row["content_hash"]): row for row in matched}
    taken = take_unseen(task_id, f"recalled_{kind}", list(by_hash), _MAX_RECALLED)
    if not taken:
        return None
    shown = [by_hash[key] for key in taken]
    template = _RECALL_BLOCK if kind == "pattern" else _RECALL_NOTE
    return template.format(observations=_lines(shown))
