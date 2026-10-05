"""Tests for the baseline compare that reports size moves and fails on size growth."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from tests.eval.compare_baseline import NO_CHANGES_TEXT, main

if TYPE_CHECKING:
    from pathlib import Path

_Capsys = pytest.CaptureFixture[str]
_RANKING = {"mrr": 0.5, "mean_recall_at_k": 0.8, "mean_success_at_k": 0.9, "mean_ndcg_at_k": 0.5}


def _write(
    path: Path,
    sizes: dict[str, int],
    ranking: dict[str, float] | None = None,
    unreachable: dict[str, float] | None = None,
) -> Path:
    tools = {name: {"probes": 1, "total_bytes": total} for name, total in sizes.items()}
    payload = {
        "ranking": {"k": 10, "query_count": 96, "metrics": ranking or _RANKING},
        "size": {"entity_count": 130, "tools": tools},
    }
    if unreachable is not None:
        payload["ranking"]["unreachable"] = {"query_count": 15, "metrics": unreachable}
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _compare(
    tmp_path: Path,
    base: dict[str, int],
    head: dict[str, int],
    *flags: str,
    head_ranking: dict[str, float] | None = None,
    base_unreachable: dict[str, float] | None = None,
    head_unreachable: dict[str, float] | None = None,
    head_on_base: dict[str, int] | None = None,
    benchmark_changed: bool = False,
) -> int:
    cross_flags: list[str] = []
    if benchmark_changed or head_on_base is not None:
        path = tmp_path / "head-on-base.json"
        cross_flags = ["--head-on-base-data", str(path if head_on_base is None else _write(path, head_on_base))]
    return main([
        "--base",
        str(_write(tmp_path / "base.json", base, unreachable=base_unreachable)),
        "--head",
        str(_write(tmp_path / "head.json", head, head_ranking, head_unreachable)),
        *cross_flags,
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
        headline, ranking, block = body.split("\n\n", 2)
        assert headline.startswith("**Output size**")
        assert ranking.startswith("**Ranking**")
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
        _compare(tmp_path, {"read_graph": 1}, {"read_graph": 1}, head_ranking=_RANKING | {"mean_ndcg_at_k": 0.6})
        assert NO_CHANGES_TEXT in capsys.readouterr().out

    def test_ranking_table_lists_only_the_moved_metrics(self, tmp_path: Path, capsys: _Capsys) -> None:
        base = _write(tmp_path / "base.json", {"read_graph": 1})
        head = _write(tmp_path / "head.json", {"read_graph": 1}, {"mean_ndcg_at_k": 0.6, "mean_recall_at_k": 0.8})
        assert main(["--base", str(base), "--head", str(head)]) == 0
        out = capsys.readouterr().out
        assert "<summary>Ranking details</summary>" in out
        details = out.partition("<summary>Ranking details</summary>")[2]
        assert "|  | mean_ndcg_at_k | 0.5000 | 0.6000 | +0.1000 |" in details
        assert "mean_recall_at_k" not in out

    def test_ranking_headline_shows_the_mean_gate_score_under_the_size_headline(
        self, tmp_path: Path, capsys: _Capsys
    ) -> None:
        _compare(tmp_path, {"read_graph": 1}, {"read_graph": 1}, head_ranking=_RANKING | {"mean_recall_at_k": 0.9})
        assert "bytes (+0.0%)\n\n**Ranking** 🟢 0.7333 -> 0.7667 (+4.5%)\n" in capsys.readouterr().out

    def test_ranking_headline_is_red_when_a_gate_metric_dropped_even_if_the_mean_held(
        self, tmp_path: Path, capsys: _Capsys
    ) -> None:
        head = _RANKING | {"mean_recall_at_k": 0.7, "mean_success_at_k": 1.0}
        _compare(tmp_path, {"read_graph": 1}, {"read_graph": 1}, head_ranking=head)
        assert "**Ranking** 🔴 0.7333 -> 0.7333 (+0.0%), recall@10 fell 0.8000 -> 0.7000\n" in capsys.readouterr().out

    def test_ranking_headline_is_white_when_no_gate_metric_moved(self, tmp_path: Path, capsys: _Capsys) -> None:
        _compare(tmp_path, {"read_graph": 1}, {"read_graph": 1}, head_ranking=_RANKING | {"mean_ndcg_at_k": 0.6})
        assert "**Ranking** ⚪ 0.7333 -> 0.7333 (+0.0%)" in capsys.readouterr().out

    def test_ranking_details_stay_collapsed_after_a_gate_drop(self, tmp_path: Path, capsys: _Capsys) -> None:
        _compare(tmp_path, {"read_graph": 1}, {"read_graph": 1}, head_ranking=_RANKING | {"mean_recall_at_k": 0.7})
        assert "<details>\n<summary>Ranking details</summary>" in capsys.readouterr().out

    def test_ranking_rows_mark_gate_drops_red_and_gate_rises_green(self, tmp_path: Path, capsys: _Capsys) -> None:
        head = _RANKING | {"mean_recall_at_k": 0.7, "mean_success_at_k": 0.95}
        _compare(tmp_path, {"read_graph": 1}, {"read_graph": 1}, head_ranking=head)
        out = capsys.readouterr().out
        assert "| 🔴 | mean_recall_at_k | 0.8000 | 0.7000 | -0.1000 |" in out
        assert "| 🟢 | mean_success_at_k | 0.9000 | 0.9500 | +0.0500 |" in out

    def test_unreachable_metrics_get_their_own_collapsed_table(self, tmp_path: Path, capsys: _Capsys) -> None:
        unreachable = {"mean_recall_at_k": 0.0}
        assert _compare(tmp_path, {"read_graph": 1}, {"read_graph": 1}, head_unreachable=unreachable) == 0
        out = capsys.readouterr().out
        assert "<summary>Ranking details</summary>" not in out
        table = out.partition("<details>\n<summary>Unreachable queries</summary>")[2]
        assert "|  | mean_recall_at_k | - | 0.0000 |  |" in table

    def test_an_unreachable_drop_neither_fails_nor_reddens_the_ranking_badge(
        self, tmp_path: Path, capsys: _Capsys
    ) -> None:
        exit_code = _compare(
            tmp_path,
            {"read_graph": 1},
            {"read_graph": 1},
            base_unreachable={"mean_recall_at_k": 0.5},
            head_unreachable={"mean_recall_at_k": 0.1},
        )
        out = capsys.readouterr().out
        assert exit_code == 0
        assert "ranking-no_regression-brightgreen" in out
        assert "|  | mean_recall_at_k | 0.5000 | 0.1000 | -0.4000 |" in out.partition("Unreachable queries")[2]

    def test_an_unreachable_move_alone_keeps_the_no_change_sentence(self, tmp_path: Path, capsys: _Capsys) -> None:
        _compare(
            tmp_path,
            {"read_graph": 1},
            {"read_graph": 1},
            base_unreachable={"mean_recall_at_k": 0.0},
            head_unreachable={"mean_recall_at_k": 0.5},
        )
        assert NO_CHANGES_TEXT in capsys.readouterr().out

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
        rise = _RANKING | {"mean_recall_at_k": 0.95, "mean_ndcg_at_k": 0.1}
        _compare(tmp_path, {"read_graph": 1}, {"read_graph": 1}, head_ranking=rise)
        assert "![ranking](https://img.shields.io/badge/ranking-no_regression-brightgreen)" in capsys.readouterr().out

    def test_ranking_badge_is_red_and_names_each_dropped_metric_with_its_delta(
        self, tmp_path: Path, capsys: _Capsys
    ) -> None:
        drop = _RANKING | {"mean_recall_at_k": 0.788, "mean_success_at_k": 0.89}
        _compare(tmp_path, {"read_graph": 1}, {"read_graph": 1}, head_ranking=drop)
        first_line = capsys.readouterr().out.splitlines()[0]
        assert "/badge/ranking-recall@10_--0.0120,_success@10_--0.0100-red)" in first_line

    def test_ranking_badge_names_an_mrr_drop(self, tmp_path: Path, capsys: _Capsys) -> None:
        drop = _RANKING | {"mrr": 0.45}
        _compare(tmp_path, {"read_graph": 1}, {"read_graph": 1}, head_ranking=drop)
        assert "/badge/ranking-mrr_--0.0500-red)" in capsys.readouterr().out.splitlines()[0]

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

    def test_mrr_drop_exits_one_and_names_the_metric(self, tmp_path: Path, capsys: _Capsys) -> None:
        drop = _RANKING | {"mrr": 0.4}
        assert _compare(tmp_path, {"read_graph": 1}, {"read_graph": 1}, head_ranking=drop) == 1
        assert "mrr" in capsys.readouterr().err

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
        drop = _RANKING | {"mean_ndcg_at_k": 0.1}
        assert _compare(tmp_path, {"read_graph": 1}, {"read_graph": 1}, head_ranking=drop) == 0

    def test_gate_ranking_rise_exits_zero(self, tmp_path: Path) -> None:
        rise = _RANKING | {"mean_recall_at_k": 0.95}
        assert _compare(tmp_path, {"read_graph": 1}, {"read_graph": 1}, head_ranking=rise) == 0

    def test_missing_base_file_exits_two_with_a_message(self, tmp_path: Path, capsys: _Capsys) -> None:
        head = _write(tmp_path / "head.json", {"read_graph": 1})
        assert main(["--base", str(tmp_path / "absent.json"), "--head", str(head)]) == 2
        assert "absent.json" in capsys.readouterr().err


class TestChangedBenchmark:
    def test_opens_with_how_much_bigger_results_are_with_the_same_code(self, tmp_path: Path, capsys: _Capsys) -> None:
        _compare(tmp_path, {"read_graph": 1000}, {"read_graph": 1500}, head_on_base={"read_graph": 1000})
        assert capsys.readouterr().out.startswith(
            "**Benchmark changed:** results are 50.0% bigger on the new benchmark\n\n### Old benchmark\n\n"
        )

    def test_says_how_much_smaller_results_are_with_the_same_code(self, tmp_path: Path, capsys: _Capsys) -> None:
        _compare(tmp_path, {"read_graph": 1000}, {"read_graph": 800}, head_on_base={"read_graph": 1000})
        first_paragraph = capsys.readouterr().out.partition("\n\n")[0]
        assert first_paragraph == "**Benchmark changed:** results are 20.0% smaller on the new benchmark"

    def test_says_results_are_the_same_size_when_the_same_code_measures_no_change(
        self, tmp_path: Path, capsys: _Capsys
    ) -> None:
        sizes = {"read_graph": 1000}
        _compare(tmp_path, sizes, sizes, head_on_base=sizes)
        first_paragraph = capsys.readouterr().out.partition("\n\n")[0]
        assert first_paragraph == "**Benchmark changed:** results are the same size on the new benchmark"

    def test_only_says_the_test_data_changed_when_the_old_data_was_not_measured(
        self, tmp_path: Path, capsys: _Capsys
    ) -> None:
        _compare(tmp_path, {"read_graph": 1000}, {"read_graph": 1500}, benchmark_changed=True)
        assert capsys.readouterr().out.partition("\n\n")[0] == "**Benchmark changed**"

    def test_code_section_compares_base_with_the_head_code_on_the_old_data(
        self, tmp_path: Path, capsys: _Capsys
    ) -> None:
        _compare(tmp_path, {"read_graph": 1000}, {"read_graph": 1500}, head_on_base={"read_graph": 900})
        code = capsys.readouterr().out.partition("### Old benchmark\n\n")[2]
        assert "1000 -> 900 bytes (-10.0%)" in code.partition("### New benchmark")[0]

    def test_data_section_compares_the_head_code_on_the_old_data_with_the_head(
        self, tmp_path: Path, capsys: _Capsys
    ) -> None:
        _compare(tmp_path, {"read_graph": 1000}, {"read_graph": 1500}, head_on_base={"read_graph": 900})
        data = capsys.readouterr().out.partition("### New benchmark\n\n")[2]
        assert data.startswith("![output size]")
        assert "900 -> 1500 bytes (+66.7%)" in data

    def test_missing_old_data_artefact_is_not_measured_and_the_data_section_compares_base_with_head(
        self, tmp_path: Path, capsys: _Capsys
    ) -> None:
        _compare(tmp_path, {"read_graph": 1000}, {"read_graph": 1500}, benchmark_changed=True)
        code, _, data = capsys.readouterr().out.partition("### New benchmark\n\n")
        assert code.endswith("### Old benchmark\n\nNot measured.\n\n")
        assert "1000 -> 1500 bytes (+50.0%)" in data

    def test_growth_from_the_code_change_on_the_old_data_exits_one_unless_allowed(self, tmp_path: Path) -> None:
        sizes, grown = {"read_graph": 1000}, {"read_graph": 1500}
        assert _compare(tmp_path, sizes, sizes, head_on_base=grown) == 1
        assert _compare(tmp_path, sizes, sizes, "--allow-growth", head_on_base=grown) == 0

    def test_growth_from_the_data_change_alone_does_not_gate_when_the_old_data_was_measured(
        self, tmp_path: Path
    ) -> None:
        sizes = {"read_graph": 1000}
        assert _compare(tmp_path, sizes, {"read_graph": 1500}, head_on_base=sizes) == 0

    def test_code_regression_is_prefixed_with_the_code_section_heading(self, tmp_path: Path, capsys: _Capsys) -> None:
        sizes = {"read_graph": 1000}
        _compare(tmp_path, sizes, sizes, head_on_base={"read_graph": 1500})
        assert "Old benchmark: output size grew" in capsys.readouterr().err

    def test_ranking_drop_from_the_code_change_on_the_old_data_gates(self, tmp_path: Path, capsys: _Capsys) -> None:
        sizes = {"read_graph": 1}
        base = _write(tmp_path / "base.json", sizes)
        head = _write(tmp_path / "head.json", sizes)
        head_on_base = _write(tmp_path / "head-on-base.json", sizes, _RANKING | {"mrr": 0.4})
        args = ["--base", str(base), "--head", str(head), "--head-on-base-data", str(head_on_base)]
        assert main(args) == 1
        assert "Old benchmark: ranking dropped for ['mrr']" in capsys.readouterr().err
        assert main([*args, "--allow-ranking-drop"]) == 0

    def test_data_regression_gates_when_the_old_data_was_not_measured(self, tmp_path: Path, capsys: _Capsys) -> None:
        assert _compare(tmp_path, {"read_graph": 1000}, {"read_graph": 1500}, benchmark_changed=True) == 1
        assert "New benchmark: output size grew" in capsys.readouterr().err

    def test_unreadable_cross_artefact_exits_two(self, tmp_path: Path, capsys: _Capsys) -> None:
        corrupt = tmp_path / "corrupt.json"
        corrupt.write_text("not json", encoding="utf-8")
        base = _write(tmp_path / "base.json", {"read_graph": 1})
        assert main(["--base", str(base), "--head", str(base), "--head-on-base-data", str(corrupt)]) == 2
        assert "cannot read baseline" in capsys.readouterr().err
