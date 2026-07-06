"""Ranking evaluation harness.

Encodes the intended search-ranking behaviour as golden cases over a seeded, synthetic
database so the recency half-lives and vote constants can be tuned by measurement rather
than guesswork. Coupled to no live state: every entity, age, and vote is set explicitly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from mcp_memory.database import DatabaseManager

if TYPE_CHECKING:
    from pathlib import Path

    from mcp_memory.models import Entity


@pytest.fixture
def db(tmp_path: Path) -> DatabaseManager:
    """Create a fresh database for each test."""
    return DatabaseManager(tmp_path / "eval.db")


def _backdate(db: DatabaseManager, name: str, days: int) -> None:
    """Age an entity by rewriting its created_at/updated_at to `days` ago."""
    db._db.execute(
        "UPDATE entities SET created_at = datetime('now', ?), updated_at = datetime('now', ?) "
        "WHERE name = ?",
        (f"-{days} days", f"-{days} days", name),
    )
    db._db.commit()


def _rank_of(name: str, entities: list[Entity]) -> int:
    """Return the 0-based rank of an entity in a result list, or -1 if absent."""
    for index, entity in enumerate(entities):
        if entity.name == name:
            return index
    return -1


class TestTypeAwareDecay:
    def test_durable_pattern_not_buried_below_fresh_task(self, db: DatabaseManager) -> None:
        db.create_entities(
            "proj",
            [
                {"name": "pattern/retry", "entityType": "pattern", "observations": ["backoff"]},
                {"name": "task/retry", "entityType": "task", "observations": ["backoff"]},
            ],
        )
        _backdate(db, "pattern/retry", 120)
        _backdate(db, "task/retry", 20)

        entities = db.search_nodes("proj", "backoff")["entities"]
        assert _rank_of("pattern/retry", entities) < _rank_of("task/retry", entities)


class TestVoteInfluence:
    def test_upvoted_outranks_equal_unvoted(self, db: DatabaseManager) -> None:
        db.create_entities(
            "proj",
            [
                {"name": "task/a", "entityType": "task", "observations": ["deploy"]},
                {"name": "task/b", "entityType": "task", "observations": ["deploy"]},
            ],
        )
        db.vote_entity("proj", "task/b", 1)

        entities = db.search_nodes("proj", "deploy")["entities"]
        assert _rank_of("task/b", entities) < _rank_of("task/a", entities)

    def test_heavily_downvoted_still_returned_but_last(self, db: DatabaseManager) -> None:
        db.create_entities(
            "proj",
            [
                {"name": "task/good", "entityType": "task", "observations": ["cache"]},
                {"name": "task/bad", "entityType": "task", "observations": ["cache"]},
            ],
        )
        for _ in range(10):
            db.vote_entity("proj", "task/bad", -1)

        entities = db.search_nodes("proj", "cache")["entities"]
        assert _rank_of("task/bad", entities) != -1
        assert _rank_of("task/bad", entities) > _rank_of("task/good", entities)
