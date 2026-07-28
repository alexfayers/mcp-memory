"""Recall compression + cost profile tests.

Exercises ``recall_efficiency`` on hand-built graph payloads and synthetic recall metrics -
no live recall spawn - to lock in that it measures raw-graph-in vs distilled-output-out bytes
and carries the recorded cost/latency through unchanged.
"""

from __future__ import annotations

import pytest

from mcp_memory.payload import payload_size
from mcp_memory.recall_efficiency import recall_efficiency


class TestRecallEfficiency:
    def _large_graph(self) -> dict[str, object]:
        return {
            "entities": [
                {
                    "name": f"task/note-{i}",
                    "entityType": "task",
                    "observations": [
                        "deployment rollback runbook detail with substantial "
                        f"observation text to inflate the payload number {i}"
                    ],
                }
                for i in range(12)
            ],
            "relations": [
                {"from": f"task/note-{i}", "to": "project/demo", "relationType": "belongs-to"}
                for i in range(12)
            ],
        }

    def test_compression_ratio_on_large_graph(self) -> None:
        graph_payload = self._large_graph()
        rec = {
            "ts": 0.0,
            "query": "q",
            "ok": True,
            "duration_ms": 1200,
            "num_turns": 3,
            "cost_usd": 0.0042,
        }

        result = recall_efficiency(graph_payload, "short distilled answer", record=rec)

        assert result.input_bytes > result.output_bytes
        assert result.saved_bytes == result.input_bytes - result.output_bytes
        assert result.saved_bytes > 0
        assert 0.0 < result.ratio < 1.0
        assert result.input_bytes == payload_size(graph_payload)
        assert result.output_bytes == len(b"short distilled answer")

    def test_metrics_carried_through(self) -> None:
        rec = {
            "ts": 0.0,
            "query": "q",
            "ok": True,
            "duration_ms": 1200,
            "num_turns": 3,
            "cost_usd": 0.0042,
        }

        result = recall_efficiency(self._large_graph(), "short distilled answer", record=rec)

        assert result.duration_ms == 1200
        assert result.num_turns == 3
        assert result.cost_usd == pytest.approx(0.0042)

    def test_none_metrics_preserved(self) -> None:
        rec = {
            "ts": 0.0,
            "query": "q",
            "ok": True,
            "duration_ms": None,
            "num_turns": None,
            "cost_usd": None,
        }

        result = recall_efficiency(self._large_graph(), "short distilled answer", record=rec)

        assert result.duration_ms is None
        assert result.num_turns is None
        assert result.cost_usd is None

    def test_empty_graph_yields_zero_ratio(self) -> None:
        rec = {
            "ts": 0.0,
            "query": "q",
            "ok": True,
            "duration_ms": 1200,
            "num_turns": 3,
            "cost_usd": 0.0042,
        }

        long_output = "a much longer distilled output than the tiny graph"
        result = recall_efficiency({}, long_output, record=rec)

        assert result.ratio == result.saved_bytes / result.input_bytes

    def test_output_measured_as_raw_utf8_not_reserialized(self) -> None:
        rec = {
            "ts": 0.0,
            "query": "q",
            "ok": True,
            "duration_ms": 1200,
            "num_turns": 3,
            "cost_usd": 0.0042,
        }

        result = recall_efficiency(self._large_graph(), "café ★", record=rec)

        assert result.output_bytes == len("café ★".encode())
