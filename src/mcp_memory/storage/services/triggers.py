"""The write paths for observation recall triggers."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

    from mcp_memory.storage.connection import Connection


def add_triggers(
    connection: Connection, observation_ids: Iterable[int], triggers: list[tuple[str, str, str, str]]
) -> None:
    """Give each observation every (kind, value, tool, action) trigger it does not already have."""
    connection.write_many(
        "INSERT OR IGNORE INTO observation_triggers (observation_id, kind, value, tool, action) VALUES (?, ?, ?, ?, ?)",
        [(observation_id, *trigger) for observation_id in observation_ids for trigger in triggers],
    )


def copy_triggers(connection: Connection, pairs: Iterable[tuple[int, int]]) -> None:
    """Copy every trigger of each (source, target) observation id pair's source to its target."""
    connection.write_many(
        "INSERT OR IGNORE INTO observation_triggers (observation_id, kind, value, tool, action) "
        "SELECT ?, kind, value, tool, action FROM observation_triggers WHERE observation_id = ?",
        [(target, source) for source, target in pairs],
    )


def triggers_of(connection: Connection, observation_id: int) -> list[dict[str, str]]:
    """Return an observation's triggers as export dicts."""
    rows = connection.query_all(
        "SELECT kind, value, tool FROM observation_triggers WHERE observation_id = ? ORDER BY id", (observation_id,)
    )
    return [dict(row) for row in rows]
