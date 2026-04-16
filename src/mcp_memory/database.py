"""Database manager for the MCP memory server."""

from __future__ import annotations

import math
import re
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

from .migrations.runner import run_migrations
from .models import VALID_STATUSES, Entity, EntityStatus, Relation

_RECENCY_HALF_LIFE_DAYS = 30.0
_RECENCY_FLOOR = 0.1

_RELATIVE_DATE_RE = re.compile(r"^(\d+)([dwm])$")
_RELATIVE_UNITS = {"d": 1, "w": 7, "m": 30}


def _parse_date(value: str) -> str:
    """Parse a relative ('7d', '2w', '3m') or ISO date string to an ISO timestamp."""
    match = _RELATIVE_DATE_RE.match(value.strip())
    if match:
        amount, unit = int(match.group(1)), match.group(2)
        days = amount * _RELATIVE_UNITS[unit]
        dt = datetime.now(tz=UTC) - timedelta(days=days)
        return dt.isoformat()
    return datetime.fromisoformat(value).isoformat()


class DatabaseManager:
    """Manages all database operations for the MCP memory knowledge graph."""

    def __init__(self, db_path: str | Path) -> None:
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        self._db = sqlite3.connect(str(path))
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.execute("PRAGMA cache_size=1000")
        self._db.execute("PRAGMA temp_store=MEMORY")
        self._db.execute("PRAGMA foreign_keys=ON")

        run_migrations(self._db)

    def _get_or_create_project_id(self, project: str) -> int:
        self._db.execute("INSERT OR IGNORE INTO projects (name) VALUES (?)", (project,))
        row = self._db.execute("SELECT id FROM projects WHERE name = ?", (project,)).fetchone()
        return int(row["id"])

    def _get_or_create_entity_type_id(self, entity_type: str) -> int:
        self._db.execute("INSERT OR IGNORE INTO entity_types (name) VALUES (?)", (entity_type,))
        row = self._db.execute(
            "SELECT id FROM entity_types WHERE name = ?", (entity_type,)
        ).fetchone()
        return int(row["id"])

    def _get_or_create_relation_type_id(self, relation_type: str) -> int:
        self._db.execute("INSERT OR IGNORE INTO relation_types (name) VALUES (?)", (relation_type,))
        row = self._db.execute(
            "SELECT id FROM relation_types WHERE name = ?", (relation_type,)
        ).fetchone()
        return int(row["id"])

    def _get_entity_id(self, name: str, project_id: int) -> int | None:
        row = self._db.execute(
            "SELECT id FROM entities WHERE name = ? AND project_id = ?",
            (name, project_id),
        ).fetchone()
        return row["id"] if row else None

    def entity_exists_in_project(self, name: str, project: str) -> bool:
        """Check if an entity name exists in a specific project scope."""
        row = self._db.execute(
            "SELECT 1 FROM entities e JOIN projects p ON e.project_id = p.id "
            "WHERE e.name = ? AND p.name = ?",
            (name, project),
        ).fetchone()
        return row is not None

    def entity_exists_outside_project(self, name: str, project: str) -> str | None:
        """Return the first project where this entity exists, excluding the given project."""
        row = self._db.execute(
            "SELECT p.name FROM entities e JOIN projects p ON e.project_id = p.id "
            "WHERE e.name = ? AND p.name != ?",
            (name, project),
        ).fetchone()
        return row["name"] if row else None

    def _get_observations(self, entity_id: int) -> list[str]:
        rows = self._db.execute(
            "SELECT content FROM observations WHERE entity_id = ? ORDER BY id",
            (entity_id,),
        ).fetchall()
        return [row["content"] for row in rows]

    def _build_entity(self, row: sqlite3.Row, entity_id: int) -> Entity:
        return Entity(
            name=row["name"],
            entity_type=row["entity_type"],
            observations=self._get_observations(entity_id),
            status=row["status"],
            created_at=row["created_at"],
        )

    def _sanitize_fts_query(self, query: str) -> str:
        """Escape and quote tokens for FTS5 MATCH."""
        tokens = query.split()
        return " ".join(
            f'"{token.replace(chr(34), chr(34) + chr(34))}"' for token in tokens if token
        )

    def _get_relations_for_entities(self, project_id: int, entity_ids: list[int]) -> list[Relation]:
        if not entity_ids:
            return []
        placeholders = ",".join("?" * len(entity_ids))
        rows = self._db.execute(
            f"SELECT e_src.name AS source, e_tgt.name AS target, "
            f"rt.name AS relation_type "
            f"FROM relations r "
            f"JOIN entities e_src ON r.source_id = e_src.id "
            f"JOIN entities e_tgt ON r.target_id = e_tgt.id "
            f"JOIN relation_types rt ON r.relation_type_id = rt.id "
            f"WHERE e_src.project_id = ? "
            f"AND (r.source_id IN ({placeholders}) "
            f"OR r.target_id IN ({placeholders}))",
            [project_id, *entity_ids, *entity_ids],
        ).fetchall()
        return [
            Relation(source=row["source"], target=row["target"], relation_type=row["relation_type"])
            for row in rows
        ]

    def create_entities(self, project: str, entities: list[dict[str, object]]) -> None:
        """Upsert entities with observations, overwriting existing observations."""
        project_id = self._get_or_create_project_id(project)

        with self._db:
            for entity_data in entities:
                name = entity_data.get("name")
                entity_type = entity_data.get("entityType")
                observations = entity_data.get("observations")
                status = entity_data.get("status")

                if not name or not isinstance(name, str):
                    raise ValueError(f"Entity name must be a non-empty string, got: {name!r}")
                if not entity_type or not isinstance(entity_type, str):
                    raise ValueError(
                        f"Entity type must be a non-empty string, got: {entity_type!r}"
                    )
                if not isinstance(observations, list) or not observations:
                    raise ValueError(f"Observations must be a non-empty list for entity '{name}'")
                for obs in observations:
                    if not isinstance(obs, str) or not obs:
                        raise ValueError(
                            f"Each observation must be a non-empty string "
                            f"for entity '{name}', got: {obs!r}"
                        )

                if status is not None and status not in VALID_STATUSES:
                    raise ValueError(
                        f"Invalid status '{status}' for entity '{name}'. "
                        f"Must be one of: {VALID_STATUSES}"
                    )

                entity_type_id = self._get_or_create_entity_type_id(str(entity_type))
                existing_id = self._get_entity_id(str(name), project_id)

                if existing_id is not None:
                    self._db.execute(
                        "UPDATE entities SET entity_type_id = ?, status = ? WHERE id = ?",
                        (entity_type_id, status, existing_id),
                    )
                    self._db.execute("DELETE FROM observations WHERE entity_id = ?", (existing_id,))
                    entity_id = existing_id
                else:
                    cursor = self._db.execute(
                        "INSERT INTO entities (name, entity_type_id, project_id, status) "
                        "VALUES (?, ?, ?, ?)",
                        (name, entity_type_id, project_id, status),
                    )
                    entity_id = cast("int", cursor.lastrowid)

                self._db.executemany(
                    "INSERT INTO observations (entity_id, content) VALUES (?, ?)",
                    [(entity_id, obs) for obs in observations],
                )

    def add_observations(self, project: str, entity_name: str, observations: list[str]) -> int:
        """Append deduplicated observations to an existing entity."""
        project_id = self._get_or_create_project_id(project)
        entity_id = self._get_entity_id(entity_name, project_id)
        if entity_id is None:
            raise ValueError(f"Entity '{entity_name}' not found in project '{project}'")

        existing = set(self._get_observations(entity_id))
        new_observations = [obs for obs in observations if obs not in existing]

        if new_observations:
            self._db.executemany(
                "INSERT INTO observations (entity_id, content) VALUES (?, ?)",
                [(entity_id, obs) for obs in new_observations],
            )
            self._db.commit()

        return len(new_observations)

    def delete_observations(self, project: str, entity_name: str, observations: list[str]) -> int:
        """Delete observations by exact content match."""
        project_id = self._get_or_create_project_id(project)
        entity_id = self._get_entity_id(entity_name, project_id)
        if entity_id is None:
            raise ValueError(f"Entity '{entity_name}' not found in project '{project}'")

        count = 0
        with self._db:
            for obs in observations:
                cursor = self._db.execute(
                    "DELETE FROM observations WHERE entity_id = ? AND content = ?",
                    (entity_id, obs),
                )
                count += cursor.rowcount

        return count

    def set_entity_status(self, project: str, name: str, status: EntityStatus | None) -> None:
        """Set or clear the status of an entity."""
        if status is not None and status not in VALID_STATUSES:
            raise ValueError(f"Invalid status '{status}'. Must be one of: {VALID_STATUSES}")

        project_id = self._get_or_create_project_id(project)
        entity_id = self._get_entity_id(name, project_id)
        if entity_id is None:
            raise ValueError(f"Entity '{name}' not found in project '{project}'")

        self._db.execute("UPDATE entities SET status = ? WHERE id = ?", (status, entity_id))
        self._db.commit()

    def create_relations(self, project: str, relations: list[Relation]) -> None:
        """Create relations between entities, ignoring duplicates."""
        project_id = self._get_or_create_project_id(project)

        for relation in relations:
            if relation.source == relation.target:
                raise ValueError(f"Self-referential relation not allowed: '{relation.source}'")
            source_id = self._get_entity_id(relation.source, project_id)
            if source_id is None:
                raise ValueError(
                    f"Source entity '{relation.source}' not found in project '{project}'"
                )
            target_id = self._get_entity_id(relation.target, project_id)
            if target_id is None:
                raise ValueError(
                    f"Target entity '{relation.target}' not found in project '{project}'"
                )
            relation_type_id = self._get_or_create_relation_type_id(relation.relation_type)
            self._db.execute(
                "INSERT OR IGNORE INTO relations "
                "(source_id, target_id, relation_type_id) VALUES (?, ?, ?)",
                (source_id, target_id, relation_type_id),
            )

        self._db.commit()

    def delete_entity(self, project: str, name: str) -> None:
        """Delete an entity, cascading outgoing relations but blocking on incoming."""
        project_id = self._get_or_create_project_id(project)
        entity_id = self._get_entity_id(name, project_id)
        if entity_id is None:
            raise ValueError(f"Entity '{name}' not found in project '{project}'")

        incoming = self._db.execute(
            "SELECT e.name FROM relations r "
            "JOIN entities e ON r.source_id = e.id "
            "WHERE r.target_id = ? AND r.source_id != ?",
            (entity_id, entity_id),
        ).fetchall()
        if incoming:
            sources = [row["name"] for row in incoming]
            raise ValueError(
                f"Cannot delete '{name}': {len(sources)} incoming relation(s) from: "
                + ", ".join(sources)
            )

        with self._db:
            self._db.execute("DELETE FROM observations WHERE entity_id = ?", (entity_id,))
            self._db.execute("DELETE FROM relations WHERE source_id = ?", (entity_id,))
            self._db.execute("DELETE FROM entities WHERE id = ?", (entity_id,))

    def delete_relation(self, project: str, source: str, target: str, relation_type: str) -> None:
        """Delete a specific relation between two entities."""
        project_id = self._get_or_create_project_id(project)
        source_id = self._get_entity_id(source, project_id)
        if source_id is None:
            raise ValueError(f"Source entity '{source}' not found in project '{project}'")
        target_id = self._get_entity_id(target, project_id)
        if target_id is None:
            raise ValueError(f"Target entity '{target}' not found in project '{project}'")

        row = self._db.execute(
            "SELECT id FROM relation_types WHERE name = ?", (relation_type,)
        ).fetchone()
        if row is None:
            raise ValueError(f"Relation type '{relation_type}' not found")

        cursor = self._db.execute(
            "DELETE FROM relations WHERE source_id = ? AND target_id = ? AND relation_type_id = ?",
            (source_id, target_id, row["id"]),
        )
        if cursor.rowcount == 0:
            raise ValueError(
                f"Relation '{source}' -> '{target}' ({relation_type}) "
                f"not found in project '{project}'"
            )
        self._db.commit()

    def get_entity(self, project: str, name: str) -> Entity:
        """Get a single entity by name."""
        project_id = self._get_or_create_project_id(project)
        row = self._db.execute(
            "SELECT e.id, e.name, et.name AS entity_type, e.status, e.created_at "
            "FROM entities e "
            "JOIN entity_types et ON e.entity_type_id = et.id "
            "WHERE e.name = ? AND e.project_id = ?",
            (name, project_id),
        ).fetchone()
        if row is None:
            raise ValueError(f"Entity '{name}' not found in project '{project}'")
        return self._build_entity(row, row["id"])

    def get_entity_with_relations(
        self, project: str, name: str
    ) -> dict[str, Entity | list[Relation] | list[Entity]]:
        """Get an entity with all its relations and related entities."""
        entity = self.get_entity(project, name)
        project_id = self._get_or_create_project_id(project)
        entity_id = self._get_entity_id(name, project_id)

        relations = self._get_relations_for_entities(project_id, [entity_id])  # type: ignore[list-item]

        related_names = set()
        for rel in relations:
            if rel.source != name:
                related_names.add(rel.source)
            if rel.target != name:
                related_names.add(rel.target)

        related_entities = [self.get_entity(project, n) for n in related_names]

        return {
            "entity": entity,
            "relations": relations,
            "relatedEntities": related_entities,
        }

    def search_related_nodes(
        self,
        project: str,
        name: str,
        entity_type: str | None = None,
        relation_type: str | None = None,
    ) -> dict[str, Entity | list[Relation] | list[Entity]]:
        """Get an entity with filtered relations and related entities."""
        entity = self.get_entity(project, name)
        project_id = self._get_or_create_project_id(project)
        entity_id = self._get_entity_id(name, project_id)

        relations = self._get_relations_for_entities(project_id, [entity_id])  # type: ignore[list-item]

        if relation_type is not None:
            relations = [r for r in relations if r.relation_type == relation_type]

        related_names = set()
        for rel in relations:
            if rel.source != name:
                related_names.add(rel.source)
            if rel.target != name:
                related_names.add(rel.target)

        related_entities = [self.get_entity(project, n) for n in related_names]

        if entity_type is not None:
            related_entities = [e for e in related_entities if e.entity_type == entity_type]

        return {
            "entity": entity,
            "relations": relations,
            "relatedEntities": related_entities,
        }

    def search_nodes(
        self,
        project: str,
        query: str,
        limit: int = 10,
        entity_type: str | None = None,
        status: EntityStatus | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict[str, list[Entity] | list[Relation]]:
        """Search entities using FTS5 full-text search with recency-weighted BM25 ranking."""
        sanitized = self._sanitize_fts_query(query)
        if not sanitized:
            return {"entities": [], "relations": []}

        sql = (
            "SELECT e.id, e.name, et.name AS entity_type, e.status, "
            "e.created_at, bm25(entities_fts) AS rank "
            "FROM entities_fts fts "
            "JOIN entities e ON fts.rowid = e.id "
            "JOIN entity_types et ON e.entity_type_id = et.id "
            "JOIN projects p ON e.project_id = p.id "
            "WHERE entities_fts MATCH ? AND p.name = ?"
        )
        params: list[str | int] = [sanitized, project]

        if entity_type is not None:
            sql += " AND et.name = ?"
            params.append(entity_type)
        if status is not None:
            sql += " AND e.status = ?"
            params.append(status)
        if start_date is not None:
            sql += " AND e.created_at >= ?"
            params.append(_parse_date(start_date))
        if end_date is not None:
            sql += " AND e.created_at <= ?"
            params.append(_parse_date(end_date))

        rows = self._db.execute(sql, params).fetchall()

        now = datetime.now(tz=UTC)
        scored: list[tuple[float, sqlite3.Row]] = []
        for row in rows:
            bm25_score = -float(row["rank"])
            created_at = datetime.fromisoformat(row["created_at"]).replace(tzinfo=UTC)
            age_days = max((now - created_at).total_seconds() / 86400, 0)
            decay = -math.log(2) * age_days / _RECENCY_HALF_LIFE_DAYS
            recency = max(math.exp(decay), _RECENCY_FLOOR)
            scored.append((bm25_score * recency, row))

        scored.sort(key=lambda x: x[0], reverse=True)
        top_rows = [row for _, row in scored[:limit]]

        entities = [self._build_entity(row, row["id"]) for row in top_rows]
        entity_ids = [row["id"] for row in top_rows]

        project_id = self._get_or_create_project_id(project)
        relations = self._get_relations_for_entities(project_id, entity_ids)

        return {"entities": entities, "relations": relations}

    def read_graph(
        self, project: str, status: EntityStatus | None = None
    ) -> dict[str, list[Entity] | list[Relation]]:
        """Return the 10 most recently created entities and their relations."""
        project_id = self._get_or_create_project_id(project)

        sql = (
            "SELECT e.id, e.name, et.name AS entity_type, e.status, e.created_at "
            "FROM entities e "
            "JOIN entity_types et ON e.entity_type_id = et.id "
            "WHERE e.project_id = ?"
        )
        params: list[str | int] = [project_id]

        if status is not None:
            sql += " AND e.status = ?"
            params.append(status)

        sql += " ORDER BY e.created_at DESC LIMIT 10"

        rows = self._db.execute(sql, params).fetchall()

        entities = [self._build_entity(row, row["id"]) for row in rows]
        entity_ids = [row["id"] for row in rows]
        relations = self._get_relations_for_entities(project_id, entity_ids)

        return {"entities": entities, "relations": relations}

    def close(self) -> None:
        """Close the database connection."""
        self._db.close()
