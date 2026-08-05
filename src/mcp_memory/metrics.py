"""Durable per-call usage telemetry: recorder plus usage-report aggregator.

Records one row per ``@_track``-wrapped MCP tool call into the ``tool_calls`` table. The stored
``input_bytes``/``output_bytes`` are ``payload_size()`` byte-count PROXIES for token cost (see
payload.py), NOT a real tokenizer. Only allowlisted small scalar options are stored (never raw
content-bearing params like query/name/observations), so telemetry never captures user text. The
recorder is wired into the server's ``@_track`` wrapper alongside the usefulness observer and, like
it, never raises.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .config import get_call_metrics_enabled
from .database import _parse_date
from .payload import payload_size

if TYPE_CHECKING:
    from .database import DatabaseManager

# Safe scalar option names recorded in the per-call options breakdown. Deliberately EXCLUDES
# content-bearing params (query/name/observations/entities/relations/content) so raw text is
# never stored - only small structural knobs whose distribution is worth measuring.
_TRACKED_OPTIONS = frozenset(
    {
        "compact",
        "match_all",
        "max_observation_chars",
        "limit",
        "k",
        "since",
        "status",
        "entityType",
        "min_content_tokens",
        "vote",
        "project",
        "start",
        "end",
    }
)

_MAX_OPTION_STR_LEN = 64  # guard: skip any allowlisted str value longer than this


def _is_trackable(value: Any) -> bool:
    """Whether an option value is a small scalar safe to store (not bulky/content)."""
    if isinstance(value, (bool, int, float)):
        return True
    return isinstance(value, str) and len(value) <= _MAX_OPTION_STR_LEN


def record(db: DatabaseManager, tool_name: str, kwargs: dict[str, Any], result: Any) -> None:
    """Record one tool call's byte-size proxies and allowlisted options. Never raises."""
    try:
        if not get_call_metrics_enabled():
            return
        if isinstance(result, dict) and "error" in result:
            return
        options = {
            key: value
            for key, value in kwargs.items()
            if key in _TRACKED_OPTIONS and _is_trackable(value)
        }
        db.record_tool_call(tool_name, payload_size(kwargs), payload_size(result), options)
    except Exception:  # noqa: S110 - instrumentation must never break a tool call
        pass


@dataclass(frozen=True)
class ToolUsage:
    """Per-tool byte-size stats and option-usage frequency over recorded calls."""

    tool: str
    call_count: int
    mean_input_bytes: float
    median_input_bytes: float
    mean_output_bytes: float
    median_output_bytes: float
    option_frequencies: dict[str, dict[str, int]]


@dataclass(frozen=True)
class UsageReport:
    """Aggregate call-metrics telemetry across all tools."""

    total_calls: int
    since: str | None
    total_input_bytes: int
    total_output_bytes: int
    input_output_ratio: float | None
    tools: list[ToolUsage]


@dataclass(frozen=True)
class UsageBucket:
    """Per-time-bucket, per-tool call count and summed byte-size proxies."""

    bucket: str
    tool: str
    call_count: int
    total_input_bytes: int
    total_output_bytes: int


_BUCKET_FORMATS = {"hour": "%Y-%m-%dT%H:00", "day": "%Y-%m-%d", "week": "%Y-W%W"}


def usage_over_time(
    db: DatabaseManager, bucket: str = "day", since: str | None = None
) -> list[UsageBucket]:
    """Aggregate recorded tool_calls into per-tool call count and summed bytes per time bucket.

    ``bucket`` is one of 'hour', 'day', or 'week'; any other value raises ``ValueError``. When
    ``since`` is given (relative '30m'/'1h'/'7d'/'2w'/'3mo' or ISO date), only calls recorded on
    or after that instant are included.
    """
    fmt = _BUCKET_FORMATS.get(bucket)
    if fmt is None:
        raise ValueError(f"bucket must be one of {sorted(_BUCKET_FORMATS)}, got {bucket!r}")

    sql = (
        "SELECT strftime(?, called_at) AS b, tool, COUNT(*) AS call_count, "
        "SUM(input_bytes) AS total_input_bytes, SUM(output_bytes) AS total_output_bytes "
        "FROM tool_calls "
    )
    params: list[str] = [fmt]
    if since is not None:
        sql += "WHERE datetime(called_at) >= datetime(?) "
        params.append(_parse_date(since))
    sql += "GROUP BY b, tool ORDER BY b, tool"
    rows = db._db.execute(sql, params).fetchall()

    return [
        UsageBucket(
            bucket=row["b"],
            tool=row["tool"],
            call_count=row["call_count"],
            total_input_bytes=row["total_input_bytes"],
            total_output_bytes=row["total_output_bytes"],
        )
        for row in rows
    ]


def usage_report(db: DatabaseManager, since: str | None = None) -> UsageReport:
    """Aggregate recorded tool_calls into per-tool byte-size stats and option-usage frequency.

    When ``since`` is given (relative '30m'/'1h'/'7d'/'2w'/'3mo' or ISO date), only calls
    recorded on or after that instant are included.
    """
    sql = "SELECT tool, input_bytes, output_bytes, options FROM tool_calls "
    params: list[str] = []
    if since is not None:
        sql += "WHERE datetime(called_at) >= datetime(?) "
        params.append(_parse_date(since))
    sql += "ORDER BY tool"
    rows = db._db.execute(sql, params).fetchall()

    grouped: dict[str, list[Any]] = {}
    for row in rows:
        grouped.setdefault(row["tool"], []).append(row)

    tools: list[ToolUsage] = []
    for tool in sorted(grouped):
        tool_rows = grouped[tool]
        input_bytes = [row["input_bytes"] for row in tool_rows]
        output_bytes = [row["output_bytes"] for row in tool_rows]
        option_frequencies: dict[str, dict[str, int]] = {}
        for row in tool_rows:
            for key, value in json.loads(row["options"]).items():
                option_frequencies.setdefault(key, {})
                option_frequencies[key][str(value)] = option_frequencies[key].get(str(value), 0) + 1
        tools.append(
            ToolUsage(
                tool=tool,
                call_count=len(tool_rows),
                mean_input_bytes=statistics.mean(input_bytes),
                median_input_bytes=statistics.median(input_bytes),
                mean_output_bytes=statistics.mean(output_bytes),
                median_output_bytes=statistics.median(output_bytes),
                option_frequencies=option_frequencies,
            )
        )

    total_input_bytes = sum(row["input_bytes"] for row in rows)
    total_output_bytes = sum(row["output_bytes"] for row in rows)

    return UsageReport(
        total_calls=sum(tool.call_count for tool in tools),
        since=since,
        total_input_bytes=total_input_bytes,
        total_output_bytes=total_output_bytes,
        input_output_ratio=(total_input_bytes / total_output_bytes if total_output_bytes else None),
        tools=tools,
    )
