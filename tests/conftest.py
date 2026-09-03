"""Fixtures shared across the whole test suite."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from mcp_memory.database import DatabaseManager

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def _isolate_db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the config-resolved database and its on-disk markers at a per-test directory."""
    monkeypatch.setenv("MCP_MEMORY_DB_PATH", str(tmp_path / "memory.db"))


@pytest.fixture
def db(tmp_path: Path) -> DatabaseManager:
    """Create a fresh database for each test."""
    return DatabaseManager(tmp_path / "test.db")
