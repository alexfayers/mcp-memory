"""Tests for applying the safe steps of an audit plan."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from mcp_memory.plan_apply import apply_safe_steps, archive_stale

if TYPE_CHECKING:
    from mcp_memory.storage import Storage


def _step(tool: str, arguments: dict[str, object], *, needs_review: bool = False) -> dict[str, object]:
    return {"tool": tool, "arguments": arguments, "reason": "because", "needs_review": needs_review}


def _rename(old_name: str, new_name: str, *, needs_review: bool = False) -> dict[str, object]:
    return _step(
        "rename_entity", {"project": "proj", "old_name": old_name, "new_name": new_name}, needs_review=needs_review
    )


def _add(store: Storage, *names: str) -> None:
    store.entities.create("proj", [{"name": n, "entityType": "task", "observations": ["x"]} for n in names])


class TestApplySafeSteps:
    def test_applies_each_safe_tool_and_reports_lines(self, store: Storage) -> None:
        _add(store, "project/proj", "Dup", "task/Dup", "Plain", "feature/f")
        relation = {"source": "feature/f", "relationType": "belongs-to", "target": "project/proj"}
        steps = [
            _step("merge_entities", {"project": "proj", "source": "Dup", "target": "task/Dup"}),
            _rename("Plain", "task/Plain"),
            _step("create_relations", {"project": "proj", "relations": [relation]}),
        ]

        assert apply_safe_steps(store, steps) == [
            "[proj/Dup] - merged into task/Dup: because (auto)",
            "[proj/Plain] - renamed to task/Plain: because (auto)",
            "[proj/feature/f] - linked belongs-to -> project/proj: because (auto)",
        ]

    def test_skips_review_steps_and_rejected_steps_without_blocking_the_rest(self, store: Storage) -> None:
        _add(store, "Plain")
        steps = [
            _rename("Plain", "x/Plain", needs_review=True),
            _rename("Missing", "task/Missing"),
            _rename("Plain", "task/Plain"),
        ]

        assert apply_safe_steps(store, steps) == ["[proj/Plain] - renamed to task/Plain: because (auto)"]


def test_archive_stale_does_nothing_when_disabled(store: Storage, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_MEMORY_ARCHIVE_ENABLED", "0")
    _add(store, "task/old")
    store.entities.set_status("proj", "task/old", "resolved")
    with store.connection.transaction():
        store.connection.write("UPDATE entities SET updated_at = datetime('now', '-400 days')")

    assert archive_stale(store) == []
