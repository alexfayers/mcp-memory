"""Composition root: wires the connection, repositories, and composed reads/sweeps."""

from __future__ import annotations

from contextlib import AbstractContextManager
from pathlib import Path

from mcp_memory.migrations.runner import run_migrations

from .connection import Connection
from .operations.maintenance import Maintenance
from .operations.reads import Reads
from .operations.transfer import Transfer
from .repositories.entities import EntityRepository
from .repositories.observations import ObservationRepository
from .repositories.projects import ProjectRepository
from .repositories.relations import RelationRepository
from .repositories.telemetry import TelemetryRepository


class Storage:
    """Composition root: wires the connection, repositories, and composed reads/sweeps."""

    def __init__(self, connection: Connection) -> None:
        self.connection = connection
        self.entities = EntityRepository(connection)
        self.observations = ObservationRepository(connection)
        self.relations = RelationRepository(connection)
        self.projects = ProjectRepository(connection)
        self.telemetry = TelemetryRepository(connection)
        self.maintenance = Maintenance(connection, self.entities, self.telemetry)
        self.reads = Reads(connection, self.observations, self.relations)
        self.transfer = Transfer(connection, self.entities, self.observations, self.relations, self.projects)

    def transaction(self, commit: bool = True) -> AbstractContextManager[None]:
        return self.connection.transaction(commit)


def open_writable(path: str | Path) -> Storage:
    """Open (or create) a writable database and bring it fully up to date.

    On return: the parent directory exists, the connection's six pragmas are set, every
    pending migration has run, and every observation has a content_hash (required for
    correctness, so this runs unconditionally). Does NOT run the maintenance sweeps -
    telemetry pruning, orphan GC, soft-delete purge, stale-entity archival - since those
    are best-effort periodic work that belongs to the long-lived server process, not to
    every connection open; the caller (server.py's background loop) is responsible for
    invoking Storage.maintenance.run_sweeps() on its own cadence.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = Connection.open_writable(path)
    run_migrations(connection.raw)
    storage = Storage(connection)
    storage.maintenance.backfill_observation_hashes()
    return storage


def open_readonly(path: str | Path) -> Storage:
    """Open an existing database read-only, skipping migrations and maintenance sweeps."""
    connection = Connection.open_readonly(path)
    return Storage(connection)
