"""Link scenarios: whether an agent recalling through the real tools sees the answer on each graph shape.

The agent calls `search_nodes`, then `get_entity_with_relations` on its top hits, and reads every
observation those calls return, search hits first. The server returns a hit's related entities in no
fixed order, so the agent reads them in name order. Each scenario seeds the same facts on three shapes
(`SHAPES`): `A` links them as the memory rules ask (task implements feature, feature part-of project,
task depends-on task), `B` links them all with `relates-to`, and `C` leaves them unlinked. The relevant
item for a query is the observation holding its answer, scored with the harness's `evaluate`.
"""

from __future__ import annotations

from dataclasses import dataclass
import functools
import operator
from typing import TYPE_CHECKING
from unittest import mock

from mcp_memory import server
from mcp_memory.eval import evaluate
from mcp_memory.models import Relation
from mcp_memory.storage import open_writable
from mcp_memory.storage.operations import reads
from tests.eval.eval_harness import _FIXTURE_NOW, _pinned

from .eval_fixture import _K, _record_retrieval

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from mcp_memory.eval import EvalReport, LabelledQuery
    from mcp_memory.storage import Storage

PROJECT = "scenarios"
FOLLOWED_HITS = 3
DECOYS = 2
_WORDS = ("amber", "basalt", "cobalt")

# shape -> relation type written for a link whose #147-style relation type is given
SHAPES: dict[str, Callable[[str], str | None]] = {
    "A": lambda relation_type: relation_type,
    "B": lambda _: "relates-to",
    "C": lambda _: None,
}

Link = tuple[str, str, str]


@dataclass(frozen=True)
class Graph:
    """One copy of a case: its entities, its links (source, #147-style relation type, target) and its answers."""

    entities: list[dict[str, object]]
    links: list[Link]
    answers: dict[str, str]


@dataclass(frozen=True)
class Scenario:
    """A named way to seed a case's `Graph` copies and the query an agent asks about them.

    `one_hop` says whether the answer sits one link from a followed search hit.
    """

    name: str
    build: Callable[[str, int, str], Graph]
    query: Callable[[str], str]
    one_hop: bool = True


def _entity(kind: str, name: str, *observations: str) -> dict[str, object]:
    return {"name": f"{kind}/{name}", "entityType": kind, "observations": list(observations)}


def _mention(text: str, copy: int) -> str:
    """Return the text a copy matches the query with: the real copy's as is, decoys' stronger or weaker."""
    if copy == 0:
        return text
    return f"{text} {text}" if copy % 2 else f"{text} amid " + " ".join(f"filler{n}" for n in range(8))


def _ticket(word: str) -> str:
    return f"TKT-{4800 + _WORDS.index(word)}"


def _decision(tag: str, step: int = 0) -> str:
    return f"decided approach {tag}.{step} for the work"


def _ticket_decision(word: str, copy: int, tag: str) -> Graph:
    """A ticket's task links to the feature that holds the decision."""
    decision = _decision(tag)
    return Graph(
        [
            _entity("task", tag, _mention(f"{_ticket(word)} follow-up on the {word} work", copy)),
            _entity("feature", tag, decision, "background notes"),
            _entity("project", tag, "scope of the work"),
        ],
        [(f"task/{tag}", "implements", f"feature/{tag}"), (f"feature/{tag}", "part-of", f"project/{tag}")],
        {f"feature/{tag}": decision},
    )


def _feature_tasks(word: str, copy: int, tag: str) -> Graph:
    """A feature found by name links to its tasks, which hold the decisions."""
    decisions = {f"task/{tag}-{step}": _decision(tag, step) for step in range(3)}
    return Graph(
        [
            _entity("feature", tag, _mention(f"{word} integration", copy)),
            _entity("project", tag, "scope of the work"),
            *(_entity("task", name.removeprefix("task/"), decision) for name, decision in decisions.items()),
        ],
        [(name, "implements", f"feature/{tag}") for name in decisions]
        + [(f"feature/{tag}", "part-of", f"project/{tag}")],
        decisions,
    )


def _cross_task(word: str, copy: int, tag: str, *, direct: bool) -> Graph:
    """A stalled task's query is answered by another task's decision, linked directly or only through their feature."""
    decision = _decision(tag)
    links = [
        (f"task/stalled-{tag}", "implements", f"feature/{tag}"),
        (f"task/pilot-{tag}", "implements", f"feature/{tag}"),
        (f"feature/{tag}", "part-of", f"project/{tag}"),
    ]
    if direct:
        links.append((f"task/stalled-{tag}", "depends-on", f"task/pilot-{tag}"))
    return Graph(
        [
            _entity("task", f"stalled-{tag}", _mention(f"{word} rollout stalled", copy)),
            _entity("task", f"pilot-{tag}", decision),
            _entity("feature", tag, "background notes"),
            _entity("project", tag, "scope of the work"),
        ],
        links,
        {f"task/pilot-{tag}": decision},
    )


SCENARIOS = [
    Scenario("ticket_decision", _ticket_decision, _ticket),
    Scenario("feature_tasks", _feature_tasks, lambda word: word),
    Scenario("cross_task_direct", functools.partial(_cross_task, direct=True), lambda word: word),
    Scenario("cross_task_sibling", functools.partial(_cross_task, direct=False), lambda word: word, one_hop=False),
]


def _recall(query: LabelledQuery) -> list[str]:
    """Return every observation the agent sees, as `entity: observation`, in the order it reads them."""
    hits = server.search_nodes.__wrapped__(PROJECT, query.query)["entities"]
    related = [
        related
        for hit in hits[:FOLLOWED_HITS]
        for related in sorted(
            server.get_entity_with_relations.__wrapped__(PROJECT, hit["name"])["relatedEntities"],
            key=operator.itemgetter("name"),
        )
    ]
    seen = [f"{entity['name']}: {obs['content']}" for entity in [*hits, *related] for obs in entity["observations"]]
    return list(dict.fromkeys(seen))


def _seed(db: Storage, scenario: Scenario, shape: str, decoys: int) -> None:
    """Seed every case, plus `decoys` look-alike copies of it with their own links, and label each query's answers."""
    graphs = {
        word: [scenario.build(word, copy, f"c{_WORDS.index(word)}d{copy}") for copy in range(decoys + 1)]
        for word in _WORDS
    }
    every = [graph for copies in graphs.values() for graph in copies]
    db.entities.create(PROJECT, [entity for graph in every for entity in graph.entities])
    links = [(source, SHAPES[shape](kind), target) for graph in every for source, kind, target in graph.links]
    db.relations.create(
        PROJECT,
        [Relation(source=source, target=target, relation_type=kind) for source, kind, target in links if kind],
    )
    pinned = _pinned(0)
    with db.connection.transaction():
        db.connection.write("UPDATE entities SET created_at = ?, updated_at = ?", (pinned, pinned))
    for word, (real, *_) in graphs.items():
        answers = tuple(f"{name}: {observation}" for name, observation in real.answers.items())
        hits = [(PROJECT, answer, rank) for rank, answer in enumerate(answers, start=1)]
        _record_retrieval(db, f"rid-{word}", "search_nodes", scenario.query(word), hits, answers)


def measure_scenario(scenario: Scenario, directory: Path, *, decoys: int = 0) -> dict[str, EvalReport]:
    """Score `scenario` on each shape, on a fresh database per shape."""
    reports: dict[str, EvalReport] = {}
    for shape in SHAPES:
        db = open_writable(directory / f"{shape}.db")
        _seed(db, scenario, shape, decoys)
        with (
            mock.patch.object(server, "_db", db),
            mock.patch.object(reads, "datetime") as clock,
        ):
            clock.now.return_value = _FIXTURE_NOW
            reports[shape] = evaluate(db, k=_K, ranker=_recall)
    return reports
