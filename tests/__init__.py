from mcp_memory.models import Entity


def obs_contents(entity: Entity) -> list[str]:
    return [o.content for o in entity.observations]


def obs_votes(entity: Entity) -> list[int]:
    return [o.vote_score for o in entity.observations]
