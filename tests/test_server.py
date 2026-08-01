"""Tests for server-level logic."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from mcp_memory import server
from mcp_memory.database import DatabaseManager, _hash_observation
from mcp_memory.models import Entity, Relation
from mcp_memory.path_resolver import normalize_path
from mcp_memory.server import _GLOBAL_PROJECT, _ensure_project_root, _validate_and_extract_relations
from tests import obs_contents


@pytest.fixture
def db(tmp_path: Path) -> DatabaseManager:
    """Create a fresh database for each test."""
    return DatabaseManager(tmp_path / "test.db")


@pytest.fixture
def server_db(tmp_path: Path) -> Iterator[DatabaseManager]:
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

    def test_user_preferences_requires_relation(self) -> None:
        with pytest.raises(ValueError, match="requires at least one relation"):
            _validate_and_extract_relations(
                "proj",
                [
                    {
                        "name": "user-preferences/x",
                        "entityType": "user-preferences",
                        "observations": [],
                    }
                ],
            )

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
        entity: list[dict[str, object]] = [
            {"name": "task/abc", "entityType": "task", "observations": ["x"]}
        ]
        db.create_entities("my-proj", entity)
        assert db.entity_exists_in_project("task/abc", "my-proj")
        conflict = db.entity_exists_outside_project("task/abc", _GLOBAL_PROJECT)
        assert conflict == "my-proj"

    def test_allows_same_name_in_two_project_scopes(self, db: DatabaseManager) -> None:
        entity: list[dict[str, object]] = [
            {"name": "task/abc", "entityType": "task", "observations": ["x"]}
        ]
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
                {"name": "a", "entityType": "feature", "observations": ["x"]},
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


class TestRuntimePolicyErrors:
    def test_create_relations_returns_error_for_task_project_belongs_to(
        self, server_db: DatabaseManager
    ) -> None:
        server_db.create_entities(
            "proj",
            [
                {"name": "task/a", "entityType": "task", "observations": ["x"]},
                {"name": "project/proj", "entityType": "project", "observations": ["y"]},
            ],
        )
        result = server.create_relations(
            "proj",
            [{"source": "task/a", "target": "project/proj", "type": "belongs-to"}],
        )
        assert "error" in result

    def test_create_entities_returns_error_for_strict_project_scoped_user_preferences(
        self, monkeypatch: pytest.MonkeyPatch, server_db: DatabaseManager
    ) -> None:
        monkeypatch.setenv("MCP_MEMORY_STRICT_POLICY", "true")
        result = server.create_entities(
            "proj",
            [
                {
                    "name": "user-preferences/local",
                    "entityType": "user-preferences",
                    "observations": ["x"],
                    "relations": [{"target": "project/proj", "type": "belongs-to"}],
                }
            ],
        )
        assert "error" in result


class TestListProjects:
    def test_returns_all_projects(self, db: DatabaseManager) -> None:
        db.create_entities(
            "alpha", [{"name": "e1", "entityType": "project", "observations": ["a"]}]
        )
        db.create_entities("beta", [{"name": "e2", "entityType": "project", "observations": ["b"]}])
        assert db.list_projects() == ["alpha", "beta"]

    def test_empty_database(self, db: DatabaseManager) -> None:
        assert db.list_projects() == []


class TestSetEntityStatusTool:
    def test_bloat_warning_when_resolved_over_ceiling(self, server_db: DatabaseManager) -> None:
        server_db.create_entities(
            "proj", [{"name": "e1", "entityType": "task", "observations": ["a", "b", "c", "d"]}]
        )
        result = server.set_entity_status("proj", "e1", "resolved")
        assert "bloatWarning" in result

    def test_no_bloat_warning_at_or_below_ceiling(self, server_db: DatabaseManager) -> None:
        server_db.create_entities(
            "proj", [{"name": "e1", "entityType": "task", "observations": ["a", "b", "c"]}]
        )
        result = server.set_entity_status("proj", "e1", "resolved")
        assert "bloatWarning" not in result

    def test_no_bloat_warning_for_non_resolved_status(self, server_db: DatabaseManager) -> None:
        server_db.create_entities(
            "proj", [{"name": "e1", "entityType": "task", "observations": ["a", "b", "c", "d"]}]
        )
        result = server.set_entity_status("proj", "e1", "in-progress")
        assert "bloatWarning" not in result

    def test_missing_entity_returns_error(self, server_db: DatabaseManager) -> None:
        assert "error" in server.set_entity_status("proj", "nope", "resolved")


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


class TestVoteObservationTool:
    def test_valid_vote_by_content_returns_new_score(self, server_db: DatabaseManager) -> None:
        server_db.create_entities(
            "proj", [{"name": "e1", "entityType": "task", "observations": ["x"]}]
        )
        server.vote_observation("proj", "e1", 1, observation="x")
        assert server.vote_observation("proj", "e1", 1, observation="x") == {
            "entityName": "e1",
            "project": "proj",
            "observation": "x",
            "observationHash": None,
            "vote_score": 2,
        }

    def test_valid_vote_by_hash(self, server_db: DatabaseManager) -> None:
        server_db.create_entities(
            "proj", [{"name": "e1", "entityType": "task", "observations": ["x"]}]
        )
        content_hash = server_db.get_entity("proj", "e1").observations[0].content_hash
        result = server.vote_observation("proj", "e1", 1, observationHash=content_hash)
        assert result["vote_score"] == 1
        assert result["observationHash"] == content_hash

    def test_neither_addressing_returns_error(self, server_db: DatabaseManager) -> None:
        server_db.create_entities(
            "proj", [{"name": "e1", "entityType": "task", "observations": ["x"]}]
        )
        assert "error" in server.vote_observation("proj", "e1", 1)

    def test_both_addressing_returns_error(self, server_db: DatabaseManager) -> None:
        server_db.create_entities(
            "proj", [{"name": "e1", "entityType": "task", "observations": ["x"]}]
        )
        content_hash = server_db.get_entity("proj", "e1").observations[0].content_hash
        assert "error" in server.vote_observation(
            "proj", "e1", 1, observation="x", observationHash=content_hash
        )

    def test_invalid_vote_returns_error(self, server_db: DatabaseManager) -> None:
        server_db.create_entities(
            "proj", [{"name": "e1", "entityType": "task", "observations": ["x"]}]
        )
        assert "error" in server.vote_observation("proj", "e1", 5, observation="x")

    def test_missing_observation_returns_error(self, server_db: DatabaseManager) -> None:
        server_db.create_entities(
            "proj", [{"name": "e1", "entityType": "task", "observations": ["x"]}]
        )
        assert "error" in server.vote_observation("proj", "e1", 1, observation="nope")


class TestAddObservationsTool:
    def test_returns_hashes_of_new_observations(self, server_db: DatabaseManager) -> None:
        server_db.create_entities(
            "proj", [{"name": "e1", "entityType": "task", "observations": ["a"]}]
        )
        result = server.add_observations("proj", "e1", ["b", "c"])
        assert result["count"] == 2
        assert result["hashes"] == [_hash_observation("b"), _hash_observation("c")]


class TestDeleteObservationsTool:
    def test_delete_by_content(self, server_db: DatabaseManager) -> None:
        server_db.create_entities(
            "proj", [{"name": "e1", "entityType": "task", "observations": ["a", "b"]}]
        )
        result = server.delete_observations("proj", "e1", observations=["a"])
        assert result["count"] == 1
        assert obs_contents(server_db.get_entity("proj", "e1")) == ["b"]

    def test_delete_by_hash(self, server_db: DatabaseManager) -> None:
        server_db.create_entities(
            "proj", [{"name": "e1", "entityType": "task", "observations": ["a", "b"]}]
        )
        result = server.delete_observations(
            "proj", "e1", observationHashes=[_hash_observation("a")]
        )
        assert result["count"] == 1
        assert obs_contents(server_db.get_entity("proj", "e1")) == ["b"]


class TestTrimObservationsToOutcomeTool:
    def test_trim_returns_deleted_count(self, server_db: DatabaseManager) -> None:
        server_db.create_entities(
            "proj", [{"name": "e1", "entityType": "task", "observations": ["a", "b", "c"]}]
        )
        result = server.trim_observations_to_outcome("proj", "e1", [_hash_observation("a")])
        assert result == {"message": "Trimmed 2 observation(s) from 'e1'.", "deleted": 2}
        assert obs_contents(server_db.get_entity("proj", "e1")) == ["a"]

    def test_empty_keep_hashes_returns_error(self, server_db: DatabaseManager) -> None:
        server_db.create_entities(
            "proj", [{"name": "e1", "entityType": "task", "observations": ["a"]}]
        )
        assert "error" in server.trim_observations_to_outcome("proj", "e1", [])

    def test_missing_entity_returns_error(self, server_db: DatabaseManager) -> None:
        assert "error" in server.trim_observations_to_outcome("proj", "nope", ["deadbeef"])


class TestBulkRenameEntityTool:
    def test_rename_returns_message(self, server_db: DatabaseManager) -> None:
        server_db.create_entities(
            "proj", [{"name": "a", "entityType": "task", "observations": ["x"]}]
        )
        result = server.bulk_rename_entity("proj", "a", "a2")
        assert "message" in result
        assert server_db.entity_exists_in_project("a2", "proj")

    def test_collision_returns_error(self, server_db: DatabaseManager) -> None:
        server_db.create_entities(
            "proj",
            [
                {"name": "a", "entityType": "task", "observations": ["x"]},
                {"name": "b", "entityType": "task", "observations": ["y"]},
            ],
        )
        assert "error" in server.bulk_rename_entity("proj", "a", "b")

    def test_missing_entity_returns_error(self, server_db: DatabaseManager) -> None:
        assert "error" in server.bulk_rename_entity("proj", "missing", "new")


class TestMoveEntityCrossScopeTool:
    def test_move_returns_dropped_relations(self, server_db: DatabaseManager) -> None:
        server_db.create_entities(
            "src",
            [
                {"name": "a", "entityType": "feature", "observations": ["x"]},
                {"name": "b", "entityType": "project", "observations": ["y"]},
            ],
        )
        server_db.create_relations(
            "src", [Relation(source="a", target="b", relation_type="belongs-to")]
        )
        result = server.move_entity_cross_scope("src", "dst", "a")
        assert result["droppedRelations"] == [
            {"source": "a", "target": "b", "relation_type": "belongs-to"}
        ]
        assert server_db.entity_exists_in_project("a", "dst")

    def test_target_collision_returns_error(self, server_db: DatabaseManager) -> None:
        server_db.create_entities(
            "src", [{"name": "e1", "entityType": "task", "observations": ["x"]}]
        )
        server_db.create_entities(
            "dst", [{"name": "e1", "entityType": "task", "observations": ["y"]}]
        )
        assert "error" in server.move_entity_cross_scope("src", "dst", "e1")

    def test_missing_entity_returns_error(self, server_db: DatabaseManager) -> None:
        assert "error" in server.move_entity_cross_scope("src", "dst", "missing")


class TestSearchNodesObservationShape:
    def test_search_output_carries_content_hash_per_observation(
        self, server_db: DatabaseManager
    ) -> None:
        server_db.create_entities(
            "proj", [{"name": "task/a", "entityType": "task", "observations": ["needle"]}]
        )
        entity = server.search_nodes("proj", "needle")["entities"][0]
        observation = entity.observations[0]
        assert observation.content == "needle"
        assert observation.content_hash == _hash_observation("needle")
        assert observation.vote_score == 0


class TestRestoreEntityTool:
    def test_restore_makes_soft_deleted_entity_visible(self, server_db: DatabaseManager) -> None:
        server_db.create_entities(
            "proj", [{"name": "e1", "entityType": "task", "observations": ["x"]}]
        )
        server_db.soft_delete_entity("proj", "e1")
        result = server.restore_entity("proj", "e1")
        assert result == {"message": "Restored entity 'e1' in project 'proj'."}
        assert obs_contents(server_db.get_entity("proj", "e1")) == ["x"]

    def test_missing_entity_returns_error(self, server_db: DatabaseManager) -> None:
        assert "error" in server.restore_entity("proj", "nope")


class TestMergeEntitiesTool:
    def test_merge_reports_counts_and_folds_source(self, server_db: DatabaseManager) -> None:
        server_db.create_entities(
            "proj",
            [
                {"name": "dup", "entityType": "task", "observations": ["from-source"]},
                {"name": "canon", "entityType": "task", "observations": ["from-target"]},
            ],
        )
        result = server.merge_entities("proj", "dup", "canon")
        assert result["message"] == "Merged 'dup' into 'canon' in project 'proj'."
        assert result["observations_merged"] == 1
        assert "from-source" in obs_contents(server_db.get_entity("proj", "canon"))
        with pytest.raises(ValueError, match="not found"):
            server_db.get_entity("proj", "dup")

    def test_merge_into_self_returns_error(self, server_db: DatabaseManager) -> None:
        server_db.create_entities(
            "proj", [{"name": "e1", "entityType": "task", "observations": ["a"]}]
        )
        assert "error" in server.merge_entities("proj", "e1", "e1")

    def test_missing_entity_returns_error(self, server_db: DatabaseManager) -> None:
        server_db.create_entities(
            "proj", [{"name": "canon", "entityType": "task", "observations": ["b"]}]
        )
        assert "error" in server.merge_entities("proj", "nope", "canon")


class TestMergeObservationsTool:
    def test_merge_reports_count_and_removes_source(self, server_db: DatabaseManager) -> None:
        server_db.create_entities(
            "proj", [{"name": "e1", "entityType": "task", "observations": ["source", "target"]}]
        )
        entity = server_db.get_entity("proj", "e1")
        hashes = {o.content: o.content_hash for o in entity.observations}
        result = server.merge_observations("proj", "e1", hashes["source"], hashes["target"])
        assert result["message"] == "Merged observation into target in 'e1'."
        assert result["merged"] == 1
        assert obs_contents(server_db.get_entity("proj", "e1")) == ["target"]

    def test_unknown_hash_returns_error(self, server_db: DatabaseManager) -> None:
        server_db.create_entities(
            "proj", [{"name": "e1", "entityType": "task", "observations": ["t"]}]
        )
        target = server_db.get_entity("proj", "e1").observations[0].content_hash
        assert "error" in server.merge_observations("proj", "e1", "deadbeef", target)


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
        assert obs_contents(server_db.get_entity("dst", "e1")) == ["a"]

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


_BUDGET_SENTINEL = "[{n} lower-voted observation(s) omitted to save tokens]"


class TestSearchToolsObservationBudget:
    def _seed(self, server_db: DatabaseManager) -> None:
        server_db.create_entities(
            "proj",
            [{"name": "task/foo", "entityType": "task", "observations": ["aaa", "bbb", "ccc"]}],
        )

    def test_search_nodes_negative_returns_all(self, server_db: DatabaseManager) -> None:
        self._seed(server_db)
        entity = server.search_nodes("proj", "foo", max_observation_chars=-1)["entities"][0]
        assert obs_contents(entity) == ["aaa", "bbb", "ccc"]

    def test_search_nodes_small_budget_trims_with_sentinel(
        self, server_db: DatabaseManager
    ) -> None:
        self._seed(server_db)
        entity = server.search_nodes("proj", "foo", max_observation_chars=6)["entities"][0]
        assert obs_contents(entity) == ["aaa", "bbb", _BUDGET_SENTINEL.format(n=1)]

    def test_search_nodes_zero_keeps_only_top(self, server_db: DatabaseManager) -> None:
        self._seed(server_db)
        entity = server.search_nodes("proj", "foo", max_observation_chars=0)["entities"][0]
        assert obs_contents(entity) == ["aaa", _BUDGET_SENTINEL.format(n=2)]

    def test_read_graph_small_budget_trims_with_sentinel(self, server_db: DatabaseManager) -> None:
        self._seed(server_db)
        entity = server.read_graph("proj", max_observation_chars=6)["entities"][0]
        assert obs_contents(entity) == ["aaa", "bbb", _BUDGET_SENTINEL.format(n=1)]

    def test_read_graph_negative_returns_all(self, server_db: DatabaseManager) -> None:
        self._seed(server_db)
        entity = server.read_graph("proj", max_observation_chars=-1)["entities"][0]
        assert obs_contents(entity) == ["aaa", "bbb", "ccc"]

    def test_search_all_projects_small_budget_trims_with_sentinel(
        self, server_db: DatabaseManager
    ) -> None:
        self._seed(server_db)
        result = server.search_all_projects("foo", max_observation_chars=6)
        entity = result["results"]["proj"]["entities"][0]
        assert obs_contents(entity) == ["aaa", "bbb", _BUDGET_SENTINEL.format(n=1)]

    def test_search_all_projects_negative_returns_all(self, server_db: DatabaseManager) -> None:
        self._seed(server_db)
        result = server.search_all_projects("foo", max_observation_chars=-1)
        entity = result["results"]["proj"]["entities"][0]
        assert obs_contents(entity) == ["aaa", "bbb", "ccc"]


class TestGraphToolsObservationBudget:
    def _seed(self, server_db: DatabaseManager) -> None:
        server_db.create_entities(
            "proj",
            [
                {"name": "a", "entityType": "feature", "observations": ["aaa", "bbb", "ccc"]},
                {"name": "b", "entityType": "project", "observations": ["xxx", "yyy", "zzz"]},
            ],
        )
        server_db.create_relations(
            "proj", [Relation(source="a", target="b", relation_type="belongs-to")]
        )

    def _related(self, result: dict[str, object]) -> Entity:
        related = result["relatedEntities"]
        assert isinstance(related, list)
        return next(e for e in related if isinstance(e, Entity))

    def test_get_entity_with_relations_compact_empties_both(
        self, server_db: DatabaseManager
    ) -> None:
        self._seed(server_db)
        result = server.get_entity_with_relations("proj", "a", compact=True)
        assert result["entity"].observations == []
        assert self._related(result).observations == []

    def test_get_entity_with_relations_small_budget_trims_both(
        self, server_db: DatabaseManager
    ) -> None:
        self._seed(server_db)
        result = server.get_entity_with_relations("proj", "a", max_observation_chars=6)
        assert obs_contents(result["entity"]) == ["aaa", "bbb", _BUDGET_SENTINEL.format(n=1)]
        assert obs_contents(self._related(result)) == ["xxx", "yyy", _BUDGET_SENTINEL.format(n=1)]

    def test_get_entity_with_relations_zero_keeps_only_top_both(
        self, server_db: DatabaseManager
    ) -> None:
        self._seed(server_db)
        result = server.get_entity_with_relations("proj", "a", max_observation_chars=0)
        assert obs_contents(result["entity"]) == ["aaa", _BUDGET_SENTINEL.format(n=2)]
        assert obs_contents(self._related(result)) == ["xxx", _BUDGET_SENTINEL.format(n=2)]

    def test_get_entity_with_relations_negative_returns_all(
        self, server_db: DatabaseManager
    ) -> None:
        self._seed(server_db)
        result = server.get_entity_with_relations("proj", "a", max_observation_chars=-1)
        assert obs_contents(result["entity"]) == ["aaa", "bbb", "ccc"]
        assert obs_contents(self._related(result)) == ["xxx", "yyy", "zzz"]

    def test_search_related_nodes_compact_empties_both(self, server_db: DatabaseManager) -> None:
        self._seed(server_db)
        result = server.search_related_nodes("proj", "a", compact=True)
        assert result["entity"].observations == []
        assert self._related(result).observations == []

    def test_search_related_nodes_small_budget_trims_both(self, server_db: DatabaseManager) -> None:
        self._seed(server_db)
        result = server.search_related_nodes("proj", "a", max_observation_chars=6)
        assert obs_contents(result["entity"]) == ["aaa", "bbb", _BUDGET_SENTINEL.format(n=1)]
        assert obs_contents(self._related(result)) == ["xxx", "yyy", _BUDGET_SENTINEL.format(n=1)]

    def test_search_related_nodes_zero_keeps_only_top_both(
        self, server_db: DatabaseManager
    ) -> None:
        self._seed(server_db)
        result = server.search_related_nodes("proj", "a", max_observation_chars=0)
        assert obs_contents(result["entity"]) == ["aaa", _BUDGET_SENTINEL.format(n=2)]
        assert obs_contents(self._related(result)) == ["xxx", _BUDGET_SENTINEL.format(n=2)]

    def test_search_related_nodes_negative_returns_all(self, server_db: DatabaseManager) -> None:
        self._seed(server_db)
        result = server.search_related_nodes("proj", "a", max_observation_chars=-1)
        assert obs_contents(result["entity"]) == ["aaa", "bbb", "ccc"]
        assert obs_contents(self._related(result)) == ["xxx", "yyy", "zzz"]


class TestImplicitUsefulnessAutoVote:
    def test_search_then_edit_raises_vote_score_by_one(self, server_db: DatabaseManager) -> None:
        server_db.create_entities(
            "proj", [{"name": "task/a", "entityType": "task", "observations": ["needle"]}]
        )

        server.search_nodes("proj", "needle")
        server.add_observations("proj", "task/a", ["follow-up"])

        assert server_db.get_entity("proj", "task/a").vote_score == 1

    def test_edit_without_prior_search_does_not_vote(self, server_db: DatabaseManager) -> None:
        server_db.create_entities(
            "proj", [{"name": "task/a", "entityType": "task", "observations": ["needle"]}]
        )

        server.add_observations("proj", "task/a", ["follow-up"])

        assert server_db.get_entity("proj", "task/a").vote_score == 0
