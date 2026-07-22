"""Tests for the read-only memory-graph structural audit."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mcp_memory import cli
from mcp_memory.audit import audit_graph
from mcp_memory.database import DatabaseManager
from mcp_memory.models import Relation


@pytest.fixture
def db(tmp_path: Path) -> DatabaseManager:
    """Create a fresh database for each test."""
    return DatabaseManager(tmp_path / "test.db")


def _names(findings: list[dict[str, object]]) -> set[str]:
    return {f["name"] for f in findings}  # type: ignore[misc]


class TestOrphans:
    def test_flags_non_exempt_entity_with_no_relations(self, db: DatabaseManager) -> None:
        db.create_entities(
            "proj", [{"name": "task/lonely", "entityType": "task", "observations": ["o"]}]
        )
        assert _names(audit_graph(db, "proj")["orphans"]) == {"task/lonely"}  # type: ignore[arg-type]

    def test_exempt_types_never_orphaned(self, db: DatabaseManager) -> None:
        db.create_entities(
            "proj", [{"name": "project/proj", "entityType": "project", "observations": ["o"]}]
        )
        db.create_entities(
            "global",
            [
                {
                    "name": "user-preferences/x",
                    "entityType": "user-preferences",
                    "observations": ["o"],
                }
            ],
        )
        assert audit_graph(db, "proj")["orphans"] == []
        assert audit_graph(db, "global")["orphans"] == []

    def test_related_entity_not_orphaned(self, db: DatabaseManager) -> None:
        db.create_entities(
            "proj",
            [
                {"name": "feature/f", "entityType": "feature", "observations": ["o"]},
                {"name": "task/t", "entityType": "task", "observations": ["o"]},
            ],
        )
        db.create_relations("proj", [Relation("task/t", "feature/f", "implements")])
        assert audit_graph(db, "proj")["orphans"] == []

    def test_relation_to_soft_deleted_still_orphan(self, db: DatabaseManager) -> None:
        db.create_entities(
            "proj",
            [
                {"name": "feature/f", "entityType": "feature", "observations": ["o"]},
                {"name": "task/t", "entityType": "task", "observations": ["o"]},
            ],
        )
        db.create_relations("proj", [Relation("task/t", "feature/f", "implements")])
        db.soft_delete_entity("proj", "feature/f")
        assert "task/t" in _names(audit_graph(db, "proj")["orphans"])  # type: ignore[arg-type]


class TestMisusedProjectType:
    def test_flags_project_type_with_wrong_name(self, db: DatabaseManager) -> None:
        db.create_entities(
            "proj", [{"name": "auth_investigation", "entityType": "project", "observations": ["o"]}]
        )
        assert _names(audit_graph(db, "proj")["misused_project_type"]) == {  # type: ignore[arg-type]
            "auth_investigation"
        }

    def test_correct_root_not_flagged(self, db: DatabaseManager) -> None:
        db.create_entities(
            "proj", [{"name": "project/proj", "entityType": "project", "observations": ["o"]}]
        )
        assert audit_graph(db, "proj")["misused_project_type"] == []


class TestUnprefixed:
    def test_flags_name_without_standard_prefix(self, db: DatabaseManager) -> None:
        db.create_entities(
            "proj", [{"name": "RandomThing", "entityType": "task", "observations": ["o"]}]
        )
        assert _names(audit_graph(db, "proj")["unprefixed"]) == {"RandomThing"}  # type: ignore[arg-type]

    def test_all_standard_prefixes_pass(self, db: DatabaseManager) -> None:
        db.create_entities(
            "proj",
            [
                {"name": "feature/a", "entityType": "feature", "observations": ["o"]},
                {"name": "knowledge/b", "entityType": "knowledge", "observations": ["o"]},
            ],
        )
        assert audit_graph(db, "proj")["unprefixed"] == []


class TestGhostScopes:
    def test_flags_scope_with_zero_entities(self, db: DatabaseManager) -> None:
        db._get_or_create_project_id("ghost")
        db.create_entities(
            "real", [{"name": "task/t", "entityType": "task", "observations": ["o"]}]
        )
        assert "ghost" in audit_graph(db, None)["ghost_scopes"]  # type: ignore[operator]
        assert "real" not in audit_graph(db, None)["ghost_scopes"]  # type: ignore[operator]

    def test_scoped_audit_only_reports_that_scope(self, db: DatabaseManager) -> None:
        db._get_or_create_project_id("ghost")
        assert audit_graph(db, "ghost")["ghost_scopes"] == ["ghost"]
        db.create_entities(
            "real", [{"name": "task/t", "entityType": "task", "observations": ["o"]}]
        )
        assert audit_graph(db, "real")["ghost_scopes"] == []


class TestOversized:
    def test_resolved_task_over_three_obs_flagged(self, db: DatabaseManager) -> None:
        db.create_entities(
            "proj",
            [
                {
                    "name": "task/big",
                    "entityType": "task",
                    "observations": ["a", "b", "c", "d"],
                    "status": "resolved",
                }
            ],
        )
        oversized = audit_graph(db, "proj")["oversized"]
        assert oversized == [
            {
                "name": "task/big",
                "entity_type": "task",
                "project": "proj",
                "count": 4,
                "threshold": 3,
            }
        ]

    def test_unresolved_task_not_size_checked(self, db: DatabaseManager) -> None:
        db.create_entities(
            "proj",
            [
                {
                    "name": "task/wip",
                    "entityType": "task",
                    "observations": ["a", "b", "c", "d", "e"],
                    "status": "in-progress",
                }
            ],
        )
        assert audit_graph(db, "proj")["oversized"] == []

    def test_feature_at_ceiling_not_flagged(self, db: DatabaseManager) -> None:
        db.create_entities(
            "proj",
            [
                {
                    "name": "feature/f",
                    "entityType": "feature",
                    "observations": [str(i) for i in range(10)],
                }
            ],
        )
        assert audit_graph(db, "proj")["oversized"] == []


class TestRelationViolationsAndStarGraph:
    def test_task_belongs_to_project_is_violation_and_star(self, db: DatabaseManager) -> None:
        db.create_entities(
            "proj",
            [
                {"name": "project/proj", "entityType": "project", "observations": ["o"]},
                {"name": "task/t", "entityType": "task", "observations": ["o"]},
            ],
        )
        db.create_relations("proj", [Relation("task/t", "project/proj", "belongs-to")])
        report = audit_graph(db, "proj")
        assert [v["task"] for v in report["relation_violations"]] == ["task/t"]  # type: ignore[index,union-attr]
        assert [v["task"] for v in report["star_graph_tasks"]] == ["task/t"]  # type: ignore[index,union-attr]

    def test_task_to_project_via_other_relation_is_star_not_violation(
        self, db: DatabaseManager
    ) -> None:
        db.create_entities(
            "proj",
            [
                {"name": "project/proj", "entityType": "project", "observations": ["o"]},
                {"name": "task/t", "entityType": "task", "observations": ["o"]},
            ],
        )
        db.create_relations("proj", [Relation("task/t", "project/proj", "relates-to")])
        report = audit_graph(db, "proj")
        assert report["relation_violations"] == []
        assert [v["task"] for v in report["star_graph_tasks"]] == ["task/t"]  # type: ignore[index,union-attr]

    def test_task_implements_feature_is_neither(self, db: DatabaseManager) -> None:
        db.create_entities(
            "proj",
            [
                {"name": "feature/f", "entityType": "feature", "observations": ["o"]},
                {"name": "task/t", "entityType": "task", "observations": ["o"]},
            ],
        )
        db.create_relations("proj", [Relation("task/t", "feature/f", "implements")])
        report = audit_graph(db, "proj")
        assert report["relation_violations"] == []
        assert report["star_graph_tasks"] == []


class TestNegativeVotes:
    def test_strongly_downvoted_flagged(self, db: DatabaseManager) -> None:
        db.create_entities(
            "proj",
            [
                {"name": "feature/f", "entityType": "feature", "observations": ["o"]},
                {"name": "task/t", "entityType": "task", "observations": ["o"]},
            ],
        )
        db.create_relations("proj", [Relation("task/t", "feature/f", "implements")])
        for _ in range(5):
            db.vote_entity("proj", "task/t", -1)
        flagged = audit_graph(db, "proj")["negative_vote_entities"]
        assert flagged == [
            {"name": "task/t", "entity_type": "task", "project": "proj", "vote_score": -5}
        ]

    def test_mildly_downvoted_not_flagged(self, db: DatabaseManager) -> None:
        db.create_entities(
            "proj",
            [
                {"name": "feature/f", "entityType": "feature", "observations": ["o"]},
                {"name": "task/t", "entityType": "task", "observations": ["o"]},
            ],
        )
        db.create_relations("proj", [Relation("task/t", "feature/f", "implements")])
        for _ in range(4):
            db.vote_entity("proj", "task/t", -1)
        assert audit_graph(db, "proj")["negative_vote_entities"] == []


class TestProjectScoping:
    def test_scoped_audit_excludes_other_projects(self, db: DatabaseManager) -> None:
        db.create_entities("a", [{"name": "orphan_a", "entityType": "task", "observations": ["o"]}])
        db.create_entities("b", [{"name": "orphan_b", "entityType": "task", "observations": ["o"]}])
        assert _names(audit_graph(db, "a")["orphans"]) == {"orphan_a"}  # type: ignore[arg-type]

    def test_all_projects_reports_project_on_each_finding(self, db: DatabaseManager) -> None:
        db.create_entities("a", [{"name": "orphan_a", "entityType": "task", "observations": ["o"]}])
        db.create_entities("b", [{"name": "orphan_b", "entityType": "task", "observations": ["o"]}])
        orphans = audit_graph(db, None)["orphans"]
        by_name = {f["name"]: f["project"] for f in orphans}  # type: ignore[union-attr]
        assert by_name == {"orphan_a": "a", "orphan_b": "b"}


class TestAuditCommand:
    def test_emits_json_for_scope(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        db_path = tmp_path / "memory.db"
        monkeypatch.setenv("MCP_MEMORY_DB_PATH", str(db_path))
        DatabaseManager(db_path).create_entities(
            "proj", [{"name": "task/lonely", "entityType": "task", "observations": ["o"]}]
        )

        cli._cmd_audit(cli.argparse.Namespace(project="proj", all_projects=False))

        report = json.loads(capsys.readouterr().out)
        assert report["project"] == "proj"
        assert _names(report["orphans"]) == {"task/lonely"}

    def test_parser_requires_a_scope(self) -> None:
        parser = cli._build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["audit"])
