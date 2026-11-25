"""Backfill of observation provenance from wording markers, run once on server start."""

from __future__ import annotations

from datetime import UTC, datetime
import json
import re
from typing import TYPE_CHECKING

from mcp_memory.backup import backup_database
from mcp_memory.config import get_backup_dir
from mcp_memory.storage.services.provenance import raise_provenance

if TYPE_CHECKING:
    from mcp_memory.storage import Storage

_RECORD_PREFIX = "provenance-backfill-"

# The first rule to match an observation wins, so an inferred marker beats a user lead.
_RULES = (
    ("inferred", re.compile(r"^(INFERRED|Inferred|UNVERIFIED|Likely)|hypothesis:")),
    ("observed", re.compile(r"^(VERIFIED|OBSERVED|Verified|Source-verified|Confirmed)")),
    ("user", re.compile(r"^(User|The user) ")),
)


def backfill_once(db: Storage) -> int | None:
    """Tag untagged observations by wording and mark the run done, returning the rows tagged or None on a rerun."""
    if db.connection.query_one("SELECT 1 FROM provenance_backfill") is not None:
        return None
    rows = db.connection.query_all(
        "SELECT id, content FROM observations WHERE provenance IS NULL AND deleted_at IS NULL"
    )
    tagged = [
        (provenance, row["id"])
        for row in rows
        if (provenance := next((p for p, rule in _RULES if rule.search(row["content"])), None))
    ]
    if tagged:
        backup_database()
    with db.connection.transaction():
        raise_provenance(db.connection, tagged)
        db.connection.write("INSERT INTO provenance_backfill DEFAULT VALUES")
    if tagged:
        record_dir = get_backup_dir()
        record_dir.mkdir(parents=True, exist_ok=True)
        record = record_dir / f"{_RECORD_PREFIX}{datetime.now(UTC):%Y%m%dT%H%M%SZ}.json"
        record.write_text(json.dumps(tagged), encoding="utf-8")
    return len(tagged)
