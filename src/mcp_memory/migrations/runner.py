"""Database migration runner."""

from __future__ import annotations

import logging
import sqlite3

from .schema import MIGRATIONS

logger = logging.getLogger(__name__)


def run_migrations(db: sqlite3.Connection) -> None:
    """Apply pending database migrations."""
    db.execute(
        "CREATE TABLE IF NOT EXISTS schema_version "
        "(version INTEGER PRIMARY KEY, applied_at DATETIME DEFAULT CURRENT_TIMESTAMP)"
    )

    has_entities = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='entities'"
    ).fetchone()
    has_version = db.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]

    if has_entities and has_version is None:
        logger.info("Existing database without version tracking detected, marking as v1")
        db.execute("INSERT INTO schema_version (version) VALUES (?)", (1,))
        has_version = 1

    current_version = has_version or 0

    pending = [m for m in MIGRATIONS if m.version > current_version]
    if not pending:
        return

    original_fk = db.execute("PRAGMA foreign_keys").fetchone()[0]
    db.execute("PRAGMA foreign_keys = OFF")

    try:
        for migration in pending:
            logger.info("Applying migration v%d", migration.version)
            with db:
                for statement in migration.statements:
                    db.execute(statement)
                db.execute(
                    "INSERT INTO schema_version (version) VALUES (?)",
                    (migration.version,),
                )
    finally:
        db.execute(f"PRAGMA foreign_keys = {'ON' if original_fk else 'OFF'}")
