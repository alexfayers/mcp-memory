"""Shared configuration for the mcp-memory package."""

from __future__ import annotations

import os
from pathlib import Path

_DEFAULT_DB_PATH = "~/.local/share/mcp-memory/memory.db"


def get_db_path() -> Path:
    """Return the resolved database file path."""
    return Path(os.environ.get("MCP_MEMORY_DB_PATH", _DEFAULT_DB_PATH)).expanduser()


def get_data_dir() -> Path:
    """Return the directory containing the database file."""
    return get_db_path().parent
