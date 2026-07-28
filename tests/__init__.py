from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mcp_memory.database import DatabaseManager
    from mcp_memory.models import Entity


def obs_contents(entity: Entity) -> list[str]:
    return [o.content for o in entity.observations]


def obs_votes(entity: Entity) -> list[int]:
    return [o.vote_score for o in entity.observations]


@dataclass(frozen=True)
class SeedEntity:
    """A single entity to seed into a test database, with optional age/vote backdating."""

    name: str
    observations: list[str] = field(default_factory=list)
    entity_type: str = "task"
    age_days: int = 0
    votes: int = 0


def seed(db: DatabaseManager, project: str, entities: list[SeedEntity]) -> None:
    """Create entities, then apply age backdating and votes described by each SeedEntity."""
    db.create_entities(
        project,
        [
            {
                "name": entity.name,
                "entityType": entity.entity_type,
                "observations": entity.observations,
            }
            for entity in entities
        ],
    )
    for entity in entities:
        if entity.age_days > 0:
            backdate(db, entity.name, entity.age_days)
        if entity.votes != 0:
            vote = 1 if entity.votes > 0 else -1
            for _ in range(abs(entity.votes)):
                db.vote_entity(project, entity.name, vote)


def backdate(db: DatabaseManager, name: str, days: int) -> None:
    """Age an entity by rewriting its created_at/updated_at to `days` ago."""
    db._db.execute(
        "UPDATE entities SET created_at = datetime('now', ?), updated_at = datetime('now', ?) "
        "WHERE name = ?",
        (f"-{days} days", f"-{days} days", name),
    )
    db._db.commit()


def rank_of(name: str, entities: list[Entity]) -> int:
    """Return the 0-based rank of an entity in a result list, or -1 if absent."""
    for index, entity in enumerate(entities):
        if entity.name == name:
            return index
    return -1
