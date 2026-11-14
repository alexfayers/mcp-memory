"""Tests for the anonymous usage telemetry the server sends through llm-prompts."""

from __future__ import annotations

import asyncio
from collections import Counter
import functools
import json
import statistics
import time
from typing import TYPE_CHECKING, Any

from llm_prompts import telemetry_core as core
import pytest

from mcp_memory import activity, dream, nudge_count, server, telemetry
from mcp_memory.eval import clear_cache
from mcp_memory.models import Relation
from mcp_memory.nudge_count import NudgeKind

from . import SeedEntity, seed_store, soft_delete_store
from .eval.eval_fixture import mark_used
from .eval.eval_harness import _build_eval_fixture

if TYPE_CHECKING:
    from pathlib import Path

    from mcp_memory.storage import Storage


INSTALL_ID = "install-123"


@pytest.fixture
def sent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Opt in a known install under a temporary home and collect the events the core would send."""
    for name in core.OPT_OUT_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(telemetry, "_reported_failures", set())
    monkeypatch.setattr(telemetry, "_errors", Counter())
    monkeypatch.setattr(core, "resolved_versions", functools.cache(core.resolved_versions.__wrapped__))
    monkeypatch.setenv("HOME", str(tmp_path))
    config_dir = tmp_path / ".config" / "llm-prompts"
    config_dir.mkdir(parents=True)
    state = {"install_id": INSTALL_ID, "opted_out": False, "at": time.time()}
    (config_dir / "telemetry.json").write_text(json.dumps(state), encoding="utf-8")
    events: list[dict[str, Any]] = []
    monkeypatch.setattr(core, "send", lambda event, install_id, **_: events.append({**event, "id": install_id}))

    def post(body: str) -> bool:
        event = json.loads(body)
        events.append({**event, "id": event["distinct_id"]})
        return True

    monkeypatch.setattr(core, "post", post)
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


def test_usage_report_counts_searches_searches_used_and_writes(store: Storage, sent: list[dict[str, Any]]) -> None:
    store.telemetry.record_surfaced("search_nodes", "before start", "rid-old", [("proj", "task/old", 1)])
    mark_used(store, "rid-old", "task/old")
    usage = telemetry.UsageTelemetry(store)
    for tool in ("search_nodes", "search_nodes", "search_all_projects", "add_observations", "vote", "read_graph"):
        store.telemetry.record_tool_call(tool, 1, 1, {})
    store.telemetry.record_surfaced(
        "search_nodes", "private text", "rid-a", [("proj", "task/a", 1), ("proj", "task/b", 2)]
    )
    store.telemetry.record_surfaced("search_nodes", "private text", "rid-b", [("proj", "task/c", 1)])
    mark_used(store, "rid-a", "task/a")
    mark_used(store, "rid-a", "task/b")

    usage.report()
    store.telemetry.record_tool_call("search_nodes", 1, 1, {})
    usage.report()

    first, second = (event["properties"] for event in sent)
    names = ("searches", "searches_used", "empty_searches", "writes")
    assert {name: first[name] for name in names} == {
        "searches": 3,
        "searches_used": 1,
        "empty_searches": 1,
        "writes": 2,
    }
    assert {name: second[name] for name in names} == {
        "searches": 1,
        "searches_used": 0,
        "empty_searches": 1,
        "writes": 0,
    }
    assert "private text" not in json.dumps(sent)


def test_usage_report_counts_the_links_made_since_the_last_report_by_type(
    store: Storage, sent: list[dict[str, Any]]
) -> None:
    seed_store(store, "secret-project", [SeedEntity(name=name, observations=["o"]) for name in "abcd"])
    store.relations.create("secret-project", [Relation("a", "b", "implements")])
    usage = telemetry.UsageTelemetry(store)
    store.relations.create(
        "secret-project",
        [
            Relation("a", "c", "implements"),
            Relation("c", "d", "part-of"),
            Relation("b", "c", "blocks"),
        ],
    )
    with store.transaction():
        store.connection.write("INSERT INTO relation_types (name) VALUES ('secret-type')")
        store.connection.write(
            "INSERT INTO relations (source_id, target_id, relation_type_id) SELECT 1, 2, id FROM relation_types "
            "WHERE name = 'secret-type'"
        )
    store.telemetry.record_tool_call("create_relations", 1, 1, {})

    usage.report()
    store.telemetry.record_tool_call("search_nodes", 1, 1, {})
    usage.report()

    assert [event["properties"]["relations_created"] for event in sent] == [
        {"implements": 1, "part-of": 1, "blocks": 1, "other": 1},
        {},
    ]
    assert "secret" not in json.dumps(sent)


def test_searches_used_after_an_idle_gap_wait_for_the_next_report(store: Storage, sent: list[dict[str, Any]]) -> None:
    usage = telemetry.UsageTelemetry(store)
    store.telemetry.record_tool_call("search_nodes", 1, 1, {})
    store.telemetry.record_surfaced("search_nodes", "q", "rid-a", [("proj", "task/a", 1)])
    mark_used(store, "rid-a", "task/a")
    usage.note_call(telemetry.IDLE_SECONDS)
    store.telemetry.record_tool_call("search_nodes", 1, 1, {})
    store.telemetry.record_surfaced("search_nodes", "q", "rid-b", [("proj", "task/b", 1)])
    mark_used(store, "rid-b", "task/b")
    with store.transaction():
        store.connection.write(
            "UPDATE surfaced_entities SET surfaced_at = '2099-01-01 00:00:00' WHERE retrieval_id = 'rid-b'"
        )

    usage.report_when_due(0)
    usage.report()

    assert [event["properties"]["searches_used"] for event in sent] == [1, 1]


def test_usage_report_counts_the_nudges_shown_and_keeps_them_when_the_send_fails(
    store: Storage, sent: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    usage = telemetry.UsageTelemetry(store)
    store.telemetry.record_tool_call("search_nodes", 1, 1, {})
    nudge_count.record(NudgeKind.REMINDER)
    nudge_count.record(NudgeKind.COMPACT)
    delivered = core.post
    monkeypatch.setattr(core, "post", lambda _: False)
    usage.report()
    monkeypatch.setattr(core, "post", delivered)
    nudge_count.record(NudgeKind.REMINDER)
    usage.report()
    store.telemetry.record_tool_call("search_nodes", 1, 1, {})
    usage.report()

    assert [(event["properties"]["nudges"], event["properties"]["nudges_by_kind"]) for event in sent] == [
        (3, {"reminder": 2, "compact": 1}),
        (0, {}),
    ]


def test_a_failed_send_is_retried_with_the_same_calls_and_counted_once(
    store: Storage, sent: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    usage = telemetry.UsageTelemetry(store)
    store.telemetry.record_tool_call("search_nodes", 10, 100, {})
    telemetry.note_result("search_nodes", {"error": "x"})
    delivered = core.post
    monkeypatch.setattr(core, "post", lambda _: False)

    usage.report()

    monkeypatch.setattr(core, "post", delivered)
    store.telemetry.record_tool_call("search_nodes", 30, 300, {})
    usage.report()
    usage.report()

    assert len(sent) == 1
    assert sent[0]["properties"]["tools"]["search_nodes"] == {
        "ok": 2,
        "errors": 1,
        "median_input_bytes": 20,
        "median_output_bytes": 200,
    }


def test_debug_prints_the_usage_payload_and_whether_it_arrived(
    store: Storage, sent: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("LLM_PROMPTS_TELEMETRY_DEBUG", "1")
    usage = telemetry.UsageTelemetry(store)
    store.telemetry.record_tool_call("search_nodes", 10, 100, {})

    usage.report()

    err = capsys.readouterr().err
    assert err.startswith("telemetry payload: {")
    assert '"event": "memory_usage"' in err
    assert len(sent) == 1
    assert err.endswith("telemetry sent: True\n")


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
    store.relations.create("alpha", [Relation("e0", "e1", "relates-to"), Relation("e0", "e2", "relates-to")])
    store.relations.create("beta", [Relation("kept", "gone", "relates-to")])
    soft_delete_store(store, "beta", "gone")
    usage = telemetry.UsageTelemetry(store)
    store.telemetry.record_tool_call("search_nodes", 10, 100, {})

    usage.report()

    properties = sent[0]["properties"]
    assert properties["entities"] == "10-99"
    assert (properties["relations"], properties["linked_entities"]) == ("1-9", "1-9")
    assert properties["projects"] == "1-9"
    assert properties["db_mb"] == "0"
    assert "alpha" not in json.dumps(sent[0])


TOOLS = ("llm-prompts", "cline-hooks", "mcp-memory")
ENTRY_KEYS = {"commit", "base", "local", "dirty", "ahead"}


def _assert_versions(properties: dict[str, Any], tmp_path: Path) -> None:
    assert set(properties["versions"]) == set(TOOLS)
    assert all(set(entry) == ENTRY_KEYS for entry in properties["versions"].values())
    assert properties["overlays"] == {}
    assert "llm_prompts_commit" not in properties
    assert "mcp_memory_commit" not in properties
    assert str(tmp_path) not in json.dumps(properties)


def test_report_names_the_installed_commit_of_a_remote_install(
    store: Storage, sent: list[dict[str, Any]], tmp_path: Path
) -> None:
    dist_info = tmp_path / ".local/share/uv/tools/mcp-memory/lib/site-packages/mcp_memory-1.0.dist-info"
    dist_info.mkdir(parents=True)
    (dist_info / "direct_url.json").write_text(json.dumps({"vcs_info": {"commit_id": "abc123"}}), encoding="utf-8")
    usage = telemetry.UsageTelemetry(store)
    store.telemetry.record_tool_call("search_nodes", 10, 100, {})

    usage.report()

    entry = sent[0]["properties"]["versions"]["mcp-memory"]
    assert entry == {"commit": "abc123", "base": "abc123", "local": False, "dirty": False, "ahead": None}


def test_report_names_a_local_checkout_without_its_path(
    store: Storage, sent: list[dict[str, Any]], tmp_path: Path
) -> None:
    checkout = tmp_path / "checkouts" / "mcp-memory"
    checkout.mkdir(parents=True)
    config = tmp_path / ".config" / "llm-prompts" / "config.toml"
    config.write_text(f'[[tools]]\nname = "mcp-memory"\nsource = "{checkout}"\n', encoding="utf-8")
    usage = telemetry.UsageTelemetry(store)
    store.telemetry.record_tool_call("search_nodes", 10, 100, {})

    usage.report()

    properties = sent[0]["properties"]
    assert properties["versions"]["mcp-memory"]["local"] is True
    assert properties["versions"]["mcp-memory"]["commit"] is None
    assert str(tmp_path) not in json.dumps(properties)


def _raise(message: str) -> None:
    raise RuntimeError(message)


def test_report_exception_sends_the_type_and_package_frames_with_the_message_withheld(
    sent: list[dict[str, Any]], tmp_path: Path
) -> None:
    with pytest.raises(RuntimeError) as caught:
        _raise(f"cannot open {tmp_path}/notes.txt for jane@example.com")

    telemetry.report_exception(caught.value, "sweep")

    assert len(sent) == 1
    event = sent[0]
    assert event["event"] == "$exception"
    assert event["properties"]["where"] == "sweep"
    item = event["properties"]["$exception_list"][0]
    assert item["type"] == "RuntimeError"
    assert item["value"] == "<message withheld>"
    assert "frames" in item["stacktrace"]


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


def test_report_when_due_sends_once_per_idle_stretch(store: Storage, sent: list[dict[str, Any]]) -> None:
    usage = telemetry.UsageTelemetry(store)
    store.telemetry.record_tool_call("search_nodes", 10, 100, {})

    usage.report_when_due(telemetry.IDLE_SECONDS - 1)
    assert sent == []

    usage.report_when_due(telemetry.IDLE_SECONDS)
    store.telemetry.record_tool_call("search_nodes", 10, 100, {})
    usage.report_when_due(telemetry.IDLE_SECONDS * 2)
    assert len(sent) == 1

    usage.report_when_due(0)
    usage.report_when_due(telemetry.IDLE_SECONDS)
    assert len(sent) == 2


def test_a_send_that_failed_when_idle_is_retried_on_the_next_idle_stretch(
    store: Storage, sent: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    usage = telemetry.UsageTelemetry(store)
    store.telemetry.record_tool_call("search_nodes", 10, 100, {})
    delivered = core.post
    monkeypatch.setattr(core, "post", lambda _: False)

    usage.report_when_due(telemetry.IDLE_SECONDS)

    monkeypatch.setattr(core, "post", delivered)
    usage.report_when_due(telemetry.IDLE_SECONDS * 2)
    assert sent == []

    usage.report_when_due(0)
    usage.report_when_due(telemetry.IDLE_SECONDS)
    assert [event["properties"]["tools"]["search_nodes"]["ok"] for event in sent] == [1]


def test_dream_pass_reports_counts_without_text(sent: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch) -> None:
    async def failing_pass() -> str:
        raise ValueError("secret-project text")

    for run_pass in (_audit_pass, failing_pass):
        spec = dream.TierSpec("heavy", lambda: True, lambda: 0.0, lambda: 1.0, run_pass)
        asyncio.run(dream._run_claimed(spec))

    ok, failed = (event["properties"] for event in sent)
    assert ok["tier"] == "heavy"
    assert ok["ok"] is True
    assert ok["operations"] == {"demote": 1}
    assert failed["ok"] is False
    assert failed["error_type"] == "ValueError"
    assert "secret-project" not in json.dumps(sent)


async def _audit_pass() -> str:
    return "[secret-project/task/old] - demoted: stale"


def test_usage_and_dream_events_carry_the_version_tags(
    store: Storage, sent: list[dict[str, Any]], tmp_path: Path
) -> None:
    usage = telemetry.UsageTelemetry(store)
    store.telemetry.record_tool_call("search_nodes", 10, 100, {})
    usage.report()
    asyncio.run(dream._run_claimed(dream.TierSpec("heavy", lambda: True, lambda: 0.0, lambda: 1.0, _audit_pass)))

    assert [event["event"] for event in sent] == ["memory_usage", "memory_dream"]
    for event in sent:
        properties = event["properties"]
        _assert_versions(properties, tmp_path)


def test_the_suite_is_opted_out_of_telemetry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".config" / "llm-prompts").mkdir(parents=True)
    _write_state(tmp_path)
    spawned: list[object] = []
    monkeypatch.setattr(core, "Popen", lambda *args, **_: spawned.append(args))
    spec = dream.TierSpec("light", lambda: True, lambda: 0.0, lambda: 1.0, _audit_pass)
    asyncio.run(dream._run_claimed(spec))
    assert spawned == []


def test_calls_before_an_idle_gap_are_sent_on_the_first_call_after_it(
    store: Storage, sent: list[dict[str, Any]]
) -> None:
    usage = telemetry.UsageTelemetry(store)
    store.telemetry.record_tool_call("search_nodes", 10, 100, {})
    store.telemetry.record_tool_call("read_graph", 10, 100, {})

    usage.note_call(telemetry.IDLE_SECONDS)
    store.telemetry.record_tool_call("create_entities", 10, 100, {})
    usage.report_when_due(0)

    assert set(sent[0]["properties"]["tools"]) == {"search_nodes", "read_graph"}
    usage.report()
    assert set(sent[1]["properties"]["tools"]) == {"create_entities"}


def test_the_first_call_after_idle_sends_nothing_when_the_idle_send_worked(
    store: Storage, sent: list[dict[str, Any]]
) -> None:
    usage = telemetry.UsageTelemetry(store)
    store.telemetry.record_tool_call("search_nodes", 10, 100, {})
    usage.report_when_due(telemetry.IDLE_SECONDS)
    sent.clear()

    usage.note_call(telemetry.IDLE_SECONDS * 2)
    store.telemetry.record_tool_call("search_nodes", 10, 100, {})
    usage.report_when_due(0)

    assert sent == []


def test_a_tool_call_after_idle_marks_the_end_of_the_previous_batch(
    store: Storage, sent: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(server, "_db", store)
    monkeypatch.setattr(server, "_usage", None)
    server.search_nodes("proj", "x")
    monkeypatch.setattr(activity, "_last_activity", time.time() - telemetry.IDLE_SECONDS - 1)

    server.search_nodes("proj", "x")
    server.search_nodes("proj", "x")
    server._get_usage().report_when_due(0)

    assert sent[0]["properties"]["tools"]["search_nodes"]["ok"] == 1


@pytest.fixture
def labelled(store: Storage) -> Storage:
    """Give the store one labelled query, so an eval has something to score."""
    clear_cache()
    _build_eval_fixture(store)
    mark_used(store, "rid-2", "task/unrelated")
    return store


def _report_eval(store: Storage, idle_seconds: float = telemetry.IDLE_SECONDS) -> None:
    asyncio.run(telemetry.EvalTelemetry(store).report_when_due(idle_seconds))


def _search_and_use(store: Storage, retrieval_id: str, query: str = "retry backoff") -> None:
    """Record one more labelled search, so the store has new searches since the last eval."""
    store.telemetry.record_surfaced("search_nodes", query, retrieval_id, [("proj", "task/new", 1)])
    mark_used(store, retrieval_id, "task/new")
    clear_cache()


def test_eval_report_sends_only_score_means_and_a_bucketed_query_count(
    labelled: Storage, sent: list[dict[str, Any]]
) -> None:
    _report_eval(labelled)

    assert len(sent) == 1
    assert sent[0]["id"] == INSTALL_ID
    assert sent[0]["event"] == "memory_eval"
    properties = sent[0]["properties"]
    assert {
        name: properties[name]
        for name in (
            "k",
            "queries",
            "mrr",
            "mean_recall_at_k",
            "mean_ndcg_at_k",
            "mean_success_at_k",
        )
    } == {
        "k": 10,
        "queries": "1-9",
        "mrr": 1.0,
        "mean_recall_at_k": 1.0,
        "mean_ndcg_at_k": 1.0,
        "mean_success_at_k": 1.0,
    }
    assert 0 < properties["mean_precision_at_k"] <= 1
    assert "schema" in properties
    payload = json.dumps(sent)
    for private in ("backoff", "cache eviction", "task/unrelated", '"proj"'):
        assert private not in payload


def test_eval_report_tags_the_prompt_identity_and_store_size(
    labelled: Storage, sent: list[dict[str, Any]], tmp_path: Path
) -> None:
    _report_eval(labelled)

    properties = sent[0]["properties"]
    _assert_versions(properties, tmp_path)
    assert properties["entities"] == "1-9"
    assert properties["projects"] == "1-9"
    assert properties["db_mb"] == "0"


def test_eval_report_leaves_out_single_word_queries_and_says_so(labelled: Storage, sent: list[dict[str, Any]]) -> None:
    labelled.telemetry.record_surfaced("search_nodes", "backoff", "rid-3", [("proj", "task/fresh", 1)])
    mark_used(labelled, "rid-3", "task/fresh")

    _report_eval(labelled)

    assert sent[0]["properties"]["queries"] == "1-9"
    assert sent[0]["properties"]["min_content_tokens"] == telemetry.EVAL_MIN_CONTENT_TOKENS
    clear_cache()
    assert asyncio.run(telemetry.evaluate_cached_async(labelled, min_content_tokens=0)).query_count == 3


def test_eval_is_sent_on_each_idle_stretch_that_follows_new_searches(
    labelled: Storage, sent: list[dict[str, Any]]
) -> None:
    evaluation = telemetry.EvalTelemetry(labelled)

    asyncio.run(evaluation.report_when_due(telemetry.IDLE_SECONDS))
    asyncio.run(evaluation.report_when_due(telemetry.IDLE_SECONDS * 2))
    assert len(sent) == 1

    _search_and_use(labelled, "rid-new")
    asyncio.run(evaluation.report_when_due(0))
    asyncio.run(evaluation.report_when_due(telemetry.IDLE_SECONDS))
    assert len(sent) == 2


def test_eval_is_not_sent_again_after_a_restart_without_new_searches(
    labelled: Storage, sent: list[dict[str, Any]]
) -> None:
    _report_eval(labelled)
    clear_cache()
    _report_eval(labelled)
    assert len(sent) == 1

    _search_and_use(labelled, "rid-new")
    _report_eval(labelled)
    assert len(sent) == 2


def test_a_failed_eval_send_is_retried_on_the_next_idle_stretch(
    labelled: Storage, sent: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    delivered = core.post
    monkeypatch.setattr(core, "post", lambda _: False)
    evaluation = telemetry.EvalTelemetry(labelled)

    asyncio.run(evaluation.report_when_due(telemetry.IDLE_SECONDS))

    monkeypatch.setattr(core, "post", delivered)
    asyncio.run(evaluation.report_when_due(telemetry.IDLE_SECONDS * 2))
    assert sent == []

    asyncio.run(evaluation.report_when_due(0))
    asyncio.run(evaluation.report_when_due(telemetry.IDLE_SECONDS))
    assert [event["event"] for event in sent] == ["memory_eval"]


@pytest.mark.parametrize("kind", ["env-opt-out", "saved-opt-out", "stale-record", "no-install-id", "core-missing"])
def test_nothing_is_scored_or_sent_when_telemetry_is_off(
    labelled: Storage, sent: list[dict[str, Any]], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    def fail(*_: object, **__: object) -> None:
        raise AssertionError("scored while telemetry is off")

    monkeypatch.setattr(telemetry, "evaluate_cached_async", fail)
    _turn_off(kind, tmp_path, monkeypatch)

    _report_eval(labelled)
    assert sent == []


def test_nothing_is_sent_before_the_server_is_idle_or_without_labelled_queries(
    labelled: Storage, sent: list[dict[str, Any]]
) -> None:
    _report_eval(labelled, telemetry.IDLE_SECONDS - 1)
    assert sent == []

    clear_cache()
    with labelled.transaction():
        labelled.connection.write("UPDATE surfaced_entities SET used_at = NULL")
    _report_eval(labelled)
    assert sent == []
