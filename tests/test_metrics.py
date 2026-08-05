"""Tests for the call-metrics storage layer and metrics module."""

from __future__ import annotations

import json
import statistics
from typing import TYPE_CHECKING

import pytest

from mcp_memory import cli, metrics, server
from mcp_memory.database import DatabaseManager
from mcp_memory.models import Relation
from mcp_memory.payload import payload_size

from . import SeedEntity, seed

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def db(tmp_path: Path) -> DatabaseManager:
    """Create a fresh database for each test."""
    return DatabaseManager(tmp_path / "test.db")


class TestStorage:
    def test_record_tool_call_inserts_row(self, db: DatabaseManager) -> None:
        db.record_tool_call("search_nodes", 120, 3400, {"limit": 5, "compact": True})

        row = db._db.execute(
            "SELECT tool, input_bytes, output_bytes, options FROM tool_calls"
        ).fetchone()
        assert row["tool"] == "search_nodes"
        assert row["input_bytes"] == 120
        assert row["output_bytes"] == 3400
        assert json.loads(row["options"]) == {"limit": 5, "compact": True}

    def test_migration_created_tool_calls_table(self, db: DatabaseManager) -> None:
        row = db._db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'tool_calls'"
        ).fetchone()
        assert row is not None

    def test_prune_tool_calls_removes_only_old_rows(self, db: DatabaseManager) -> None:
        db.record_tool_call("search_nodes", 10, 20, {})
        db.record_tool_call("read_graph", 30, 40, {})
        db._db.execute(
            "UPDATE tool_calls SET called_at = datetime('now', '-100 days') "
            "WHERE tool = 'read_graph'"
        )
        db._db.commit()

        assert db.prune_tool_calls(90) == 1
        remaining = db._db.execute("SELECT tool FROM tool_calls").fetchall()
        assert [r["tool"] for r in remaining] == ["search_nodes"]


class TestRecord:
    def test_records_payload_sizes(self, db: DatabaseManager) -> None:
        kwargs = {"query": "hello world", "limit": 5}
        result = {"entities": [{"name": "a"}, {"name": "b"}]}
        metrics.record(db, "search_nodes", kwargs, result)

        row = db._db.execute("SELECT input_bytes, output_bytes FROM tool_calls").fetchone()
        assert row["input_bytes"] == payload_size(kwargs)
        assert row["output_bytes"] == payload_size(result)

    def test_skips_error_result(self, db: DatabaseManager) -> None:
        metrics.record(db, "search_nodes", {"query": "x"}, {"error": "boom"})
        assert db._db.execute("SELECT COUNT(*) AS n FROM tool_calls").fetchone()["n"] == 0

    def test_stores_only_allowlisted_scalar_options(self, db: DatabaseManager) -> None:
        kwargs = {
            "query": "some long text that must never be stored",
            "compact": True,
            "match_all": False,
            "project": "p",
        }
        metrics.record(db, "search_nodes", kwargs, {"entities": []})

        options = json.loads(db._db.execute("SELECT options FROM tool_calls").fetchone()["options"])
        assert options == {"compact": True, "match_all": False, "project": "p"}
        assert "query" not in options

    def test_honours_disabled_flag(
        self, db: DatabaseManager, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MCP_MEMORY_CALL_METRICS_ENABLED", "false")
        metrics.record(db, "search_nodes", {"query": "x"}, {"entities": []})
        assert db._db.execute("SELECT COUNT(*) AS n FROM tool_calls").fetchone()["n"] == 0

    def test_drops_long_allowlisted_string_option(self, db: DatabaseManager) -> None:
        metrics.record(db, "search_nodes", {"since": "y" * 65}, {"entities": []})
        options = json.loads(db._db.execute("SELECT options FROM tool_calls").fetchone()["options"])
        assert "since" not in options


class TestUsageReport:
    def test_aggregates_per_tool_stats(self, db: DatabaseManager) -> None:
        db.record_tool_call("search_nodes", 100, 1000, {"compact": True})
        db.record_tool_call("search_nodes", 200, 3000, {"compact": True})
        db.record_tool_call("search_nodes", 300, 2000, {"compact": False})
        db.record_tool_call("read_graph", 50, 500, {})

        report = metrics.usage_report(db)
        assert report.total_calls == 4
        assert report.since is None
        assert [tool.tool for tool in report.tools] == ["read_graph", "search_nodes"]

        search = next(tool for tool in report.tools if tool.tool == "search_nodes")
        assert search.call_count == 3
        assert search.mean_input_bytes == statistics.mean([100, 200, 300])
        assert search.median_input_bytes == statistics.median([100, 200, 300])
        assert search.mean_output_bytes == statistics.mean([1000, 3000, 2000])
        assert search.median_output_bytes == statistics.median([1000, 3000, 2000])
        assert search.option_frequencies == {"compact": {"True": 2, "False": 1}}

    def test_totals_and_ratio_sum_across_all_tools(self, db: DatabaseManager) -> None:
        db.record_tool_call("search_nodes", 100, 1000, {})
        db.record_tool_call("search_nodes", 200, 3000, {})
        db.record_tool_call("read_graph", 50, 500, {})

        report = metrics.usage_report(db)
        assert report.total_input_bytes == 350
        assert report.total_output_bytes == 4500
        assert report.input_output_ratio == pytest.approx(350 / 4500)

    def test_ratio_is_none_when_no_calls_recorded(self, db: DatabaseManager) -> None:
        report = metrics.usage_report(db)
        assert report.total_input_bytes == 0
        assert report.total_output_bytes == 0
        assert report.input_output_ratio is None

    def test_since_filter_excludes_old_calls(self, db: DatabaseManager) -> None:
        db.record_tool_call("search_nodes", 10, 20, {})
        db.record_tool_call("read_graph", 30, 40, {})
        db._db.execute(
            "UPDATE tool_calls SET called_at = datetime('now', '-100 days') "
            "WHERE tool = 'read_graph'"
        )
        db._db.commit()

        report = metrics.usage_report(db, since="30d")
        assert report.total_calls == 1
        assert [tool.tool for tool in report.tools] == ["search_nodes"]


class TestUsageOverTime:
    def test_day_bucketing_splits_across_dates(self, db: DatabaseManager) -> None:
        db.record_tool_call("search_nodes", 100, 1000, {})
        db.record_tool_call("search_nodes", 200, 2000, {})
        db._db.execute(
            "UPDATE tool_calls SET called_at = datetime('now', '-2 days') WHERE input_bytes = 200"
        )
        db._db.commit()

        buckets = metrics.usage_over_time(db, bucket="day")
        assert len(buckets) == 2
        assert len({b.bucket for b in buckets}) == 2
        assert all(b.tool == "search_nodes" for b in buckets)

    def test_per_tool_breakdown_same_day(self, db: DatabaseManager) -> None:
        db.record_tool_call("search_nodes", 100, 1000, {})
        db.record_tool_call("search_nodes", 200, 3000, {})
        db.record_tool_call("read_graph", 50, 500, {})

        buckets = metrics.usage_over_time(db, bucket="day")
        assert len(buckets) == 2
        by_tool = {b.tool: b for b in buckets}
        assert by_tool["search_nodes"].call_count == 2
        assert by_tool["search_nodes"].total_input_bytes == 300
        assert by_tool["search_nodes"].total_output_bytes == 4000
        assert by_tool["read_graph"].call_count == 1
        assert by_tool["read_graph"].total_input_bytes == 50
        assert by_tool["read_graph"].total_output_bytes == 500

    def test_since_filter_excludes_old_calls(self, db: DatabaseManager) -> None:
        db.record_tool_call("search_nodes", 10, 20, {})
        db.record_tool_call("read_graph", 30, 40, {})
        db._db.execute(
            "UPDATE tool_calls SET called_at = datetime('now', '-100 days') "
            "WHERE tool = 'read_graph'"
        )
        db._db.commit()

        buckets = metrics.usage_over_time(db, bucket="day", since="30d")
        assert [b.tool for b in buckets] == ["search_nodes"]

    def test_hour_grouping(self, db: DatabaseManager) -> None:
        db.record_tool_call("search_nodes", 10, 20, {})
        buckets = metrics.usage_over_time(db, bucket="hour")
        assert len(buckets) == 1
        assert "T" in buckets[0].bucket
        assert buckets[0].bucket.endswith(":00")

    def test_week_grouping(self, db: DatabaseManager) -> None:
        db.record_tool_call("search_nodes", 10, 20, {})
        buckets = metrics.usage_over_time(db, bucket="week")
        assert len(buckets) == 1
        assert "-W" in buckets[0].bucket

    def test_invalid_bucket_raises(self, db: DatabaseManager) -> None:
        with pytest.raises(ValueError, match="bucket"):
            metrics.usage_over_time(db, bucket="year")

    def test_empty_database_returns_empty(self, db: DatabaseManager) -> None:
        assert metrics.usage_over_time(db, bucket="day") == []


class TestMetricsCommand:
    def test_metrics_command_reports_usage(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        db_path = tmp_path / "cli-metrics.db"
        seed = DatabaseManager(db_path)
        seed.record_tool_call("search_nodes", 100, 1000, {"compact": True})
        seed.record_tool_call("search_nodes", 200, 3000, {"compact": True})
        seed.record_tool_call("read_graph", 50, 500, {})
        seed.close()

        monkeypatch.setenv("MCP_MEMORY_DB_PATH", str(db_path))
        monkeypatch.setattr("sys.argv", ["mcp-memory", "metrics"])
        cli.main()

        report = json.loads(capsys.readouterr().out)
        assert report["total_calls"] == 3
        assert report["since"] is None
        tools = {tool["tool"]: tool for tool in report["tools"]}
        assert set(tools) == {"read_graph", "search_nodes"}
        assert tools["search_nodes"]["call_count"] == 2
        assert tools["read_graph"]["call_count"] == 1


_BENCH_OBSERVATIONS = [
    "process_ticket requires an explicit relationship_manager argument on every call path",
    "The idempotency key is derived from the ticket id combined with the manager alias",
    "Retries reuse the recorded key so duplicate submissions collapse into one row",
    "Payload budgeting trims observation text before serialization to bound output bytes",
    "The ranking gate keeps budgeting orthogonal to recency-weighted BM25 ordering",
]


class TestCostRegression:
    """Permanent gate catching response-payload bloat via real @_track telemetry.

    Drives fixed real calls through the server tool layer over a deterministic
    synthetic seed, then asserts each tool's mean output bytes stays under a
    ceiling. A deliberate response-shape change must bump the ceiling in the
    same reviewed diff.
    """

    @pytest.fixture
    def seeded_db(self, tmp_path: Path) -> DatabaseManager:
        """Seed five multi-observation entities with relations for stable payload sizes."""
        db = DatabaseManager(tmp_path / "cost.db")
        seed(
            db,
            "bench",
            [
                SeedEntity(
                    name=f"task/bench-{index}",
                    observations=_BENCH_OBSERVATIONS,
                    entity_type="task",
                )
                for index in range(5)
            ],
        )
        db.create_relations(
            "bench",
            [
                Relation("task/bench-0", "task/bench-1", "relates-to"),
                Relation("task/bench-0", "task/bench-2", "depends-on"),
            ],
        )
        return db

    def test_output_bytes_stay_under_ceiling(
        self, seeded_db: DatabaseManager, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Each tool's real recorded mean output bytes must stay below its ceiling."""
        monkeypatch.setattr(server, "_db", seeded_db)

        server.search_nodes("bench", "ticket")
        server.read_graph("bench")
        server.get_entity_with_relations("bench", "task/bench-0")

        assert server._db is not None
        report = metrics.usage_report(server._db)
        assert report.total_calls == 3

        # Ceilings = measured baseline + ~30% headroom (rounded). Headroom absorbs
        # incidental serialization variance while still tripping on a meaningful
        # payload bloat (an added response field or doubled observations).
        ceilings = {
            "search_nodes": 6200,  # measured baseline 4754
            "read_graph": 6200,  # measured baseline 4739
            "get_entity_with_relations": 3800,  # measured baseline 2940
        }
        for tool in report.tools:
            assert tool.mean_output_bytes < ceilings[tool.tool], (
                f"{tool.tool} output bytes {tool.mean_output_bytes} exceeded "
                f"ceiling {ceilings[tool.tool]}"
            )


class TestServerWiring:
    def test_track_records_tool_call(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        manager = DatabaseManager(tmp_path / "server.db")
        monkeypatch.setattr(server, "_db", manager)

        @server._track
        def search_nodes(query: str, limit: int = 5) -> dict[str, list[str]]:
            return {"entities": []}

        search_nodes("needle", limit=3)

        row = manager._db.execute("SELECT tool, options FROM tool_calls").fetchone()
        assert row["tool"] == "search_nodes"
        assert json.loads(row["options"]) == {"limit": 3}
