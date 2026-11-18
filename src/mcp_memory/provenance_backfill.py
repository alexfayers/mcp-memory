"""Backfill of observation provenance from wording markers, run once on server start."""

from __future__ import annotations

from datetime import UTC, datetime
import json
import re
from typing import TYPE_CHECKING

from mcp_memory.backup import backup_database
from mcp_memory.config import get_backup_dir

if TYPE_CHECKING:
    from mcp_memory.storage import Storage

_RECORD_PREFIX = "provenance-backfill-"

# The first rule to match an observation wins, so an inferred marker beats a user lead.
_RULES = (
    ("inferred", re.compile(r"^(INFERRED|Inferred|UNVERIFIED|Likely)|hypothesis:")),
    ("observed", re.compile(r"^(VERIFIED|OBSERVED|Verified|Source-verified|Confirmed)")),
    ("user", re.compile(r"^(User|The user) |(?i:user: ')")),
)


def backfill_once(db: Storage) -> int | None:
    """Tag untagged observations by wording and record the run, returning the rows tagged, or None if it ran before."""
    record_dir = get_backup_dir()
    if record_dir.is_dir() and any(record_dir.glob(f"{_RECORD_PREFIX}*")):
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
    record = record_dir / f"{_RECORD_PREFIX}{datetime.now(UTC):%Y%m%dT%H%M%SZ}.json"
    # The leading dot keeps a crash-left temp file out of the record glob.
    temp_file = record_dir / f".{record.name}.tmp"
    record_dir.mkdir(parents=True, exist_ok=True)
    temp_file.write_text(json.dumps(tagged), encoding="utf-8")
    try:
        with db.connection.transaction():
            db.connection.write_many("UPDATE observations SET provenance = ? WHERE id = ?", tagged)
            temp_file.replace(record)
    except BaseException:
        temp_file.unlink(missing_ok=True)
        record.unlink(missing_ok=True)
        raise
    return len(tagged)
