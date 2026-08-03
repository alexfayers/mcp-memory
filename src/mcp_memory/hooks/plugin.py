"""Memory plugin for cline-hooks - provides memory tracking behaviour."""

from __future__ import annotations

import json
import os
import random
import socket
import sqlite3
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse

from cline_hooks.core.plugin import HookResult, HooksPlugin

from mcp_memory.config import get_db_path, get_memory_url, get_workspace_markers
from mcp_memory.database import DatabaseManager
from mcp_memory.hooks.review_tracker import (
    record_write,
    should_nudge,
)
from mcp_memory.hooks.review_tracker import (
    reset as reset_review,
)
from mcp_memory.hooks.tracker import (
    clear,
    has_scope_blocked,
    increment,
    mark_scope_blocked,
    reset,
    should_block,
)
from mcp_memory.path_resolver import normalize_path, resolve_project_for_path

_MEMORY_WRITE_TOOL_NAMES = frozenset(
    {
        "create_entities",
        "create_relations",
        "add_observations",
        "delete_entity",
        "delete_relation",
        "delete_observations",
        "set_entity_status",
    }
)

_MEMORY_READ_TOOL_NAMES = frozenset(
    {
        "search_nodes",
        "read_graph",
        "list_metadata",
        "search_all_projects",
        "get_entity_with_relations",
    }
)

_MEMORY_REMINDER_TOOLS = frozenset(
    {
        "replace_in_file",
        "write_to_file",
        "execute_command",
        "plan_mode_respond",
    }
)

_MEMORY_REMINDER_TEMPLATE = (
    "MEMORY UPDATE REQUIRED: Update the `{project}` project and `global`"
    " scopes in the memory server now.\n"
    "Record what you just did and why. One fact per observation."
)
_MEMORY_REMINDER_CHANCE = 0.6
_MEMORY_COOLDOWN_STEPS = 5
_MEMORY_BLOCK_TEMPLATE = (
    "MEMORY UPDATE REQUIRED: You have made many tool calls without updating memory. "
    "Update the `{project}` project and `global` scopes in the memory server before continuing."
)
_MEMORY_SERVER_DOWN_NOTE = (
    "Memory server appears to be unreachable ({url}) - skipping the memory-update "
    "block so you aren't stuck. Let the user know memory is currently unavailable."
)
_MEMORY_COMPLETION_REMINDER = (
    "REQUIRED before completing:\n"
    "1. Update `memory`\n"
    "2. One observation per fact (what changed, why, TODOs)"
)
_MEMORY_COMPACT_WARNING = (
    "Save any important context, decisions, or progress to memory NOW before it's lost."
)
_MEMORY_REVIEW_NUDGE = (
    "MEMORY REVIEW DUE: many memory writes have accumulated since the last review. "
    "Mention this to the user and let them run `/memory-review` when they choose - "
    "e.g. at the next lull (session wind-down, no active task). This fires "
    "periodically by design."
)
_AUTO_REGISTERED_NOTE = (
    "Registered this workspace to memory project `{project}` (path `{anchor}`), "
    "so the scope now persists for this directory."
)
_AUTO_REGISTER_UNKNOWN_NOTE = (
    "This workspace (`{anchor}`) maps to no known memory project and its name could not"
    " be matched to an existing one, so no scope was created. If it belongs to a project,"
    " register it with `set_metadata(project=..., kind='paths', values=['{anchor}'])`."
)

_DEFAULT_READ_ONLY_AGENT_TYPES = frozenset({"Explore", "Plan"})
_READ_ONLY_AGENTS_ENV = "MCP_MEMORY_READONLY_AGENTS"

_DEFAULT_FILE_EDIT_TOOL_NAMES = frozenset(
    {"Edit", "Write", "MultiEdit", "NotebookEdit", "replace_in_file", "write_to_file"}
)
_FILE_EDIT_TOOLS_ENV = "MCP_MEMORY_EDIT_TOOLS"
_EDIT_TOOL_WEIGHT = 0.25


def _read_only_agent_types() -> frozenset[str]:
    """Return the agent types exempt from the memory gate, including env overrides."""
    raw = os.environ.get(_READ_ONLY_AGENTS_ENV, "")
    extra = {name.strip() for name in raw.split(",") if name.strip()}
    return _DEFAULT_READ_ONLY_AGENT_TYPES | extra


def _is_exempt_agent(agent_type: str) -> bool:
    """Return True if a read-only subagent type is exempt from the memory gate."""
    return bool(agent_type) and agent_type in _read_only_agent_types()


def _file_edit_tool_names() -> frozenset[str]:
    """Return file-edit tool names, including MCP_MEMORY_EDIT_TOOLS extras."""
    raw = os.environ.get(_FILE_EDIT_TOOLS_ENV, "")
    extra = {name.strip() for name in raw.split(",") if name.strip()}
    return _DEFAULT_FILE_EDIT_TOOL_NAMES | extra


def _is_file_edit(tool_name: str) -> bool:
    """Return True if a tool call is a reduced-weight file-edit operation."""
    return _extract_mcp_suffix(tool_name) in _file_edit_tool_names()


def _is_subagent(agent_type: str) -> bool:
    """Return True if running inside a spawned subagent (any non-empty agent type).

    Memory-persistence discipline is a main-thread concern: a subagent's findings
    return to its parent, which persists them. Hard-blocking a subagent only
    deadlocks it (or drives junk gate-satisfying writes), so the block is never
    applied to subagents - only the main agent loop, which has no agent type.
    """
    return bool(agent_type)


class _ReminderChance:
    """Tracks the probability of triggering a memory reminder."""

    def __init__(self) -> None:
        self.chance: float = _MEMORY_REMINDER_CHANCE

    def step(self) -> None:
        """Increment the reminder chance by one cooldown step."""
        increment_amount = _MEMORY_REMINDER_CHANCE / _MEMORY_COOLDOWN_STEPS
        self.chance = min(_MEMORY_REMINDER_CHANCE, self.chance + increment_amount)

    def reset(self) -> None:
        """Reset the reminder chance to zero."""
        self.chance = 0.0


def _extract_mcp_suffix(tool_name: str) -> str:
    """Extract the bare tool name from a prefixed MCP tool call."""
    if "__" in tool_name:
        return tool_name.rsplit("__", 1)[-1]
    if tool_name.startswith("mcp_"):
        parts = tool_name.split("_", 2)
        if len(parts) == 3 and parts[1]:
            return parts[2]
    return tool_name


def _is_memory_write(tool_name: str, parameters: dict[str, object]) -> bool:
    """Check if a tool call is a memory write operation."""
    if tool_name == "use_mcp_tool":
        return str(parameters.get("tool_name", "")) in _MEMORY_WRITE_TOOL_NAMES
    return _extract_mcp_suffix(tool_name) in _MEMORY_WRITE_TOOL_NAMES


def _is_memory_read(tool_name: str, parameters: dict[str, object]) -> bool:
    """Check if a tool call is a read-only memory operation."""
    if tool_name == "use_mcp_tool":
        return str(parameters.get("tool_name", "")) in _MEMORY_READ_TOOL_NAMES
    return _extract_mcp_suffix(tool_name) in _MEMORY_READ_TOOL_NAMES


def _find_git_root(file_path: str) -> Path | None:
    """Return the nearest ancestor directory containing a .git entry, or None."""
    current = Path(file_path).resolve()
    if current.is_file():
        current = current.parent
    while current != current.parent:
        if (current / ".git").exists():
            return current
        current = current.parent
    return None


def _find_project_from_path(file_path: str) -> str | None:
    """Derive a project name by walking up from a file path to find a .git directory."""
    root = _find_git_root(file_path)
    return root.name if root else None


def _resolve_anchor(path: str) -> tuple[Path, str] | None:
    """Return the (anchor, candidate name) to register for a path, or None.

    The candidate name is the package's own repository-root folder name. The anchor
    is the path to register: normally the repository root, but when a configured
    workspace-marker directory (see config.get_workspace_markers) sits above the
    repository root, the anchor is that outer workspace root instead, so every
    sibling package under it resolves to one project scope.
    """
    git_root = _find_git_root(path)
    if git_root is None:
        return None
    anchor = git_root
    markers = get_workspace_markers()
    if markers:
        current = git_root.parent
        while current != current.parent:
            if any((current / marker).exists() for marker in markers):
                anchor = current
                break
            current = current.parent
    return anchor, git_root.name


def _registration_target(workspace_roots: list[str]) -> tuple[Path, str] | None:
    """Return the (anchor, candidate name) eligible for auto-registration, or None.

    None means no registration should happen: no workspace, the path is already mapped
    (never clobber), no repository root was found, or the anchor is the home directory.
    """
    if not workspace_roots:
        return None
    cwd = workspace_roots[0]
    if resolve_project_for_path(cwd) is not None:
        return None
    resolved = _resolve_anchor(cwd)
    if resolved is None or resolved[0] == Path.home().resolve():
        return None
    return resolved


def _resolve_project(path: str) -> str | None:
    """Resolve a path to a project, preferring registered paths over .git detection."""
    return resolve_project_for_path(path) or _find_project_from_path(path)


def _safe_project(anchor: Path, basename: str, db: DatabaseManager) -> str | None:
    """Return the project an anchor may be safely registered to, or None.

    Only identifies a project that already exists, never invents one:
    - if the anchor (or an ancestor) is already registered, reuse that project - this
      also means an existing mapping is never overwritten;
    - else if a sibling package under the anchor is already mapped to a single project,
      reuse it so the whole workspace collapses to that scope;
    - else if a project scope already exists whose name matches the repository-root
      folder name (case-insensitively), pin to that project;
    - otherwise return None so the caller prompts for deliberate registration.
    """
    existing = db.get_project_for_path(str(anchor))
    if existing:
        return existing
    anchor_norm = Path(normalize_path(str(anchor)))
    below = {
        project
        for project, registered in db.list_project_paths()
        if Path(registered).is_relative_to(anchor_norm)
    }
    if len(below) == 1:
        return next(iter(below))
    for project in db.list_projects():
        if project.casefold() == basename.casefold():
            return project
    return None


_SCOPE_MISMATCH_WARNING = (
    "WRONG SCOPE: You are writing to `{target}` but the current"
    ' workspace project is `{detected}`. Use `project="{detected}"`'
    ' for project-specific data, or `project="global"` for'
    " cross-project knowledge. If the write to `{target}` is"
    " intentional, run the exact same call again to proceed."
)


def _extract_memory_project(
    tool_name: str,
    parameters: dict[str, object],
) -> str | None:
    """Extract the project parameter from a memory write tool call."""
    args = _parse_mcp_arguments(tool_name, parameters)
    return str(args.get("project", "")) or None


def _parse_mcp_arguments(
    tool_name: str,
    parameters: dict[str, object],
) -> dict[str, object]:
    """Parse the arguments dict from an MCP tool call or direct call."""
    if tool_name == "use_mcp_tool":
        raw = parameters.get("arguments", "{}")
        if isinstance(raw, str):
            try:
                return dict(json.loads(raw))
            except (json.JSONDecodeError, TypeError):
                return {}
        if isinstance(raw, dict):
            return raw
    return parameters


def _workspace_entity_note(workspace_roots: list[str]) -> str | None:
    """Return the project memory entity note for the first workspace root."""
    if not workspace_roots:
        return None
    root = workspace_roots[0]
    basename = Path(root).name
    resolved = _resolve_project(root)
    name = resolved or basename
    note = f"The project memory entity for this workspace is `project/{name}`."
    if resolved and resolved != basename:
        note += f" (resolved from a registered path; folder is `{basename}`)"
    return note


def _resolved_project_set(workspace_roots: list[str]) -> list[str]:
    """Return [global, <repo-name>, *group siblings] for the current workspace.

    Group siblings come from the project_groups table (set via set_project_groups),
    so distinct project scopes can be scanned together without a hardcoded name or
    a cross-scope relation (relations are hard-scoped to one project). A DB failure
    degrades to [global, repo-name] rather than breaking task start.
    """
    projects = ["global"]
    if not workspace_roots:
        return projects
    repo_name = _resolve_project(workspace_roots[0]) or Path(workspace_roots[0]).name
    projects.append(repo_name)
    try:
        db = DatabaseManager(get_db_path())
        projects.extend(db.get_group_members(repo_name))
    except (sqlite3.Error, OSError):
        pass
    return projects


def _build_task_start_context(workspace_roots: list[str]) -> list[str]:
    """Build memory-related context notes for task start."""
    parts: list[str] = []
    note = _workspace_entity_note(workspace_roots)
    if note:
        parts.append(note)
    projects = _resolved_project_set(workspace_roots)
    project_list = ", ".join(f"`{project}`" for project in projects)
    parts.append(
        "REQUIRED before starting:\n"
        "1. `read_graph` on BOTH `global` and `<repo-name>` projects\n"
        f"2. For each of {project_list}, call "
        "`search_nodes(project=<name>, query='task', status='in-progress', compact=true)` "
        "and `search_nodes(project=<name>, query='task', status='planned', compact=true)` "
        "for the task summary "
        "(use `search_all_projects` instead only on explicit request for the full, "
        "unrestricted scan across every project)\n"
        "3. `search_nodes` for task keywords in `<repo-name>` project\n"
        "4. `get_entity_with_relations` on any relevant result"
    )
    return parts


_LIVENESS_TIMEOUT_SECONDS = 1.0


def _is_memory_server_reachable() -> bool:
    """Return True if a process is accepting connections on the memory server's host:port."""
    parsed = urlparse(get_memory_url())
    host, port = parsed.hostname or "localhost", parsed.port or 80
    try:
        with socket.create_connection((host, port), timeout=_LIVENESS_TIMEOUT_SECONDS):
            return True
    except OSError:
        return False


def _check_block(task_id: str, project_scope: str) -> HookResult | None:
    """Return a block result if the task has exceeded the memory update threshold."""
    if not should_block(task_id):
        return None
    if not _is_memory_server_reachable():
        return HookResult(notes=[_MEMORY_SERVER_DOWN_NOTE.format(url=get_memory_url())])
    return HookResult(block=_MEMORY_BLOCK_TEMPLATE.format(project=project_scope))


def _str_list(value: object) -> list[str]:
    """Coerce an object to a list of strings."""
    if isinstance(value, list):
        return [str(v) for v in value]
    return []


def _str_dict(value: object) -> dict[str, object]:
    """Coerce an object to a string-keyed dict."""
    if isinstance(value, dict):
        return value
    return {}


class MemoryPlugin(HooksPlugin):
    """Plugin that provides memory tracking for the hook system."""

    def __init__(self) -> None:
        self._reminder = _ReminderChance()
        self._project_scope = "unknown"
        self._handlers: dict[str, Callable[..., HookResult | None]] = {
            "TaskStart": self._on_task_start,
            "TaskCancel": self._on_task_end,
            "TaskComplete": self._on_task_end,
            "TaskResume": self._on_task_resume,
            "PreToolUse": self._on_pre_tool_use,
            "PreMcpToolUse": self._on_pre_mcp_tool_use,
            "PostToolUse": self._on_post_tool_use,
            "UserPromptSubmit": self._on_user_prompt_submit,
            "AttemptCompletion": lambda **_: HookResult(notes=[_MEMORY_COMPLETION_REMINDER]),
            "PreCompact": lambda **_: HookResult(notes=[_MEMORY_COMPACT_WARNING]),
        }

    def get_state_write_tool_names(self) -> frozenset[str]:
        """Return memory write tool names."""
        return _MEMORY_WRITE_TOOL_NAMES

    def on_hook(self, hook_name: str, **kwargs: object) -> HookResult | None:
        """Handle hook events for memory tracking."""
        handler = self._handlers.get(hook_name)
        if handler is None:
            return None
        return handler(**kwargs)

    def _on_task_start(self, **kwargs: object) -> HookResult:
        task_id = str(kwargs.get("task_id", ""))
        workspace_roots = _str_list(kwargs.get("workspace_roots", []))
        clear(task_id)
        self._reminder.reset()
        auto_note = self._maybe_auto_register(workspace_roots)
        if workspace_roots:
            self._project_scope = (
                _resolve_project(workspace_roots[0]) or Path(workspace_roots[0]).name
            )
        notes = _build_task_start_context(workspace_roots)
        if auto_note:
            notes.append(auto_note)
        return HookResult(notes=notes)

    def _maybe_auto_register(self, workspace_roots: list[str]) -> str | None:
        """Self-heal the workspace-to-project mapping, returning a note or None.

        Best-effort and non-destructive: registers the workspace only when its project
        can be identified safely (see _safe_project) and never overwrites an existing
        mapping or the home directory. A database failure is swallowed so task start is
        never broken. Returns None when a path is registered silently or nothing is done,
        and a guidance note when the workspace maps to no identifiable project.
        """
        target = _registration_target(workspace_roots)
        if target is None:
            return None
        anchor, basename = target
        try:
            db = DatabaseManager(get_db_path())
            project = _safe_project(anchor, basename, db)
            if project is None:
                return _AUTO_REGISTER_UNKNOWN_NOTE.format(anchor=anchor)
            db.add_project_path(project, str(anchor))
        except (sqlite3.Error, OSError):
            return None
        return _AUTO_REGISTERED_NOTE.format(project=project, anchor=anchor)

    def _on_task_end(self, **kwargs: object) -> None:
        clear(str(kwargs.get("task_id", "")))

    def _on_task_resume(self, **kwargs: object) -> HookResult | None:
        workspace_roots = _str_list(kwargs.get("workspace_roots", []))
        note = _workspace_entity_note(workspace_roots)
        return HookResult(notes=[note]) if note else None

    def _on_user_prompt_submit(self, **_kwargs: object) -> HookResult | None:
        """Nudge a memory-review once enough cumulative writes have accumulated."""
        if should_nudge():
            reset_review()
            return HookResult(notes=[_MEMORY_REVIEW_NUDGE])
        return None

    def _on_pre_tool_use(self, **kwargs: object) -> HookResult | None:
        agent_type = str(kwargs.get("agent_type", ""))
        if _is_exempt_agent(agent_type):
            return None
        task_id = str(kwargs.get("task_id", ""))
        tool_name = str(kwargs.get("tool_name", ""))
        parameters = _str_dict(kwargs.get("parameters", {}))
        self._derive_scope_from_workspace_roots(kwargs)
        if _is_memory_write(tool_name, parameters):
            return self._check_memory_scope(task_id, tool_name, parameters)
        if _is_memory_read(tool_name, parameters):
            return None
        if _is_subagent(agent_type):
            return None
        return _check_block(task_id, self._project_scope)

    def _on_pre_mcp_tool_use(self, **kwargs: object) -> HookResult | None:
        agent_type = str(kwargs.get("agent_type", ""))
        if _is_exempt_agent(agent_type):
            return None
        task_id = str(kwargs.get("task_id", ""))
        mcp_tool_name = str(kwargs.get("mcp_tool_name", ""))
        if mcp_tool_name in _MEMORY_WRITE_TOOL_NAMES:
            mcp_arguments = kwargs.get("mcp_arguments", "{}")
            params: dict[str, object] = {
                "tool_name": mcp_tool_name,
                "arguments": mcp_arguments,
            }
            return self._check_memory_scope(task_id, "use_mcp_tool", params)
        if mcp_tool_name in _MEMORY_READ_TOOL_NAMES:
            return None
        if _is_subagent(agent_type):
            return None
        return _check_block(task_id, self._project_scope)

    def _check_memory_scope(
        self,
        task_id: str,
        tool_name: str,
        parameters: dict[str, object],
    ) -> HookResult | None:
        """Block or warn if a memory write targets the wrong project scope."""
        target = _extract_memory_project(tool_name, parameters)
        if target and target != "global" and self._project_scope not in {"unknown", target}:
            message = _SCOPE_MISMATCH_WARNING.format(
                target=target,
                detected=self._project_scope,
            )
            if has_scope_blocked(task_id, target):
                return HookResult(notes=[message])
            mark_scope_blocked(task_id, target)
            return HookResult(block=message)
        return None

    def _on_post_tool_use(self, **kwargs: object) -> HookResult | None:
        agent_type = str(kwargs.get("agent_type", ""))
        if _is_exempt_agent(agent_type) or _is_subagent(agent_type):
            return None
        task_id = str(kwargs.get("task_id", ""))
        tool_name = str(kwargs.get("tool_name", ""))
        parameters = _str_dict(kwargs.get("parameters", {}))
        is_state_write = bool(kwargs.get("is_state_write", False))
        is_memory_write = _is_memory_write(tool_name, parameters)
        self._derive_scope_from_workspace_roots(kwargs)

        if is_state_write or is_memory_write:
            reset(task_id)
            self._reminder.reset()
            record_write()
            return None

        if _is_memory_read(tool_name, parameters):
            return None

        if _is_file_edit(tool_name):
            increment(task_id, _EDIT_TOOL_WEIGHT)
        else:
            increment(task_id)
        self._update_scope_from_parameters(tool_name, parameters)

        if tool_name in _MEMORY_REMINDER_TOOLS:
            self._reminder.step()
            if random.random() < self._reminder.chance:  # noqa: S311
                self._reminder.reset()
                reminder = _MEMORY_REMINDER_TEMPLATE.format(
                    project=self._project_scope,
                )
                return HookResult(notes=[reminder])

        return None

    def _derive_scope_from_workspace_roots(self, kwargs: dict[str, object]) -> None:
        """Derive project scope from workspace_roots when scope is unknown."""
        if self._project_scope != "unknown":
            return
        for root in _str_list(kwargs.get("workspace_roots", [])):
            detected = _resolve_project(root)
            if detected:
                self._project_scope = detected
                return

    def _update_scope_from_parameters(self, tool_name: str, parameters: dict[str, object]) -> None:
        """Update the cached project scope from file paths in tool parameters."""
        path_str = ""
        if tool_name in {"replace_in_file", "write_to_file", "read_file"}:
            path_str = str(parameters.get("path", ""))
        elif tool_name in {"Edit", "Write", "MultiEdit", "Read", "NotebookEdit"}:
            path_str = str(parameters.get("file_path", "") or parameters.get("notebook_path", ""))
        elif tool_name in {"execute_command", "execute_bash"}:
            path_str = str(parameters.get("working_dir", "") or parameters.get("cwd", ""))

        if path_str:
            detected = _resolve_project(path_str)
            if detected:
                self._project_scope = detected
