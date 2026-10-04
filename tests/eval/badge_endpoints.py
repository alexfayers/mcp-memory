"""Write the shields.io endpoint files (tool output size, recall at k) for a baseline artefact."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import TYPE_CHECKING

from tests.eval.compare_baseline import load_artefact

if TYPE_CHECKING:
    from collections.abc import Sequence

_BYTES_PER_KB = 1000
_RECALL_METRIC = "mean_recall_at_k"
_RANKING_DECIMALS = 4


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the endpoint writer."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artefact", type=Path, help="baseline artefact to summarise")
    parser.add_argument("out_dir", type=Path, help="directory to write size.json and ranking.json into")
    return parser


def _endpoint(label: str, message: str) -> str:
    """Return the shields.io endpoint JSON for a blue badge."""
    return json.dumps({"schemaVersion": 1, "label": label, "message": message, "color": "blue"}) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    """Write `size.json` and `ranking.json` for the artefact into the output directory."""
    args = build_parser().parse_args(argv)
    artefact = load_artefact(args.artefact)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    total_kb = sum(artefact.sizes.values()) / _BYTES_PER_KB
    (args.out_dir / "size.json").write_text(_endpoint("tool output", f"{total_kb:.1f} kB"), encoding="utf-8")
    recall = artefact.ranking[_RECALL_METRIC]
    (args.out_dir / "ranking.json").write_text(
        _endpoint(f"recall@{artefact.k}", f"{recall:.{_RANKING_DECIMALS}f}"), encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
