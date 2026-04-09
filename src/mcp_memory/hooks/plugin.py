"""Memory plugin for cline-hooks - provides memory tracking behaviour."""

from __future__ import annotations

import random

from cline_hooks.core.plugin import HooksPlugin

from mcp_memory.hooks.tracker import clear, increment, reset, should_block

_MEMORY_WRITE_TOOL_NAMES = frozenset(
    {
        "create_entities",
        "create_relations",
        "add_observations",
        "delete_entity",
        "delete_relation",
        "delete_observations",
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


class MemoryPlugin(HooksPlugin):
    """Plugin that provides memory tracking for the hook system."""

    def __init__(self) -> None:
        self._reminder = _ReminderChance()

    def _is_memory_write(self, tool_name: str, parameters: dict[str, object]) -> bool:
        """Check if a tool call is a memory write operation."""
        if tool_name == "use_mcp_tool":
            inner_tool = str(parameters.get("tool_name", ""))
            return inner_tool in _MEMORY_WRITE_TOOL_NAMES
        return tool_name in _MEMORY_WRITE_TOOL_NAMES

    def validate_tool(
        self,
        task_id: str,
        tool_name: str,
        parameters: dict[str, object],
    ) -> str | None:
        """Block all tool calls if memory has not been updated recently."""
        if self._is_memory_write(tool_name, parameters):
            return None
        if should_block(task_id):
            return _MEMORY_BLOCK_MESSAGE
        return None

    def on_post_tool_use(
        self,
        task_id: str,
        tool_name: str,
        is_memory_write: bool,  # noqa: FBT001
    ) -> str | None:
        """Track tool calls and emit memory reminders."""
        if is_memory_write:
            reset(task_id)
            self._reminder.reset()
            return None

        increment(task_id)

        if tool_name in _MEMORY_REMINDER_TOOLS:
            self._reminder.step()
            if random.random() < self._reminder.chance:  # noqa: S311
                self._reminder.reset()
                return _MEMORY_REMINDER

        return None

    def on_task_start(self, task_id: str) -> None:
        """Clear tracking state for a new task."""
        clear(task_id)
        self._reminder.reset()

    def on_task_end(self, task_id: str) -> None:
        """Clear tracking state when a task ends."""
        clear(task_id)
