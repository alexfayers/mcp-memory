"""In-memory tracker of recent memory-tool activity for the live visualiser.

Records a lightweight, JSON-serialisable event for each MCP tool call so the
graph visualiser can show reads and writes as they happen. The buffer is held
entirely in memory (a bounded deque) and is never persisted to the database.
"""

from __future__ import annotations

import time
from collections import deque
from typing import Any, Literal, TypedDict

Kind = Literal["read", "create", "update", "delete"]

_MAX_EVENTS = 200

_KIND_BY_TOOL: dict[str, Kind] = {
    "search_nodes": "read",
    "read_graph": "read",
    "search_all_projects": "read",
    "get_entity_with_relations": "read",
    "search_related_nodes": "read",
    "list_projects": "read",
    "list_project_paths": "read",
    "get_project_for_path": "read",
    "get_paths_for_project": "read",
    "get_paths_for_entity": "read",
    "create_entities": "create",
    "create_relations": "create",
    "add_observations": "update",
    "delete_observations": "update",
    "set_entity_status": "update",
    "set_project_paths": "update",
    "move_project_entities": "update",
    "delete_entity": "delete",
    "delete_relation": "delete",
    "delete_project": "delete",
}


class ActivityEvent(TypedDict):
    """A single recorded tool call affecting the knowledge graph."""

    id: int
    ts: float
    kind: Kind
    tool: str
    entities: list[str]
    project: str | None


_events: deque[ActivityEvent] = deque(maxlen=_MAX_EVENTS)
_seq = 0


def record_tool(tool_name: str, kwargs: dict[str, Any], result: Any) -> None:
    """Record one tool call, skipping calls that returned an error."""
    if isinstance(result, dict) and "error" in result:
        return

    global _seq  # noqa: PLW0603
    kind = _KIND_BY_TOOL.get(tool_name, "read")
    entities, project = _extract(tool_name, kind, kwargs, result)
    _seq += 1
    _events.append(
        {
            "id": _seq,
            "ts": time.time(),
            "kind": kind,
            "tool": tool_name,
            "entities": entities,
            "project": project,
        }
    )


def recent(since: int) -> list[ActivityEvent]:
    """Return events recorded after the given sequence id, oldest first."""
    return [e for e in _events if e["id"] > since]


def latest_seq() -> int:
    """Return the most recent event sequence id (0 if none recorded)."""
    return _seq


def clear() -> None:
    """Empty the buffer and reset the sequence counter (for test isolation)."""
    global _seq  # noqa: PLW0603
    _events.clear()
    _seq = 0


def _extract(
    tool_name: str, kind: Kind, kwargs: dict[str, Any], result: Any
) -> tuple[list[str], str | None]:
    """Derive the affected entity names and project from a tool call."""
    if kind == "read":
        return _names_from_read_result(result), kwargs.get("project")
    project = (
        kwargs.get("source") if tool_name == "move_project_entities" else kwargs.get("project")
    )
    return _names_from_write_kwargs(tool_name, kwargs), project


def _names_from_write_kwargs(tool_name: str, kwargs: dict[str, Any]) -> list[str]:
    """Pull the entity names a write tool affects out of its arguments."""
    names: list[str] = []
    if tool_name == "create_entities":
        for entity in kwargs.get("entities", []) or []:
            if isinstance(entity, dict) and entity.get("name"):
                names.append(str(entity["name"]))
    elif tool_name in ("add_observations", "delete_observations") and kwargs.get("entityName"):
        names.append(str(kwargs["entityName"]))
    elif tool_name in ("delete_entity", "set_entity_status") and kwargs.get("name"):
        names.append(str(kwargs["name"]))
    elif tool_name == "create_relations":
        for relation in kwargs.get("relations", []) or []:
            if isinstance(relation, dict):
                names.extend(str(relation[k]) for k in ("source", "target") if relation.get(k))
    elif tool_name == "delete_relation":
        names.extend(str(kwargs[k]) for k in ("source", "target") if kwargs.get(k))
    return _dedup(names)


def _names_from_read_result(result: Any) -> list[str]:
    """Pull entity names out of a read tool's result, handling every shape."""
    names: list[str] = []
    if not isinstance(result, dict):
        return names

    entity = result.get("entity")
    if entity is not None:
        _append_name(names, entity)
    for key in ("entities", "relatedEntities"):
        for item in result.get(key, []) or []:
            _append_name(names, item)

    grouped = result.get("results")
    if isinstance(grouped, dict):
        for group in grouped.values():
            if isinstance(group, dict):
                for item in group.get("entities", []) or []:
                    _append_name(names, item)

    return _dedup(names)


def _append_name(names: list[str], obj: Any) -> None:
    """Append obj's name, whether it is an Entity dataclass or a dict."""
    name = getattr(obj, "name", None)
    if name is None and isinstance(obj, dict):
        name = obj.get("name")
    if name:
        names.append(str(name))


def _dedup(names: list[str]) -> list[str]:
    """Remove duplicate names while preserving first-seen order."""
    return list(dict.fromkeys(names))
