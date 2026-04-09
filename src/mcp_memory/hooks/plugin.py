"""Memory plugin for cline-hooks - provides memory tracking behaviour."""

from __future__ import annotations

import random
from collections.abc import Callable
from pathlib import Path

from cline_hooks.core.plugin import HookResult, HooksPlugin

from mcp_memory.hooks.tracker import clear, increment, reset, should_block

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

_MEMORY_REMINDER_TOOLS = frozenset(
    {
        "replace_in_file",
        "write_to_file",
        "execute_command",
        "plan_mode_respond",
    }
)

_MEMORY_REMINDER = (
    "MEMORY UPDATE REQUIRED: Update the project and global scopes in the memory server now.\n"
    "Record what you just did and why. One fact per observation."
)
_MEMORY_REMINDER_CHANCE = 0.6
_MEMORY_COOLDOWN_STEPS = 5
_MEMORY_BLOCK_MESSAGE = (
    "MEMORY UPDATE REQUIRED: You have made many tool calls without updating memory. "
    "Update the project and global scopes in the memory server before continuing."
)
_MEMORY_COMPLETION_REMINDER = (
    "REQUIRED before completing:\n"
    "1. Update `memory`\n"
    "2. One observation per fact (what changed, why, TODOs)"
)
_MEMORY_COMPACT_WARNING = (
    "Save any important context, decisions, or progress to memory NOW before it's lost."
)


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


def _is_memory_write(tool_name: str, parameters: dict[str, object]) -> bool:
    """Check if a tool call is a memory write operation."""
    if tool_name == "use_mcp_tool":
        return str(parameters.get("tool_name", "")) in _MEMORY_WRITE_TOOL_NAMES
    return tool_name in _MEMORY_WRITE_TOOL_NAMES


def _workspace_entity_note(workspace_roots: list[str]) -> str | None:
    """Return the project memory entity note for the first workspace root."""
    workspace_name = Path(workspace_roots[0]).name if workspace_roots else None
    if workspace_name:
        return f"The project memory entity for this workspace is `project/{workspace_name}`."
    return None


def _build_task_start_context(workspace_roots: list[str]) -> list[str]:
    """Build memory-related context notes for task start."""
    parts: list[str] = []
    note = _workspace_entity_note(workspace_roots)
    if note:
        parts.append(note)
    parts.append(
        "REQUIRED before starting:\n"
        "1. `read_graph` on BOTH `memory`\n"
        "2. `search_nodes` for task keywords in both servers\n"
        "3. `search_nodes` for `user-preferences` in global `memory`\n"
        "4. `search_nodes` for `task/*` in `memory` (pending TODOs)\n"
        "5. `search_related_nodes` on any relevant result\n"
        "6. Include a `task_progress` checklist in your first tool call"
    )
    return parts


def _check_block(task_id: str) -> HookResult | None:
    """Return a block result if the task has exceeded the memory update threshold."""
    if should_block(task_id):
        return HookResult(block=_MEMORY_BLOCK_MESSAGE)
    return None


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
        self._handlers: dict[str, Callable[..., HookResult | None]] = {
            "TaskStart": self._on_task_start,
            "TaskCancel": self._on_task_end,
            "TaskComplete": self._on_task_end,
            "TaskResume": self._on_task_resume,
            "PreToolUse": self._on_pre_tool_use,
            "PreMcpToolUse": self._on_pre_mcp_tool_use,
            "PostToolUse": self._on_post_tool_use,
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
        return HookResult(notes=_build_task_start_context(workspace_roots))

    def _on_task_end(self, **kwargs: object) -> None:
        clear(str(kwargs.get("task_id", "")))

    def _on_task_resume(self, **kwargs: object) -> HookResult | None:
        workspace_roots = _str_list(kwargs.get("workspace_roots", []))
        note = _workspace_entity_note(workspace_roots)
        return HookResult(notes=[note]) if note else None

    def _on_pre_tool_use(self, **kwargs: object) -> HookResult | None:
        task_id = str(kwargs.get("task_id", ""))
        tool_name = str(kwargs.get("tool_name", ""))
        parameters = _str_dict(kwargs.get("parameters", {}))
        if _is_memory_write(tool_name, parameters):
            return None
        return _check_block(task_id)

    def _on_pre_mcp_tool_use(self, **kwargs: object) -> HookResult | None:
        task_id = str(kwargs.get("task_id", ""))
        mcp_tool_name = str(kwargs.get("mcp_tool_name", ""))
        if mcp_tool_name in _MEMORY_WRITE_TOOL_NAMES:
            return None
        return _check_block(task_id)

    def _on_post_tool_use(self, **kwargs: object) -> HookResult | None:
        task_id = str(kwargs.get("task_id", ""))
        tool_name = str(kwargs.get("tool_name", ""))
        is_state_write = bool(kwargs.get("is_state_write", False))

        if is_state_write:
            reset(task_id)
            self._reminder.reset()
            return None

        increment(task_id)

        if tool_name in _MEMORY_REMINDER_TOOLS:
            self._reminder.step()
            if random.random() < self._reminder.chance:  # noqa: S311
                self._reminder.reset()
                return HookResult(notes=[_MEMORY_REMINDER])

        return None
