"""Tests for the provenance backfill."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from mcp_memory import server
from mcp_memory.provenance_backfill import backfill_once

if TYPE_CHECKING:
    from pathlib import Path

    from mcp_memory.storage import Storage


@pytest.fixture
def backups(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("MCP_MEMORY_DB_PATH", str(tmp_path / "test.db"))
    return tmp_path / "backups"


def _provenances(store: Storage) -> dict[str, str | None]:
    return {o.content: o.provenance for o in store.reads.get_entity("proj", "task/t").observations}


def test_tags_by_the_first_matching_rule_once(store: Storage, backups: Path) -> None:
    lines = ["INFERRED: User said it", "Verified from source", "User chose B", "On Oct 28, the user: 'go'", "plain"]
    store.entities.create("proj", [{"name": "task/t", "entityType": "task", "observations": lines}])

    assert backfill_once(store) == 3
    assert list(backups.glob("test-*.db"))
    store.observations.add("proj", "task/t", ["User asked later"])
    assert backfill_once(store) is None
    assert _provenances(store) == dict(
        zip([*lines, "User asked later"], ["inferred", "observed", "user", None, None, None], strict=True)
    )


def test_an_empty_first_run_is_recorded_so_later_rows_stay_untagged(store: Storage, backups: Path) -> None:
    assert backfill_once(store) == 0
    store.entities.create("proj", [{"name": "task/t", "entityType": "task", "observations": ["User chose B"]}])
    assert backfill_once(store) is None
    assert _provenances(store) == {"User chose B": None}


def test_a_failed_run_leaves_no_record(store: Storage, backups: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store.entities.create("proj", [{"name": "task/t", "entityType": "task", "observations": ["User chose B"]}])
    monkeypatch.setattr(store.connection, "write_many", lambda *_: 1 / 0)
    with pytest.raises(ZeroDivisionError):
        backfill_once(store)
    assert not list(backups.glob("*provenance-backfill-*"))


def test_server_logs_a_failed_backfill_and_returns(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(server.provenance_backfill, "backfill_once", lambda *_: 1 / 0)
    server._backfill_provenance_once()
    assert "Provenance backfill failed" in caplog.text
