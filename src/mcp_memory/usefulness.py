"""Deterministic implicit-usefulness observer.

Turns observed behaviour into a ranking signal without any LLM judgement: when an entity
surfaced by a ranked search is edited within a short window, that is treated as an implicit
"this was useful" and a bounded ``+1`` is auto-cast. Surfacings are also recorded durably so
the ranking eval has ground-truth query -> result -> use data.

Wired into the server's ``@_track`` wrapper alongside activity recording, so it runs in-process
with a live database handle. Only real ranked-search tools surface; the visualiser's own
``/api/search`` bypasses ``@_track`` and so never pollutes the signal.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from .config import get_auto_vote_max_per_day, get_auto_vote_window_seconds

if TYPE_CHECKING:
    from .database import DatabaseManager

# Ranked-retrieval tools whose hits are recorded as surfacings. read_graph and direct lookups
# are excluded: their ordering is recency/identity, not relevance, so ranking quality is moot.
_SURFACE_TOOLS = frozenset({"search_nodes", "search_all_projects"})

# Write tools that count as "using" a surfaced entity. Deletions are deliberately excluded.
_USE_TOOLS = frozenset(
    {"add_observations", "create_entities", "create_relations", "set_entity_status"}
)


def observe(db: DatabaseManager, tool_name: str, kwargs: dict[str, Any], result: Any) -> None:
    """Record surfacings from searches and cast auto-votes when surfaced entities are edited.

    Never raises: instrumentation must not break the tool call it observes.
    """
    try:
        if isinstance(result, dict) and "error" in result:
            return
        if tool_name in _SURFACE_TOOLS:
            _record_surfacing(db, tool_name, kwargs, result)
        elif tool_name in _USE_TOOLS:
            _register_uses(db, tool_name, kwargs)
    except Exception:  # noqa: S110 - instrumentation must never break a tool call
        pass


def _record_surfacing(
    db: DatabaseManager, tool_name: str, kwargs: dict[str, Any], result: Any
) -> None:
    """Persist the (project, name, rank) hits a ranked search returned under one retrieval id."""
    hits = _surfaced_hits(result)
    if not hits:
        return
    query = str(kwargs.get("query", ""))
    db.record_surfaced(tool_name, query, uuid.uuid4().hex, hits)


def _register_uses(db: DatabaseManager, tool_name: str, kwargs: dict[str, Any]) -> None:
    """Cast an auto-vote for each entity a write touched, if it was recently surfaced."""
    window = get_auto_vote_window_seconds()
    max_per_day = get_auto_vote_max_per_day()
    for project, name in _used_targets(tool_name, kwargs):
        db.register_use(project, name, window_seconds=window, max_per_day=max_per_day)


def _surfaced_hits(result: Any) -> list[tuple[str, str, int]]:
    """Extract 1-based-ranked ``(project, name, rank)`` hits from a search result.

    Handles both the flat ``search_nodes`` shape (``{"entities": [...]}``) and the grouped
    ``search_all_projects`` shape (``{"results": {project: {"entities": [...]}}}``).
    """
    entities: list[Any] = []
    if isinstance(result, dict):
        entities.extend(result.get("entities", []) or [])
        grouped = result.get("results")
        if isinstance(grouped, dict):
            for group in grouped.values():
                if isinstance(group, dict):
                    entities.extend(group.get("entities", []) or [])

    hits: list[tuple[str, str, int]] = []
    for entity in entities:
        name = _attr(entity, "name")
        project = _attr(entity, "project_name")
        if name and project:
            hits.append((str(project), str(name), len(hits) + 1))
    return hits


def _used_targets(tool_name: str, kwargs: dict[str, Any]) -> list[tuple[str, str]]:
    """Extract the ``(project, entity_name)`` pairs a write tool affected."""
    project = kwargs.get("project")
    if not isinstance(project, str) or not project:
        return []

    names: list[str] = []
    if tool_name == "create_entities":
        for entity in kwargs.get("entities", []) or []:
            if isinstance(entity, dict) and entity.get("name"):
                names.append(str(entity["name"]))
    elif tool_name == "add_observations" and kwargs.get("entityName"):
        names.append(str(kwargs["entityName"]))
    elif tool_name == "set_entity_status" and kwargs.get("name"):
        names.append(str(kwargs["name"]))
    elif tool_name == "create_relations":
        for relation in kwargs.get("relations", []) or []:
            if isinstance(relation, dict):
                names.extend(str(relation[k]) for k in ("source", "target") if relation.get(k))
    return [(project, name) for name in dict.fromkeys(names)]


def _attr(obj: Any, field: str) -> Any:
    """Read a field from an Entity dataclass or a plain dict, returning None if absent."""
    value = getattr(obj, field, None)
    if value is None and isinstance(obj, dict):
        value = obj.get(field)
    return value
