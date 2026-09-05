"""Composed entity reads: lookup, graph traversal, ranked search and the visualiser feed."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import TYPE_CHECKING, NotRequired, TypedDict

from mcp_memory.storage.pure.ranking import score_row
from mcp_memory.storage.pure.rows import build_entity
from mcp_memory.storage.pure.sql import parse_date, placeholders
from mcp_memory.storage.services.fts import sanitize_fts_query
from mcp_memory.storage.services.ids import get_entity_id, get_or_create_project_id

if TYPE_CHECKING:
    from mcp_memory.models import Entity, EntityStatus, Observation, Relation
    from mcp_memory.storage.connection import Connection
    from mcp_memory.storage.repositories.observations import ObservationRepository
    from mcp_memory.storage.repositories.relations import RelationRepository


class GraphResult(TypedDict):
    entity: Entity
    relations: list[Relation]
    relatedEntities: list[Entity]


class NodeList(TypedDict):
    entities: list[Entity]
    relations: list[Relation]
    relations_by_project: NotRequired[dict[str, list[Relation]]]


def _search_nodes_filters(
    project: str | list[str] | None,
    entity_type: str | None,
    status: EntityStatus | list[EntityStatus] | None,
    start: str | None,
    end: str | None,
    *,
    include_archived: bool = False,
) -> tuple[str, list[str | int]]:
    """Build the WHERE-clause fragment and params for ``search``'s optional filters.

    Archived entities are excluded unless ``include_archived`` is set or an explicit
    ``status`` filter is given.
    """
    sql = ""
    params: list[str | int] = []
    if isinstance(project, str):
        sql += " AND p.name = ?"
        params.append(project)
    elif project is not None:
        sql += f" AND p.name IN ({placeholders(len(project))})"
        params.extend(project)
    if entity_type is not None:
        sql += " AND et.name = ?"
        params.append(entity_type)
    if status is not None:
        statuses = [status] if isinstance(status, str) else status
        sql += f" AND e.status IN ({placeholders(len(statuses))})"
        params.extend(statuses)
    elif not include_archived:
        sql += " AND e.status IS NOT ?"
        params.append("archived")
    if start is not None:
        sql += " AND datetime(e.created_at) >= datetime(?)"
        params.append(parse_date(start))
    if end is not None:
        sql += " AND datetime(e.created_at) <= datetime(?)"
        params.append(parse_date(end))
    return sql, params


_SEARCH_SQL = (
    "SELECT e.id, e.project_id, p.name AS project_name, "
    "e.name, et.name AS entity_type, e.status, "
    "e.created_at, e.updated_at, e.vote_score, "
    "bm25(entities_fts, 10.0, 1.0, 1.0, 1.0) AS rank "
    "FROM entities_fts fts "
    "JOIN entities e ON fts.rowid = e.id "
    "JOIN entity_types et ON e.entity_type_id = et.id "
    "JOIN projects p ON e.project_id = p.id "
    "WHERE entities_fts MATCH ? AND e.deleted_at IS NULL"
)


class Reads:
    """Composed entity reads: lookup, graph traversal, ranked search and the visualiser feed."""

    def __init__(
        self,
        connection: Connection,
        observations: ObservationRepository,
        relations: RelationRepository,
    ) -> None:
        self._conn = connection
        self._observations = observations
        self._relations = relations

    def _hydrate_entity(
        self,
        row: sqlite3.Row,
        entity_id: int,
        *,
        compact: bool = False,
        max_observation_chars: int | None = None,
        project_name: str | None = None,
    ) -> Entity:
        if compact:
            observations: list[Observation] = []
            omitted = 0
        else:
            observations, omitted = self._observations.budgeted(entity_id, max_observation_chars)
        return build_entity(row, observations, omitted, project_name=project_name)

    def get_entity(
        self,
        project: str,
        name: str,
        compact: bool = False,
        max_observation_chars: int | None = None,
    ) -> Entity:
        """Get a single entity by name."""
        project_id = get_or_create_project_id(self._conn, project)
        row = self._conn.query_one(
            "SELECT e.id, e.name, et.name AS entity_type, e.status, e.created_at, e.updated_at, "
            "e.vote_score "
            "FROM entities e "
            "JOIN entity_types et ON e.entity_type_id = et.id "
            "WHERE e.name = ? AND e.project_id = ? AND e.deleted_at IS NULL",
            (name, project_id),
        )
        if row is None:
            raise ValueError(f"Entity '{name}' not found in project '{project}'")
        return self._hydrate_entity(row, row["id"], compact=compact, max_observation_chars=max_observation_chars)

    def get_entity_with_relations(
        self,
        project: str,
        name: str,
        entity_type: str | None = None,
        relation_type: str | None = None,
        compact: bool = False,
        max_observation_chars: int | None = None,
    ) -> GraphResult:
        """Get an entity with its relations and related entities, optionally filtered.

        Args:
            project: Project scope to look up the entity in.
            name: Name of the entity to retrieve.
            entity_type: If given, keep only related entities of this type (post-traversal).
            relation_type: If given, keep only relations of this type (pre-traversal, so
                related entities are derived only from the surviving relations).
            compact: Omit observations from the returned entities when True.
            max_observation_chars: Per-entity observation character budget.
        """
        entity = self.get_entity(project, name, compact=compact, max_observation_chars=max_observation_chars)
        project_id = get_or_create_project_id(self._conn, project)
        entity_id = get_entity_id(self._conn, name, project_id)

        relations = self._relations.for_entities(project_id, [entity_id])  # type: ignore[list-item]

        if relation_type is not None:
            relations = [r for r in relations if r.relation_type == relation_type]

        related_names = set()
        for rel in relations:
            if rel.source != name:
                related_names.add(rel.source)
            if rel.target != name:
                related_names.add(rel.target)

        related_entities = [
            self.get_entity(project, n, compact=compact, max_observation_chars=max_observation_chars)
            for n in related_names
        ]

        if entity_type is not None:
            related_entities = [e for e in related_entities if e.entity_type == entity_type]

        return {
            "entity": entity,
            "relations": relations,
            "relatedEntities": related_entities,
        }

    def search(
        self,
        project: str | list[str] | None,
        query: str,
        limit: int = 10,
        entity_type: str | None = None,
        status: EntityStatus | list[EntityStatus] | None = None,
        start: str | None = None,
        end: str | None = None,
        compact: bool = False,
        match_all: bool = False,
        max_observation_chars: int | None = None,
        now: datetime | None = None,
        include_archived: bool = False,
    ) -> NodeList:
        """Search entities using FTS5 full-text search with recency-weighted BM25 ranking.

        Multi-term queries match any term by default; pass match_all to require all terms.
        A list of statuses is OR'd together. A list of projects unions results across them.
        ``now`` pins the instant recency decay is measured from, defaulting to the current
        time; a replay (see ``eval.evaluate``) passes a fixed instant so the same graph scores
        identically on any day. Exact score ties break on ascending entity id, since the FTS
        scan order is implementation-defined. Archived entities are excluded unless
        ``include_archived`` is set or an explicit ``status`` is given.
        """
        sanitized = sanitize_fts_query(query, match_all=match_all)
        if not sanitized:
            return {"entities": [], "relations": []}

        sql = _SEARCH_SQL
        params: list[str | int] = [sanitized]

        filter_sql, filter_params = _search_nodes_filters(
            project, entity_type, status, start, end, include_archived=include_archived
        )
        sql += filter_sql
        params.extend(filter_params)

        rows = self._conn.query_all(sql, params)

        if now is None:
            now = datetime.now(tz=UTC)
        scored: list[tuple[float, sqlite3.Row]] = []
        for row in rows:
            score = score_row(
                rank=row["rank"],
                updated_at=row["updated_at"],
                entity_type=row["entity_type"],
                vote_score=int(row["vote_score"]),
                now=now,
            )
            scored.append((score, row))

        scored.sort(key=lambda x: (-x[0], int(x[1]["id"])))
        top_rows = [row for _, row in scored[:limit]]

        entities = [
            self._hydrate_entity(
                row,
                row["id"],
                compact=compact,
                max_observation_chars=max_observation_chars,
                project_name=row["project_name"],
            )
            for row in top_rows
        ]
        entity_ids = [row["id"] for row in top_rows]

        if isinstance(project, str):
            project_id = get_or_create_project_id(self._conn, project)
            return {
                "entities": entities,
                "relations": self._relations.for_entities(project_id, entity_ids),
            }

        by_project = self._relations.for_entity_ids(entity_ids)
        return {
            "entities": entities,
            "relations": [rel for group in by_project.values() for rel in group],
            "relations_by_project": by_project,
        }

    def recent(
        self,
        project: str,
        status: EntityStatus | None = None,
        compact: bool = False,
        max_observation_chars: int | None = None,
    ) -> NodeList:
        """Return the 10 most recently created entities and their relations."""
        project_id = get_or_create_project_id(self._conn, project)

        sql = (
            "SELECT e.id, e.name, et.name AS entity_type, e.status, e.created_at, e.updated_at, "
            "e.vote_score "
            "FROM entities e "
            "JOIN entity_types et ON e.entity_type_id = et.id "
            "WHERE e.project_id = ? AND e.deleted_at IS NULL"
        )
        params: list[str | int] = [project_id]

        if status is not None:
            sql += " AND e.status = ?"
            params.append(status)

        sql += " ORDER BY e.created_at DESC LIMIT 10"

        rows = self._conn.query_all(sql, params)

        entities = [
            self._hydrate_entity(row, row["id"], compact=compact, max_observation_chars=max_observation_chars)
            for row in rows
        ]
        entity_ids = [row["id"] for row in rows]
        relations = self._relations.for_entities(project_id, entity_ids)

        return {"entities": entities, "relations": relations}

    def all_entities(self, project: str | None = None) -> NodeList:
        """Return every entity and its relations, optionally scoped to one project.

        Serves the visualiser. Observations are unbudgeted - the full detail, not the
        config-limited read every other method here applies.
        """
        where = "WHERE e.deleted_at IS NULL"
        params: list[str | int] = []
        if project is not None:
            where += " AND e.project_id = ?"
            params.append(get_or_create_project_id(self._conn, project))

        rows = self._conn.query_all(
            "SELECT e.id, e.name, et.name AS entity_type, e.status, e.project_id, "
            "p.name AS project_name, e.created_at, e.updated_at, e.vote_score "
            "FROM entities e "
            "JOIN entity_types et ON e.entity_type_id = et.id "
            "JOIN projects p ON e.project_id = p.id " + where,
            params,
        )

        entities: list[Entity] = []
        entity_ids: list[int] = []
        project_ids: set[int] = set()
        for row in rows:
            observations, omitted = self._observations.budgeted(row["id"], -1)
            entities.append(build_entity(row, observations, omitted, project_name=row["project_name"]))
            entity_ids.append(row["id"])
            project_ids.add(row["project_id"])

        relations: list[Relation] = []
        for pid in project_ids:
            relations.extend(self._relations.for_entities(pid, entity_ids))

        return {"entities": entities, "relations": relations}
