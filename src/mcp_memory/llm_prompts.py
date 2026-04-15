"""Prompt overlay for llm-prompts."""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path


def get_prompts_dir() -> Path:
    """Return the path to this package's prompt overlay directory."""
    return Path(str(files("mcp_memory") / "prompts"))
