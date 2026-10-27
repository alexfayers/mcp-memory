"""Anonymous usage telemetry, sent through the helpers llm-prompts shares.

Off when llm-prompts is not installed or its opt-out applies. Only tool names, counts and byte sizes
leave the machine: never the stored options, project names or memory text.
"""

from __future__ import annotations

from collections import Counter
import importlib
from importlib.metadata import PackageNotFoundError, distribution
import json
import platform
import statistics
import sys
from types import ModuleType
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .storage import Storage

_SCHEMA = 1
REPORT_INTERVAL_SECONDS = 60 * 60


def _load_core() -> ModuleType | None:
    try:
        return importlib.import_module("llm_prompts.telemetry_core")
    except ImportError:
        return None


_core = _load_core()
_errors: Counter[str] = Counter()
_reported_failures: set[tuple[str | int, ...]] = set()


def note_result(tool: str, result: dict[str, Any]) -> None:
    """Count the call as failed when its result is an error."""
    if "error" in result:
        _errors[tool] += 1


def _install_id() -> str | None:
    """Return the install id when telemetry is on and the opt-out record is fresh, else None."""
    if _core is None or not (_core.enabled() and _core.may_send()):
        return None
    return str(_core.install_id() or "") or None


def _bucket(count: int) -> str:
    """Return the power-of-ten range holding a count, such as 10-99."""
    if count <= 0:
        return "0"
    low = 10 ** (len(str(count)) - 1)
    return f"{low}-{low * 10 - 1}"


def _installed_commit() -> dict[str, Any]:
    """Return the commit mcp-memory was installed from, or none for a local or unknown install."""
    try:
        text = distribution("mcp-memory").read_text("direct_url.json")
    except PackageNotFoundError:
        text = None
    source = json.loads(text) if text else {}
    return {"commit": source.get("vcs_info", {}).get("commit_id"), "local": "dir_info" in source}


def _context() -> dict[str, Any]:
    return {
        "mcp_memory_commit": _installed_commit(),
        "os": platform.system(),
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
        "schema": _SCHEMA,
        "$process_person_profile": False,
    }


def _exception_event(exc: BaseException, where: str) -> dict[str, Any] | None:
    """Build a crash event unless this failure site was already reported."""
    items = _core.exception_items(exc, {}) if _core is not None else []
    if not items:
        return None
    frames = items[0]["stacktrace"]["frames"]
    site = (items[0]["type"], *((frames[-1]["function"], frames[-1]["lineno"]) if frames else ()))
    if site in _reported_failures:
        return None
    _reported_failures.add(site)
    return {"event": "$exception", "properties": {"$exception_list": items, "where": where, **_context()}}


def report_exception(exc: BaseException, where: str) -> None:
    """Send a crash's type and package frames, once per failure site. Never raises."""
    try:
        install_id = _install_id()
        if install_id is not None and _core is not None and _core.reportable(exc):
            event = _exception_event(exc, where)
            if event is not None:
                _core.send(event, install_id)
    except Exception:  # ruff: ignore[try-except-pass] - telemetry must never break the server
        pass


class UsageTelemetry:
    """Reports the tool calls recorded since it started, a rollup at a time."""

    def __init__(self, db: Storage) -> None:
        """Start counting from the newest recorded call."""
        self._db = db
        self._last_id = self._newest_id()

    def _newest_id(self) -> int:
        row = self._db.connection.query_one("SELECT COALESCE(MAX(id), 0) AS id FROM tool_calls")
        return int(row["id"]) if row else 0

    def _count(self, sql: str) -> int:
        row = self._db.connection.query_one(sql)
        return int(row["n"]) if row else 0

    def _store_size(self) -> dict[str, str]:
        """Return the store's size as buckets, never exact figures."""
        return {
            "db_mb": _bucket(self._db.connection.path.stat().st_size // 1_000_000),
            "entities": _bucket(self._count("SELECT COUNT(*) AS n FROM entities WHERE deleted_at IS NULL")),
            "projects": _bucket(self._count("SELECT COUNT(*) AS n FROM projects")),
        }

    def _rollup(self) -> dict[str, dict[str, int | float]]:
        """Return per-tool counts and median sizes since the last report, then move past them."""
        rows = self._db.connection.query_all(
            "SELECT id, tool, input_bytes, output_bytes FROM tool_calls WHERE id > ?", (self._last_id,)
        )
        calls: dict[str, list[Any]] = {}
        for row in rows:
            calls.setdefault(row["tool"], []).append(row)
            self._last_id = max(self._last_id, row["id"])
        errors = dict(_errors)
        _errors.clear()
        return {
            tool: {
                "ok": len(calls.get(tool, [])),
                "errors": errors.get(tool, 0),
                "median_input_bytes": statistics.median([r["input_bytes"] for r in calls.get(tool, [])] or [0]),
                "median_output_bytes": statistics.median([r["output_bytes"] for r in calls.get(tool, [])] or [0]),
            }
            for tool in sorted({*calls, *errors})
        }

    def report(self) -> None:
        """Send the calls since the last report, unless telemetry is off or there were none."""
        tools = self._rollup()
        install_id = _install_id()
        if tools and install_id and _core is not None:
            _core.send(
                {"event": "memory_usage", "properties": {"tools": tools, **self._store_size(), **_context()}},
                install_id,
            )
