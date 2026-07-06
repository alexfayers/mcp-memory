"""Database schema migrations.

Ported from the original TypeScript version (fayers-mcp-memory-sqlite).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Migration:
    """A versioned database migration."""

    version: int
    statements: list[str]


# Frozen snapshot of the variant -> canonical relation-type map at migration authoring time.
# Kept local to the migration so later edits to the runtime alias map never rewrite history.
_RELATION_TYPE_BACKFILL = {
    "related-to": "relates-to",
    "extends": "implements",
    "uses": "implements",
    "tests": "implements",
    "blocked-by": "depends-on",
    "follows": "depends-on",
    "subproject-of": "part-of",
    "has-feature": "part-of",
    "overlay-for": "used-in",
    "has-overlay": "used-by",
    "informs": "relates-to",
    "constrains": "relates-to",
    "applies-to": "relates-to",
    "supports": "relates-to",
    "participates-in": "relates-to",
    "explores": "relates-to",
    "examines": "relates-to",
    "enables": "relates-to",
    "finding-in": "relates-to",
    "assigned-to": "belongs-to",
    "owned-by": "belongs-to",
    "changes": "implements",
    "modifies": "implements",
    "modified": "implements",
    "verifies": "implements",
    "continues": "depends-on",
    "replaces": "relates-to",
    "discovered-by": "relates-to",
    "research-for": "relates-to",
    "context-for": "relates-to",
    "has-architecture": "part-of",
    "has-todos": "relates-to",
    "self": "relates-to",
}


# Canonical relation-type vocabulary at migration authoring time. Frozen here so
# later edits to the runtime vocabulary never rewrite migration history.
_CANONICAL_RELATION_TYPES = (
    "implements",
    "depends-on",
    "blocks",
    "relates-to",
    "belongs-to",
    "part-of",
    "used-by",
    "used-in",
)


def _squash(name: str) -> str:
    """Separator- and case-insensitive key: lowercased with '-' and '_' removed."""
    return name.replace("-", "").replace("_", "").lower()


def _relation_type_backfill_statements() -> list[str]:
    """Build SQL that merges legacy relation types into their canonical form.

    Matching is separator- and case-insensitive (so ``related_to``, ``related-to``
    and ``relatedTo`` all collapse), covering both spelling variants of canonical
    types and the semantic synonyms in the backfill map. For each canonical target:
    ensure it exists, repoint matching relations (skipping rows that would collide
    with an existing canonical edge), drop the collided leftovers, then remove the
    now-unreferenced legacy types.
    """
    squash_to_canonical: dict[str, str] = {}
    for canonical in _CANONICAL_RELATION_TYPES:
        squash_to_canonical[_squash(canonical)] = canonical
    for variant, canonical in _RELATION_TYPE_BACKFILL.items():
        squash_to_canonical[_squash(variant)] = canonical

    sql_squash = "LOWER(REPLACE(REPLACE(name, '_', ''), '-', ''))"
    statements: list[str] = []
    for squash_key, canonical in squash_to_canonical.items():
        match = f"{sql_squash} = '{squash_key}' AND name != '{canonical}'"
        statements.extend(
            [
                f"INSERT OR IGNORE INTO relation_types (name) VALUES ('{canonical}')",
                f"""UPDATE OR IGNORE relations SET relation_type_id =
                    (SELECT id FROM relation_types WHERE name = '{canonical}')
                    WHERE relation_type_id IN
                    (SELECT id FROM relation_types WHERE {match})""",
                f"""DELETE FROM relations WHERE relation_type_id IN
                    (SELECT id FROM relation_types WHERE {match})""",
                f"DELETE FROM relation_types WHERE {match}",
            ]
        )
    return statements


MIGRATIONS: list[Migration] = [
    Migration(
        version=1,
        statements=[
            """CREATE TABLE IF NOT EXISTS entities (
                name TEXT PRIMARY KEY,
                entity_type TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )""",
            """CREATE TABLE IF NOT EXISTS observations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_name TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (entity_name) REFERENCES entities(name)
            )""",
            """CREATE TABLE IF NOT EXISTS relations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                target TEXT NOT NULL,
                relation_type TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (source) REFERENCES entities(name),
                FOREIGN KEY (target) REFERENCES entities(name),
                UNIQUE(source, target, relation_type)
            )""",
            "CREATE INDEX IF NOT EXISTS idx_entities_name ON entities(name)",
            "CREATE INDEX IF NOT EXISTS idx_observations_entity ON observations(entity_name)",
            "CREATE INDEX IF NOT EXISTS idx_relations_source ON relations(source)",
            "CREATE INDEX IF NOT EXISTS idx_relations_target ON relations(target)",
        ],
    ),
    Migration(
        version=2,
        statements=[
            "CREATE VIRTUAL TABLE IF NOT EXISTS entities_fts USING fts5(name, entity_type, observations, content='', tokenize='unicode61')",
            "INSERT INTO entities_fts(rowid, name, entity_type, observations) SELECT e.rowid, e.name, e.entity_type, COALESCE((SELECT GROUP_CONCAT(content, ' ') FROM observations WHERE entity_name = e.name), '') FROM entities e",
            "CREATE TRIGGER IF NOT EXISTS entities_fts_insert AFTER INSERT ON entities BEGIN INSERT INTO entities_fts(rowid, name, entity_type, observations) VALUES (new.rowid, new.name, new.entity_type, ''); END",
            "CREATE TRIGGER IF NOT EXISTS entities_fts_delete AFTER DELETE ON entities BEGIN INSERT INTO entities_fts(entities_fts, rowid, name, entity_type, observations) VALUES('delete', old.rowid, old.name, old.entity_type, ''); END",
            "CREATE TRIGGER IF NOT EXISTS observations_fts_insert AFTER INSERT ON observations BEGIN INSERT INTO entities_fts(entities_fts, rowid, name, entity_type, observations) VALUES('delete', (SELECT rowid FROM entities WHERE name = new.entity_name), (SELECT name FROM entities WHERE name = new.entity_name), (SELECT entity_type FROM entities WHERE name = new.entity_name), ''); INSERT INTO entities_fts(rowid, name, entity_type, observations) SELECT rowid, name, entity_type, (SELECT GROUP_CONCAT(content, ' ') FROM observations WHERE entity_name = new.entity_name) FROM entities WHERE name = new.entity_name; END",
            "CREATE TRIGGER IF NOT EXISTS observations_fts_delete AFTER DELETE ON observations BEGIN INSERT INTO entities_fts(entities_fts, rowid, name, entity_type, observations) VALUES('delete', (SELECT rowid FROM entities WHERE name = old.entity_name), (SELECT name FROM entities WHERE name = old.entity_name), (SELECT entity_type FROM entities WHERE name = old.entity_name), ''); INSERT INTO entities_fts(rowid, name, entity_type, observations) SELECT rowid, name, entity_type, (SELECT GROUP_CONCAT(content, ' ') FROM observations WHERE entity_name = old.entity_name) FROM entities WHERE name = old.entity_name; END",
        ],
    ),
    Migration(
        version=3,
        statements=[
            "ALTER TABLE entities ADD COLUMN project TEXT NOT NULL DEFAULT ''",
            "CREATE INDEX IF NOT EXISTS idx_entities_project ON entities(project)",
            "DROP TRIGGER IF EXISTS entities_fts_insert",
            "DROP TRIGGER IF EXISTS entities_fts_delete",
            "DROP TRIGGER IF EXISTS observations_fts_insert",
            "DROP TRIGGER IF EXISTS observations_fts_delete",
            "INSERT INTO entities_fts(entities_fts) VALUES('delete-all')",
            "DROP TABLE IF EXISTS entities_fts",
            "CREATE VIRTUAL TABLE IF NOT EXISTS entities_fts USING fts5(name, entity_type, observations, project, content='', tokenize='unicode61')",
            "INSERT INTO entities_fts(rowid, name, entity_type, observations, project) SELECT e.rowid, e.name, e.entity_type, COALESCE((SELECT GROUP_CONCAT(content, ' ') FROM observations WHERE entity_name = e.name), ''), e.project FROM entities e",
            "CREATE TRIGGER IF NOT EXISTS entities_fts_insert AFTER INSERT ON entities BEGIN INSERT INTO entities_fts(rowid, name, entity_type, observations, project) VALUES (new.rowid, new.name, new.entity_type, '', new.project); END",
            "CREATE TRIGGER IF NOT EXISTS entities_fts_delete AFTER DELETE ON entities BEGIN INSERT INTO entities_fts(entities_fts, rowid, name, entity_type, observations, project) VALUES('delete', old.rowid, old.name, old.entity_type, '', old.project); END",
            "CREATE TRIGGER IF NOT EXISTS observations_fts_insert AFTER INSERT ON observations BEGIN INSERT INTO entities_fts(entities_fts, rowid, name, entity_type, observations, project) VALUES('delete', (SELECT rowid FROM entities WHERE name = new.entity_name), (SELECT name FROM entities WHERE name = new.entity_name), (SELECT entity_type FROM entities WHERE name = new.entity_name), '', (SELECT project FROM entities WHERE name = new.entity_name)); INSERT INTO entities_fts(rowid, name, entity_type, observations, project) SELECT rowid, name, entity_type, (SELECT GROUP_CONCAT(content, ' ') FROM observations WHERE entity_name = new.entity_name), project FROM entities WHERE name = new.entity_name; END",
            "CREATE TRIGGER IF NOT EXISTS observations_fts_delete AFTER DELETE ON observations BEGIN INSERT INTO entities_fts(entities_fts, rowid, name, entity_type, observations, project) VALUES('delete', (SELECT rowid FROM entities WHERE name = old.entity_name), (SELECT name FROM entities WHERE name = old.entity_name), (SELECT entity_type FROM entities WHERE name = old.entity_name), '', (SELECT project FROM entities WHERE name = old.entity_name)); INSERT INTO entities_fts(rowid, name, entity_type, observations, project) SELECT rowid, name, entity_type, (SELECT GROUP_CONCAT(content, ' ') FROM observations WHERE entity_name = old.entity_name), project FROM entities WHERE name = old.entity_name; END",
        ],
    ),
    Migration(
        version=4,
        statements=[
            "DROP TRIGGER IF EXISTS entities_fts_insert",
            "DROP TRIGGER IF EXISTS entities_fts_delete",
            "DROP TRIGGER IF EXISTS observations_fts_insert",
            "DROP TRIGGER IF EXISTS observations_fts_delete",
            "INSERT INTO entities_fts(entities_fts) VALUES('delete-all')",
            "DROP TABLE IF EXISTS entities_fts",
            "ALTER TABLE entities RENAME TO entities_old",
            """CREATE TABLE entities (
                name TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                project TEXT NOT NULL DEFAULT '',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(name, project)
            )""",
            "INSERT INTO entities (name, entity_type, project, created_at) SELECT name, entity_type, project, created_at FROM entities_old",
            "DROP TABLE entities_old",
            "ALTER TABLE observations RENAME TO observations_old",
            """CREATE TABLE observations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_name TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (entity_name) REFERENCES entities(name)
            )""",
            "INSERT INTO observations SELECT * FROM observations_old",
            "DROP TABLE observations_old",
            "DROP INDEX IF EXISTS idx_entities_name",
            "CREATE INDEX IF NOT EXISTS idx_entities_name_project ON entities(name, project)",
            "CREATE VIRTUAL TABLE IF NOT EXISTS entities_fts USING fts5(name, entity_type, observations, project, content='', tokenize='unicode61')",
            "INSERT INTO entities_fts(rowid, name, entity_type, observations, project) SELECT e.rowid, e.name, e.entity_type, COALESCE((SELECT GROUP_CONCAT(content, ' ') FROM observations WHERE entity_name = e.name), ''), e.project FROM entities e",
            "CREATE TRIGGER IF NOT EXISTS entities_fts_insert AFTER INSERT ON entities BEGIN INSERT INTO entities_fts(rowid, name, entity_type, observations, project) VALUES (new.rowid, new.name, new.entity_type, '', new.project); END",
            "CREATE TRIGGER IF NOT EXISTS entities_fts_delete AFTER DELETE ON entities BEGIN INSERT INTO entities_fts(entities_fts, rowid, name, entity_type, observations, project) VALUES('delete', old.rowid, old.name, old.entity_type, '', old.project); END",
            "CREATE TRIGGER IF NOT EXISTS observations_fts_insert AFTER INSERT ON observations BEGIN INSERT INTO entities_fts(entities_fts, rowid, name, entity_type, observations, project) VALUES('delete', (SELECT rowid FROM entities WHERE name = new.entity_name), (SELECT name FROM entities WHERE name = new.entity_name), (SELECT entity_type FROM entities WHERE name = new.entity_name), '', (SELECT project FROM entities WHERE name = new.entity_name)); INSERT INTO entities_fts(rowid, name, entity_type, observations, project) SELECT rowid, name, entity_type, (SELECT GROUP_CONCAT(content, ' ') FROM observations WHERE entity_name = new.entity_name), project FROM entities WHERE name = new.entity_name; END",
            "CREATE TRIGGER IF NOT EXISTS observations_fts_delete AFTER DELETE ON observations BEGIN INSERT INTO entities_fts(entities_fts, rowid, name, entity_type, observations, project) VALUES('delete', (SELECT rowid FROM entities WHERE name = old.entity_name), (SELECT name FROM entities WHERE name = old.entity_name), (SELECT entity_type FROM entities WHERE name = old.entity_name), '', (SELECT project FROM entities WHERE name = old.entity_name)); INSERT INTO entities_fts(rowid, name, entity_type, observations, project) SELECT rowid, name, entity_type, (SELECT GROUP_CONCAT(content, ' ') FROM observations WHERE entity_name = old.entity_name), project FROM entities WHERE name = old.entity_name; END",
        ],
    ),
    Migration(
        version=5,
        statements=[
            "ALTER TABLE relations RENAME TO relations_old",
            """CREATE TABLE relations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                target TEXT NOT NULL,
                relation_type TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(source, target, relation_type)
            )""",
            "INSERT INTO relations (id, source, target, relation_type, created_at) SELECT id, source, target, relation_type, created_at FROM relations_old",
            "DROP TABLE relations_old",
        ],
    ),
    Migration(
        version=6,
        statements=[
            "DELETE FROM observations WHERE entity_name NOT IN (SELECT name FROM entities)",
            "DELETE FROM relations WHERE source NOT IN (SELECT name FROM entities) OR target NOT IN (SELECT name FROM entities)",
            "DROP TRIGGER IF EXISTS entities_fts_insert",
            "DROP TRIGGER IF EXISTS entities_fts_delete",
            "DROP TRIGGER IF EXISTS observations_fts_insert",
            "DROP TRIGGER IF EXISTS observations_fts_delete",
            "CREATE TRIGGER IF NOT EXISTS entities_fts_insert AFTER INSERT ON entities BEGIN INSERT INTO entities_fts(rowid, name, entity_type, observations, project) VALUES (new.rowid, new.name, new.entity_type, '', new.project); END",
            "CREATE TRIGGER IF NOT EXISTS entities_fts_delete AFTER DELETE ON entities BEGIN INSERT INTO entities_fts(entities_fts, rowid, name, entity_type, observations, project) VALUES('delete', old.rowid, old.name, old.entity_type, '', old.project); END",
            "CREATE TRIGGER IF NOT EXISTS observations_fts_insert AFTER INSERT ON observations BEGIN INSERT INTO entities_fts(entities_fts, rowid, name, entity_type, observations, project) VALUES('delete', (SELECT rowid FROM entities WHERE name = new.entity_name), (SELECT name FROM entities WHERE name = new.entity_name), (SELECT entity_type FROM entities WHERE name = new.entity_name), '', (SELECT project FROM entities WHERE name = new.entity_name)); INSERT INTO entities_fts(rowid, name, entity_type, observations, project) SELECT rowid, name, entity_type, (SELECT GROUP_CONCAT(content, ' ') FROM observations WHERE entity_name = new.entity_name), project FROM entities WHERE name = new.entity_name; END",
            "CREATE TRIGGER IF NOT EXISTS observations_fts_delete AFTER DELETE ON observations BEGIN INSERT INTO entities_fts(entities_fts, rowid, name, entity_type, observations, project) VALUES('delete', (SELECT rowid FROM entities WHERE name = old.entity_name), (SELECT name FROM entities WHERE name = old.entity_name), (SELECT entity_type FROM entities WHERE name = old.entity_name), '', (SELECT project FROM entities WHERE name = old.entity_name)); INSERT INTO entities_fts(rowid, name, entity_type, observations, project) SELECT rowid, name, entity_type, (SELECT GROUP_CONCAT(content, ' ') FROM observations WHERE entity_name = old.entity_name), project FROM entities WHERE name = old.entity_name; END",
        ],
    ),
    Migration(
        version=7,
        statements=[
            "ALTER TABLE observations RENAME TO observations_old",
            """CREATE TABLE observations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_name TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )""",
            "INSERT INTO observations SELECT * FROM observations_old",
            "DROP TABLE observations_old",
            "DROP TRIGGER IF EXISTS observations_fts_insert",
            "DROP TRIGGER IF EXISTS observations_fts_delete",
            "CREATE TRIGGER IF NOT EXISTS observations_fts_insert AFTER INSERT ON observations BEGIN INSERT INTO entities_fts(entities_fts, rowid, name, entity_type, observations, project) VALUES('delete', (SELECT rowid FROM entities WHERE name = new.entity_name), (SELECT name FROM entities WHERE name = new.entity_name), (SELECT entity_type FROM entities WHERE name = new.entity_name), '', (SELECT project FROM entities WHERE name = new.entity_name)); INSERT INTO entities_fts(rowid, name, entity_type, observations, project) SELECT rowid, name, entity_type, (SELECT GROUP_CONCAT(content, ' ') FROM observations WHERE entity_name = new.entity_name), project FROM entities WHERE name = new.entity_name; END",
            "CREATE TRIGGER IF NOT EXISTS observations_fts_delete AFTER DELETE ON observations BEGIN INSERT INTO entities_fts(entities_fts, rowid, name, entity_type, observations, project) VALUES('delete', (SELECT rowid FROM entities WHERE name = old.entity_name), (SELECT name FROM entities WHERE name = old.entity_name), (SELECT entity_type FROM entities WHERE name = old.entity_name), '', (SELECT project FROM entities WHERE name = old.entity_name)); INSERT INTO entities_fts(rowid, name, entity_type, observations, project) SELECT rowid, name, entity_type, (SELECT GROUP_CONCAT(content, ' ') FROM observations WHERE entity_name = old.entity_name), project FROM entities WHERE name = old.entity_name; END",
        ],
    ),
    Migration(
        version=8,
        statements=[
            "DROP TRIGGER IF EXISTS entities_fts_insert",
            "DROP TRIGGER IF EXISTS entities_fts_delete",
            "DROP TRIGGER IF EXISTS observations_fts_insert",
            "DROP TRIGGER IF EXISTS observations_fts_delete",
            "ALTER TABLE entities RENAME TO entities_old",
            """CREATE TABLE entities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                project TEXT NOT NULL DEFAULT '',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(name, project)
            )""",
            "INSERT INTO entities (id, name, entity_type, project, created_at) SELECT rowid, name, entity_type, project, created_at FROM entities_old",
            "DROP TABLE entities_old",
            "ALTER TABLE observations RENAME TO observations_old",
            """CREATE TABLE observations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_id INTEGER NOT NULL,
                content TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (entity_id) REFERENCES entities(id)
            )""",
            "INSERT INTO observations (id, entity_id, content, created_at) SELECT o.id, (SELECT MIN(e.id) FROM entities e WHERE e.name = o.entity_name), o.content, o.created_at FROM observations_old o WHERE EXISTS (SELECT 1 FROM entities e WHERE e.name = o.entity_name)",
            "DROP TABLE observations_old",
            "ALTER TABLE relations RENAME TO relations_old",
            """CREATE TABLE relations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id INTEGER NOT NULL,
                target_id INTEGER NOT NULL,
                relation_type TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (source_id) REFERENCES entities(id),
                FOREIGN KEY (target_id) REFERENCES entities(id),
                UNIQUE(source_id, target_id, relation_type)
            )""",
            "INSERT INTO relations (id, source_id, target_id, relation_type, created_at) SELECT r.id, (SELECT MIN(e.id) FROM entities e WHERE e.name = r.source), (SELECT MIN(e.id) FROM entities e WHERE e.name = r.target), r.relation_type, r.created_at FROM relations_old r WHERE EXISTS (SELECT 1 FROM entities e WHERE e.name = r.source) AND EXISTS (SELECT 1 FROM entities e WHERE e.name = r.target)",
            "DROP TABLE relations_old",
            "DROP INDEX IF EXISTS idx_entities_name_project",
            "DROP INDEX IF EXISTS idx_entities_project",
            "DROP INDEX IF EXISTS idx_observations_entity",
            "DROP INDEX IF EXISTS idx_relations_source",
            "DROP INDEX IF EXISTS idx_relations_target",
            "CREATE INDEX IF NOT EXISTS idx_entities_name_project ON entities(name, project)",
            "CREATE INDEX IF NOT EXISTS idx_entities_project ON entities(project)",
            "CREATE INDEX IF NOT EXISTS idx_observations_entity_id ON observations(entity_id)",
            "CREATE INDEX IF NOT EXISTS idx_relations_source_id ON relations(source_id)",
            "CREATE INDEX IF NOT EXISTS idx_relations_target_id ON relations(target_id)",
            "INSERT INTO entities_fts(entities_fts) VALUES('delete-all')",
            "DROP TABLE IF EXISTS entities_fts",
            "CREATE VIRTUAL TABLE IF NOT EXISTS entities_fts USING fts5(name, entity_type, observations, project, content='', tokenize='unicode61')",
            "INSERT INTO entities_fts(rowid, name, entity_type, observations, project) SELECT e.id, e.name, e.entity_type, COALESCE((SELECT GROUP_CONCAT(o.content, ' ') FROM observations o WHERE o.entity_id = e.id), ''), e.project FROM entities e",
            """CREATE TRIGGER IF NOT EXISTS entities_fts_insert AFTER INSERT ON entities BEGIN
                INSERT INTO entities_fts(rowid, name, entity_type, observations, project)
                VALUES (new.id, new.name, new.entity_type, '', new.project);
            END""",
            """CREATE TRIGGER IF NOT EXISTS entities_fts_delete AFTER DELETE ON entities BEGIN
                INSERT INTO entities_fts(entities_fts, rowid, name, entity_type, observations, project)
                VALUES('delete', old.id, old.name, old.entity_type, '', old.project);
            END""",
            """CREATE TRIGGER IF NOT EXISTS observations_fts_insert AFTER INSERT ON observations BEGIN
                INSERT INTO entities_fts(entities_fts, rowid, name, entity_type, observations, project)
                VALUES('delete', new.entity_id, (SELECT name FROM entities WHERE id = new.entity_id), (SELECT entity_type FROM entities WHERE id = new.entity_id), '', (SELECT project FROM entities WHERE id = new.entity_id));
                INSERT INTO entities_fts(rowid, name, entity_type, observations, project)
                SELECT id, name, entity_type, (SELECT GROUP_CONCAT(content, ' ') FROM observations WHERE entity_id = new.entity_id), project
                FROM entities WHERE id = new.entity_id;
            END""",
            """CREATE TRIGGER IF NOT EXISTS observations_fts_delete AFTER DELETE ON observations BEGIN
                INSERT INTO entities_fts(entities_fts, rowid, name, entity_type, observations, project)
                VALUES('delete', old.entity_id, (SELECT name FROM entities WHERE id = old.entity_id), (SELECT entity_type FROM entities WHERE id = old.entity_id), '', (SELECT project FROM entities WHERE id = old.entity_id));
                INSERT INTO entities_fts(rowid, name, entity_type, observations, project)
                SELECT id, name, entity_type, (SELECT GROUP_CONCAT(content, ' ') FROM observations WHERE entity_id = old.entity_id), project
                FROM entities WHERE id = old.entity_id;
            END""",
        ],
    ),
    Migration(
        version=9,
        statements=[
            """CREATE TABLE projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                UNIQUE(name)
            )""",
            "INSERT INTO projects (name) SELECT DISTINCT project FROM entities",
            "DROP TRIGGER IF EXISTS entities_fts_insert",
            "DROP TRIGGER IF EXISTS entities_fts_delete",
            "DROP TRIGGER IF EXISTS observations_fts_insert",
            "DROP TRIGGER IF EXISTS observations_fts_delete",
            "ALTER TABLE entities RENAME TO entities_old",
            """CREATE TABLE entities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                project_id INTEGER NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(name, project_id),
                FOREIGN KEY (project_id) REFERENCES projects(id)
            )""",
            "INSERT INTO entities (id, name, entity_type, project_id, created_at) SELECT e.id, e.name, e.entity_type, p.id, e.created_at FROM entities_old e JOIN projects p ON p.name = e.project",
            "DROP TABLE entities_old",
            "DROP INDEX IF EXISTS idx_entities_name_project",
            "DROP INDEX IF EXISTS idx_entities_project",
            "CREATE INDEX IF NOT EXISTS idx_entities_name_project_id ON entities(name, project_id)",
            "CREATE INDEX IF NOT EXISTS idx_entities_project_id ON entities(project_id)",
            "CREATE INDEX IF NOT EXISTS idx_projects_name ON projects(name)",
            "INSERT INTO entities_fts(entities_fts) VALUES('delete-all')",
            "DROP TABLE IF EXISTS entities_fts",
            "CREATE VIRTUAL TABLE IF NOT EXISTS entities_fts USING fts5(name, entity_type, observations, project, content='', tokenize='unicode61')",
            "INSERT INTO entities_fts(rowid, name, entity_type, observations, project) SELECT e.id, e.name, e.entity_type, COALESCE((SELECT GROUP_CONCAT(o.content, ' ') FROM observations o WHERE o.entity_id = e.id), ''), p.name FROM entities e JOIN projects p ON p.id = e.project_id",
            """CREATE TRIGGER IF NOT EXISTS entities_fts_insert AFTER INSERT ON entities BEGIN
                INSERT INTO entities_fts(rowid, name, entity_type, observations, project)
                VALUES (new.id, new.name, new.entity_type, '', (SELECT name FROM projects WHERE id = new.project_id));
            END""",
            """CREATE TRIGGER IF NOT EXISTS entities_fts_delete AFTER DELETE ON entities BEGIN
                INSERT INTO entities_fts(entities_fts, rowid, name, entity_type, observations, project)
                VALUES('delete', old.id, old.name, old.entity_type, '', (SELECT name FROM projects WHERE id = old.project_id));
            END""",
            """CREATE TRIGGER IF NOT EXISTS observations_fts_insert AFTER INSERT ON observations BEGIN
                INSERT INTO entities_fts(entities_fts, rowid, name, entity_type, observations, project)
                VALUES('delete', new.entity_id, (SELECT name FROM entities WHERE id = new.entity_id), (SELECT entity_type FROM entities WHERE id = new.entity_id), '', (SELECT p.name FROM projects p JOIN entities e ON e.project_id = p.id WHERE e.id = new.entity_id));
                INSERT INTO entities_fts(rowid, name, entity_type, observations, project)
                SELECT e.id, e.name, e.entity_type, (SELECT GROUP_CONCAT(content, ' ') FROM observations WHERE entity_id = new.entity_id), p.name
                FROM entities e JOIN projects p ON p.id = e.project_id WHERE e.id = new.entity_id;
            END""",
            """CREATE TRIGGER IF NOT EXISTS observations_fts_delete AFTER DELETE ON observations BEGIN
                INSERT INTO entities_fts(entities_fts, rowid, name, entity_type, observations, project)
                VALUES('delete', old.entity_id, (SELECT name FROM entities WHERE id = old.entity_id), (SELECT entity_type FROM entities WHERE id = old.entity_id), '', (SELECT p.name FROM projects p JOIN entities e ON e.project_id = p.id WHERE e.id = old.entity_id));
                INSERT INTO entities_fts(rowid, name, entity_type, observations, project)
                SELECT e.id, e.name, e.entity_type, (SELECT GROUP_CONCAT(content, ' ') FROM observations WHERE entity_id = old.entity_id), p.name
                FROM entities e JOIN projects p ON p.id = e.project_id WHERE e.id = old.entity_id;
            END""",
        ],
    ),
    Migration(
        version=10,
        statements=[
            """CREATE TABLE entity_types (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                UNIQUE(name)
            )""",
            "INSERT INTO entity_types (name) SELECT DISTINCT entity_type FROM entities",
            "DROP TRIGGER IF EXISTS entities_fts_insert",
            "DROP TRIGGER IF EXISTS entities_fts_delete",
            "DROP TRIGGER IF EXISTS observations_fts_insert",
            "DROP TRIGGER IF EXISTS observations_fts_delete",
            "ALTER TABLE entities RENAME TO entities_old",
            """CREATE TABLE entities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                entity_type_id INTEGER NOT NULL,
                project_id INTEGER NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(name, project_id),
                FOREIGN KEY (entity_type_id) REFERENCES entity_types(id),
                FOREIGN KEY (project_id) REFERENCES projects(id)
            )""",
            "INSERT INTO entities (id, name, entity_type_id, project_id, created_at) SELECT e.id, e.name, t.id, e.project_id, e.created_at FROM entities_old e JOIN entity_types t ON t.name = e.entity_type",
            "DROP TABLE entities_old",
            "DROP INDEX IF EXISTS idx_entities_name_project_id",
            "DROP INDEX IF EXISTS idx_entities_project_id",
            "CREATE INDEX IF NOT EXISTS idx_entities_name_project_id ON entities(name, project_id)",
            "CREATE INDEX IF NOT EXISTS idx_entities_project_id ON entities(project_id)",
            "CREATE INDEX IF NOT EXISTS idx_entity_types_name ON entity_types(name)",
            "INSERT INTO entities_fts(entities_fts) VALUES('delete-all')",
            "DROP TABLE IF EXISTS entities_fts",
            "CREATE VIRTUAL TABLE IF NOT EXISTS entities_fts USING fts5(name, entity_type, observations, project, content='', tokenize='unicode61')",
            "INSERT INTO entities_fts(rowid, name, entity_type, observations, project) SELECT e.id, e.name, t.name, COALESCE((SELECT GROUP_CONCAT(o.content, ' ') FROM observations o WHERE o.entity_id = e.id), ''), p.name FROM entities e JOIN entity_types t ON t.id = e.entity_type_id JOIN projects p ON p.id = e.project_id",
            """CREATE TRIGGER IF NOT EXISTS entities_fts_insert AFTER INSERT ON entities BEGIN
                INSERT INTO entities_fts(rowid, name, entity_type, observations, project)
                VALUES (new.id, new.name, (SELECT name FROM entity_types WHERE id = new.entity_type_id), '', (SELECT name FROM projects WHERE id = new.project_id));
            END""",
            """CREATE TRIGGER IF NOT EXISTS entities_fts_delete AFTER DELETE ON entities BEGIN
                INSERT INTO entities_fts(entities_fts, rowid, name, entity_type, observations, project)
                VALUES('delete', old.id, old.name, (SELECT name FROM entity_types WHERE id = old.entity_type_id), '', (SELECT name FROM projects WHERE id = old.project_id));
            END""",
            """CREATE TRIGGER IF NOT EXISTS observations_fts_insert AFTER INSERT ON observations BEGIN
                INSERT INTO entities_fts(entities_fts, rowid, name, entity_type, observations, project)
                VALUES('delete', new.entity_id, (SELECT name FROM entities WHERE id = new.entity_id), (SELECT t.name FROM entity_types t JOIN entities e ON e.entity_type_id = t.id WHERE e.id = new.entity_id), '', (SELECT p.name FROM projects p JOIN entities e ON e.project_id = p.id WHERE e.id = new.entity_id));
                INSERT INTO entities_fts(rowid, name, entity_type, observations, project)
                SELECT e.id, e.name, t.name, (SELECT GROUP_CONCAT(content, ' ') FROM observations WHERE entity_id = new.entity_id), p.name
                FROM entities e JOIN entity_types t ON t.id = e.entity_type_id JOIN projects p ON p.id = e.project_id WHERE e.id = new.entity_id;
            END""",
            """CREATE TRIGGER IF NOT EXISTS observations_fts_delete AFTER DELETE ON observations BEGIN
                INSERT INTO entities_fts(entities_fts, rowid, name, entity_type, observations, project)
                VALUES('delete', old.entity_id, (SELECT name FROM entities WHERE id = old.entity_id), (SELECT t.name FROM entity_types t JOIN entities e ON e.entity_type_id = t.id WHERE e.id = old.entity_id), '', (SELECT p.name FROM projects p JOIN entities e ON e.project_id = p.id WHERE e.id = old.entity_id));
                INSERT INTO entities_fts(rowid, name, entity_type, observations, project)
                SELECT e.id, e.name, t.name, (SELECT GROUP_CONCAT(content, ' ') FROM observations WHERE entity_id = old.entity_id), p.name
                FROM entities e JOIN entity_types t ON t.id = e.entity_type_id JOIN projects p ON p.id = e.project_id WHERE e.id = old.entity_id;
            END""",
        ],
    ),
    Migration(
        version=11,
        statements=[
            """CREATE TABLE relation_types (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                UNIQUE(name)
            )""",
            "INSERT INTO relation_types (name) SELECT DISTINCT relation_type FROM relations",
            "ALTER TABLE relations RENAME TO relations_old",
            """CREATE TABLE relations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id INTEGER NOT NULL,
                target_id INTEGER NOT NULL,
                relation_type_id INTEGER NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (source_id) REFERENCES entities(id),
                FOREIGN KEY (target_id) REFERENCES entities(id),
                FOREIGN KEY (relation_type_id) REFERENCES relation_types(id),
                UNIQUE(source_id, target_id, relation_type_id)
            )""",
            "INSERT INTO relations (id, source_id, target_id, relation_type_id, created_at) SELECT r.id, r.source_id, r.target_id, t.id, r.created_at FROM relations_old r JOIN relation_types t ON t.name = r.relation_type",
            "DROP TABLE relations_old",
            "DROP INDEX IF EXISTS idx_relations_source_id",
            "DROP INDEX IF EXISTS idx_relations_target_id",
            "CREATE INDEX IF NOT EXISTS idx_relations_source_id ON relations(source_id)",
            "CREATE INDEX IF NOT EXISTS idx_relations_target_id ON relations(target_id)",
            "CREATE INDEX IF NOT EXISTS idx_relation_types_name ON relation_types(name)",
        ],
    ),
    Migration(
        version=12,
        statements=[
            "ALTER TABLE observations RENAME TO observations_old",
            """CREATE TABLE observations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_id INTEGER NOT NULL,
                content TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (entity_id) REFERENCES entities(id)
            )""",
            "INSERT INTO observations SELECT * FROM observations_old",
            "DROP TABLE observations_old",
            "DROP INDEX IF EXISTS idx_observations_entity_id",
            "CREATE INDEX IF NOT EXISTS idx_observations_entity_id ON observations(entity_id)",
        ],
    ),
    Migration(
        version=13,
        statements=[
            "DROP TRIGGER IF EXISTS entities_fts_insert",
            "DROP TRIGGER IF EXISTS entities_fts_delete",
            "DROP TRIGGER IF EXISTS observations_fts_insert",
            "DROP TRIGGER IF EXISTS observations_fts_delete",
            "INSERT INTO entities_fts(entities_fts) VALUES('delete-all')",
            "INSERT INTO entities_fts(rowid, name, entity_type, observations, project) SELECT e.id, e.name, t.name, COALESCE((SELECT GROUP_CONCAT(o.content, ' ') FROM observations o WHERE o.entity_id = e.id), ''), p.name FROM entities e JOIN entity_types t ON t.id = e.entity_type_id JOIN projects p ON p.id = e.project_id",
            """CREATE TRIGGER IF NOT EXISTS entities_fts_insert AFTER INSERT ON entities BEGIN
                INSERT INTO entities_fts(rowid, name, entity_type, observations, project)
                VALUES (new.id, new.name, (SELECT name FROM entity_types WHERE id = new.entity_type_id), '', (SELECT name FROM projects WHERE id = new.project_id));
            END""",
            """CREATE TRIGGER IF NOT EXISTS entities_fts_delete AFTER DELETE ON entities BEGIN
                INSERT INTO entities_fts(entities_fts, rowid, name, entity_type, observations, project)
                VALUES('delete', old.id, old.name, (SELECT name FROM entity_types WHERE id = old.entity_type_id), '', (SELECT name FROM projects WHERE id = old.project_id));
            END""",
            """CREATE TRIGGER IF NOT EXISTS observations_fts_insert AFTER INSERT ON observations BEGIN
                INSERT INTO entities_fts(entities_fts, rowid, name, entity_type, observations, project)
                VALUES('delete', new.entity_id, (SELECT name FROM entities WHERE id = new.entity_id), (SELECT t.name FROM entity_types t JOIN entities e ON e.entity_type_id = t.id WHERE e.id = new.entity_id), COALESCE((SELECT GROUP_CONCAT(content, ' ') FROM observations WHERE entity_id = new.entity_id AND id != new.id), ''), (SELECT p.name FROM projects p JOIN entities e ON e.project_id = p.id WHERE e.id = new.entity_id));
                INSERT INTO entities_fts(rowid, name, entity_type, observations, project)
                SELECT e.id, e.name, t.name, (SELECT GROUP_CONCAT(content, ' ') FROM observations WHERE entity_id = new.entity_id), p.name
                FROM entities e JOIN entity_types t ON t.id = e.entity_type_id JOIN projects p ON p.id = e.project_id WHERE e.id = new.entity_id;
            END""",
            """CREATE TRIGGER IF NOT EXISTS observations_fts_delete AFTER DELETE ON observations BEGIN
                INSERT INTO entities_fts(entities_fts, rowid, name, entity_type, observations, project)
                VALUES('delete', old.entity_id, (SELECT name FROM entities WHERE id = old.entity_id), (SELECT t.name FROM entity_types t JOIN entities e ON e.entity_type_id = t.id WHERE e.id = old.entity_id), COALESCE((SELECT GROUP_CONCAT(content, ' ') || ' ' FROM observations WHERE entity_id = old.entity_id), '') || old.content, (SELECT p.name FROM projects p JOIN entities e ON e.project_id = p.id WHERE e.id = old.entity_id));
                INSERT INTO entities_fts(rowid, name, entity_type, observations, project)
                SELECT e.id, e.name, t.name, COALESCE((SELECT GROUP_CONCAT(content, ' ') FROM observations WHERE entity_id = old.entity_id), ''), p.name
                FROM entities e JOIN entity_types t ON t.id = e.entity_type_id JOIN projects p ON p.id = e.project_id WHERE e.id = old.entity_id;
            END""",
        ],
    ),
    Migration(
        version=14,
        statements=[
            "ALTER TABLE entities ADD COLUMN status TEXT",
            "CREATE INDEX IF NOT EXISTS idx_entities_status ON entities(status)",
        ],
    ),
    Migration(
        version=15,
        statements=[
            "DROP TRIGGER IF EXISTS entities_fts_insert",
            "DROP TRIGGER IF EXISTS entities_fts_delete",
            "DROP TRIGGER IF EXISTS observations_fts_insert",
            "DROP TRIGGER IF EXISTS observations_fts_delete",
            "INSERT INTO entities_fts(entities_fts) VALUES('delete-all')",
            "DROP TABLE IF EXISTS entities_fts",
            "CREATE VIRTUAL TABLE IF NOT EXISTS entities_fts USING fts5(name, entity_type, observations, project, content='', tokenize='porter unicode61')",
            "INSERT INTO entities_fts(rowid, name, entity_type, observations, project) SELECT e.id, e.name, t.name, COALESCE((SELECT GROUP_CONCAT(o.content, ' ') FROM observations o WHERE o.entity_id = e.id), ''), p.name FROM entities e JOIN entity_types t ON t.id = e.entity_type_id JOIN projects p ON p.id = e.project_id",
            """CREATE TRIGGER IF NOT EXISTS entities_fts_insert AFTER INSERT ON entities BEGIN
                INSERT INTO entities_fts(rowid, name, entity_type, observations, project)
                VALUES (new.id, new.name, (SELECT name FROM entity_types WHERE id = new.entity_type_id), '', (SELECT name FROM projects WHERE id = new.project_id));
            END""",
            """CREATE TRIGGER IF NOT EXISTS entities_fts_delete AFTER DELETE ON entities BEGIN
                INSERT INTO entities_fts(entities_fts, rowid, name, entity_type, observations, project)
                VALUES('delete', old.id, old.name, (SELECT name FROM entity_types WHERE id = old.entity_type_id), '', (SELECT name FROM projects WHERE id = old.project_id));
            END""",
            """CREATE TRIGGER IF NOT EXISTS observations_fts_insert AFTER INSERT ON observations BEGIN
                INSERT INTO entities_fts(entities_fts, rowid, name, entity_type, observations, project)
                VALUES('delete', new.entity_id, (SELECT name FROM entities WHERE id = new.entity_id), (SELECT t.name FROM entity_types t JOIN entities e ON e.entity_type_id = t.id WHERE e.id = new.entity_id), COALESCE((SELECT GROUP_CONCAT(content, ' ') FROM observations WHERE entity_id = new.entity_id AND id != new.id), ''), (SELECT p.name FROM projects p JOIN entities e ON e.project_id = p.id WHERE e.id = new.entity_id));
                INSERT INTO entities_fts(rowid, name, entity_type, observations, project)
                SELECT e.id, e.name, t.name, (SELECT GROUP_CONCAT(content, ' ') FROM observations WHERE entity_id = new.entity_id), p.name
                FROM entities e JOIN entity_types t ON t.id = e.entity_type_id JOIN projects p ON p.id = e.project_id WHERE e.id = new.entity_id;
            END""",
            """CREATE TRIGGER IF NOT EXISTS observations_fts_delete AFTER DELETE ON observations BEGIN
                INSERT INTO entities_fts(entities_fts, rowid, name, entity_type, observations, project)
                VALUES('delete', old.entity_id, (SELECT name FROM entities WHERE id = old.entity_id), (SELECT t.name FROM entity_types t JOIN entities e ON e.entity_type_id = t.id WHERE e.id = old.entity_id), COALESCE((SELECT GROUP_CONCAT(content, ' ') || ' ' FROM observations WHERE entity_id = old.entity_id), '') || old.content, (SELECT p.name FROM projects p JOIN entities e ON e.project_id = p.id WHERE e.id = old.entity_id));
                INSERT INTO entities_fts(rowid, name, entity_type, observations, project)
                SELECT e.id, e.name, t.name, COALESCE((SELECT GROUP_CONCAT(content, ' ') FROM observations WHERE entity_id = old.entity_id), ''), p.name
                FROM entities e JOIN entity_types t ON t.id = e.entity_type_id JOIN projects p ON p.id = e.project_id WHERE e.id = old.entity_id;
            END""",
        ],
    ),
    Migration(
        version=16,
        statements=[
            "ALTER TABLE entities ADD COLUMN updated_at DATETIME",
            "UPDATE entities SET updated_at = created_at",
            """CREATE TRIGGER IF NOT EXISTS entities_updated_at_status
                AFTER UPDATE OF status ON entities BEGIN
                UPDATE entities SET updated_at = CURRENT_TIMESTAMP WHERE id = new.id;
            END""",
            """CREATE TRIGGER IF NOT EXISTS entities_updated_at_obs_insert
                AFTER INSERT ON observations BEGIN
                UPDATE entities SET updated_at = CURRENT_TIMESTAMP WHERE id = new.entity_id;
            END""",
            """CREATE TRIGGER IF NOT EXISTS entities_updated_at_obs_delete
                AFTER DELETE ON observations BEGIN
                UPDATE entities SET updated_at = CURRENT_TIMESTAMP WHERE id = old.entity_id;
            END""",
        ],
    ),
    Migration(
        version=17,
        statements=[
            "PRAGMA legacy_alter_table = ON",
            "DROP TRIGGER IF EXISTS entities_fts_insert",
            "DROP TRIGGER IF EXISTS entities_fts_delete",
            "DROP TRIGGER IF EXISTS observations_fts_insert",
            "DROP TRIGGER IF EXISTS observations_fts_delete",
            "DROP TRIGGER IF EXISTS entities_updated_at_obs_insert",
            "DROP TRIGGER IF EXISTS entities_updated_at_obs_delete",
            "ALTER TABLE projects RENAME TO projects_old",
            """CREATE TABLE projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL COLLATE NOCASE,
                UNIQUE(name)
            )""",
            """INSERT INTO projects (id, name)
                SELECT MIN(id), name FROM projects_old GROUP BY name COLLATE NOCASE""",
            """UPDATE entities SET project_id = (
                SELECT p.id FROM projects p
                JOIN projects_old po ON po.id = entities.project_id
                WHERE p.name = po.name COLLATE NOCASE
            ) WHERE project_id NOT IN (SELECT id FROM projects)""",
            "DROP TABLE projects_old",
            "PRAGMA legacy_alter_table = OFF",
            "CREATE INDEX IF NOT EXISTS idx_projects_name ON projects(name)",
            """CREATE TRIGGER IF NOT EXISTS entities_fts_insert AFTER INSERT ON entities BEGIN
                INSERT INTO entities_fts(rowid, name, entity_type, observations, project)
                VALUES (new.id, new.name, (SELECT name FROM entity_types WHERE id = new.entity_type_id), '', (SELECT name FROM projects WHERE id = new.project_id));
            END""",
            """CREATE TRIGGER IF NOT EXISTS entities_fts_delete AFTER DELETE ON entities BEGIN
                INSERT INTO entities_fts(entities_fts, rowid, name, entity_type, observations, project)
                VALUES('delete', old.id, old.name, (SELECT name FROM entity_types WHERE id = old.entity_type_id), '', (SELECT name FROM projects WHERE id = old.project_id));
            END""",
            """CREATE TRIGGER IF NOT EXISTS observations_fts_insert AFTER INSERT ON observations BEGIN
                INSERT INTO entities_fts(entities_fts, rowid, name, entity_type, observations, project)
                VALUES('delete', new.entity_id, (SELECT name FROM entities WHERE id = new.entity_id), (SELECT t.name FROM entity_types t JOIN entities e ON e.entity_type_id = t.id WHERE e.id = new.entity_id), COALESCE((SELECT GROUP_CONCAT(content, ' ') FROM observations WHERE entity_id = new.entity_id AND id != new.id), ''), (SELECT p.name FROM projects p JOIN entities e ON e.project_id = p.id WHERE e.id = new.entity_id));
                INSERT INTO entities_fts(rowid, name, entity_type, observations, project)
                SELECT e.id, e.name, t.name, (SELECT GROUP_CONCAT(content, ' ') FROM observations WHERE entity_id = new.entity_id), p.name
                FROM entities e JOIN entity_types t ON t.id = e.entity_type_id JOIN projects p ON p.id = e.project_id WHERE e.id = new.entity_id;
            END""",
            """CREATE TRIGGER IF NOT EXISTS observations_fts_delete AFTER DELETE ON observations BEGIN
                INSERT INTO entities_fts(entities_fts, rowid, name, entity_type, observations, project)
                VALUES('delete', old.entity_id, (SELECT name FROM entities WHERE id = old.entity_id), (SELECT t.name FROM entity_types t JOIN entities e ON e.entity_type_id = t.id WHERE e.id = old.entity_id), COALESCE((SELECT GROUP_CONCAT(content, ' ') || ' ' FROM observations WHERE entity_id = old.entity_id), '') || old.content, (SELECT p.name FROM projects p JOIN entities e ON e.project_id = p.id WHERE e.id = old.entity_id));
                INSERT INTO entities_fts(rowid, name, entity_type, observations, project)
                SELECT e.id, e.name, t.name, COALESCE((SELECT GROUP_CONCAT(content, ' ') FROM observations WHERE entity_id = old.entity_id), ''), p.name
                FROM entities e JOIN entity_types t ON t.id = e.entity_type_id JOIN projects p ON p.id = e.project_id WHERE e.id = old.entity_id;
            END""",
            """CREATE TRIGGER IF NOT EXISTS entities_updated_at_obs_insert
                AFTER INSERT ON observations BEGIN
                UPDATE entities SET updated_at = CURRENT_TIMESTAMP WHERE id = new.entity_id;
            END""",
            """CREATE TRIGGER IF NOT EXISTS entities_updated_at_obs_delete
                AFTER DELETE ON observations BEGIN
                UPDATE entities SET updated_at = CURRENT_TIMESTAMP WHERE id = old.entity_id;
            END""",
        ],
    ),
    Migration(
        version=18,
        statements=[
            """CREATE TABLE IF NOT EXISTS project_paths (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                path TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(path),
                FOREIGN KEY (project_id) REFERENCES projects(id)
            )""",
            "CREATE INDEX IF NOT EXISTS idx_project_paths_project_id ON project_paths(project_id)",
        ],
    ),
    Migration(
        version=19,
        statements=_relation_type_backfill_statements(),
    ),
    Migration(
        version=20,
        statements=[
            "ALTER TABLE entities ADD COLUMN vote_score INTEGER NOT NULL DEFAULT 0",
        ],
    ),
]
