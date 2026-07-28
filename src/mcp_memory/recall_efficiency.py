"""Compression + cost profile of the memory-agent recall tool, measured without a live spawn.

Recall distils a large slice of the graph into a short plain-text answer. This measures how
much it compressed the raw graph payload it was given (the tool response a non-distilled
retrieval would have returned) against the distilled output, plus the cost and latency the
recall reported - all from inputs the caller passes in, so no live paid spawn is required.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .payload import payload_size

if TYPE_CHECKING:
    from .recall_status import RecallRecord


@dataclass(frozen=True)
class RecallEfficiency:
    """Compression + cost profile of one recall vs the raw graph it distilled.

    ``ratio`` is the fraction of the raw-graph payload saved by the distilled output.
    Cost/latency fields are carried through from the recorded recall metrics and are
    None when the source recall did not report them.
    """

    input_bytes: int
    output_bytes: int
    saved_bytes: int
    ratio: float
    duration_ms: int | None
    num_turns: int | None
    cost_usd: float | None


def recall_efficiency(
    graph_payload: object, recall_output: str, *, record: RecallRecord
) -> RecallEfficiency:
    """Measure how much a recall compressed the raw graph it was given, plus its cost.

    ``input_bytes`` is the serialized size of the raw graph payload (the tool response a
    non-distilled retrieval would return), measured with the Phase 3 ``payload_size`` proxy.
    ``output_bytes`` is the UTF-8 byte length of the distilled recall output string itself
    (recall returns plain text, so it is measured directly rather than re-serialized).
    Cost and latency are copied from the recorded recall metrics.
    """
    input_bytes = payload_size(graph_payload)
    output_bytes = len(recall_output.encode("utf-8"))
    saved_bytes = input_bytes - output_bytes
    return RecallEfficiency(
        input_bytes=input_bytes,
        output_bytes=output_bytes,
        saved_bytes=saved_bytes,
        ratio=saved_bytes / input_bytes if input_bytes else 0.0,
        duration_ms=record["duration_ms"],
        num_turns=record["num_turns"],
        cost_usd=record["cost_usd"],
    )
