"""Data models for the MCP memory server."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

EntityStatus = Literal["planned", "in-progress", "blocked", "resolved", "archived"]

VALID_STATUSES = ("planned", "in-progress", "blocked", "resolved", "archived")


@dataclass
class Entity:
    name: str
    entity_type: str
    observations: list[str]
    status: EntityStatus | None = None


@dataclass
class Relation:
    source: str
    target: str
    relation_type: str
