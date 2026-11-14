"""Link-reachability eval: labelled queries whose relevant entity is reachable only through a link.

Each case pairs an anchor entity that matches the query text with a target that does not, joined by
one link: `task` -> `feature` (implements) or `feature` -> `project` (part-of). The old graph shape
has no such link, so the target is out of reach. `measure_link_reachability` scores both shapes
under a ranker that follows each search hit one hop along the relations the search returns.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from mcp_memory.models import Relation
from tests.eval.eval_harness import _FIXTURE_NOW, MeasuredChange, _pinned, measure_change

from .eval_fixture import _K, _record_retrieval

if TYPE_CHECKING:
    from collections.abc import Callable

    from mcp_memory.eval import LabelledQuery
    from mcp_memory.storage import Storage

PROJECT = "links"

# anchor type -> (target type, relation type linking anchor to target)
LINK_ARCHETYPES: dict[str, tuple[str, str]] = {
    "task": ("feature", "implements"),
    "feature": ("project", "part-of"),
}

_TERMS = ("amber", "basalt", "cobalt", "dune")


def _entity(name: str, entity_type: str, observation: str) -> dict[str, object]:
    return {"name": name, "entityType": entity_type, "observations": [observation]}


def _case_names(anchor_type: str, slug: str) -> dict[str, str]:
    """Return the case's entity names, which never contain its query term."""
    target_type, _ = LINK_ARCHETYPES[anchor_type]
    return {
        "anchor": f"{anchor_type}/anchor-{slug}",
        "decoy": f"{anchor_type}/decoy-{slug}",
        "target": f"{target_type}/target-{slug}",
        "other": f"{target_type}/other-{slug}",
        "scope": f"project/scope-{slug}",
    }


def _cases() -> list[tuple[str, str, str]]:
    """Return every case as (anchor type, query term, name slug)."""
    return [
        (anchor_type, f"{term}{anchor_type}", f"{anchor_type}-{index}")
        for anchor_type in LINK_ARCHETYPES
        for index, term in enumerate(_TERMS)
    ]


def build_link_fixture(db: Storage) -> None:
    """Seed the old graph shape: anchors relate to a project scope and nothing links to a feature."""
    entities: list[dict[str, object]] = []
    relations: list[Relation] = []
    for anchor_type, term, slug in _cases():
        names = _case_names(anchor_type, slug)
        target_type, _ = LINK_ARCHETYPES[anchor_type]
        entities += [
            _entity(names["decoy"], anchor_type, f"{term} notes"),
            _entity(names["anchor"], anchor_type, f"{term} work"),
            _entity(names["target"], target_type, "capability behind the work"),
            _entity(names["other"], target_type, "capability behind other work"),
            _entity(names["scope"], "project", "scope of the work"),
        ]
        if anchor_type == "task":
            relations += [
                Relation(source=names[who], target=names["scope"], relation_type="relates-to")
                for who in ("anchor", "decoy")
            ]
    db.entities.create(PROJECT, entities)
    db.relations.create(PROJECT, relations)
    pinned = _pinned(0)
    with db.connection.transaction():
        db.connection.write("UPDATE entities SET created_at = ?, updated_at = ?", (pinned, pinned))
    for anchor_type, term, slug in _cases():
        names = _case_names(anchor_type, slug)
        hits = [(PROJECT, names[role], rank) for rank, role in enumerate(("decoy", "anchor", "target"), start=1)]
        _record_retrieval(db, f"rid-link-{term}", "search_nodes", term, hits, (names["target"],))


def add_links(db: Storage) -> None:
    """Add the link shape: each anchor links to its target, and each feature target to its project scope."""
    relations: list[Relation] = []
    for anchor_type, _, slug in _cases():
        names = _case_names(anchor_type, slug)
        _, relation_type = LINK_ARCHETYPES[anchor_type]
        relations += [
            Relation(source=names[who], target=names[tgt], relation_type=relation_type)
            for who, tgt in (("anchor", "target"), ("decoy", "other"))
        ]
        if anchor_type == "task":
            relations += [
                Relation(source=names[who], target=names["scope"], relation_type="part-of")
                for who in ("target", "other")
            ]
    db.relations.create(PROJECT, relations)


def link_following_ranker(db: Storage, k: int = _K) -> Callable[[LabelledQuery], list[str]]:
    """Return a ranker listing the search hits, then each hit's linked entities in hit order."""

    def rank(query: LabelledQuery) -> list[str]:
        result = db.reads.search(query.project, query.query, limit=k, now=_FIXTURE_NOW)
        ranked = [entity.name for entity in result["entities"]]
        for hit in list(ranked):
            for relation in result["relations"]:
                if hit in {relation.source, relation.target}:
                    linked = relation.target if relation.source == hit else relation.source
                    if linked not in ranked:
                        ranked.append(linked)
        return ranked

    return rank


def measure_link_reachability(db: Storage, *, follow_links: bool) -> MeasuredChange:
    """Score the old shape (before) against the link shape (after), optionally following links."""
    return measure_change(
        db,
        add_links,
        k=_K,
        now=_FIXTURE_NOW,
        ranker=link_following_ranker(db) if follow_links else None,
    )
