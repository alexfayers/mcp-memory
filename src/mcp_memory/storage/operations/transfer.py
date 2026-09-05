"""Cross-project export/import: full snapshots and dry-run-capable merges."""

from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING, Any, TypedDict, cast

from mcp_memory.migrations.runner import schema_version
from mcp_memory.models import normalize_relation_type
from mcp_memory.storage.pure.rows import build_entity
from mcp_memory.storage.services.ids import (
    get_entity_id,
    get_entity_type_name,
    get_or_create_entity_type_id,
    get_or_create_project_id,
    get_or_create_relation_type_id,
)

if TYPE_CHECKING:
    from mcp_memory.models import Relation
    from mcp_memory.storage.connection import Connection
    from mcp_memory.storage.repositories.entities import EntityRepository
    from mcp_memory.storage.repositories.observations import ObservationRepository
    from mcp_memory.storage.repositories.projects import ProjectRepository
    from mcp_memory.storage.repositories.relations import RelationRepository


class ImportCounts(TypedDict):
    entities_new: int
    entities_merged: int
    entities_skipped_type_mismatch: list[str]
    observations_new: int
    observations_duplicate: int
    relations_new: int
    relations_duplicate: int
    groups_added: int


class Transfer:
    """Cross-project export, import and dry-run merge of entities, relations and groups."""

    def __init__(
        self,
        connection: Connection,
        entities: EntityRepository,
        observations: ObservationRepository,
        relations: RelationRepository,
        projects: ProjectRepository,
    ) -> None:
        self._conn = connection
        self._entities = entities
        self._observations = observations
        self._relations = relations
        self._projects = projects

    def export_data(self) -> dict[str, object]:
        """Return a live-only, all-projects snapshot: schema version plus per-project data.

        Excludes soft-deleted entities and the ephemeral telemetry tables. Relations are
        intra-project only. Callers wrap this in the export file envelope.
        """
        projects: dict[str, object] = {}
        for project in self._projects.names():
            project_id = get_or_create_project_id(self._conn, project)
            rows = self._conn.query_all(
                "SELECT e.id, e.name, et.name AS entity_type, e.status, e.created_at, "
                "e.updated_at, e.vote_score FROM entities e "
                "JOIN entity_types et ON e.entity_type_id = et.id "
                "WHERE e.project_id = ? AND e.deleted_at IS NULL ORDER BY e.id",
                (project_id,),
            )
            entities: list[dict[str, object]] = []
            entity_ids: list[int] = []
            for row in rows:
                entity_id = int(row["id"])
                entity_ids.append(entity_id)
                built = build_entity(row, [], 0, project_name=project)
                entities.append({
                    "name": built.name,
                    "entity_type": built.entity_type,
                    "observations": self._observations.export_rows(entity_id),
                    "status": built.status,
                    "created_at": built.created_at,
                    "updated_at": built.updated_at,
                    "project_name": project,
                    "vote_score": built.vote_score,
                })
            relations = self._relations.for_entities(project_id, entity_ids)
            projects[project] = {
                "paths": self._projects.paths_for(project),
                "groups": [group for _, group in self._projects.groups(project)],
                "entities": entities,
                "relations": [asdict(relation) for relation in relations],
            }
        return {"schema_version": schema_version(self._conn.raw), "projects": projects}

    def import_project_data(
        self,
        project: str,
        entities: list[dict[str, Any]],
        relations: list[Relation],
        groups: list[str],
        *,
        dry_run: bool = False,
    ) -> ImportCounts:
        """Merge one project's exported entities, relations and groups into this database.

        Additive and merge-safe: new entities are inserted verbatim (preserving
        created_at/updated_at/status/vote_score and each observation's created_at), existing
        same-type entities gain the higher vote_score and any missing observations, and
        different-type collisions are skipped. Relations and groups are deduped. With dry_run,
        every write is rolled back so the returned counts match a real run without changing
        the database. Entities are raw export dicts, mirroring create's dict contract.
        """
        counts: ImportCounts = {
            "entities_new": 0,
            "entities_merged": 0,
            "entities_skipped_type_mismatch": [],
            "observations_new": 0,
            "observations_duplicate": 0,
            "relations_new": 0,
            "relations_duplicate": 0,
            "groups_added": 0,
        }
        with self._conn.transaction(commit=not dry_run):
            project_id = get_or_create_project_id(self._conn, project)
            for entity in entities:
                classification, entity_id, restore_updated_at = self._import_entity(project, project_id, entity)
                if classification == "skipped" or entity_id is None:
                    counts["entities_skipped_type_mismatch"].append(entity["name"])
                    continue
                if classification == "new":
                    counts["entities_new"] += 1
                else:
                    counts["entities_merged"] += 1
                new_obs, dup_obs = self._import_observations(entity_id, entity.get("observations", []))
                counts["observations_new"] += new_obs
                counts["observations_duplicate"] += dup_obs
                if restore_updated_at is not None:
                    self._conn.write(
                        "UPDATE entities SET updated_at = ? WHERE id = ?",
                        (restore_updated_at, entity_id),
                    )
            new_rel, dup_rel = self._import_relations(project_id, relations)
            counts["relations_new"] = new_rel
            counts["relations_duplicate"] = dup_rel
            counts["groups_added"] = self._import_groups(project_id, groups)
        return counts

    def _import_entity(
        self, project: str, project_id: int, entity: dict[str, Any]
    ) -> tuple[str, int | None, str | None]:
        """Insert or merge one entity. Returns (classification, entity_id, updated_at_to_restore).

        A new entity is inserted preserving its timestamps/status/vote_score. An existing
        same-type entity keeps its own timestamps and status but takes the higher vote_score.
        A different-type collision is left untouched and reported as skipped.
        """
        name = entity["name"]
        entity_type = entity["entity_type"]
        existing_id = get_entity_id(self._conn, name, project_id)
        if existing_id is None:
            self._entities.purge_tombstone(name, project, project_id)
            entity_type_id = get_or_create_entity_type_id(self._conn, entity_type)
            cursor = self._conn.write(
                "INSERT INTO entities "
                "(name, entity_type_id, project_id, status, created_at, updated_at, vote_score) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    name,
                    entity_type_id,
                    project_id,
                    entity.get("status"),
                    entity.get("created_at"),
                    entity.get("updated_at"),
                    entity.get("vote_score", 0),
                ),
            )
            return "new", cast("int", cursor.lastrowid), entity.get("updated_at")

        if get_entity_type_name(self._conn, existing_id) != entity_type:
            return "skipped", None, None

        existing_row = self._conn.query_one("SELECT updated_at FROM entities WHERE id = ?", (existing_id,))
        assert existing_row is not None
        existing_updated_at = existing_row["updated_at"]
        self._conn.write(
            "UPDATE entities SET vote_score = MAX(vote_score, ?) WHERE id = ?",
            (entity.get("vote_score", 0), existing_id),
        )
        return "merged", existing_id, existing_updated_at

    def _import_observations(self, entity_id: int, observations: list[dict[str, Any]]) -> tuple[int, int]:
        """Append observations, deduping by content_hash, preserving each observation's
        created_at. Returns (new_count, duplicate_count).
        """
        existing = {
            row["content_hash"]
            for row in self._conn.query_all("SELECT content_hash FROM observations WHERE entity_id = ?", (entity_id,))
        }
        new_count = 0
        duplicate_count = 0
        for obs in observations:
            content_hash = obs["content_hash"]
            if content_hash in existing:
                duplicate_count += 1
                continue
            existing.add(content_hash)
            new_count += 1
            created_at = obs.get("created_at")
            if created_at is None:
                self._conn.write(
                    "INSERT INTO observations (entity_id, content, content_hash, vote_score) VALUES (?, ?, ?, ?)",
                    (entity_id, obs["content"], content_hash, obs.get("vote_score", 0)),
                )
            else:
                self._conn.write(
                    "INSERT INTO observations "
                    "(entity_id, content, content_hash, vote_score, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (entity_id, obs["content"], content_hash, obs.get("vote_score", 0), created_at),
                )
        return new_count, duplicate_count

    def _import_relations(self, project_id: int, relations: list[Relation]) -> tuple[int, int]:
        """Insert relations, ignoring duplicates. Returns (new_count, duplicate_count).

        Relations whose endpoints are absent from the project (e.g. a skipped entity) are
        silently ignored, so a partial import never raises on a dangling reference.
        """
        new_count = 0
        duplicate_count = 0
        for relation in relations:
            source_id = get_entity_id(self._conn, relation.source, project_id)
            target_id = get_entity_id(self._conn, relation.target, project_id)
            if source_id is None or target_id is None:
                continue
            relation_type_id = get_or_create_relation_type_id(
                self._conn, normalize_relation_type(relation.relation_type)
            )
            exists = self._conn.query_one(
                "SELECT 1 FROM relations WHERE source_id = ? AND target_id = ? AND relation_type_id = ?",
                (source_id, target_id, relation_type_id),
            )
            if exists:
                duplicate_count += 1
                continue
            new_count += 1
            self._conn.write(
                "INSERT OR IGNORE INTO relations (source_id, target_id, relation_type_id) VALUES (?, ?, ?)",
                (source_id, target_id, relation_type_id),
            )
        return new_count, duplicate_count

    def _import_groups(self, project_id: int, groups: list[str]) -> int:
        """Add group memberships that are not already present. Returns the number added."""
        existing = {
            row["group_name"]
            for row in self._conn.query_all("SELECT group_name FROM project_groups WHERE project_id = ?", (project_id,))
        }
        added = 0
        for group_name in groups:
            if group_name in existing:
                continue
            existing.add(group_name)
            added += 1
            self._conn.write(
                "INSERT OR IGNORE INTO project_groups (project_id, group_name) VALUES (?, ?)",
                (project_id, group_name),
            )
        return added
