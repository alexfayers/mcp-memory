"""Fixtures shared across the whole test suite."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from mcp_memory.storage import open_writable

if TYPE_CHECKING:
    from pathlib import Path

    from mcp_memory.storage import Storage


@pytest.fixture(autouse=True)
def _isolate_db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the config-resolved database and its on-disk markers at a per-test directory."""
    monkeypatch.setenv("MCP_MEMORY_DB_PATH", str(tmp_path / "memory.db"))


@pytest.fixture
def store(tmp_path: Path) -> Storage:
    """Create a fresh store-backed database for each test."""
    return open_writable(tmp_path / "test.db")
