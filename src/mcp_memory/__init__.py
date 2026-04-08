"""SQLite-backed persistent memory MCP server."""

from __future__ import annotations

from pathlib import Path


def get_prompts_dir() -> Path:
    """Return the path to this package's prompts overlay directory."""
    return Path(__file__).parent.parent.parent / "prompts"
