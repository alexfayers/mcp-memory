"""The one write path that raises stored observation provenance."""

from __future__ import annotations

from typing import TYPE_CHECKING

from mcp_memory.models import PROVENANCE_RANK

if TYPE_CHECKING:
    from collections.abc import Iterable

    from mcp_memory.storage.connection import Connection


def _rank_sql(column: str) -> str:
    cases = " ".join(f"WHEN '{p}' THEN {rank}" for p, rank in PROVENANCE_RANK.items() if p is not None)
    return f"(CASE {column} {cases} ELSE {PROVENANCE_RANK[None]} END)"


_RAISE_SQL = (
    "UPDATE observations SET provenance = ?1 WHERE id = ?2 AND ?1 IS NOT NULL "
    f"AND {_rank_sql('provenance')} < {_rank_sql('?1')}"
)


def raise_provenance(connection: Connection, updates: Iterable[tuple[str | None, int]]) -> None:
    """Apply each (provenance, row id) only where it outranks the row's stored provenance.

    Updates apply in order, so several candidates for one row leave it at the highest.
    """
    connection.write_many(_RAISE_SQL, list(updates))
