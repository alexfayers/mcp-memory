"""Anonymous usage telemetry, sent through the helpers llm-prompts shares.

Off when llm-prompts is not installed or its opt-out applies. Only tool names, counts, byte sizes, ranking-score
means, power-of-ten bucketed counts and version identifiers (commits of the installed
llm-prompts, cline-hooks and mcp-memory) leave the machine: never the stored options, queries, entity or
project names or memory text.

The ``memory_usage``, ``memory_eval``, ``memory_dream`` and crash events carry ``versions`` (for llm-prompts,
cline-hooks and mcp-memory, each {commit, base, local, dirty, ahead}, where ``base`` is the newest origin/main commit
the install contains) and ``overlays`` (the same for overlay installs from GitHub).

The ``memory_usage`` event also carries the counts ``searches``, ``searches_used`` (searches with a result that was
used afterwards), ``empty_searches``, ``writes`` and ``nudges`` (reminders the hooks showed, also by kind in
``nudges_by_kind``), so real use can be compared across versions. ``relations_created`` counts the links made, by
type (any valid relation type, else ``other``), and the ``relations`` and
``linked_entities`` size buckets show how connected the store is.

The ``memory_eval`` event is sent each time the server goes idle after new searches. It carries the score means, the
``db_mb``, ``entities`` and ``projects`` size buckets and ``min_content_tokens``, the shortest query, in words, that
was scored.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass, field
import importlib
import json
from pathlib import Path
import statistics
import threading
from types import ModuleType
from typing import TYPE_CHECKING, Any

from . import nudge_count
from .atomic_write import write_json_atomic
from .config import get_data_dir
from .eval import EvalReport, evaluate_cached_async
from .models import VALID_RELATION_TYPES
from .tool_names import MUTATING_TOOLS, SEARCH_TOOLS

if TYPE_CHECKING:
    from .storage import Storage

_SCHEMA = 2
IDLE_SECONDS = 15 * 60
POLL_SECONDS = 60
EVAL_K = 10
EVAL_MIN_CONTENT_TOKENS = 2
_EVAL_STATE_FILE = "eval-telemetry.json"
_OTHER_LINK_TYPE = "other"
_LIVE_LINKS = (
    "SELECT r.source_id, r.target_id FROM relations r "
    "JOIN entities s ON s.id = r.source_id AND s.deleted_at IS NULL "
    "JOIN entities t ON t.id = r.target_id AND t.deleted_at IS NULL"
)
_UP_TO_CALL = "COALESCE((SELECT called_at FROM tool_calls WHERE id = ?), '9999-12-31 23:59:59')"


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
    return _core.active_install_id() if _core is not None else None


def tool_versions() -> dict[str, Any]:
    """Return the version entry of every tracked tool and overlay, resolved once per process."""
    return _core.resolved_versions() if _core is not None else {}


def _score_properties(report: EvalReport) -> dict[str, Any]:
    """Return a report's score means, rounded."""
    return {
        "mean_precision_at_k": round(report.mean_precision_at_k, 3),
        "mrr": round(report.mrr, 3),
        "mean_recall_at_k": round(report.mean_recall_at_k, 3),
        "mean_ndcg_at_k": round(report.mean_ndcg_at_k, 3),
        "mean_success_at_k": round(report.mean_success_at_k, 3),
    }


def _store_size(db: Storage, core: ModuleType) -> dict[str, str]:
    """Return the store's size as buckets, never exact figures."""

    def count(sql: str) -> int:
        row = db.connection.query_one(sql)
        return int(row["n"]) if row else 0

    return {
        "db_mb": core.bucket(db.connection.path.stat().st_size // 1_000_000),
        "entities": core.bucket(count("SELECT COUNT(*) AS n FROM entities WHERE deleted_at IS NULL")),
        "projects": core.bucket(count("SELECT COUNT(*) AS n FROM projects")),
        "relations": core.bucket(count(f"SELECT COUNT(*) AS n FROM ({_LIVE_LINKS})")),
        "linked_entities": core.bucket(
            count(
                f"SELECT COUNT(*) AS n FROM (SELECT source_id FROM ({_LIVE_LINKS}) "
                f"UNION SELECT target_id FROM ({_LIVE_LINKS}))"
            )
        ),
    }


def _context(core: ModuleType) -> dict[str, Any]:
    return {**core.resolved_versions(), **core.context(_SCHEMA)}


def _exception_event(core: ModuleType, exc: BaseException, where: str) -> dict[str, Any] | None:
    """Build a crash event unless this failure site was already reported."""
    event: dict[str, Any] = core.exception_event(exc, {}, {"where": where, **_context(core)})
    items = event["properties"]["$exception_list"]
    frames = items[0]["stacktrace"]["frames"]
    site = (items[0]["type"], *((frames[-1]["function"], frames[-1]["lineno"]) if frames else ()))
    if site in _reported_failures:
        return None
    _reported_failures.add(site)
    return event


def report_exception(exc: BaseException, where: str) -> None:
    """Send a crash's type and package frames, once per failure site. Never raises."""
    try:
        install_id = _install_id()
        if install_id is not None and _core is not None and _core.reportable(exc):
            event = _exception_event(_core, exc, where)
            if event is not None:
                _core.send(event, install_id)
    except Exception:  # ruff: ignore[try-except-pass] - telemetry must never break the server
        pass


def report_dream(tier: str, *, ok: bool, duration_ms: int, error_type: str | None, operations: Counter[str]) -> None:
    """Send one dream pass's tier, outcome, duration, error class name and per-action counts. Never raises."""
    try:
        install_id = _install_id()
        if install_id is not None and _core is not None:
            properties = {
                "tier": tier,
                "ok": ok,
                "duration_ms": duration_ms,
                "error_type": error_type,
                "operations": dict(operations),
                **_context(_core),
            }
            _core.send({"event": "memory_dream", "properties": properties}, install_id)
    except Exception:  # ruff: ignore[try-except-pass] - telemetry must never break the server
        pass


@dataclass(frozen=True)
class _WindowCounts:
    """The counts besides tool calls that one report covers."""

    searches_used: int
    searches_with_results: int
    relations_created: dict[str, int] = field(default_factory=dict)
    nudges_by_kind: dict[str, int] = field(default_factory=dict)


class UsageTelemetry:
    """Reports the tool calls recorded since it started, a rollup at a time."""

    def __init__(self, db: Storage) -> None:
        """Start counting from the newest recorded call."""
        self._db = db
        self._report_lock = threading.Lock()
        self._reported_this_idle = False
        self._new_use_cutoff: int | None = None
        self._last_id = self._newest_id()
        self._last_surfaced_id = self._newest_surfaced_id()
        self._last_relation_id = self._newest_relation_id()

    def _newest_surfaced_id(self) -> int:
        row = self._db.connection.query_one("SELECT COALESCE(MAX(id), 0) AS id FROM surfaced_entities")
        return int(row["id"]) if row else 0

    def _newest_relation_id(self) -> int:
        row = self._db.connection.query_one("SELECT COALESCE(MAX(id), 0) AS id FROM relations")
        return int(row["id"]) if row else 0

    def _newest_id(self) -> int:
        row = self._db.connection.query_one("SELECT COALESCE(MAX(id), 0) AS id FROM tool_calls")
        return int(row["id"]) if row else 0

    def _rollup(self, up_to: int | None = None) -> tuple[dict[str, dict[str, int | float]], int]:
        """Return per-tool counts and median sizes since the last report, and the newest call id they cover.

        With ``up_to``, only calls up to that id count.
        """
        rows = self._db.connection.query_all(
            "SELECT id, tool, input_bytes, output_bytes FROM tool_calls WHERE id > ? AND id <= ?",
            (self._last_id, self._newest_id() if up_to is None else up_to),
        )
        calls: dict[str, list[Any]] = {}
        for row in rows:
            calls.setdefault(row["tool"], []).append(row)
        errors = {tool: count for tool, count in _errors.items() if count > 0}
        tools = {
            tool: {
                "ok": len(calls.get(tool, [])),
                "errors": errors.get(tool, 0),
                "median_input_bytes": statistics.median([r["input_bytes"] for r in calls.get(tool, [])] or [0]),
                "median_output_bytes": statistics.median([r["output_bytes"] for r in calls.get(tool, [])] or [0]),
            }
            for tool in sorted({*calls, *errors})
        }
        return tools, max((row["id"] for row in rows), default=self._last_id)

    def _surfaced_since_last_report(self, up_to: int | None) -> tuple[int, int, int]:
        """Return the searches with a used result, the searches with any result, and the newest result id.

        Counts cover the results surfaced since the last report. With ``up_to``, only results surfaced by the time
        of that call count.
        """
        rows = self._db.connection.query_all(
            f"SELECT id, retrieval_id, used_at IS NOT NULL AS used FROM surfaced_entities WHERE id > ? "
            f"AND surfaced_at <= {_UP_TO_CALL}",
            (self._last_surfaced_id, up_to),
        )
        used = len({row["retrieval_id"] for row in rows if row["used"]})
        answered = len({row["retrieval_id"] for row in rows})
        return used, answered, max((row["id"] for row in rows), default=self._last_surfaced_id)

    def _links_since_last_report(self, up_to: int | None) -> tuple[dict[str, int], int]:
        """Return the links created since the last report by type, and the newest link id.

        Types outside the valid relation types count as ``other``. With ``up_to``, only links created by the time
        of that call count.
        """
        rows = self._db.connection.query_all(
            f"SELECT r.id, t.name FROM relations r JOIN relation_types t ON t.id = r.relation_type_id "
            f"WHERE r.id > ? AND r.created_at <= {_UP_TO_CALL}",
            (self._last_relation_id, up_to),
        )
        counts = Counter(row["name"] if row["name"] in VALID_RELATION_TYPES else _OTHER_LINK_TYPE for row in rows)
        return dict(counts), max((row["id"] for row in rows), default=self._last_relation_id)

    def _deliver(
        self,
        core: ModuleType,
        tools: dict[str, dict[str, int | float]],
        counts: _WindowCounts,
        install_id: str,
    ) -> bool:
        """Post the rollup inline and return whether it arrived."""
        searches = sum(tools[tool]["ok"] for tool in SEARCH_TOOLS if tool in tools)
        properties = {
            "tools": tools,
            "searches": searches,
            "searches_used": counts.searches_used,
            "empty_searches": max(searches - counts.searches_with_results, 0),
            "relations_created": counts.relations_created,
            "nudges": sum(counts.nudges_by_kind.values()),
            "nudges_by_kind": counts.nudges_by_kind,
            "writes": sum(tools[tool]["ok"] for tool in MUTATING_TOOLS if tool in tools),
            **_store_size(self._db, core),
            **_context(core),
        }
        return bool(core.post_event({"event": "memory_usage", "properties": properties}, install_id))

    def note_call(self, idle_seconds: float) -> None:
        """Mark where the previous batch ends when a call arrives after an idle stretch."""
        if idle_seconds >= IDLE_SECONDS:
            self._new_use_cutoff = self._newest_id()

    def report(self, up_to: int | None = None) -> None:
        """Send the calls since the last report, unless telemetry is off or there were none.

        A failed send leaves the calls and error counts in place for the next report.
        With ``up_to``, calls after that id wait. Error counts are not tied to call ids, so all current ones are sent.
        """
        with self._report_lock:
            tools, newest_id = self._rollup(up_to)
            searches_used, searches_with_results, newest_surfaced_id = self._surfaced_since_last_report(up_to)
            relations_created, newest_relation_id = self._links_since_last_report(up_to)
            counts = _WindowCounts(
                searches_used, searches_with_results, relations_created, nudge_count.read() if tools else {}
            )
            install_id = _install_id()
            if tools and install_id and _core is not None and not self._deliver(_core, tools, counts, install_id):
                return
            self._last_id = newest_id
            self._last_surfaced_id = newest_surfaced_id
            self._last_relation_id = newest_relation_id
            nudge_count.subtract(counts.nudges_by_kind)
            _errors.subtract({tool: counts["errors"] for tool, counts in tools.items()})

    def report_when_due(self, idle_seconds: float) -> None:
        """Send the calls made before a new use began, then once per idle stretch, when it reaches IDLE_SECONDS."""
        if self._new_use_cutoff is not None:
            cutoff, self._new_use_cutoff = self._new_use_cutoff, None
            self.report(up_to=cutoff)
        if idle_seconds < IDLE_SECONDS:
            self._reported_this_idle = False
        elif not self._reported_this_idle:
            self._reported_this_idle = True
            self.report()


class _IdleReporter:
    """Lets a report be attempted once per idle stretch and keeps its state across restarts."""

    _state_file: str

    def __init__(self) -> None:
        """Start with no attempt made in the current idle stretch."""
        self._attempted_this_idle = False

    def _state_path(self) -> Path:
        return get_data_dir() / self._state_file

    def _read_state(self) -> dict[str, Any]:
        try:
            state = json.loads(self._state_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return state if isinstance(state, dict) else {}

    def _begin_attempt(self, idle_seconds: float) -> tuple[ModuleType, str] | None:
        """Return the core and install id once per idle stretch, and only when telemetry is on."""
        if idle_seconds < IDLE_SECONDS:
            self._attempted_this_idle = False
            return None
        if self._attempted_this_idle:
            return None
        self._attempted_this_idle = True
        install_id = _install_id()
        return (_core, install_id) if _core is not None and install_id is not None else None


class EvalTelemetry(_IdleReporter):
    """Reports the search ranking quality as score means, once per idle stretch that follows new searches."""

    _state_file = _EVAL_STATE_FILE

    def __init__(self, db: Storage) -> None:
        """Score the database the server serves."""
        super().__init__()
        self._db = db

    def _newest_search_id(self) -> int:
        row = self._db.connection.query_one("SELECT COALESCE(MAX(id), 0) AS id FROM surfaced_entities")
        return int(row["id"]) if row else 0

    def _deliver(self, core: ModuleType, report: EvalReport, install_id: str) -> bool:
        """Post the score means, bucketed query count, store size and version tags inline."""
        properties = {
            "k": report.k,
            "queries": core.bucket(report.query_count),
            "min_content_tokens": EVAL_MIN_CONTENT_TOKENS,
            **_score_properties(report),
            **_store_size(self._db, core),
            **_context(core),
        }
        return bool(core.post_event({"event": "memory_eval", "properties": properties}, install_id))

    async def report_when_due(self, idle_seconds: float) -> None:
        """Score and send once per idle stretch with new searches since the last eval, off the event loop.

        Nothing is scored when telemetry is off. A failed send is retried on the next idle stretch.
        """
        attempt = self._begin_attempt(idle_seconds)
        if attempt is None:
            return
        core, install_id = attempt
        newest = self._newest_search_id()
        if newest <= self._read_state().get("search_id", 0):
            return
        report = await evaluate_cached_async(self._db, k=EVAL_K, min_content_tokens=EVAL_MIN_CONTENT_TOKENS)
        delivered = report.query_count > 0 and await asyncio.to_thread(self._deliver, core, report, install_id)
        if delivered or not report.query_count:
            write_json_atomic(self._state_path(), {"search_id": newest})
