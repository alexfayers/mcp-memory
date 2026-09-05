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
    pending migration has run, every observation has a content_hash (required for correctness,
    so this runs unconditionally), and the config-gated startup sweeps - telemetry pruning,
    orphan GC, soft-delete purge, stale-entity archival - have been attempted best-effort under
    a single OperationalError swallow, since they may lose a lock race with another process.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = Connection.open_writable(path)
    run_migrations(connection.raw)
    storage = Storage(connection)
    storage.maintenance.backfill_observation_hashes()
    storage.maintenance.run_startup_sweeps()
    return storage


def open_readonly(path: str | Path) -> Storage:
    """Open an existing database read-only, skipping migrations and startup maintenance."""
    connection = Connection.open_readonly(path)
    return Storage(connection)
