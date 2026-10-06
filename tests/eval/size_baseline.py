"""The measured baseline for MCP read-tool output size, in bytes.

Owns the size section's shape, its serialisation and the probe table, within the shared
`tests/eval/baseline.json` artefact - see `eval_baseline` for the ranking section and
`regen_baseline` for the single entry point that measures and writes both. The artefact is a
measurement of the current code, written by `just baseline`; `compare_baseline` diffs two of them.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from dataclasses import dataclass
import functools
from typing import TYPE_CHECKING

from mcp_memory import server
from mcp_memory.payload import payload_size
from tests.eval.eval_fixture import _PROJECTS, _TOPICS

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from tests.eval.eval_fixture import EvalFixture


def _search_nodes_probes(fixture: EvalFixture, *, compact: bool = False) -> list[int]:
    """Return one search_nodes payload size per `_TOPICS` entry, pinned to `fixture.now`."""
    return [
        payload_size(
            server._prepare_read_result(fixture.db.reads.search(project, term, now=fixture.now, compact=compact))
        )
        for _, project, term in _TOPICS
    ]


def _read_graph_probes(fixture: EvalFixture, *, compact: bool = False) -> list[int]:
    """Return one read_graph payload size per `_PROJECTS` entry."""
    return [
        payload_size(server._prepare_read_result(fixture.db.reads.recent(project, compact=compact)))
        for project, _, _ in _PROJECTS
    ]


def _get_entity_with_relations_probes(
    fixture: EvalFixture, *, compact: bool = False, max_observation_chars: int | None = None
) -> list[int]:
    """Return one get_entity_with_relations payload size per topic's durable-hit entity."""
    return [
        payload_size(
            server._prepare_read_result(
                fixture.db.reads.get_entity_with_relations(
                    project,
                    fixture.name_for(topic_index, "durable-hit"),
                    compact=compact,
                    max_observation_chars=max_observation_chars,
                )
            )
        )
        for topic_index, project, _ in _TOPICS
    ]


@contextmanager
def _serving(fixture: EvalFixture) -> Iterator[None]:
    """Point the server's module-level database handle at `fixture.db` for the duration."""
    original = server._db
    server._db = fixture.db
    try:
        yield
    finally:
        server._db = original


def _search_all_projects_probes(fixture: EvalFixture, *, compact: bool = False, names_only: bool = False) -> list[int]:
    """Return one search_all_projects payload size per `_TOPICS` term, via the real tool body."""
    with _serving(fixture):
        return [
            payload_size(server.search_all_projects.__wrapped__(term, compact=compact, names_only=names_only))
            for _, _, term in _TOPICS
        ]


def _tool_list_probes(_fixture: EvalFixture) -> list[int]:
    """Return the size of the advertised tool list."""
    tools = asyncio.run(server.mcp.list_tools())
    return [payload_size([tool.model_dump(by_alias=True, mode="json", exclude_none=True) for tool in tools])]


# probe name -> a probe function returning one payload byte-size per item it measures. Each
# probe resolves its own project/query/entity-name arguments from `fixture` at call time.
_PROBES: dict[str, Callable[[EvalFixture], list[int]]] = {
    "search_nodes": _search_nodes_probes,
    "search_nodes[compact]": functools.partial(_search_nodes_probes, compact=True),
    "read_graph": _read_graph_probes,
    "read_graph[compact]": functools.partial(_read_graph_probes, compact=True),
    "get_entity_with_relations": _get_entity_with_relations_probes,
    "get_entity_with_relations[compact]": functools.partial(_get_entity_with_relations_probes, compact=True),
    "get_entity_with_relations[full]": functools.partial(_get_entity_with_relations_probes, max_observation_chars=-1),
    "search_all_projects": _search_all_projects_probes,
    "search_all_projects[compact]": functools.partial(_search_all_projects_probes, compact=True),
    "search_all_projects[names_only]": functools.partial(_search_all_projects_probes, names_only=True),
    "tools/list": _tool_list_probes,
}

TOOLS = tuple(_PROBES)


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


def section(baseline: SizeBaseline) -> dict[str, object]:
    """Return the size section's payload: fixed key order."""
    return {
        "entity_count": baseline.entity_count,
        "tools": {
            tool: {"probes": baseline.probe_counts[tool], "total_bytes": baseline.total_bytes[tool]} for tool in TOOLS
        },
    }


def move_row(old: int, new: int) -> tuple[int, float]:
    """Return a probe's (delta bytes, delta percent) from `old` to `new`."""
    delta = new - old
    return delta, delta / old * 100
