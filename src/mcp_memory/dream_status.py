"""Persisted status of the autonomous dream curation pass, for the visualiser.

The dream runs inside the memory-agent process; the visualiser is served by the
separate mcp-memory process. The two share a data directory, so the agent writes
the latest pass and its config to a small JSON marker here (next to the database),
and mcp-memory reads it back to expose the dream's state over ``/api/dream``.

Only the most recent pass is kept - the current demoted state of the graph is the
ground truth for what remains demoted, so no rolling history is needed.
"""

from __future__ import annotations

import json
import re
import time
from typing import TYPE_CHECKING, TypedDict, cast

from .config import get_data_dir

if TYPE_CHECKING:
    from pathlib import Path

_SCHEMA = 2
_OPERATION_RE = re.compile(r"\[([^/\]]+)/([^\]]+)\]")

_configs: dict[str, DreamConfig] = {}
_last_pass: PassRecord | None = None


class Operation(TypedDict):
    """One entity the dream reported acting on, parsed from its audit line.

    ``action`` is ``"merge"`` when the reason describes a merge (heavy tier) and
    ``"demote"`` otherwise (the light tier only ever downvotes).
    """

    project: str
    name: str
    reason: str
    action: str


class PassRecord(TypedDict):
    """The outcome of a single dream curation pass."""

    ts: float
    ok: bool
    tier: str
    audit_text: str
    operations: list[Operation]


class DreamConfig(TypedDict):
    """A single dream tier's configuration, snapshotted at startup."""

    enabled: bool
    idle_threshold_seconds: float
    interval_seconds: float
    poll_seconds: float


class DreamStatus(TypedDict):
    """The persisted dream state: per-tier config plus the most recent pass."""

    schema: int
    configs: dict[str, DreamConfig]
    last_pass: PassRecord | None


def parse_operations(audit_text: str) -> list[Operation]:
    """Best-effort extract ``[project/entity] - reason`` operation lines from an audit.

    Lines without a bracketed slug are ignored, so narration around the operations
    does not produce spurious entries. The entity name keeps its type prefix. A reason
    beginning with "merged" (case-insensitive) is a merge; anything else is a demote.
    """
    operations: list[Operation] = []
    for line in audit_text.splitlines():
        match = _OPERATION_RE.search(line)
        if match is None:
            continue
        reason = line[match.end() :].lstrip(" :-\t").strip()
        action = "merge" if reason.lower().startswith("merged") else "demote"
        operations.append(
            {"project": match.group(1), "name": match.group(2), "reason": reason, "action": action}
        )
    return operations


def record_startup(
    *,
    tier: str = "light",
    enabled: bool,
    idle_threshold_seconds: float,
    interval_seconds: float,
    poll_seconds: float,
) -> None:
    """Snapshot one tier's config to the marker, preserving any prior pass on disk.

    ``tier`` keys the config so the light and heavy watchers each record their own
    without clobbering the other - both are asyncio tasks in the one memory-agent
    process, so the second call accumulates into the shared ``_configs`` dict.
    """
    global _last_pass  # noqa: PLW0603
    _configs[tier] = {
        "enabled": enabled,
        "idle_threshold_seconds": idle_threshold_seconds,
        "interval_seconds": interval_seconds,
        "poll_seconds": poll_seconds,
    }
    if _last_pass is None:
        _last_pass = _read_last_pass()
    _write()


def record_pass(audit_text: str, *, ok: bool, tier: str = "light") -> None:
    """Record the most recent dream pass (raw audit plus parsed operations).

    ``tier`` names which curation tier ran the pass ("light" or "heavy"); the two
    tiers share this single latest-pass slot, so it disambiguates whichever ran last.
    """
    global _last_pass  # noqa: PLW0603
    _last_pass = {
        "ts": time.time(),
        "ok": ok,
        "tier": tier,
        "audit_text": audit_text,
        "operations": parse_operations(audit_text),
    }
    _write()


def read_status() -> DreamStatus | None:
    """Read the persisted dream status, or None if the marker is absent or corrupt."""
    return _read_file()


def clear() -> None:
    """Reset the in-memory config and pass (for test isolation)."""
    global _configs, _last_pass  # noqa: PLW0603
    _configs = {}
    _last_pass = None


def _status_path() -> Path:
    """Return the path of the persisted dream-status marker file."""
    return get_data_dir() / "dream-status.json"


def _write() -> None:
    """Persist the current per-tier config and last pass to the marker file."""
    status: DreamStatus = {"schema": _SCHEMA, "configs": _configs, "last_pass": _last_pass}
    try:
        path = _status_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(status), encoding="utf-8")
    except OSError:
        pass


def _read_file() -> DreamStatus | None:
    """Read and validate the marker file, tolerating a missing or malformed file.

    A legacy schema-1 marker (a single flat ``config`` and ``demotions`` on the last
    pass) is normalised on read: the flat config becomes ``configs["light"]`` and its
    ``interval_seconds`` defaults to the idle threshold. The marker self-heals on the
    next ``record_startup`` (which fires on the deploy restart), so this is a read-shim,
    not a persisted migration.
    """
    try:
        raw = json.loads(_status_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    return {
        "schema": _SCHEMA,
        "configs": _normalise_configs(raw),
        "last_pass": _normalise_last_pass(raw.get("last_pass")),
    }


def _normalise_configs(raw: dict[str, object]) -> dict[str, DreamConfig]:
    """Return the per-tier configs, wrapping a legacy flat ``config`` as the light tier."""
    configs = raw.get("configs")
    if isinstance(configs, dict):
        return cast("dict[str, DreamConfig]", configs)
    legacy = raw.get("config")
    if not isinstance(legacy, dict):
        return {}
    idle_threshold = float(legacy["idle_threshold_seconds"])
    return {
        "light": {
            "enabled": bool(legacy.get("enabled")),
            "idle_threshold_seconds": idle_threshold,
            "interval_seconds": float(legacy.get("interval_seconds", idle_threshold)),
            "poll_seconds": float(legacy["poll_seconds"]),
        }
    }


def _normalise_last_pass(last_pass: object) -> PassRecord | None:
    """Return the last pass, upgrading a legacy ``demotions`` list to ``operations``."""
    if not isinstance(last_pass, dict):
        return None
    if "operations" not in last_pass and "demotions" in last_pass:
        legacy = last_pass.get("demotions") or []
        last_pass = {
            **last_pass,
            "operations": [
                {
                    "project": d.get("project", ""),
                    "name": d.get("name", ""),
                    "reason": d.get("reason", ""),
                    "action": "merge"
                    if str(d.get("reason", "")).lower().startswith("merged")
                    else "demote",
                }
                for d in legacy
            ],
        }
    return cast("PassRecord", last_pass)


def _read_last_pass() -> PassRecord | None:
    """Return the last pass persisted on disk, or None if unavailable."""
    status = _read_file()
    return status["last_pass"] if status else None
