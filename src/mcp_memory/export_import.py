"""Export the whole memory database and merge selected projects back in.

Owns file I/O, format validation, and orchestration; the CLI stays a thin dispatcher and
the merge itself lives in DatabaseManager.import_project_data.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from .migrations.schema import MIGRATIONS
from .models import Relation

if TYPE_CHECKING:
    from pathlib import Path

    from .database import DatabaseManager

EXPORT_FORMAT = "mcp-memory-export"
EXPORT_FORMAT_VERSION = 1


@dataclass
class ImportSummary:
    """Aggregated counts and notices from importing one or more projects."""

    entities_new: int = 0
    entities_merged: int = 0
    entities_skipped_type_mismatch: list[str] = field(default_factory=list)
    observations_new: int = 0
    observations_duplicate: int = 0
    relations_new: int = 0
    relations_duplicate: int = 0
    groups_added: int = 0
    path_notices: dict[str, list[str]] = field(default_factory=dict)
    dry_run: bool = False

    def render(self) -> str:
        """Render the summary as human-readable lines."""
        prefix = "[dry run] " if self.dry_run else ""
        lines = [
            f"{prefix}Import summary:",
            f"  entities: {self.entities_new} new, {self.entities_merged} merged",
            f"  observations: {self.observations_new} new, {self.observations_duplicate} duplicate",
            f"  relations: {self.relations_new} new, {self.relations_duplicate} duplicate",
            f"  groups added: {self.groups_added}",
        ]
        if self.entities_skipped_type_mismatch:
            names = ", ".join(self.entities_skipped_type_mismatch)
            lines.append(f"  skipped (type mismatch): {names}")
        for project, paths in self.path_notices.items():
            source = ", ".join(paths) if paths else "(none recorded)"
            lines.append(
                f"  note: project '{project}' had source path(s) {source}; "
                "register the local path with set_metadata if needed"
            )
        return "\n".join(lines)


def export_database(db: DatabaseManager, output_path: Path) -> None:
    """Write every live project of the database to a JSON export file."""
    snapshot = db.export_data()
    data = {
        "format": EXPORT_FORMAT,
        "format_version": EXPORT_FORMAT_VERSION,
        "schema_version": snapshot["schema_version"],
        "exported_at": datetime.now(tz=UTC).isoformat(),
        "projects": snapshot["projects"],
    }
    output_path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def load_export(input_path: Path) -> dict[str, Any]:
    """Parse and validate an export file, enforcing the schema-version guard.

    Raises ValueError for an unrecognised format/version or a schema_version newer than
    this build supports. An older schema_version imports fine, since import goes through
    the current write path rather than raw rows.
    """
    data: dict[str, Any] = json.loads(input_path.read_text(encoding="utf-8"))
    if data.get("format") != EXPORT_FORMAT:
        raise ValueError(f"Not an mcp-memory export file (format: {data.get('format')!r})")
    if data.get("format_version") != EXPORT_FORMAT_VERSION:
        raise ValueError(
            f"Unsupported export format_version {data.get('format_version')!r}; "
            f"this build understands {EXPORT_FORMAT_VERSION}"
        )
    max_schema = max(m.version for m in MIGRATIONS)
    if data.get("schema_version", 0) > max_schema:
        raise ValueError(
            f"Export schema_version {data.get('schema_version')} is newer than this build "
            f"supports (max {max_schema}); upgrade mcp-memory before importing"
        )
    return data


def list_export_projects(data: dict[str, Any]) -> list[str]:
    """Return the project names present in an export file."""
    return sorted(data.get("projects", {}))


def import_projects(
    db: DatabaseManager, data: dict[str, Any], projects: list[str], *, dry_run: bool
) -> ImportSummary:
    """Merge the named projects from an export into the database, returning a summary."""
    available = data.get("projects", {})
    missing = [p for p in projects if p not in available]
    if missing:
        raise ValueError(
            f"Project(s) not found in export file: {', '.join(missing)}. "
            f"Available: {', '.join(sorted(available)) or '(none)'}"
        )

    summary = ImportSummary(dry_run=dry_run)
    for project in projects:
        block = available[project]
        entities = block.get("entities", [])
        relations = [Relation(**r) for r in block.get("relations", [])]
        groups = block.get("groups", [])
        counts = db.import_project_data(project, entities, relations, groups, dry_run=dry_run)
        summary.entities_new += counts["entities_new"]
        summary.entities_merged += counts["entities_merged"]
        summary.entities_skipped_type_mismatch += counts["entities_skipped_type_mismatch"]
        summary.observations_new += counts["observations_new"]
        summary.observations_duplicate += counts["observations_duplicate"]
        summary.relations_new += counts["relations_new"]
        summary.relations_duplicate += counts["relations_duplicate"]
        summary.groups_added += counts["groups_added"]
        summary.path_notices[project] = block.get("paths", [])
    return summary
