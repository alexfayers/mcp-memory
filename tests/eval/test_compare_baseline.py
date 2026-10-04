"""Tests for the baseline compare that reports size moves and fails on size growth."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from tests.eval.compare_baseline import NO_CHANGES_TEXT, main

if TYPE_CHECKING:
    from pathlib import Path

_Capsys = pytest.CaptureFixture[str]
_RANKING = {"mrr": 0.5, "mean_recall_at_k": 0.8, "mean_success_at_k": 0.9}


def _write(path: Path, sizes: dict[str, int], ranking: dict[str, float] | None = None) -> Path:
    tools = {name: {"probes": 1, "total_bytes": total} for name, total in sizes.items()}
    payload = {
        "ranking": {"k": 10, "query_count": 96, "metrics": ranking or _RANKING},
        "size": {"entity_count": 130, "tools": tools},
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _compare(
    tmp_path: Path,
    base: dict[str, int],
    head: dict[str, int],
    *flags: str,
    head_ranking: dict[str, float] | None = None,
) -> int:
    return main([
        "--base",
        str(_write(tmp_path / "base.json", base)),
        "--head",
        str(_write(tmp_path / "head.json", head, head_ranking)),
        *flags,
    ])


class TestReport:
    def test_headline_states_only_total_bytes_and_percent_change_over_shared_probes(
        self, tmp_path: Path, capsys: _Capsys
    ) -> None:
        _compare(tmp_path, {"read_graph": 1000, "gone": 10}, {"read_graph": 800, "added": 5})
        assert "1000 -> 800 bytes (-20.0%)\n" in capsys.readouterr().out

    def test_moved_probe_row_has_status_emoji_delta_and_percent(self, tmp_path: Path, capsys: _Capsys) -> None:
        _compare(tmp_path, {"read_graph": 1000, "search_nodes": 1000}, {"read_graph": 800, "search_nodes": 1250})
        out = capsys.readouterr().out
        assert "| 🟢 | read_graph | 1000 | 800 | -200 | -20.0% |" in out
        assert "| 🔴 | search_nodes | 1000 | 1250 | +250 | +25.0% |" in out

    def test_new_and_removed_probes_are_labelled(self, tmp_path: Path, capsys: _Capsys) -> None:
        assert _compare(tmp_path, {"old_probe": 500}, {"new_probe": 700}) == 0
        out = capsys.readouterr().out
        assert "no tool call was measured both before and after" in out
        assert "| 🆕 | new_probe | - | 700 | | |" in out
        assert "| 🗑️ | old_probe | 500 | - | | |" in out

    def test_probe_table_is_inside_a_size_details_block_after_the_headline(
        self, tmp_path: Path, capsys: _Capsys
    ) -> None:
        _compare(tmp_path, {"read_graph": 1000}, {"read_graph": 800})
        _, _, body = capsys.readouterr().out.partition("\n\n")
        headline, _, block = body.partition("\n\n")
        assert headline.startswith("**Output size**")
        assert block.startswith("<details>\n<summary>Size details</summary>\n\n| | tool call | before | after |")
        assert block.strip().endswith("</details>")

    def test_unchanged_probes_are_left_out_entirely(self, tmp_path: Path, capsys: _Capsys) -> None:
        _compare(tmp_path, {"read_graph": 1000, "search_nodes": 10}, {"read_graph": 1000, "search_nodes": 20})
        out = capsys.readouterr().out
        assert "| search_nodes |" in out
        assert "read_graph" not in out

    def test_nothing_moved_shows_the_headline_and_the_no_change_sentence_without_details(
        self, tmp_path: Path, capsys: _Capsys
    ) -> None:
        _compare(tmp_path, {"read_graph": 1000}, {"read_graph": 1000})
        out = capsys.readouterr().out
        assert "<details>" not in out
        assert "1000 -> 1000 bytes (+0.0%)" in out
        assert out.splitlines()[-1] == NO_CHANGES_TEXT

    def test_a_size_move_omits_the_no_change_sentence(self, tmp_path: Path, capsys: _Capsys) -> None:
        _compare(tmp_path, {"read_graph": 1000}, {"read_graph": 800})
        assert NO_CHANGES_TEXT not in capsys.readouterr().out

    def test_a_gate_ranking_move_omits_the_no_change_sentence(self, tmp_path: Path, capsys: _Capsys) -> None:
        rise = _RANKING | {"mean_recall_at_k": 0.95}
        _compare(tmp_path, {"read_graph": 1}, {"read_graph": 1}, head_ranking=rise)
        assert NO_CHANGES_TEXT not in capsys.readouterr().out

    def test_a_non_gate_ranking_move_alone_keeps_the_no_change_sentence(self, tmp_path: Path, capsys: _Capsys) -> None:
        _compare(tmp_path, {"read_graph": 1}, {"read_graph": 1}, head_ranking=_RANKING | {"mrr": 0.6})
        assert NO_CHANGES_TEXT in capsys.readouterr().out

    def test_ranking_table_lists_only_the_moved_metrics(self, tmp_path: Path, capsys: _Capsys) -> None:
        base = _write(tmp_path / "base.json", {"read_graph": 1})
        head = _write(tmp_path / "head.json", {"read_graph": 1}, {"mrr": 0.6, "mean_recall_at_k": 0.8})
        assert main(["--base", str(base), "--head", str(head)]) == 0
        out = capsys.readouterr().out
        assert "<summary>Ranking details</summary>" in out
        assert "|  | mrr | 0.5000 | 0.6000 | +0.1000 |" in out.partition("<summary>Ranking details</summary>")[2]
        assert "mean_recall_at_k" not in out

    def test_ranking_rows_mark_gate_drops_red_and_gate_rises_green(self, tmp_path: Path, capsys: _Capsys) -> None:
        head = _RANKING | {"mean_recall_at_k": 0.7, "mean_success_at_k": 0.95}
        _compare(tmp_path, {"read_graph": 1}, {"read_graph": 1}, head_ranking=head)
        out = capsys.readouterr().out
        assert "| 🔴 | mean_recall_at_k | 0.8000 | 0.7000 | -0.1000 |" in out
        assert "| 🟢 | mean_success_at_k | 0.9000 | 0.9500 | +0.0500 |" in out

    def test_ranking_block_is_omitted_when_no_metric_moved(self, tmp_path: Path, capsys: _Capsys) -> None:
        _compare(tmp_path, {"read_graph": 1000}, {"read_graph": 800})
        assert "Ranking details" not in capsys.readouterr().out

    def test_no_approximation_marker_appears(self, tmp_path: Path, capsys: _Capsys) -> None:
        _compare(tmp_path, {"read_graph": 1000}, {"read_graph": 800})
        assert "~" not in capsys.readouterr().out


class TestBadges:
    def test_size_badge_is_green_with_the_escaped_percent_when_output_shrank(
        self, tmp_path: Path, capsys: _Capsys
    ) -> None:
        _compare(tmp_path, {"read_graph": 1000}, {"read_graph": 968})
        first_line = capsys.readouterr().out.splitlines()[0]
        assert "![output size](https://img.shields.io/badge/output_size---3.2%25-brightgreen)" in first_line

    def test_size_badge_is_red_with_an_encoded_plus_when_output_grew(self, tmp_path: Path, capsys: _Capsys) -> None:
        _compare(tmp_path, {"read_graph": 1000}, {"read_graph": 1100}, "--allow-growth")
        first_line = capsys.readouterr().out.splitlines()[0]
        assert "![output size](https://img.shields.io/badge/output_size-%2B10.0%25-red)" in first_line

    def test_size_badge_is_grey_when_output_is_unchanged(self, tmp_path: Path, capsys: _Capsys) -> None:
        _compare(tmp_path, {"read_graph": 1000}, {"read_graph": 1000})
        assert "/badge/output_size-0.0%25-lightgrey)" in capsys.readouterr().out.splitlines()[0]

    def test_ranking_badge_is_green_when_no_gate_metric_fell(self, tmp_path: Path, capsys: _Capsys) -> None:
        rise = _RANKING | {"mean_recall_at_k": 0.95, "mrr": 0.1}
        _compare(tmp_path, {"read_graph": 1}, {"read_graph": 1}, head_ranking=rise)
        assert "![ranking](https://img.shields.io/badge/ranking-no_regression-brightgreen)" in capsys.readouterr().out

    def test_ranking_badge_is_red_and_names_each_dropped_metric_with_its_delta(
        self, tmp_path: Path, capsys: _Capsys
    ) -> None:
        drop = _RANKING | {"mean_recall_at_k": 0.788, "mean_success_at_k": 0.89}
        _compare(tmp_path, {"read_graph": 1}, {"read_graph": 1}, head_ranking=drop)
        first_line = capsys.readouterr().out.splitlines()[0]
        assert "/badge/ranking-recall@10_--0.0120,_success@10_--0.0100-red)" in first_line

    def test_badges_come_before_the_headline(self, tmp_path: Path, capsys: _Capsys) -> None:
        _compare(tmp_path, {"read_graph": 1000}, {"read_graph": 800})
        lines = capsys.readouterr().out.splitlines()
        assert lines[0].startswith("![output size]")
        assert lines[1] == ""
        assert lines[2].startswith("**Output size**")


class TestExitCode:
    def test_growth_exits_one(self, tmp_path: Path) -> None:
        assert _compare(tmp_path, {"read_graph": 1000}, {"read_graph": 1001}) == 1

    def test_growth_with_allow_growth_exits_zero(self, tmp_path: Path) -> None:
        assert _compare(tmp_path, {"read_graph": 1000}, {"read_graph": 2000}, "--allow-growth") == 0

    def test_shrink_exits_zero(self, tmp_path: Path) -> None:
        assert _compare(tmp_path, {"read_graph": 1000}, {"read_graph": 10}) == 0

    def test_gate_ranking_drop_exits_one_and_names_the_metric(self, tmp_path: Path, capsys: _Capsys) -> None:
        drop = _RANKING | {"mean_recall_at_k": 0.79}
        assert _compare(tmp_path, {"read_graph": 1000}, {"read_graph": 1000}, head_ranking=drop) == 1
        assert "mean_recall_at_k" in capsys.readouterr().err

    def test_gate_ranking_drop_with_allow_ranking_drop_exits_zero(self, tmp_path: Path) -> None:
        drop = _RANKING | {"mean_success_at_k": 0.1}
        assert _compare(tmp_path, {"read_graph": 1}, {"read_graph": 1}, "--allow-ranking-drop", head_ranking=drop) == 0

    def test_allow_growth_does_not_excuse_a_ranking_drop(self, tmp_path: Path) -> None:
        drop = _RANKING | {"mean_success_at_k": 0.1}
        assert _compare(tmp_path, {"read_graph": 1}, {"read_graph": 2}, "--allow-growth", head_ranking=drop) == 1

    def test_allow_ranking_drop_does_not_excuse_growth(self, tmp_path: Path) -> None:
        drop = _RANKING | {"mean_success_at_k": 0.1}
        assert _compare(tmp_path, {"read_graph": 1}, {"read_graph": 2}, "--allow-ranking-drop", head_ranking=drop) == 1

    def test_non_gate_metric_drop_exits_zero(self, tmp_path: Path) -> None:
        drop = _RANKING | {"mrr": 0.1}
        assert _compare(tmp_path, {"read_graph": 1}, {"read_graph": 1}, head_ranking=drop) == 0

    def test_gate_ranking_rise_exits_zero(self, tmp_path: Path) -> None:
        rise = _RANKING | {"mean_recall_at_k": 0.95}
        assert _compare(tmp_path, {"read_graph": 1}, {"read_graph": 1}, head_ranking=rise) == 0

    def test_missing_base_file_exits_two_with_a_message(self, tmp_path: Path, capsys: _Capsys) -> None:
        head = _write(tmp_path / "head.json", {"read_graph": 1})
        assert main(["--base", str(tmp_path / "absent.json"), "--head", str(head)]) == 2
        assert "absent.json" in capsys.readouterr().err
