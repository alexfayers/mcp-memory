"""The ranking replay: the fixture's labelled queries scored on what the real search tools return.

Replays every labelled query through the `search_nodes` and `search_all_projects` tool bodies and
scores the names they return, which is the ranking section of the shared `tests/eval/baseline.json`
artefact - see `eval_baseline` for its shape and `regen_baseline` for the entry point that writes it.
The queries labelled unreachable are scored apart from the gate metrics.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest import mock

from mcp_memory import server, usefulness
from mcp_memory.eval import evaluate
from mcp_memory.storage.operations import reads
from tests.eval.eval_baseline import Baseline
from tests.eval.size_baseline import _serving

if TYPE_CHECKING:
    from collections.abc import Callable

    from mcp_memory.eval import EvalReport, LabelledQuery
    from tests.eval.eval_fixture import EvalFixture


def _tool_ranking(query: LabelledQuery) -> list[str]:
    """Return the entity names the real search tool yields for `query`, in rank order."""
    if query.project is None:
        result = server.search_all_projects.__wrapped__(query.query)
    else:
        result = server.search_nodes.__wrapped__(query.project, query.query)
    return [name for _, name, _ in usefulness._surfaced_hits(result)]


def _replay(fixture: EvalFixture, include: Callable[[LabelledQuery], bool]) -> EvalReport:
    """Score the labelled queries `include` accepts on the tool rankings, with recency pinned to `fixture.now`."""
    with _serving(fixture), mock.patch.object(reads, "datetime", **{"now.return_value": fixture.now}) as clock:
        report = evaluate(fixture.db, k=fixture.k, ranker=_tool_ranking, include=include)
    if not clock.now.called:
        msg = "the search never read the pinned clock, so recency is not pinned to the fixture"
        raise RuntimeError(msg)
    return report


def measure(fixture: EvalFixture) -> Baseline:
    """Score `fixture`'s labelled queries on the tool rankings; the unreachable-labelled ones are scored apart."""

    def is_reachable(query: LabelledQuery) -> bool:
        return not fixture.is_unreachable(query)

    return Baseline.from_report(_replay(fixture, is_reachable), unreachable=_replay(fixture, fixture.is_unreachable))
