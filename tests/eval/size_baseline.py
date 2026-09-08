"""The committed measured baseline for MCP read-tool output size, in bytes.

Owns the size section's shape, its (de)serialisation and the probe table, within the shared
`tests/eval/baseline.json` artefact - see `eval_baseline` for the ranking section and
`regen_baseline` for the single entry point that regenerates both. Regenerating it is a
separate, explicit act (`just baseline --rebaseline`) - never something a test run does,
since a reference value recomputed from the code under test can never fail.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import TYPE_CHECKING

from mcp_memory import server
from mcp_memory.payload import payload_size
from tests.eval.eval_baseline import BASELINE_PATH
from tests.eval.eval_fixture import _PROJECTS, _TOPICS

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from tests.eval.eval_fixture import EvalFixture

TOOLS = ("search_nodes", "read_graph", "get_entity_with_relations")

# Not a real tokenizer - no tiktoken dependency, and it would be the wrong tokenizer family
# for a Claude-facing tool anyway. A rough estimate purely to make byte deltas legible as an
# approximate token count.
_BYTES_PER_TOKEN = 4

_REBASELINE_HINT = "run `just baseline --rebaseline` to regenerate it"


def _search_nodes_probes(fixture: EvalFixture) -> list[int]:
    """Return one search_nodes payload size per `_TOPICS` entry, pinned to `fixture.now`."""
    return [
        payload_size(server._prepare_read_result(fixture.db.reads.search(project, term, now=fixture.now)))
        for _, project, term in _TOPICS
    ]


def _read_graph_probes(fixture: EvalFixture) -> list[int]:
    """Return one read_graph payload size per `_PROJECTS` entry."""
    return [payload_size(server._prepare_read_result(fixture.db.reads.recent(project))) for project, _, _ in _PROJECTS]


def _get_entity_with_relations_probes(fixture: EvalFixture) -> list[int]:
    """Return one get_entity_with_relations payload size per topic's durable-hit entity."""
    return [
        payload_size(
            server._prepare_read_result(
                fixture.db.reads.get_entity_with_relations(project, fixture.name_for(topic_index, "durable-hit"))
            )
        )
        for topic_index, project, _ in _TOPICS
    ]


# tool name -> a probe function returning one payload byte-size per item it measures. Each
# probe resolves its own project/query/entity-name arguments from `fixture` at call time.
_PROBES: dict[str, Callable[[EvalFixture], list[int]]] = {
    "search_nodes": _search_nodes_probes,
    "read_graph": _read_graph_probes,
    "get_entity_with_relations": _get_entity_with_relations_probes,
}


@dataclass(frozen=True)
class SizeBaseline:
    """A measured output-size baseline: total bytes per tool, at a given fixture entity count."""

    entity_count: int
    probe_counts: dict[str, int]
    total_bytes: dict[str, int]

    @classmethod
    def from_probes(cls, entity_count: int, sizes_by_tool: dict[str, list[int]]) -> SizeBaseline:
        """Build a canonical baseline from raw per-probe byte sizes, keyed by tool name."""
        return cls(
            entity_count=entity_count,
            probe_counts={tool: len(sizes) for tool, sizes in sizes_by_tool.items()},
            total_bytes={tool: sum(sizes) for tool, sizes in sizes_by_tool.items()},
        )


def measure(fixture: EvalFixture) -> SizeBaseline:
    """Run every probe in `_PROBES` against `fixture` and return the resulting baseline."""
    return SizeBaseline.from_probes(
        entity_count=len(fixture.entity_names),
        sizes_by_tool={tool: probe(fixture) for tool, probe in _PROBES.items()},
    )


def load_baseline(path: Path = BASELINE_PATH) -> SizeBaseline:
    """Read and validate the size section of the committed baseline artefact at `path`."""
    if not path.exists():
        raise TypeError(f"no size baseline at {path} - {_REBASELINE_HINT}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise TypeError(f"size baseline at {path} is not valid JSON - {_REBASELINE_HINT}") from exc
    if "size" not in payload:
        raise TypeError(f"size baseline at {path} has no 'size' section - {_REBASELINE_HINT}")
    section_payload = payload["size"]
    if not isinstance(section_payload, dict):
        raise TypeError(f"size baseline at {path} has a non-dict 'size' section - {_REBASELINE_HINT}")
    entity_count = section_payload["entity_count"]
    tools = section_payload["tools"]
    if set(tools) != set(TOOLS):
        raise TypeError(f"size baseline at {path} does not cover exactly {TOOLS} - {_REBASELINE_HINT}")
    if entity_count <= 0:
        raise TypeError(f"size baseline at {path} was measured at entity_count={entity_count} - {_REBASELINE_HINT}")
    probe_counts = {tool: tools[tool]["probes"] for tool in TOOLS}
    total_bytes = {tool: tools[tool]["total_bytes"] for tool in TOOLS}
    if any(count <= 0 for count in probe_counts.values()):
        raise TypeError(f"size baseline at {path} has a non-positive probe count - {_REBASELINE_HINT}")
    if any(size <= 0 for size in total_bytes.values()):
        raise TypeError(f"size baseline at {path} has a non-positive total_bytes - {_REBASELINE_HINT}")
    return SizeBaseline(entity_count=entity_count, probe_counts=probe_counts, total_bytes=total_bytes)


def section(baseline: SizeBaseline) -> dict[str, object]:
    """Return the size section's payload: fixed key order."""
    return {
        "entity_count": baseline.entity_count,
        "tools": {
            tool: {"probes": baseline.probe_counts[tool], "total_bytes": baseline.total_bytes[tool]} for tool in TOOLS
        },
    }


def moved(committed: SizeBaseline, measured: SizeBaseline) -> dict[str, tuple[int, int]]:
    """Return {tool: (old, new)} for every tool whose total_bytes differs."""
    return {
        tool: (committed.total_bytes[tool], measured.total_bytes[tool])
        for tool in TOOLS
        if committed.total_bytes[tool] != measured.total_bytes[tool]
    }


def format_moves(committed: SizeBaseline, measured: SizeBaseline) -> str:
    """Render the tool-name list and byte/token delta for every tool whose size moved."""
    drift = moved(committed, measured)
    delta = sum(new - old for old, new in drift.values())
    tokens = delta // _BYTES_PER_TOKEN
    sign = "+" if delta >= 0 else ""
    return (
        f"output size moved for {sorted(drift)}\n"
        f"({sign}{delta} bytes, ~{sign}{tokens} tokens estimated at {_BYTES_PER_TOKEN} bytes/token)"
    )
