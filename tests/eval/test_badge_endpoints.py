"""Tests for the shields.io endpoint files built from a baseline artefact."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from tests.eval.badge_endpoints import main

if TYPE_CHECKING:
    from pathlib import Path


def _artefact(path: Path, sizes: dict[str, int], recall: float, k: int = 10) -> Path:
    tools = {name: {"probes": 1, "total_bytes": size} for name, size in sizes.items()}
    payload = {
        "ranking": {"k": k, "query_count": 96, "metrics": {"mean_recall_at_k": recall, "mrr": 0.5}},
        "size": {"entity_count": 130, "tools": tools},
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _run(tmp_path: Path, sizes: dict[str, int], recall: float, k: int = 10) -> Path:
    out = tmp_path / "out"
    assert main([str(_artefact(tmp_path / "baseline.json", sizes, recall, k)), str(out)]) == 0
    return out


class TestEndpoints:
    def test_size_endpoint_reports_total_kilobytes_over_every_probe(self, tmp_path: Path) -> None:
        out = _run(tmp_path, {"read_graph": 1500, "search_nodes": 2300}, 0.8)
        assert json.loads((out / "size.json").read_text()) == {
            "schemaVersion": 1,
            "label": "tool output",
            "message": "3.8 kB",
            "color": "blue",
        }

    def test_ranking_endpoint_reports_recall_at_k_to_four_places(self, tmp_path: Path) -> None:
        out = _run(tmp_path, {"read_graph": 1}, 0.83501, k=5)
        assert json.loads((out / "ranking.json").read_text()) == {
            "schemaVersion": 1,
            "label": "recall@5",
            "message": "0.8350",
            "color": "blue",
        }

    def test_creates_the_output_directory(self, tmp_path: Path) -> None:
        assert not (tmp_path / "out").exists()
        assert (_run(tmp_path, {"read_graph": 1}, 0.5) / "size.json").is_file()
