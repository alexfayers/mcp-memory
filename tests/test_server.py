"""Tests for server-level logic."""

from __future__ import annotations

from pathlib import Path

import pytest

from mcp_memory.database import DatabaseManager
from mcp_memory.server import _GLOBAL_PROJECT, _ensure_project_root, _validate_and_extract_relations


@pytest.fixture
def db(tmp_path: Path) -> DatabaseManager:
    """Create a fresh database for each test."""
    return DatabaseManager(tmp_path / "test.db")


class TestEnsureProjectRoot:
    def test_creates_root_entity(self, db: DatabaseManager) -> None:
        _ensure_project_root(db, "my-project")
        entity = db.get_entity("my-project", "project/my-project")
        assert entity.entity_type == "project"

    def test_idempotent(self, db: DatabaseManager) -> None:
        _ensure_project_root(db, "my-project")
        _ensure_project_root(db, "my-project")
        entity = db.get_entity("my-project", "project/my-project")
        assert entity.entity_type == "project"

    def test_skips_global(self, db: DatabaseManager) -> None:
        _ensure_project_root(db, "global")
        with pytest.raises(ValueError, match="not found"):
            db.get_entity("global", "project/global")


class TestValidateEntityTypes:
    def test_rejects_invalid_type(self) -> None:
        with pytest.raises(ValueError, match="Invalid entity type"):
            _validate_and_extract_relations(
                [{"name": "x", "entityType": "changelog", "observations": []}]
            )

    def test_accepts_valid_type(self) -> None:
        result = _validate_and_extract_relations(
            [{"name": "x", "entityType": "pattern", "observations": []}]
        )
        assert result == []


class TestScopeUniqueness:
    def test_rejects_project_entity_in_global_if_exists_in_project(
        self, db: DatabaseManager
    ) -> None:
        entity = [{"name": "task/abc", "entityType": "task", "observations": ["x"]}]
        db.create_entities("my-proj", entity)
        assert db.entity_exists_in_project("task/abc", "my-proj")
        conflict = db.entity_exists_outside_project("task/abc", _GLOBAL_PROJECT)
        assert conflict == "my-proj"

    def test_allows_same_name_in_two_project_scopes(self, db: DatabaseManager) -> None:
        entity = [{"name": "task/abc", "entityType": "task", "observations": ["x"]}]
        db.create_entities("proj-a", entity)
        assert db.entity_exists_outside_project("task/abc", "proj-b") == "proj-a"
        # But the check only applies to global, so proj-b should be allowed
        assert not db.entity_exists_in_project("task/abc", _GLOBAL_PROJECT)
