"""Tests for applying the safe steps of an audit plan."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from mcp_memory.plan_apply import apply_safe_steps, archive_stale

if TYPE_CHECKING:
    from mcp_memory.storage import Storage


def _step(tool: str, arguments: dict[str, object], *, needs_review: bool = False) -> dict[str, object]:
    return {"tool": tool, "arguments": arguments, "reason": "because", "needs_review": needs_review}


class TestApplySafeSteps:
    def test_applies_each_safe_tool_and_reports_lines(self, store: Storage) -> None:
        store.entities.create(
            "proj",
            [
                {"name": "project/proj", "entityType": "project", "observations": ["root"]},
                {"name": "Dup", "entityType": "task", "observations": ["a", "a longer a"]},
                {"name": "task/Dup", "entityType": "task", "observations": ["b"]},
                {"name": "Plain", "entityType": "task", "observations": ["c"]},
                {"name": "feature/f", "entityType": "feature", "observations": ["d"]},
            ],
        )
        steps = [
            _step("merge_entities", {"project": "proj", "source": "Dup", "target": "task/Dup"}),
            _step("rename_entity", {"project": "proj", "old_name": "Plain", "new_name": "task/Plain"}),
            _step(
                "create_relations",
                {
                    "project": "proj",
                    "relations": [{"source": "feature/f", "relationType": "belongs-to", "target": "project/proj"}],
                },
            ),
        ]

        lines = apply_safe_steps(store, steps)

        assert lines == [
            "[proj/Dup] - merged into task/Dup: because (auto)",
            "[proj/Plain] - renamed to task/Plain: because (auto)",
            "[proj/feature/f] - linked belongs-to -> project/proj: because (auto)",
        ]
        assert store.reads.get_entity("proj", "task/Plain") is not None

    def test_merges_observation_into_its_container(self, store: Storage) -> None:
        store.entities.create(
            "proj", [{"name": "task/a", "entityType": "task", "observations": ["done", "done and dusted"]}]
        )
        by_content = {o.content: o.content_hash for o in store.reads.get_entity("proj", "task/a").observations}
        step = _step(
            "merge_observations",
            {
                "project": "proj",
                "entityName": "task/a",
                "sourceHash": by_content["done"],
                "targetHash": by_content["done and dusted"],
            },
        )

        lines = apply_safe_steps(store, [step])

        assert len(lines) == 1
        assert "merged observation into" in lines[0]
        assert [o.content for o in store.reads.get_entity("proj", "task/a").observations] == ["done and dusted"]

    def test_skips_needs_review_steps(self, store: Storage) -> None:
        store.entities.create("proj", [{"name": "Plain", "entityType": "task", "observations": ["c"]}])
        step = _step(
            "rename_entity", {"project": "proj", "old_name": "Plain", "new_name": "task/Plain"}, needs_review=True
        )

        assert apply_safe_steps(store, [step]) == []
        assert store.reads.get_entity("proj", "Plain") is not None

    def test_rejected_step_is_skipped_and_the_rest_still_run(self, store: Storage) -> None:
        store.entities.create("proj", [{"name": "Plain", "entityType": "task", "observations": ["c"]}])
        steps = [
            _step("rename_entity", {"project": "proj", "old_name": "Missing", "new_name": "task/Missing"}),
            _step("rename_entity", {"project": "proj", "old_name": "Plain", "new_name": "task/Plain"}),
        ]

        assert apply_safe_steps(store, steps) == ["[proj/Plain] - renamed to task/Plain: because (auto)"]


class TestArchiveStale:
    def _seed_stale(self, store: Storage) -> None:
        store.entities.create(
            "proj", [{"name": "task/old", "entityType": "task", "observations": ["x"], "status": "resolved"}]
        )
        with store.connection.transaction():
            store.connection.write("UPDATE entities SET updated_at = datetime('now', '-400 days')")

    def test_archives_and_reports_when_enabled(self, store: Storage) -> None:
        self._seed_stale(store)

        assert archive_stale(store) == [
            "[proj/task/old] - archived: resolved and untouched past the archive window (auto)"
        ]
        assert store.reads.get_entity("proj", "task/old").status == "archived"

    def test_does_nothing_when_disabled(self, store: Storage, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MCP_MEMORY_ARCHIVE_ENABLED", "0")
        self._seed_stale(store)

        assert archive_stale(store) == []
        assert store.reads.get_entity("proj", "task/old").status == "resolved"

    def test_project_scope_spares_other_projects(self, store: Storage) -> None:
        self._seed_stale(store)
        store.entities.create(
            "other", [{"name": "task/old", "entityType": "task", "observations": ["x"], "status": "resolved"}]
        )
        with store.connection.transaction():
            store.connection.write("UPDATE entities SET updated_at = datetime('now', '-400 days')")

        assert archive_stale(store, "proj") == [
            "[proj/task/old] - archived: resolved and untouched past the archive window (auto)"
        ]
        assert store.reads.get_entity("other", "task/old").status == "resolved"
