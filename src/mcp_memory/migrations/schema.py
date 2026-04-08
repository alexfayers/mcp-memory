"""Database schema migrations."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Migration:
    """A versioned database migration."""

    version: int
    statements: list[str]


MIGRATIONS: list[Migration] = [
    Migration(
        version=1,
        statements=[
            # Lookup tables
            """CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                UNIQUE(name)
            )""",
            """CREATE TABLE IF NOT EXISTS entity_types (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                UNIQUE(name)
            )""",
            """CREATE TABLE IF NOT EXISTS relation_types (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                UNIQUE(name)
            )""",
            # Core tables
            """CREATE TABLE IF NOT EXISTS entities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                entity_type_id INTEGER NOT NULL REFERENCES entity_types(id),
                project_id INTEGER NOT NULL REFERENCES projects(id),
                status TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(name, project_id)
            )""",
            """CREATE TABLE IF NOT EXISTS observations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_id INTEGER NOT NULL REFERENCES entities(id),
                content TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )""",
            """CREATE TABLE IF NOT EXISTS relations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id INTEGER NOT NULL REFERENCES entities(id),
                target_id INTEGER NOT NULL REFERENCES entities(id),
                relation_type_id INTEGER NOT NULL REFERENCES relation_types(id),
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(source_id, target_id, relation_type_id)
            )""",
            # Indexes
            "CREATE INDEX IF NOT EXISTS idx_projects_name ON projects(name)",
            "CREATE INDEX IF NOT EXISTS idx_entity_types_name ON entity_types(name)",
            "CREATE INDEX IF NOT EXISTS idx_relation_types_name ON relation_types(name)",
            "CREATE INDEX IF NOT EXISTS idx_entities_name_project_id ON entities(name, project_id)",
            "CREATE INDEX IF NOT EXISTS idx_entities_project_id ON entities(project_id)",
            "CREATE INDEX IF NOT EXISTS idx_entities_status ON entities(status)",
            "CREATE INDEX IF NOT EXISTS idx_observations_entity_id ON observations(entity_id)",
            "CREATE INDEX IF NOT EXISTS idx_relations_source_id ON relations(source_id)",
            "CREATE INDEX IF NOT EXISTS idx_relations_target_id ON relations(target_id)",
            # FTS5 virtual table
            """CREATE VIRTUAL TABLE IF NOT EXISTS entities_fts USING fts5(
                name,
                entity_type,
                observations,
                project,
                content='',
                tokenize='unicode61'
            )""",
            # FTS triggers
            """CREATE TRIGGER IF NOT EXISTS entities_fts_insert
            AFTER INSERT ON entities
            BEGIN
                INSERT INTO entities_fts(rowid, name, entity_type, observations, project)
                VALUES (
                    NEW.id,
                    NEW.name,
                    (SELECT name FROM entity_types WHERE id = NEW.entity_type_id),
                    '',
                    (SELECT name FROM projects WHERE id = NEW.project_id)
                );
            END""",
            """CREATE TRIGGER IF NOT EXISTS entities_fts_delete
            AFTER DELETE ON entities
            BEGIN
                INSERT INTO entities_fts(entities_fts, rowid, name, entity_type, observations, project)
                VALUES (
                    'delete',
                    OLD.id,
                    OLD.name,
                    (SELECT name FROM entity_types WHERE id = OLD.entity_type_id),
                    '',
                    (SELECT name FROM projects WHERE id = OLD.project_id)
                );
            END""",
            """CREATE TRIGGER IF NOT EXISTS observations_fts_insert
            AFTER INSERT ON observations
            BEGIN
                INSERT INTO entities_fts(entities_fts, rowid, name, entity_type, observations, project)
                VALUES (
                    'delete',
                    NEW.entity_id,
                    (SELECT name FROM entities WHERE id = NEW.entity_id),
                    (SELECT et.name FROM entity_types et JOIN entities e ON e.entity_type_id = et.id WHERE e.id = NEW.entity_id),
                    '',
                    (SELECT p.name FROM projects p JOIN entities e ON e.project_id = p.id WHERE e.id = NEW.entity_id)
                );
                INSERT INTO entities_fts(rowid, name, entity_type, observations, project)
                VALUES (
                    NEW.entity_id,
                    (SELECT name FROM entities WHERE id = NEW.entity_id),
                    (SELECT et.name FROM entity_types et JOIN entities e ON e.entity_type_id = et.id WHERE e.id = NEW.entity_id),
                    (SELECT GROUP_CONCAT(content, ' ') FROM observations WHERE entity_id = NEW.entity_id),
                    (SELECT p.name FROM projects p JOIN entities e ON e.project_id = p.id WHERE e.id = NEW.entity_id)
                );
            END""",
            """CREATE TRIGGER IF NOT EXISTS observations_fts_delete
            AFTER DELETE ON observations
            BEGIN
                INSERT INTO entities_fts(entities_fts, rowid, name, entity_type, observations, project)
                VALUES (
                    'delete',
                    OLD.entity_id,
                    (SELECT name FROM entities WHERE id = OLD.entity_id),
                    (SELECT et.name FROM entity_types et JOIN entities e ON e.entity_type_id = et.id WHERE e.id = OLD.entity_id),
                    '',
                    (SELECT p.name FROM projects p JOIN entities e ON e.project_id = p.id WHERE e.id = OLD.entity_id)
                );
                INSERT INTO entities_fts(rowid, name, entity_type, observations, project)
                VALUES (
                    OLD.entity_id,
                    (SELECT name FROM entities WHERE id = OLD.entity_id),
                    (SELECT et.name FROM entity_types et JOIN entities e ON e.entity_type_id = et.id WHERE e.id = OLD.entity_id),
                    (SELECT GROUP_CONCAT(content, ' ') FROM observations WHERE entity_id = OLD.entity_id),
                    (SELECT p.name FROM projects p JOIN entities e ON e.project_id = p.id WHERE e.id = OLD.entity_id)
                );
            END""",
        ],
    ),
]
