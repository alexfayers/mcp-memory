from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from mcp_memory.storage.services.ids import get_entity_id, get_or_create_project_id

if TYPE_CHECKING:
    from mcp_memory.models import Entity
    from mcp_memory.storage import Storage


def _observations(entity: Entity | dict[str, Any]) -> list[Any]:
    """Read observations from an Entity dataclass or the sparse dict a read tool returns."""
    if isinstance(entity, dict):
        return entity.get("observations", [])
    return entity.observations


def obs_contents(entity: Entity | dict[str, Any]) -> list[str]:
    obs = _observations(entity)
    return [o["content"] if isinstance(o, dict) else o.content for o in obs]


def obs_votes(entity: Entity | dict[str, Any]) -> list[int]:
    obs = _observations(entity)
    return [o.get("vote_score", 0) if isinstance(o, dict) else o.vote_score for o in obs]


@dataclass(frozen=True)
class SeedEntity:
    """A single entity to seed into a test database, with optional age/vote backdating."""

    name: str
    observations: list[str] = field(default_factory=list)
    entity_type: str = "task"
    age_days: int = 0
    votes: int = 0


def seed_store(store: Storage, project: str, entities: list[SeedEntity]) -> None:
    """Create entities, then apply age backdating and votes described by each SeedEntity."""
    store.entities.create(
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
            backdate_store(store, entity.name, entity.age_days)
        if entity.votes != 0:
            vote = 1 if entity.votes > 0 else -1
            for _ in range(abs(entity.votes)):
                store.entities.vote(project, entity.name, vote)


def backdate_store(store: Storage, name: str, days: int) -> None:
    """Age an entity by rewriting its created_at/updated_at to `days` ago."""
    with store.transaction():
        store.connection.write(
            "UPDATE entities SET created_at = datetime('now', ?), updated_at = datetime('now', ?) WHERE name = ?",
            (f"-{days} days", f"-{days} days", name),
        )


def soft_delete_store(store: Storage, project: str, name: str) -> None:
    """Tombstone an entity the way EntityRepository.merge and the orphan sweep do internally."""
    entity_id = get_entity_id(store.connection, name, get_or_create_project_id(store.connection, project))
    assert entity_id is not None, f"Entity '{name}' not found in project '{project}'"
    with store.transaction():
        store.connection.write("UPDATE entities SET deleted_at = CURRENT_TIMESTAMP WHERE id = ?", (entity_id,))


def rank_of(name: str, entities: list[Entity]) -> int:
    """Return the 0-based rank of an entity in a result list, or -1 if absent."""
    for index, entity in enumerate(entities):
        if entity.name == name:
            return index
    return -1
