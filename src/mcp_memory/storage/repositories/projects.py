"""Project repository: paths, groups and cross-project entity moves."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

from mcp_memory.path_resolver import match_project_for_path, normalize_path
from mcp_memory.storage.services.fts import refresh_for_entity
from mcp_memory.storage.services.ids import get_or_create_project_id

if TYPE_CHECKING:
    from mcp_memory.storage.connection import Connection


class ProjectRepository:
    """CRUD, path/group membership and cross-scope move operations over projects."""

    def __init__(self, connection: Connection) -> None:
        self._conn = connection

    def names(self) -> list[str]:
        """Return all project names from the database."""
        rows = self._conn.query_all("SELECT name FROM projects ORDER BY name")
        return [row["name"] for row in rows]

    def set_paths(self, project: str, paths: list[str]) -> None:
        """Replace the filesystem paths registered to a project with the given list."""
        if not project or not isinstance(project, str):
            raise ValueError(f"Project must be a non-empty string, got: {project!r}")
        if not isinstance(paths, list):
            raise TypeError(f"Paths must be a list, got: {paths!r}")

        with self._conn.transaction():
            project_id = get_or_create_project_id(self._conn, project)
            self._conn.write("DELETE FROM project_paths WHERE project_id = ?", (project_id,))
            for path in paths:
                normalized = normalize_path(path)
                try:
                    self._conn.write(
                        "INSERT INTO project_paths (project_id, path) VALUES (?, ?)",
                        (project_id, normalized),
                    )
                except sqlite3.IntegrityError as exc:
                    raise ValueError(f"Path '{normalized}' is already registered to another project") from exc

    def add_path(self, project: str, path: str) -> None:
        """Register one filesystem path for a project without replacing its existing paths.

        Idempotent and additive: unlike set_paths (which replaces all of a project's
        paths), this inserts a single path via INSERT OR IGNORE. Because
        project_paths.path is globally UNIQUE, a path already registered to another
        project is silently left untouched rather than raising - the intended
        no-clobber behaviour for automatic registration, where a path another project
        already owns means "already handled, do not steal it".
        """
        if not project or not isinstance(project, str):
            raise ValueError(f"Project must be a non-empty string, got: {project!r}")

        with self._conn.transaction():
            project_id = get_or_create_project_id(self._conn, project)
            self._conn.write(
                "INSERT OR IGNORE INTO project_paths (project_id, path) VALUES (?, ?)",
                (project_id, normalize_path(path)),
            )

    def paths(self, project: str | None = None) -> list[tuple[str, str]]:
        """Return (project_name, registered_path) mappings, optionally for one project."""
        sql = "SELECT p.name, pp.path FROM project_paths pp JOIN projects p ON pp.project_id = p.id"
        params: list[str] = []
        if project is not None:
            sql += " WHERE p.name = ?"
            params.append(project)
        rows = self._conn.query_all(sql, params)
        return [(row["name"], row["path"]) for row in rows]

    def get_project_for_path(self, path: str) -> str | None:
        """Return the project owning the longest registered path containing the given path."""
        return match_project_for_path(path, self.paths())

    def paths_for(self, project: str) -> list[str]:
        """Return the filesystem paths registered to a project, empty if none or unknown."""
        return [path for _, path in self.paths(project)]

    def set_groups(self, project: str, groups: list[str]) -> None:
        """Replace the groups a project belongs to with the given list."""
        if not project or not isinstance(project, str):
            raise ValueError(f"Project must be a non-empty string, got: {project!r}")
        if not isinstance(groups, list):
            raise TypeError(f"Groups must be a list, got: {groups!r}")

        with self._conn.transaction():
            project_id = get_or_create_project_id(self._conn, project)
            self._conn.write("DELETE FROM project_groups WHERE project_id = ?", (project_id,))
            for group_name in groups:
                self._conn.write(
                    "INSERT INTO project_groups (project_id, group_name) VALUES (?, ?)",
                    (project_id, group_name),
                )

    def groups(self, project: str | None = None) -> list[tuple[str, str]]:
        """Return (project_name, group_name) mappings, optionally for one project."""
        sql = "SELECT p.name, pg.group_name FROM project_groups pg JOIN projects p ON pg.project_id = p.id"
        params: list[str] = []
        if project is not None:
            sql += " WHERE p.name = ?"
            params.append(project)
        rows = self._conn.query_all(sql, params)
        return [(row["name"], row["group_name"]) for row in rows]

    def group_members(self, project: str) -> list[str]:
        """Return the other projects sharing any group with the given project.

        Empty if the project belongs to no group. The project itself is excluded.
        """
        rows = self._conn.query_all(
            "SELECT DISTINCT p2.name FROM project_groups pg1 "
            "JOIN projects p1 ON pg1.project_id = p1.id "
            "JOIN project_groups pg2 ON pg2.group_name = pg1.group_name "
            "JOIN projects p2 ON pg2.project_id = p2.id "
            "WHERE p1.name = ? AND p2.name != ? "
            "ORDER BY p2.name",
            (project, project),
        )
        return [row["name"] for row in rows]

    def delete(self, project: str) -> None:
        """Delete an empty project and its paths. Refuses global or non-empty projects."""
        if project == "global":
            raise ValueError("Cannot delete the 'global' project")
        row = self._conn.query_one("SELECT id FROM projects WHERE name = ?", (project,))
        if row is None:
            raise ValueError(f"Project '{project}' not found")
        project_id = row["id"]

        count_row = self._conn.query_one("SELECT COUNT(*) AS n FROM entities WHERE project_id = ?", (project_id,))
        assert count_row is not None
        entity_count = count_row["n"]
        if entity_count:
            raise ValueError(
                f"Cannot delete project '{project}': it has {entity_count} entit"
                f"{'y' if entity_count == 1 else 'ies'}. Delete them first."
            )

        with self._conn.transaction():
            self._conn.write("DELETE FROM project_paths WHERE project_id = ?", (project_id,))
            self._conn.write("DELETE FROM projects WHERE id = ?", (project_id,))

    def move_entities(self, source: str, target: str) -> int:
        """Move all entities from one project scope into another, preserving relations.

        Returns the number of entities moved. Raises if either project is missing or if
        any entity name exists in both scopes.
        """
        source_row = self._conn.query_one("SELECT id FROM projects WHERE name = ?", (source,))
        if source_row is None:
            raise ValueError(f"Source project '{source}' not found")
        source_id = source_row["id"]
        target_id = get_or_create_project_id(self._conn, target)

        collisions = self._conn.query_all(
            "SELECT s.name FROM entities s JOIN entities t "
            "ON s.name = t.name AND t.project_id = ? WHERE s.project_id = ?",
            (target_id, source_id),
        )
        if collisions:
            names = ", ".join(row["name"] for row in collisions)
            raise ValueError(f"Cannot move: name collision in target '{target}' for: {names}")

        ids = [row["id"] for row in self._conn.query_all("SELECT id FROM entities WHERE project_id = ?", (source_id,))]
        with self._conn.transaction():
            for entity_id in ids:
                refresh_for_entity(self._conn, entity_id, delete=True)
            self._conn.write("UPDATE entities SET project_id = ? WHERE project_id = ?", (target_id, source_id))
            for entity_id in ids:
                refresh_for_entity(self._conn, entity_id, delete=False)
        return len(ids)
