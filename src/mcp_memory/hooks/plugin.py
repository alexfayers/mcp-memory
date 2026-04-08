"""Memory plugin for cline-hooks - provides memory tracking behaviour."""

from __future__ import annotations

from cline_hooks.core.plugin import HooksPlugin


class MemoryPlugin(HooksPlugin):
    """Plugin that provides memory tracking for the hook system."""
