"""FTS5 sync and query sanitisation for the entities_fts virtual table."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mcp_memory.storage.connection import Connection


_ENTITY_FTS_PROJECTION_SQL = (
    "e.id, e.name, t.name, "
    "COALESCE((SELECT GROUP_CONCAT(content, ' ') FROM observations WHERE "
    "entity_id = e.id), ''), p.name FROM entities e "
    "JOIN entity_types t ON t.id = e.entity_type_id "
    "JOIN projects p ON p.id = e.project_id WHERE e.id = ?"
)


def sanitize_fts_query(query: str, match_all: bool = False) -> str:
    """Escape and quote tokens for an FTS5 MATCH expression.

    Args:
        query: Raw query string, split into whitespace-separated tokens.
        match_all: Join tokens with implicit AND when True, otherwise OR.

    Returns:
        A quoted FTS5 MATCH string, or an empty string when no tokens remain.
    """
    tokens = [f'"{token.replace(chr(34), chr(34) + chr(34))}"' for token in query.split() if token]
    separator = " " if match_all else " OR "
    return separator.join(tokens)


def refresh_for_entity(connection: Connection, entity_id: int, *, delete: bool) -> None:
    """Sync the FTS row for an entity after a project change (delete old, insert new)."""
    if delete:
        connection.write(
            "INSERT INTO entities_fts(entities_fts, rowid, name, entity_type, observations, "
            f"project) SELECT 'delete', {_ENTITY_FTS_PROJECTION_SQL}",
            (entity_id,),
        )
    else:
        connection.write(
            "INSERT INTO entities_fts(rowid, name, entity_type, observations, project) "
            f"SELECT {_ENTITY_FTS_PROJECTION_SQL}",
            (entity_id,),
        )
