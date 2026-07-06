"""Tests for the in-memory activity tracker."""

from __future__ import annotations

import pytest

from mcp_memory import activity
from mcp_memory.models import Entity


@pytest.fixture(autouse=True)
def _clear_activity() -> None:
    """Reset the process-global activity buffer before each test."""
    activity.clear()


class TestRecordAndRecent:
    def test_records_a_write_event(self) -> None:
        activity.record_tool(
            "create_entities",
            {"project": "proj", "entities": [{"name": "e1", "entityType": "task"}]},
            {"message": "ok"},
        )
        events = activity.recent(0)
        assert len(events) == 1
        event = events[0]
        assert event["tool"] == "create_entities"
        assert event["kind"] == "create"
        assert event["entities"] == ["e1"]
        assert event["project"] == "proj"
        assert event["id"] == 1

    def test_since_filters_already_seen_events(self) -> None:
        for _ in range(3):
            activity.record_tool("read_graph", {"project": "p"}, {"entities": [], "relations": []})
        assert [e["id"] for e in activity.recent(2)] == [3]

    def test_latest_seq_is_monotonic(self) -> None:
        assert activity.latest_seq() == 0
        activity.record_tool("list_projects", {}, {"projects": []})
        activity.record_tool("list_projects", {}, {"projects": []})
        assert activity.latest_seq() == 2

    def test_clear_resets_buffer_and_sequence(self) -> None:
        activity.record_tool("list_projects", {}, {"projects": []})
        activity.clear()
        assert activity.recent(0) == []
        assert activity.latest_seq() == 0

    def test_maxlen_evicts_oldest_but_sequence_keeps_climbing(self) -> None:
        for _ in range(250):
            activity.record_tool("list_projects", {}, {"projects": []})
        events = activity.recent(0)
        assert len(events) == 200
        assert events[0]["id"] == 51
        assert activity.latest_seq() == 250

    def test_error_result_is_not_recorded(self) -> None:
        activity.record_tool("delete_entity", {"project": "p", "name": "e1"}, {"error": "boom"})
        assert activity.recent(0) == []
        assert activity.latest_seq() == 0

    def test_stored_event_holds_only_primitives(self) -> None:
        activity.record_tool(
            "get_entity_with_relations",
            {"project": "p", "name": "e1"},
            {"entity": Entity(name="e1", entity_type="task", observations=["o"]), "relations": []},
        )
        event = activity.recent(0)[0]
        for value in event.values():
            assert isinstance(value, (int, float, str, list, type(None)))
        assert all(isinstance(name, str) for name in event["entities"])


class TestKindMapping:
    def test_known_tools_map_to_expected_kind(self) -> None:
        cases = {
            "search_nodes": "read",
            "create_entities": "create",
            "create_relations": "create",
            "add_observations": "update",
            "delete_observations": "update",
            "set_entity_status": "update",
            "vote_entity": "update",
            "delete_entity": "delete",
            "delete_relation": "delete",
            "delete_project": "delete",
        }
        for tool, expected in cases.items():
            activity.clear()
            activity.record_tool(tool, {"project": "p"}, {"message": "ok"})
            assert activity.recent(0)[0]["kind"] == expected

    def test_unknown_tool_defaults_to_read(self) -> None:
        activity.record_tool("some_future_tool", {"project": "p"}, {"message": "ok"})
        assert activity.recent(0)[0]["kind"] == "read"


class TestWriteExtraction:
    def test_create_entities_extracts_all_names(self) -> None:
        activity.record_tool(
            "create_entities",
            {"project": "p", "entities": [{"name": "a"}, {"name": "b"}]},
            {"message": "ok"},
        )
        assert activity.recent(0)[0]["entities"] == ["a", "b"]

    def test_add_observations_extracts_entity_name(self) -> None:
        activity.record_tool(
            "add_observations",
            {"project": "p", "entityName": "task/foo", "observations": ["o"]},
            {"message": "ok"},
        )
        assert activity.recent(0)[0]["entities"] == ["task/foo"]

    def test_create_relations_extracts_source_and_target(self) -> None:
        activity.record_tool(
            "create_relations",
            {"project": "p", "relations": [{"source": "a", "target": "b", "type": "rel"}]},
            {"message": "ok"},
        )
        assert activity.recent(0)[0]["entities"] == ["a", "b"]

    def test_delete_relation_extracts_endpoints(self) -> None:
        activity.record_tool(
            "delete_relation",
            {"project": "p", "source": "a", "target": "b", "type": "rel"},
            {"message": "ok"},
        )
        assert activity.recent(0)[0]["entities"] == ["a", "b"]

    def test_set_entity_status_extracts_name(self) -> None:
        activity.record_tool(
            "set_entity_status",
            {"project": "p", "name": "task/foo", "status": "resolved"},
            {"message": "ok"},
        )
        assert activity.recent(0)[0]["entities"] == ["task/foo"]

    def test_vote_entity_extracts_name(self) -> None:
        activity.record_tool(
            "vote_entity",
            {"project": "p", "name": "task/foo", "vote": 1},
            {"name": "task/foo", "project": "p", "vote_score": 1},
        )
        assert activity.recent(0)[0]["entities"] == ["task/foo"]

    def test_project_level_tools_record_project_with_no_entities(self) -> None:
        activity.record_tool("delete_project", {"project": "scratch"}, {"message": "ok"})
        event = activity.recent(0)[0]
        assert event["entities"] == []
        assert event["project"] == "scratch"

    def test_move_project_entities_uses_source_as_project(self) -> None:
        activity.record_tool(
            "move_project_entities", {"source": "old", "target": "new"}, {"moved": 3}
        )
        event = activity.recent(0)[0]
        assert event["project"] == "old"
        assert event["entities"] == []


class TestReadExtraction:
    def test_entities_list_yields_names(self) -> None:
        activity.record_tool(
            "search_nodes",
            {"project": "p", "query": "x"},
            {
                "entities": [
                    Entity(name="e1", entity_type="task", observations=[]),
                    Entity(name="e2", entity_type="task", observations=[]),
                ],
                "relations": [],
            },
        )
        assert activity.recent(0)[0]["entities"] == ["e1", "e2"]

    def test_entity_and_related_entities_are_deduplicated(self) -> None:
        activity.record_tool(
            "get_entity_with_relations",
            {"project": "p", "name": "e1"},
            {
                "entity": Entity(name="e1", entity_type="task", observations=[]),
                "relations": [],
                "relatedEntities": [
                    Entity(name="e2", entity_type="feature", observations=[]),
                    Entity(name="e1", entity_type="task", observations=[]),
                ],
            },
        )
        assert activity.recent(0)[0]["entities"] == ["e1", "e2"]

    def test_search_all_projects_grouped_results(self) -> None:
        activity.record_tool(
            "search_all_projects",
            {"query": "x"},
            {
                "results": {
                    "p1": {"entities": [Entity(name="a", entity_type="task", observations=[])]},
                    "p2": {"entities": [Entity(name="b", entity_type="task", observations=[])]},
                }
            },
        )
        assert sorted(activity.recent(0)[0]["entities"]) == ["a", "b"]

    def test_read_with_no_entities_still_records(self) -> None:
        activity.record_tool("list_projects", {}, {"projects": ["p1", "p2"]})
        event = activity.recent(0)[0]
        assert event["kind"] == "read"
        assert event["entities"] == []
