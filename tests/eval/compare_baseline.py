"""Compare two baseline artefacts, print the size and ranking moves, and fail on regressions.

Reads each artefact's raw sections, so a base artefact that predates a probe the head adds
still compares. A probe present in both artefacts whose bytes grew fails the comparison unless
`--allow-growth` is passed; a gate ranking metric (`_GATE_METRICS`) whose value fell fails it
unless `--allow-ranking-drop` is passed. New probes, removed probes and other ranking moves never do.
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
    parser.add_argument("--allow-growth", action="store_true", help="exit 0 even when a probe's size grew")
    parser.add_argument("--allow-ranking-drop", action="store_true", help="exit 0 even when a gate metric fell")
    return parser


class Artefact(NamedTuple):
    """The sections of a baseline artefact that the comparison reads."""

    sizes: dict[str, int]
    ranking: dict[str, float]
    k: int


def load_artefact(path: Path) -> Artefact:
    """Return the (probe -> total bytes, metric -> value, k) sections of the artefact at `path`."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    sizes = {name: probe["total_bytes"] for name, probe in payload["size"]["tools"].items()}
    return Artefact(sizes, payload["ranking"]["metrics"], payload["ranking"]["k"])


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
    drops = ", ".join(
        f"{name.removeprefix('mean_').replace('_at_k', f'@{k}')} {delta:+.{_RANKING_DECIMALS}f}"
        for name, delta in dropped.items()
    )
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


def _ranking_table(base: dict[str, float], head: dict[str, float]) -> list[str]:
    """Render one markdown row per ranking metric that moved, appeared or disappeared."""
    lines = ["| | metric | before | after | change |", "| :-: | --- | ---: | ---: | ---: |"]
    for name in _names(base, head):
        old, new = base.get(name), head.get(name)
        if old == new:
            continue
        delta = "" if old is None or new is None else f"{new - old:+.{_RANKING_DECIMALS}f}"
        status = _ranking_status(old, new) if name in _GATE_METRICS and old is not None and new is not None else ""
        lines.append(f"| {status} | {name} | {_metric(old)} | {_metric(new)} | {delta} |")
    return lines


def _dropped_gate_metrics(base: dict[str, float], head: dict[str, float]) -> dict[str, float]:
    """Return {metric: head - base} for every gate metric present in both artefacts whose value fell."""
    return {
        name: head[name] - base[name]
        for name in _GATE_METRICS
        if name in base and name in head and head[name] < base[name]
    }


def main(argv: Sequence[str] | None = None) -> int:
    """Print the comparison of `--head` against `--base`; exit 1 on a regression, 2 on an unreadable file."""
    args = build_parser().parse_args(argv)
    try:
        base, head = load_artefact(args.base), load_artefact(args.head)
    except (OSError, ValueError, KeyError) as exc:
        print(f"cannot read baseline: {exc}", file=sys.stderr)
        return _EXIT_UNREADABLE
    grown = sorted(name for name in base.sizes.keys() & head.sizes.keys() if head.sizes[name] > base.sizes[name])
    dropped = _dropped_gate_metrics(base.ranking, head.ranking)
    report = [
        f"{_size_badge(base.sizes, head.sizes)} {_ranking_badge(dropped, head.k)}",
        "",
        _headline(base.sizes, head.sizes),
    ]
    size_table = _moved_table(base.sizes, head.sizes)
    if len(size_table) == _TABLE_HEADER_LINES and all(
        base.ranking.get(m) == head.ranking.get(m) for m in _GATE_METRICS
    ):
        report += ["", NO_CHANGES_TEXT]
    blocks = (("Size details", size_table), ("Ranking details", _ranking_table(base.ranking, head.ranking)))
    for summary, table in blocks:
        if len(table) > _TABLE_HEADER_LINES:
            report += _details(summary, table)
    print("\n".join(report))
    regressions = []
    if grown and not args.allow_growth:
        print(f"output size grew for {grown}", file=sys.stderr)
        regressions.append(grown)
    if dropped and not args.allow_ranking_drop:
        print(f"ranking dropped for {sorted(dropped)}", file=sys.stderr)
        regressions.append(dropped)
    return _EXIT_REGRESSION if regressions else 0


if __name__ == "__main__":
    raise SystemExit(main())
