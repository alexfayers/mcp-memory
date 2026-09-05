"""Pure SQL string helpers shared across the storage layer."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import re

_RELATIVE_DATE_RE = re.compile(r"^(\d+)(m|h|d|w|mo)$")
_RELATIVE_UNITS = {
    "m": timedelta(minutes=1),
    "h": timedelta(hours=1),
    "d": timedelta(days=1),
    "w": timedelta(weeks=1),
    "mo": timedelta(days=30),
}


def parse_date(value: str) -> str:
    """Parse a relative ('30m', '1h', '7d', '2w', '3mo') or ISO date/timestamp string to an
    ISO timestamp.
    """
    match = _RELATIVE_DATE_RE.match(value.strip())
    if match:
        amount, unit = int(match.group(1)), match.group(2)
        dt = datetime.now(tz=UTC) - amount * _RELATIVE_UNITS[unit]
        return dt.isoformat()
    return datetime.fromisoformat(value).isoformat()


def placeholders(n: int) -> str:
    return ",".join("?" * n)


def days_ago(n: int) -> str:
    return f"-{n} days"


def seconds_ago(n: float) -> str:
    return f"-{n} seconds"
