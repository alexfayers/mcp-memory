"""Tests for the read-only memory-graph structural audit."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest

from mcp_memory import cli
from mcp_memory.audit import audit_graph, propose_plan
from mcp_memory.database import DatabaseManager
from mcp_memory.models import Relation


@pytest.fixture
def db(tmp_path: Path) -> DatabaseManager:
    """Create a fresh database for each test."""
    return DatabaseManager(tmp_path / "test.db")


def _names(findings: list[dict[str, object]]) -> set[str]:
    return {f["name"] for f in findings}  # type: ignore[misc]


def _entity_id(db: DatabaseManager, name: str, project: str = "proj") -> int:
    project_id = db._get_or_create_project_id(project)
    entity_id = db._get_entity_id(name, project_id)
    assert entity_id is not None
    return entity_id


def _vote_obs(db: DatabaseManager, name: str, content: str, times: int) -> None:
    step = 1 if times > 0 else -1
    for _ in range(abs(times)):
        db.vote_observation("proj", name, step, content=content)


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
        oversized = cast("list[dict[str, object]]", audit_graph(db, "proj")["oversized"])
        assert len(oversized) == 1
        finding = oversized[0]
        assert finding["name"] == "task/big"
        assert finding["entity_type"] == "task"
        assert finding["project"] == "proj"
        assert finding["count"] == 4
        assert finding["threshold"] == 3
        assert finding["status"] == "resolved"

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
        violations = cast("list[dict[str, object]]", report["relation_violations"])
        stars = cast("list[dict[str, object]]", report["star_graph_tasks"])
        assert [v["task"] for v in violations] == ["task/t"]
        assert [v["task"] for v in stars] == ["task/t"]

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
        stars = cast("list[dict[str, object]]", report["star_graph_tasks"])
        assert [v["task"] for v in stars] == ["task/t"]

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
        orphans = cast("list[dict[str, object]]", audit_graph(db, None)["orphans"])
        by_name = {f["name"]: f["project"] for f in orphans}
        assert by_name == {"orphan_a": "a", "orphan_b": "b"}


class TestProposePlan:
    def _plan(self, db: DatabaseManager, project: str | None) -> list[dict[str, object]]:
        return propose_plan(db, audit_graph(db, project))

    def test_oversized_trim_keeps_outcome_and_top_vote_deterministically(
        self, db: DatabaseManager
    ) -> None:
        db.create_entities(
            "proj",
            [
                {
                    "name": "task/big",
                    "entityType": "task",
                    "observations": ["low", "mid", "high", "RESOLVED: shipped"],
                    "status": "resolved",
                }
            ],
        )
        _vote_obs(db, "task/big", "high", 5)
        _vote_obs(db, "task/big", "mid", 2)

        by_content = {
            o.content: o.content_hash for o in db._get_observations_full(_entity_id(db, "task/big"))
        }
        step = self._plan(db, "proj")[0]
        assert step["tool"] == "trim_observations_to_outcome"
        assert step["needs_review"] is True  # "low"/"mid" are distinct facts, not duplicates
        keep = set(step["arguments"]["keep_hashes"])  # type: ignore[index]
        assert by_content["RESOLVED: shipped"] in keep
        assert by_content["high"] in keep
        assert len(keep) <= 3

        again = self._plan(db, "proj")[0]
        assert again["arguments"]["keep_hashes"] == step["arguments"]["keep_hashes"]  # type: ignore[index]

    def test_trim_uses_full_observation_list_not_budgeted(self, db: DatabaseManager) -> None:
        db.create_entities(
            "proj",
            [
                {
                    "name": "task/big",
                    "entityType": "task",
                    "observations": ["a", "b", "c", "RESOLVED: done"],
                    "status": "resolved",
                }
            ],
        )
        _vote_obs(db, "task/big", "RESOLVED: done", -3)

        by_content = {
            o.content: o.content_hash for o in db._get_observations_full(_entity_id(db, "task/big"))
        }
        keep = set(self._plan(db, "proj")[0]["arguments"]["keep_hashes"])  # type: ignore[index]
        assert by_content["RESOLVED: done"] in keep

    def test_unprefixed_yields_prefixed_rename(self, db: DatabaseManager) -> None:
        db.create_entities("proj", [{"name": "foo", "entityType": "task", "observations": ["o"]}])
        step = next(s for s in self._plan(db, "proj") if s["tool"] == "bulk_rename_entity")
        assert step["arguments"] == {
            "project": "proj",
            "old_name": "foo",
            "new_name": "task/foo",
        }
        assert step["needs_review"] is False

    def test_ghost_scope_needs_review(self, db: DatabaseManager) -> None:
        db._get_or_create_project_id("ghost")
        step = next(s for s in self._plan(db, None) if s["tool"] == "delete_project")
        assert step["arguments"]["project"] == "ghost"  # type: ignore[index]
        assert step["needs_review"] is True

    def test_negative_vote_needs_review(self, db: DatabaseManager) -> None:
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
        step = next(s for s in self._plan(db, "proj") if s["tool"] == "delete_entity")
        assert step["arguments"]["name"] == "task/t"  # type: ignore[index]
        assert step["needs_review"] is True

    def test_orphan_needs_review(self, db: DatabaseManager) -> None:
        db.create_entities(
            "proj", [{"name": "task/lonely", "entityType": "task", "observations": ["o"]}]
        )
        step = next(s for s in self._plan(db, "proj") if s["tool"] == "create_relations")
        assert step["arguments"]["relations"][0]["source"] == "task/lonely"  # type: ignore[index]
        assert step["needs_review"] is True

    def test_relation_violation_needs_review(self, db: DatabaseManager) -> None:
        db.create_entities(
            "proj",
            [
                {"name": "project/proj", "entityType": "project", "observations": ["o"]},
                {"name": "task/t", "entityType": "task", "observations": ["o"]},
            ],
        )
        db.create_relations("proj", [Relation("task/t", "project/proj", "belongs-to")])
        steps = self._plan(db, "proj")
        assert any(s["needs_review"] and "implements" in json.dumps(s["arguments"]) for s in steps)

    def test_multiple_outcomes_yields_consider_split_not_trim(self, db: DatabaseManager) -> None:
        db.create_entities(
            "proj",
            [
                {
                    "name": "task/multi",
                    "entityType": "task",
                    "observations": ["RESOLVED: a", "RESOLVED: b", "Decided: c", "Resolved: d"],
                    "status": "resolved",
                }
            ],
        )
        steps = self._plan(db, "proj")
        split = next(s for s in steps if s.get("action") == "consider_split")
        assert split["needs_review"] is True
        assert split["entity"] == "task/multi"
        assert "relation" in split["reason"]  # type: ignore[operator]
        assert not any(s.get("tool") == "trim_observations_to_outcome" for s in steps)

    def test_oversized_non_task_yields_review_not_trim(self, db: DatabaseManager) -> None:
        db.create_entities(
            "proj",
            [
                {"name": "project/proj", "entityType": "project", "observations": ["o"]},
                {
                    "name": "feature/f",
                    "entityType": "feature",
                    "observations": [f"fact {i}" for i in range(11)],
                },
            ],
        )
        db.create_relations("proj", [Relation("feature/f", "project/proj", "belongs-to")])
        steps = self._plan(db, "proj")
        review = next(s for s in steps if s.get("action") == "review_oversized")
        assert review["entity"] == "feature/f"
        assert review["needs_review"] is True
        assert not any(s.get("tool") == "trim_observations_to_outcome" for s in steps)

    def test_single_outcome_still_trims(self, db: DatabaseManager) -> None:
        db.create_entities(
            "proj",
            [
                {
                    "name": "task/one",
                    "entityType": "task",
                    "observations": ["a", "b", "c", "RESOLVED: done"],
                    "status": "resolved",
                }
            ],
        )
        steps = self._plan(db, "proj")
        assert any(s.get("tool") == "trim_observations_to_outcome" for s in steps)
        assert not any(s.get("action") == "consider_split" for s in steps)

    def test_trim_dropping_a_distinct_fact_forces_review(self, db: DatabaseManager) -> None:
        db.create_entities(
            "proj",
            [
                {
                    "name": "task/facts",
                    "entityType": "task",
                    "observations": [
                        "RESOLVED: shipped the graph-hygiene tools",
                        "relation rows key on entity.id not name, so rename needed no repointing",
                        "relation-requirement invariant confirmed intact by all 5 capabilities",
                        "minor filler detail",
                    ],
                    "status": "resolved",
                }
            ],
        )
        step = next(
            s for s in self._plan(db, "proj") if s.get("tool") == "trim_observations_to_outcome"
        )
        assert step["needs_review"] is True

    def test_trim_dropping_only_near_duplicates_stays_auto_applied(
        self, db: DatabaseManager
    ) -> None:
        db.create_entities(
            "proj",
            [
                {
                    "name": "task/dupes",
                    "entityType": "task",
                    "observations": [
                        "RESOLVED: shipped the graph-hygiene tools",
                        "shipped the graph-hygiene tools",
                        "graph-hygiene tools",
                        "hygiene tools",
                    ],
                    "status": "resolved",
                }
            ],
        )
        step = next(
            s for s in self._plan(db, "proj") if s.get("tool") == "trim_observations_to_outcome"
        )
        assert step["needs_review"] is False


class TestAuditCommand:
    def test_emits_json_for_scope(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        db_path = tmp_path / "memory.db"
        monkeypatch.setenv("MCP_MEMORY_DB_PATH", str(db_path))
        DatabaseManager(db_path).create_entities(
            "proj", [{"name": "task/lonely", "entityType": "task", "observations": ["o"]}]
        )

        cli._cmd_audit(
            cli.argparse.Namespace(project="proj", all_projects=False, propose_plan=False)
        )

        report = json.loads(capsys.readouterr().out)
        assert report["project"] == "proj"
        assert _names(report["orphans"]) == {"task/lonely"}

    def test_propose_plan_emits_steps(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        db_path = tmp_path / "memory.db"
        monkeypatch.setenv("MCP_MEMORY_DB_PATH", str(db_path))
        DatabaseManager(db_path).create_entities(
            "proj", [{"name": "RandomThing", "entityType": "task", "observations": ["o"]}]
        )

        cli._cmd_audit(
            cli.argparse.Namespace(project="proj", all_projects=False, propose_plan=True)
        )

        payload = json.loads(capsys.readouterr().out)
        assert "bulk_rename_entity" in [s["tool"] for s in payload["steps"]]

    def test_parser_requires_a_scope(self) -> None:
        parser = cli._build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["audit"])
