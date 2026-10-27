"""Tests for the anonymous usage telemetry the server sends through llm-prompts."""

from __future__ import annotations

from collections import Counter
import json
import statistics
import time
from typing import TYPE_CHECKING, Any

import pytest

from mcp_memory import server, telemetry

from . import SeedEntity, seed_store, soft_delete_store

if TYPE_CHECKING:
    from pathlib import Path

    from mcp_memory.storage import Storage

core = pytest.importorskip("llm_prompts.telemetry_core")

INSTALL_ID = "install-123"


@pytest.fixture
def sent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Opt in a known install under a temporary home and collect the events the core would send."""
    for name in core.OPT_OUT_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(telemetry, "_reported_failures", set())
    monkeypatch.setattr(telemetry, "_errors", Counter())
    monkeypatch.setenv("HOME", str(tmp_path))
    config_dir = tmp_path / ".config" / "llm-prompts"
    config_dir.mkdir(parents=True)
    state = {"install_id": INSTALL_ID, "opted_out": False, "at": time.time()}
    (config_dir / "telemetry.json").write_text(json.dumps(state), encoding="utf-8")
    events: list[dict[str, Any]] = []
    monkeypatch.setattr(core, "send", lambda event, install_id, **_: events.append({**event, "id": install_id}))
    return events


def test_usage_report_rolls_up_calls_since_start(store: Storage, sent: list[dict[str, Any]]) -> None:
    store.telemetry.record_tool_call("search_nodes", 1, 1, {"project": "secret-project"})
    usage = telemetry.UsageTelemetry(store)
    store.telemetry.record_tool_call("search_nodes", 10, 100, {"project": "secret-project"})
    store.telemetry.record_tool_call("search_nodes", 30, 300, {"project": "secret-project"})
    store.telemetry.record_tool_call("read_graph", 5, 7, {})
    telemetry.note_result("search_nodes", {"error": "x"})

    usage.report()

    assert len(sent) == 1
    assert sent[0]["id"] == INSTALL_ID
    assert sent[0]["event"] == "memory_usage"
    tools = sent[0]["properties"]["tools"]
    assert tools["search_nodes"] == {
        "ok": 2,
        "errors": 1,
        "median_input_bytes": statistics.median([10, 30]),
        "median_output_bytes": statistics.median([100, 300]),
    }
    assert tools["read_graph"]["ok"] == 1
    assert "secret-project" not in json.dumps(sent[0])


def _write_state(tmp_path: Path, **fields: Any) -> None:
    state_file = tmp_path / ".config" / "llm-prompts" / "telemetry.json"
    state_file.write_text(json.dumps({"install_id": INSTALL_ID, "opted_out": False, "at": time.time(), **fields}))


def _turn_off(kind: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    if kind == "env-opt-out":
        monkeypatch.setenv("DO_NOT_TRACK", "1")
    elif kind == "saved-opt-out":
        _write_state(tmp_path, opted_out=True)
    elif kind == "stale-record":
        _write_state(tmp_path, at=time.time() - 2 * core.OPT_STATE_MAX_AGE)
    elif kind == "no-install-id":
        _write_state(tmp_path, install_id=None)
    else:
        monkeypatch.setattr(telemetry, "_core", None)


@pytest.mark.parametrize("kind", ["env-opt-out", "saved-opt-out", "stale-record", "no-install-id", "core-missing"])
def test_nothing_is_sent_when_telemetry_is_off(
    store: Storage, sent: list[dict[str, Any]], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    usage = telemetry.UsageTelemetry(store)
    store.telemetry.record_tool_call("search_nodes", 10, 100, {})
    _turn_off(kind, tmp_path, monkeypatch)

    usage.report()

    assert sent == []


def test_calls_made_while_off_are_not_sent_once_telemetry_is_on(
    store: Storage, sent: list[dict[str, Any]], tmp_path: Path
) -> None:
    usage = telemetry.UsageTelemetry(store)
    _write_state(tmp_path, opted_out=True)
    store.telemetry.record_tool_call("search_nodes", 10, 100, {})
    usage.report()
    _write_state(tmp_path)

    usage.report()

    assert sent == []


def test_a_report_without_new_calls_sends_nothing(store: Storage, sent: list[dict[str, Any]]) -> None:
    usage = telemetry.UsageTelemetry(store)
    store.telemetry.record_tool_call("search_nodes", 10, 100, {})
    usage.report()
    sent.clear()

    usage.report()

    assert sent == []


def test_report_sizes_the_store_in_buckets(store: Storage, sent: list[dict[str, Any]]) -> None:
    seed_store(store, "alpha", [SeedEntity(name=f"e{i}", observations=["o"]) for i in range(12)])
    seed_store(
        store, "beta", [SeedEntity(name="gone", observations=["o"]), SeedEntity(name="kept", observations=["o"])]
    )
    soft_delete_store(store, "beta", "gone")
    usage = telemetry.UsageTelemetry(store)
    store.telemetry.record_tool_call("search_nodes", 10, 100, {})

    usage.report()

    properties = sent[0]["properties"]
    assert properties["entities"] == "10-99"
    assert properties["projects"] == "1-9"
    assert properties["db_mb"] == "0"
    assert "alpha" not in json.dumps(sent[0])


class _Distribution:
    def __init__(self, direct_url: dict[str, Any] | None) -> None:
        self._text = json.dumps(direct_url) if direct_url is not None else None

    def read_text(self, filename: str) -> str | None:
        return self._text


@pytest.mark.parametrize(
    ("direct_url", "expected"),
    [
        ({"vcs_info": {"commit_id": "abc123"}}, {"commit": "abc123", "local": False}),
        ({"dir_info": {"editable": True}}, {"commit": None, "local": True}),
        (None, {"commit": None, "local": False}),
    ],
)
def test_report_names_the_installed_commit_of_a_remote_install_only(
    store: Storage,
    sent: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
    direct_url: dict[str, Any] | None,
    expected: dict[str, Any],
) -> None:
    monkeypatch.setattr(telemetry, "distribution", lambda _: _Distribution(direct_url))
    usage = telemetry.UsageTelemetry(store)
    store.telemetry.record_tool_call("search_nodes", 10, 100, {})

    usage.report()

    assert sent[0]["properties"]["mcp_memory_commit"] == expected


def _raise(message: str) -> None:
    raise RuntimeError(message)


def test_report_exception_sends_the_type_and_package_frames_without_the_message(
    sent: list[dict[str, Any]],
) -> None:
    with pytest.raises(RuntimeError) as caught:
        _raise("private memory text")

    telemetry.report_exception(caught.value, "sweep")

    assert len(sent) == 1
    event = sent[0]
    assert event["event"] == "$exception"
    assert event["properties"]["where"] == "sweep"
    assert event["properties"]["$exception_list"][0]["type"] == "RuntimeError"
    assert "private memory text" not in json.dumps(event)


def test_report_exception_sends_each_failure_once(sent: list[dict[str, Any]]) -> None:
    for _ in range(3):
        with pytest.raises(RuntimeError) as caught:
            _raise("again")
        telemetry.report_exception(caught.value, "sweep")

    assert len(sent) == 1


def test_report_exception_ignores_exits(sent: list[dict[str, Any]]) -> None:
    telemetry.report_exception(KeyboardInterrupt(), "serve")

    assert sent == []


def test_failed_tool_calls_are_counted_by_tool(
    store: Storage, sent: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(server, "_db", store)
    usage = telemetry.UsageTelemetry(store)

    server.set_entity_status("proj", "nope", "resolved")
    usage.report()

    assert sent[0]["properties"]["tools"]["set_entity_status"]["errors"] == 1
