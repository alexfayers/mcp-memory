"""Compare two baseline artefacts, print the size and ranking moves, and fail on regressions.

Reads each artefact's raw sections, so a base artefact that predates a probe the head adds
still compares. A probe present in both artefacts whose bytes grew fails the comparison unless
`--allow-growth` is passed; a gate ranking metric (`_GATE_METRICS`) whose value fell fails it
unless `--allow-ranking-drop` is passed. New probes, removed probes and other ranking moves never do.
The metrics of the queries labelled unreachable are listed but never gate, and a base artefact
without them still compares.

When the benchmark changed, `--head-on-base-data` gives the artefact of the head code on the base's
benchmark. The report then says how the result size moved between the benchmarks under the head code,
compares the base code with the head code on the old benchmark, and compares the old benchmark with the new
one under the head code. Where the file is missing ("not measured") the second comparison is base against
head. The old-benchmark comparison gates as above, or the new-benchmark one does where it was not measured.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import TYPE_CHECKING, NamedTuple

from tests.eval.eval_harness import _GATE_METRICS
from tests.eval.size_baseline import move_row

if TYPE_CHECKING:
    from collections.abc import Sequence

NO_CHANGES_TEXT = "No benchmark changes."
NOT_MEASURED_TEXT = "Not measured."
_CODE_SECTION = "Old benchmark"
_DATA_SECTION = "New benchmark"
_RANKING_DECIMALS = 4
_EXIT_REGRESSION = 1
_EXIT_UNREADABLE = 2
_ABSENT = "-"
_SMALLER, _LARGER, _NEW, _REMOVED, _UNCHANGED = "🟢", "🔴", "🆕", "🗑️", "⚪"
_SHIELDS_URL = "https://img.shields.io/badge"
_SHIELDS_ESCAPES = str.maketrans({"-": "--", "_": "__", " ": "_", "%": "%25", "+": "%2B"})
_BADGE_COLOURS = {_SMALLER: "brightgreen", _LARGER: "red", _UNCHANGED: "lightgrey"}
_TABLE_HEADER_LINES = 2


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the compare command."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, required=True, help="baseline artefact to compare against")
    parser.add_argument("--head", type=Path, required=True, help="baseline artefact under review")
    parser.add_argument("--head-on-base-data", type=Path, help="artefact of the head code run on the base's benchmark")
    parser.add_argument("--allow-growth", action="store_true", help="exit 0 even when a probe's size grew")
    parser.add_argument("--allow-ranking-drop", action="store_true", help="exit 0 even when a gate metric fell")
    return parser


class Artefact(NamedTuple):
    """The sections of a baseline artefact that the comparison reads."""

    sizes: dict[str, int]
    ranking: dict[str, float]
    k: int
    unreachable: dict[str, float]


class Comparison(NamedTuple):
    """The report of one base -> head comparison and the regressions that gate it."""

    report: list[str]
    grown: list[str]
    dropped: dict[str, float]


def load_artefact(path: Path) -> Artefact:
    """Return the sizes, ranking metrics, k and unreachable-query metrics of the artefact at `path`."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    sizes = {name: probe["total_bytes"] for name, probe in payload["size"]["tools"].items()}
    ranking = payload["ranking"]
    unreachable = ranking.get("unreachable", {"metrics": {}})["metrics"]
    return Artefact(sizes, ranking["metrics"], ranking["k"], unreachable)


def _names(base: dict[str, object], head: dict[str, object]) -> list[str]:
    """Return every name in either mapping, base order first."""
    return [*base, *(name for name in head if name not in base)]


def _shared_totals(base: dict[str, int], head: dict[str, int]) -> tuple[int, int] | None:
    """Return (base bytes, head bytes) over the probes both artefacts share, or None."""
    shared = base.keys() & head.keys()
    if not shared:
        return None
    return sum(base[name] for name in shared), sum(head[name] for name in shared)


def _headline(base: dict[str, int], head: dict[str, int]) -> str:
    """Summarise the total bytes of the probes both artefacts share."""
    totals = _shared_totals(base, head)
    if totals is None:
        return "**Output size** no tool call was measured both before and after this PR"
    old, new = totals
    _, percent = move_row(old, new)
    return f"**Output size** {_status(old, new)} {old} -> {new} bytes ({percent:+.1f}%)"


def _short_name(metric: str, k: int) -> str:
    """Shorten a ranking metric name for display, e.g. `mean_recall_at_k` to `recall@10`."""
    return metric.removeprefix("mean_").replace("_at_k", f"@{k}")


def _ranking_headline(base: dict[str, float], head: dict[str, float], dropped: dict[str, float], k: int) -> str | None:
    """Summarise the mean of the gate metrics both artefacts share, red and naming each one that fell."""
    shared = [name for name in _GATE_METRICS if name in base and name in head]
    if not shared:
        return None
    old = sum(base[name] for name in shared) / len(shared)
    new = sum(head[name] for name in shared) / len(shared)
    status = _LARGER if dropped else _ranking_status(old, new)
    percent = (new - old) / old * 100 if old else 0.0
    falls = "".join(f", {_short_name(name, k)} fell {_metric(base[name])} -> {_metric(head[name])}" for name in dropped)
    return f"**Ranking** {status} {_metric(old)} -> {_metric(new)} ({percent:+.1f}%){falls}"


def _badge(label: str, message: str, colour: str) -> str:
    """Render a static shields.io badge, escaping the label and message for its path syntax."""
    return (
        f"![{label}]({_SHIELDS_URL}/{label.translate(_SHIELDS_ESCAPES)}-{message.translate(_SHIELDS_ESCAPES)}-{colour})"
    )


def _size_badge(base: dict[str, int], head: dict[str, int]) -> str:
    """Badge the total size change over the probes both artefacts share."""
    totals = _shared_totals(base, head)
    if totals is None:
        return _badge("output size", "n/a", "lightgrey")
    old, new = totals
    _, percent = move_row(old, new)
    return _badge("output size", f"{percent:+.1f}%" if new != old else "0.0%", _BADGE_COLOURS[_status(old, new)])


def _ranking_badge(dropped: dict[str, float], k: int) -> str:
    """Badge the gate ranking metrics: green when none fell, else red naming each drop."""
    if not dropped:
        return _badge("ranking", "no regression", "brightgreen")
    drops = ", ".join(f"{_short_name(name, k)} {delta:+.{_RANKING_DECIMALS}f}" for name, delta in dropped.items())
    return _badge("ranking", drops, "red")


def _status(old: float, new: float) -> str:
    """Return the emoji for a value that went from `old` to `new`."""
    if new == old:
        return _UNCHANGED
    return _LARGER if new > old else _SMALLER


def _ranking_status(old: float, new: float) -> str:
    """Return the emoji for a ranking metric that went from `old` to `new`, where higher is better."""
    return _status(new, old)


def _moved_table(base: dict[str, int], head: dict[str, int]) -> list[str]:
    """Render one markdown row per probe that moved, appeared or disappeared."""
    lines = ["| | tool call | before | after | change | % |", "| :-: | --- | ---: | ---: | ---: | ---: |"]
    for name in _names(base, head):
        if name not in head:
            lines.append(f"| {_REMOVED} | {name} | {base[name]} | {_ABSENT} | | |")
        elif name not in base:
            lines.append(f"| {_NEW} | {name} | {_ABSENT} | {head[name]} | | |")
        elif base[name] != head[name]:
            delta, percent = move_row(base[name], head[name])
            status = _status(base[name], head[name])
            lines.append(f"| {status} | {name} | {base[name]} | {head[name]} | {delta:+} | {percent:+.1f}% |")
    return lines


def _details(summary: str, table: list[str]) -> list[str]:
    """Wrap a markdown table in a collapsed block."""
    return ["", "<details>", f"<summary>{summary}</summary>", "", *table, "", "</details>"]


def _metric(value: float | None) -> str:
    """Render a ranking metric value, or a dash where the artefact lacks it."""
    return _ABSENT if value is None else f"{value:.{_RANKING_DECIMALS}f}"


def _ranking_table(base: dict[str, float], head: dict[str, float], *, gated: bool = True) -> list[str]:
    """Render one markdown row per ranking metric that moved, appeared or disappeared, marking gate moves if `gated`."""
    lines = ["| | metric | before | after | change |", "| :-: | --- | ---: | ---: | ---: |"]
    for name in _names(base, head):
        old, new = base.get(name), head.get(name)
        if old == new:
            continue
        delta = "" if old is None or new is None else f"{new - old:+.{_RANKING_DECIMALS}f}"
        is_gate = gated and name in _GATE_METRICS
        status = _ranking_status(old, new) if is_gate and old is not None and new is not None else ""
        lines.append(f"| {status} | {name} | {_metric(old)} | {_metric(new)} | {delta} |")
    return lines


def _dropped_gate_metrics(base: dict[str, float], head: dict[str, float]) -> dict[str, float]:
    """Return {metric: head - base} for every gate metric present in both artefacts whose value fell."""
    return {
        name: head[name] - base[name]
        for name in _GATE_METRICS
        if name in base and name in head and head[name] < base[name]
    }


def _load_cross_artefact(path: Path | None) -> Artefact | None:
    """Return the artefact at `path`, or None where no path was given or the measurement left no file."""
    return load_artefact(path) if path is not None and path.exists() else None


def _compare_artefacts(base: Artefact, head: Artefact) -> Comparison:
    """Compare `head` against `base`: the report lines, the probes whose size grew and the gate metrics that fell."""
    grown = sorted(name for name in base.sizes.keys() & head.sizes.keys() if head.sizes[name] > base.sizes[name])
    dropped = _dropped_gate_metrics(base.ranking, head.ranking)
    report = [
        f"{_size_badge(base.sizes, head.sizes)} {_ranking_badge(dropped, head.k)}",
        "",
        _headline(base.sizes, head.sizes),
    ]
    ranking_headline = _ranking_headline(base.ranking, head.ranking, dropped, head.k)
    if ranking_headline:
        report += ["", ranking_headline]
    size_table = _moved_table(base.sizes, head.sizes)
    if len(size_table) == _TABLE_HEADER_LINES and all(
        base.ranking.get(m) == head.ranking.get(m) for m in _GATE_METRICS
    ):
        report += ["", NO_CHANGES_TEXT]
    blocks = (
        ("Size details", size_table),
        ("Ranking details", _ranking_table(base.ranking, head.ranking)),
        ("Unreachable queries", _ranking_table(base.unreachable, head.unreachable, gated=False)),
    )
    for summary, table in blocks:
        if len(table) > _TABLE_HEADER_LINES:
            report += _details(summary, table)
    return Comparison(report, grown, dropped)


def _benchmark_changed_text(head_on_base: Artefact | None, head: Artefact) -> str:
    """State that the benchmark changed and how its result size moved under the head code."""
    totals = _shared_totals(head_on_base.sizes, head.sizes) if head_on_base else None
    if totals is None:
        return "**Benchmark changed**"
    old, new = totals
    if new == old:
        return "**Benchmark changed:** results are the same size on the new benchmark"
    _, percent = move_row(old, new)
    direction = "bigger" if new > old else "smaller"
    return f"**Benchmark changed:** results are {abs(percent):.1f}% {direction} on the new benchmark"


def _changed_benchmark_report(
    base: Artefact, head: Artefact, head_on_base: Artefact | None
) -> tuple[list[str], dict[str, Comparison]]:
    """Render the benchmark change and both benchmark comparisons; return them with the gating one, by name."""
    code = _compare_artefacts(base, head_on_base) if head_on_base else None
    data = _compare_artefacts(head_on_base or base, head)
    report = [
        _benchmark_changed_text(head_on_base, head),
        "",
        f"### {_CODE_SECTION}",
        "",
        *(code.report if code else [NOT_MEASURED_TEXT]),
        "",
        f"### {_DATA_SECTION}",
        "",
        *data.report,
    ]
    return report, {_CODE_SECTION: code} if code else {_DATA_SECTION: data}


def main(argv: Sequence[str] | None = None) -> int:
    """Print the comparison of `--head` against `--base`; exit 1 on a regression, 2 on an unreadable file."""
    args = build_parser().parse_args(argv)
    try:
        base, head = load_artefact(args.base), load_artefact(args.head)
        head_on_base = _load_cross_artefact(args.head_on_base_data)
    except (OSError, ValueError, KeyError) as exc:
        print(f"cannot read baseline: {exc}", file=sys.stderr)
        return _EXIT_UNREADABLE
    if args.head_on_base_data is None:
        comparison = _compare_artefacts(base, head)
        report, gating = comparison.report, {"": comparison}
    else:
        report, gating = _changed_benchmark_report(base, head, head_on_base)
    print("\n".join(report))
    regressions = []
    for name, (_, grown, dropped) in gating.items():
        prefix = f"{name}: " if name else ""
        if grown and not args.allow_growth:
            print(f"{prefix}output size grew for {grown}", file=sys.stderr)
            regressions.append(grown)
        if dropped and not args.allow_ranking_drop:
            print(f"{prefix}ranking dropped for {sorted(dropped)}", file=sys.stderr)
            regressions.append(dropped)
    return _EXIT_REGRESSION if regressions else 0


if __name__ == "__main__":
    raise SystemExit(main())
