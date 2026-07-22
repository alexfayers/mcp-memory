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
import os
import re
import tempfile
import time
from pathlib import Path
from typing import NotRequired, TypedDict, cast

from .config import get_data_dir

_SCHEMA = 3
_OPERATION_RE = re.compile(r"\[([^/\]]+)/([^\]#]+)(?:#([0-9a-f]+))?\]")
# A standalone "observation"/"observations"/"obs" token marks an observation-level
# op. Word-bounded so it never fires on "obsolete", "observed", or "jobs".
_OBSERVATION_WORD_RE = re.compile(r"\bobservations?\b|\bobs\b")


def _classify_action(reason: str) -> str:
    """Classify an audit reason into demote / merge / obs-demote / obs-merge.

    Observation-level ops are flagged by an "observation"/"obs" word in the reason;
    a merge is flagged by a "merge" word (entity merges begin with "merged").
    """
    lowered = reason.lower()
    is_observation = bool(_OBSERVATION_WORD_RE.search(lowered))
    if is_observation and "merge" in lowered:
        return "obs-merge"
    if is_observation:
        return "obs-demote"
    if lowered.startswith("merged"):
        return "merge"
    return "demote"


_configs: dict[str, DreamConfig] = {}
_last_pass: PassRecord | None = None
_running: str | None = None


class Operation(TypedDict):
    """One entity or observation the dream reported acting on, parsed from its audit line.

    ``action`` is one of ``"demote"`` (entity downvote), ``"merge"`` (entity merge),
    ``"obs-demote"`` (observation downvote), or ``"obs-merge"`` (observation merge).
    ``hash`` is the observation's content_hash when the line addressed one, absent
    for entity-level ops.
    """

    project: str
    name: str
    reason: str
    action: str
    hash: NotRequired[str]


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
    poll_seconds: float


class DreamStatus(TypedDict):
    """The persisted dream state: per-tier config, the most recent pass, and any live run."""

    schema: int
    configs: dict[str, DreamConfig]
    last_pass: PassRecord | None
    running: str | None


def parse_operations(audit_text: str) -> list[Operation]:
    """Best-effort extract ``[project/entity] - reason`` operation lines from an audit.

    Lines without a bracketed slug are ignored, so narration around the operations
    does not produce spurious entries. The entity name keeps its type prefix, and an
    optional ``#hash`` suffix on the slug (e.g. ``[project/entity#a1b2c3d4]``) is
    captured as the observation's content_hash. The action is classified into
    demote / merge / obs-demote / obs-merge (see ``_classify_action``).
    """
    operations: list[Operation] = []
    for line in audit_text.splitlines():
        match = _OPERATION_RE.search(line)
        if match is None:
            continue
        reason = line[match.end() :].lstrip(" :-\t").strip()
        operation: Operation = {
            "project": match.group(1),
            "name": match.group(2),
            "reason": reason,
            "action": _classify_action(reason),
        }
        if match.group(3) is not None:
            operation["hash"] = match.group(3)
        operations.append(operation)
    return operations


def record_startup(
    *,
    tier: str = "light",
    enabled: bool,
    idle_threshold_seconds: float,
    poll_seconds: float,
) -> None:
    """Snapshot one tier's config to the marker, preserving any prior pass on disk.

    ``tier`` keys the config so the light and heavy tiers each record their own
    without clobbering the other - the coordinator records both at startup, so the
    second call accumulates into the shared ``_configs`` dict.

    Resets any ``running`` flag: on a fresh process nothing is truly in flight, so
    this bounds a stale flag a crash may have left mid-pass (mirroring recall_status).
    """
    global _last_pass, _running  # noqa: PLW0603
    _configs[tier] = {
        "enabled": enabled,
        "idle_threshold_seconds": idle_threshold_seconds,
        "poll_seconds": poll_seconds,
    }
    if _last_pass is None:
        _last_pass = _read_last_pass()
    _running = None
    _write()


def record_pass_start(tier: str) -> None:
    """Mark a curation pass of ``tier`` as in flight, for the visualiser's live indicator."""
    global _running  # noqa: PLW0603
    _running = tier
    _write()


def record_pass(audit_text: str, *, ok: bool, tier: str = "light") -> None:
    """Record the most recent dream pass (raw audit plus parsed operations) and clear ``running``.

    ``tier`` names which curation tier ran the pass ("light" or "heavy"); the two
    tiers share this single latest-pass slot, so it disambiguates whichever ran last.
    """
    global _last_pass, _running  # noqa: PLW0603
    _last_pass = {
        "ts": time.time(),
        "ok": ok,
        "tier": tier,
        "audit_text": audit_text,
        "operations": parse_operations(audit_text),
    }
    _running = None
    _write()


def read_status() -> DreamStatus | None:
    """Read the persisted dream status, or None if the marker is absent or corrupt."""
    return _read_file()


def clear() -> None:
    """Reset the in-memory config, pass, and running flag (for test isolation)."""
    global _configs, _last_pass, _running  # noqa: PLW0603
    _configs = {}
    _last_pass = None
    _running = None


def _status_path() -> Path:
    """Return the path of the persisted dream-status marker file."""
    return get_data_dir() / "dream-status.json"


def _write() -> None:
    """Persist the current config, last pass, and running flag atomically (temp file + replace).

    The running flag is written on every pass boundary and read by the separate
    mcp-memory process, so a plain write could expose a torn file; ``replace`` swaps
    it in atomically.
    """
    status: DreamStatus = {
        "schema": _SCHEMA,
        "configs": _configs,
        "last_pass": _last_pass,
        "running": _running,
    }
    try:
        path = _status_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        tmp_path = Path(tmp)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(status, handle)
            tmp_path.replace(path)
        except OSError:
            tmp_path.unlink()
            raise
    except OSError:
        pass


def _read_file() -> DreamStatus | None:
    """Read and validate the marker file, tolerating a missing or malformed file.

    Legacy markers are normalised on read: a schema-1 marker's single flat ``config``
    becomes ``configs["light"]``, and any tier config still carrying the removed
    ``interval_seconds`` key has it dropped. The marker self-heals on the next
    ``record_startup`` (which fires on the deploy restart), so this is a read-shim,
    not a persisted migration.
    """
    try:
        raw = json.loads(_status_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    running = raw.get("running")
    return {
        "schema": _SCHEMA,
        "configs": _normalise_configs(raw),
        "last_pass": _normalise_last_pass(raw.get("last_pass")),
        "running": running if isinstance(running, str) else None,
    }


def _tier_config(raw: dict[str, object]) -> DreamConfig:
    """Project a raw config dict onto DreamConfig, dropping any extra (legacy) keys."""
    return {
        "enabled": bool(raw.get("enabled")),
        "idle_threshold_seconds": float(cast("float", raw["idle_threshold_seconds"])),
        "poll_seconds": float(cast("float", raw["poll_seconds"])),
    }


def _normalise_configs(raw: dict[str, object]) -> dict[str, DreamConfig]:
    """Return the per-tier configs, wrapping a legacy flat ``config`` as the light tier."""
    configs = raw.get("configs")
    if isinstance(configs, dict):
        return {tier: _tier_config(cfg) for tier, cfg in configs.items() if isinstance(cfg, dict)}
    legacy = raw.get("config")
    if not isinstance(legacy, dict):
        return {}
    return {"light": _tier_config(legacy)}


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
                    "action": _classify_action(str(d.get("reason", ""))),
                }
                for d in legacy
            ],
        }
    return cast("PassRecord", last_pass)


def _read_last_pass() -> PassRecord | None:
    """Return the last pass persisted on disk, or None if unavailable."""
    status = _read_file()
    return status["last_pass"] if status else None
