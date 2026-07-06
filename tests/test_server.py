"""Tests for server-level logic."""

from __future__ import annotations

from pathlib import Path

import pytest

from mcp_memory import server
from mcp_memory.database import DatabaseManager
from mcp_memory.models import Relation
from mcp_memory.path_resolver import normalize_path
from mcp_memory.server import _GLOBAL_PROJECT, _ensure_project_root, _validate_and_extract_relations


@pytest.fixture
def db(tmp_path: Path) -> DatabaseManager:
    """Create a fresh database for each test."""
    return DatabaseManager(tmp_path / "test.db")


@pytest.fixture
def server_db(tmp_path: Path) -> DatabaseManager:
    """Point the server's module-level db singleton at a fresh database."""
    manager = DatabaseManager(tmp_path / "server.db")
    original = server._db
    server._db = manager
    yield manager
    server._db = original


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
                "proj", [{"name": "x", "entityType": "changelog", "observations": []}]
            )

    def test_accepts_user_preferences_without_relation(self) -> None:
        result = _validate_and_extract_relations(
            "proj",
            [{"name": "user-preferences/x", "entityType": "user-preferences", "observations": []}],
        )
        assert result == []

    def test_pattern_requires_relation(self) -> None:
        with pytest.raises(ValueError, match="requires at least one relation"):
            _validate_and_extract_relations(
                "proj", [{"name": "pattern/x", "entityType": "pattern", "observations": []}]
            )

    def test_pattern_with_relation_accepted(self) -> None:
        result = _validate_and_extract_relations(
            "proj",
            [
                {
                    "name": "pattern/x",
                    "entityType": "pattern",
                    "observations": [],
                    "relations": [{"target": "project/foo", "type": "used-in"}],
                }
            ],
        )
        assert len(result) == 1

    def test_rejects_name_without_type_prefix(self) -> None:
        with pytest.raises(ValueError, match="must start with"):
            _validate_and_extract_relations(
                "proj",
                [
                    {
                        "name": "my-task",
                        "entityType": "task",
                        "observations": ["o"],
                        "relations": [{"target": "project/proj", "type": "belongs-to"}],
                    }
                ],
            )

    def test_rejects_wrong_type_prefix(self) -> None:
        with pytest.raises(ValueError, match="must start with"):
            _validate_and_extract_relations(
                "proj",
                [
                    {
                        "name": "feature/x",
                        "entityType": "task",
                        "observations": ["o"],
                        "relations": [{"target": "project/proj", "type": "belongs-to"}],
                    }
                ],
            )

    def test_accepts_correct_prefix(self) -> None:
        result = _validate_and_extract_relations(
            "proj",
            [
                {
                    "name": "task/x",
                    "entityType": "task",
                    "observations": ["o"],
                    "relations": [{"target": "project/proj", "type": "belongs-to"}],
                }
            ],
        )
        assert len(result) == 1

    def test_rejects_empty_name(self) -> None:
        with pytest.raises(ValueError, match="must start with"):
            _validate_and_extract_relations(
                "proj",
                [
                    {
                        "name": "",
                        "entityType": "task",
                        "observations": ["o"],
                        "relations": [{"target": "project/proj", "type": "belongs-to"}],
                    }
                ],
            )

    def test_bare_prefix_accepted(self) -> None:
        result = _validate_and_extract_relations(
            "proj",
            [
                {
                    "name": "task/",
                    "entityType": "task",
                    "observations": ["o"],
                    "relations": [{"target": "project/proj", "type": "belongs-to"}],
                }
            ],
        )
        assert len(result) == 1

    def test_rejects_misnamed_project_entity(self) -> None:
        """A second/mis-named project entity is rejected: closes the entityType=project loophole."""
        with pytest.raises(ValueError, match="project/proj"):
            _validate_and_extract_relations(
                "proj", [{"name": "project/wrong", "entityType": "project", "observations": ["o"]}]
            )

    def test_accepts_matching_project_root(self) -> None:
        result = _validate_and_extract_relations(
            "proj", [{"name": "project/proj", "entityType": "project", "observations": ["o"]}]
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


class TestRelationTypeValidation:
    def test_normalizes_variant_relation_type(self) -> None:
        result = _validate_and_extract_relations(
            "proj",
            [
                {
                    "name": "task/t",
                    "entityType": "task",
                    "observations": ["obs"],
                    "relations": [{"target": "project/foo", "type": "related_to"}],
                }
            ],
        )
        assert result[0].relation_type == "relates-to"

    def test_collapses_long_tail_relation_type(self) -> None:
        result = _validate_and_extract_relations(
            "proj",
            [
                {
                    "name": "task/t",
                    "entityType": "task",
                    "observations": ["obs"],
                    "relations": [{"target": "project/foo", "type": "extends"}],
                }
            ],
        )
        assert result[0].relation_type == "implements"

    def test_rejects_unknown_relation_type(self) -> None:
        with pytest.raises(ValueError, match="Invalid relation type"):
            _validate_and_extract_relations(
                "proj",
                [
                    {
                        "name": "task/t",
                        "entityType": "task",
                        "observations": ["obs"],
                        "relations": [{"target": "project/foo", "type": "frobnicates"}],
                    }
                ],
            )


class TestRelationTypeWarnings:
    def _seed_nonconforming(self, db: DatabaseManager) -> None:
        db.create_entities(
            "proj",
            [
                {"name": "a", "entityType": "task", "observations": ["x"]},
                {"name": "b", "entityType": "project", "observations": ["y"]},
            ],
        )
        db._db.execute("INSERT OR IGNORE INTO relation_types (name) VALUES ('legacy_thing')")
        src = db._db.execute("SELECT id FROM entities WHERE name = 'a'").fetchone()[0]
        tgt = db._db.execute("SELECT id FROM entities WHERE name = 'b'").fetchone()[0]
        type_id = db._db.execute(
            "SELECT id FROM relation_types WHERE name = 'legacy_thing'"
        ).fetchone()[0]
        db._db.execute(
            "INSERT INTO relations (source_id, target_id, relation_type_id) VALUES (?, ?, ?)",
            (src, tgt, type_id),
        )
        db._db.commit()

    def test_warning_present_for_nonconforming(self, server_db: DatabaseManager) -> None:
        self._seed_nonconforming(server_db)
        result = server.get_entity_with_relations("proj", "a")
        assert "legacy_thing" in result["relationTypeWarnings"]

    def test_no_warning_for_clean_data(self, server_db: DatabaseManager) -> None:
        server_db.create_entities(
            "proj",
            [
                {"name": "a", "entityType": "task", "observations": ["x"]},
                {"name": "b", "entityType": "project", "observations": ["y"]},
            ],
        )
        server_db.create_relations(
            "proj", [Relation(source="a", target="b", relation_type="belongs-to")]
        )
        result = server.get_entity_with_relations("proj", "a")
        assert "relationTypeWarnings" not in result


class TestInlineRelations:
    def test_auto_fills_source_from_entity_name(self) -> None:
        result = _validate_and_extract_relations(
            "proj",
            [
                {
                    "name": "task/my-task",
                    "entityType": "task",
                    "observations": ["obs"],
                    "relations": [{"target": "project/foo", "type": "belongs-to"}],
                }
            ],
        )
        assert len(result) == 1
        assert result[0].source == "task/my-task"
        assert result[0].target == "project/foo"
        assert result[0].relation_type == "belongs-to"

    def test_explicit_source_preserved(self) -> None:
        result = _validate_and_extract_relations(
            "proj",
            [
                {
                    "name": "task/my-task",
                    "entityType": "task",
                    "observations": ["obs"],
                    "relations": [
                        {"source": "other/entity", "target": "project/foo", "type": "belongs-to"}
                    ],
                }
            ],
        )
        assert result[0].source == "other/entity"

    def test_multiple_relations(self) -> None:
        result = _validate_and_extract_relations(
            "proj",
            [
                {
                    "name": "task/t",
                    "entityType": "task",
                    "observations": ["obs"],
                    "relations": [
                        {"target": "project/a", "type": "belongs-to"},
                        {"target": "feature/b", "type": "implements"},
                    ],
                }
            ],
        )
        assert len(result) == 2
        assert all(r.source == "task/t" for r in result)

    def test_accepts_relation_type_key(self) -> None:
        result = _validate_and_extract_relations(
            "proj",
            [
                {
                    "name": "task/t",
                    "entityType": "task",
                    "observations": ["obs"],
                    "relations": [
                        {"target": "project/a", "relation_type": "belongs-to"},
                    ],
                }
            ],
        )
        assert len(result) == 1
        assert result[0].relation_type == "belongs-to"

    def test_type_key_takes_precedence_over_relation_type(self) -> None:
        result = _validate_and_extract_relations(
            "proj",
            [
                {
                    "name": "task/t",
                    "entityType": "task",
                    "observations": ["obs"],
                    "relations": [
                        {
                            "target": "project/a",
                            "type": "implements",
                            "relation_type": "belongs-to",
                        },
                    ],
                }
            ],
        )
        assert result[0].relation_type == "implements"

    def test_missing_both_type_keys_raises(self) -> None:
        with pytest.raises(KeyError, match=r"type.*relation_type"):
            _validate_and_extract_relations(
                "proj",
                [
                    {
                        "name": "task/t",
                        "entityType": "task",
                        "observations": ["obs"],
                        "relations": [{"target": "project/a"}],
                    }
                ],
            )


class TestListProjects:
    def test_returns_all_projects(self, db: DatabaseManager) -> None:
        db.create_entities(
            "alpha", [{"name": "e1", "entityType": "project", "observations": ["a"]}]
        )
        db.create_entities("beta", [{"name": "e2", "entityType": "project", "observations": ["b"]}])
        assert db.list_projects() == ["alpha", "beta"]

    def test_empty_database(self, db: DatabaseManager) -> None:
        assert db.list_projects() == []


class TestVoteEntityTool:
    def test_valid_vote_returns_new_score(self, server_db: DatabaseManager) -> None:
        server_db.create_entities(
            "proj", [{"name": "e1", "entityType": "task", "observations": ["x"]}]
        )
        server.vote_entity("proj", "e1", 1)
        assert server.vote_entity("proj", "e1", 1) == {
            "name": "e1",
            "project": "proj",
            "vote_score": 2,
        }

    def test_invalid_vote_returns_error(self, server_db: DatabaseManager) -> None:
        server_db.create_entities(
            "proj", [{"name": "e1", "entityType": "task", "observations": ["x"]}]
        )
        assert "error" in server.vote_entity("proj", "e1", 5)

    def test_missing_entity_returns_error(self, server_db: DatabaseManager) -> None:
        assert "error" in server.vote_entity("proj", "nope", 1)


class TestProjectPathTools:
    def test_set_project_paths_registers_and_creates_root(
        self, server_db: DatabaseManager, tmp_path: Path
    ) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        result = server.set_project_paths("platform", [str(repo)])
        assert result["paths"]
        assert server_db.get_entity("platform", "project/platform").entity_type == "project"

    def test_set_project_paths_returns_only_own_paths(
        self, server_db: DatabaseManager, tmp_path: Path
    ) -> None:
        repo_a = tmp_path / "a"
        repo_b = tmp_path / "b"
        repo_a.mkdir()
        repo_b.mkdir()
        server.set_project_paths("first", [str(repo_a)])
        result = server.set_project_paths("second", [str(repo_b)])
        assert result["paths"] == [normalize_path(str(repo_b))]

    def test_get_project_for_path_hit_and_miss(
        self, server_db: DatabaseManager, tmp_path: Path
    ) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        server.set_project_paths("platform", [str(repo)])
        assert server.get_project_for_path(str(repo / "x.py")) == {"project": "platform"}
        assert server.get_project_for_path(str(tmp_path / "other")) == {"project": None}

    def test_list_project_paths_returns_mappings(
        self, server_db: DatabaseManager, tmp_path: Path
    ) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        server.set_project_paths("platform", [str(repo)])
        mappings = server.list_project_paths()["mappings"]
        assert mappings == [{"project": "platform", "path": normalize_path(str(repo))}]

    def test_duplicate_path_returns_error(self, server_db: DatabaseManager, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        server.set_project_paths("platform", [str(repo)])
        result = server.set_project_paths("other", [str(repo)])
        assert "error" in result

    def test_delete_project_removes_empty_scope(
        self, server_db: DatabaseManager, tmp_path: Path
    ) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        server.set_project_paths("doomed", [str(repo)])
        server_db.delete_entity("doomed", "project/doomed")
        assert server.delete_project("doomed") == {"message": "Deleted project 'doomed'."}
        assert "doomed" not in server_db.list_projects()

    def test_delete_project_non_empty_returns_error(self, server_db: DatabaseManager) -> None:
        server.set_project_paths("busy", [])
        result = server.delete_project("busy")
        assert "error" in result

    def test_move_project_entities(self, server_db: DatabaseManager) -> None:
        server_db.create_entities(
            "src", [{"name": "e1", "entityType": "task", "observations": ["a"]}]
        )
        server_db.create_entities(
            "dst", [{"name": "d0", "entityType": "task", "observations": ["x"]}]
        )
        assert server.move_project_entities("src", "dst") == {
            "message": "Moved 1 entities from 'src' to 'dst'.",
            "moved": 1,
        }
        assert server_db.get_entity("dst", "e1").observations == ["a"]

    def test_move_project_entities_collision_returns_error(
        self, server_db: DatabaseManager
    ) -> None:
        server_db.create_entities(
            "src", [{"name": "dup", "entityType": "task", "observations": ["a"]}]
        )
        server_db.create_entities(
            "dst", [{"name": "dup", "entityType": "task", "observations": ["b"]}]
        )
        assert "error" in server.move_project_entities("src", "dst")

    def test_get_paths_for_project_hit_and_miss(
        self, server_db: DatabaseManager, tmp_path: Path
    ) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        server.set_project_paths("platform", [str(repo)])
        assert server.get_paths_for_project("platform") == {"paths": [normalize_path(str(repo))]}
        assert server.get_paths_for_project("ghost") == {"paths": []}

    def test_get_paths_for_entity_groups_by_project(
        self, server_db: DatabaseManager, tmp_path: Path
    ) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        server_db.create_entities(
            "platform", [{"name": "shared", "entityType": "task", "observations": ["o"]}]
        )
        server.set_project_paths("platform", [str(repo)])
        assert server.get_paths_for_entity("shared") == {
            "matches": [{"project": "platform", "paths": [normalize_path(str(repo))]}]
        }

    def test_get_paths_for_entity_missing_returns_empty_matches(
        self, server_db: DatabaseManager
    ) -> None:
        assert server.get_paths_for_entity("nope") == {"matches": []}


class TestSearchTools:
    def test_search_nodes_match_all_narrows_results(self, server_db: DatabaseManager) -> None:
        server_db.create_entities(
            "proj",
            [
                {"name": "a", "entityType": "task", "observations": ["alpha only"]},
                {"name": "both", "entityType": "task", "observations": ["alpha beta"]},
            ],
        )
        default = server.search_nodes("proj", "alpha beta")
        strict = server.search_nodes("proj", "alpha beta", match_all=True)
        assert {e.name for e in default["entities"]} == {"a", "both"}
        assert {e.name for e in strict["entities"]} == {"both"}

    def test_search_all_projects_match_all_narrows_results(
        self, server_db: DatabaseManager
    ) -> None:
        server_db.create_entities(
            "p1", [{"name": "a", "entityType": "task", "observations": ["alpha only"]}]
        )
        server_db.create_entities(
            "p2", [{"name": "both", "entityType": "task", "observations": ["alpha beta"]}]
        )
        default = server.search_all_projects("alpha beta")["results"]
        strict = server.search_all_projects("alpha beta", match_all=True)["results"]
        assert set(default) == {"p1", "p2"}
        assert set(strict) == {"p2"}
