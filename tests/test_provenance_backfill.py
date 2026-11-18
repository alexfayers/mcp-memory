"""Tests for the opt-in provenance backfill."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from mcp_memory import cli, server
from mcp_memory.provenance_backfill import apply_backfill, plan_backfill, revert_backfill

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

    from mcp_memory.storage import Storage


def _seed(store: Storage, observations: list[str]) -> None:
    store.entities.create("proj", [{"name": "task/t", "entityType": "task", "observations": observations}])


def _provenances(store: Storage) -> dict[str, str | None]:
    return {o.content: o.provenance for o in store.reads.get_entity("proj", "task/t").observations}


class TestPlan:
    def test_counts_each_rule_once_with_first_match_winning(self, store: Storage) -> None:
        _seed(
            store,
            [
                "INFERRED: a guess",
                "UNVERIFIED: User said it",
                "Verified from source: b",
                "User chose option B",
                "On 28 Oct, the user: 'do it'",
                "plain fact",
            ],
        )
        store.observations.add("proj", "task/t", ["User asked later"])
        store.observations.add("proj", "task/t", ["Verified already tagged"], provenance="inferred")

        plan = {rule.name: rule for rule in plan_backfill(store)}

        assert {name: rule.count for name, rule in plan.items()} == {
            "inferred-marker": 2,
            "observed-marker": 1,
            "user-lead": 2,
            "user-quote": 1,
        }
        assert plan["inferred-marker"].provenance == "inferred"
        assert plan["user-quote"].samples == ["On 28 Oct, the user: 'do it'"]
        assert _provenances(store)["plain fact"] is None


class TestSkip:
    def test_skipped_rule_is_not_planned_or_applied(self, store: Storage, tmp_path: Path) -> None:
        _seed(store, ["User chose option B", "On 28 Oct, the user: 'do it'"])

        plan = {rule.name: rule for rule in plan_backfill(store, skip={"user-quote"})}
        assert "user-quote" not in plan

        applied, _ = apply_backfill(store, tmp_path / "backups", skip={"user-quote"})
        assert applied == 1
        assert _provenances(store)["On 28 Oct, the user: 'do it'"] is None

    def test_a_skipped_rule_still_claims_its_rows_from_later_rules(self, store: Storage, tmp_path: Path) -> None:
        _seed(store, ["INFERRED: the user: 'maybe'"])
        applied, _ = apply_backfill(store, tmp_path / "backups", skip={"inferred-marker"})
        assert applied == 0
        assert _provenances(store)["INFERRED: the user: 'maybe'"] is None


class TestApplyAndRevert:
    def test_apply_tags_rows_and_revert_undoes_only_what_it_set(self, store: Storage, tmp_path: Path) -> None:
        _seed(store, ["User chose option B", "INFERRED: a guess", "plain fact"])

        applied, revert_file = apply_backfill(store, tmp_path / "backups")
        assert applied == 2
        assert revert_file.parent == tmp_path / "backups"
        assert _provenances(store) == {
            "User chose option B": "user",
            "INFERRED: a guess": "inferred",
            "plain fact": None,
        }

        user_hash = next(
            o.content_hash for o in store.reads.get_entity("proj", "task/t").observations if o.provenance == "user"
        )
        store.observations.edit("proj", "task/t", user_hash, "User chose option B", provenance="observed")
        assert revert_backfill(store, tmp_path / "backups") == 1
        assert _provenances(store) == {
            "User chose option B": "observed",
            "INFERRED: a guess": None,
            "plain fact": None,
        }
        assert revert_backfill(store, tmp_path / "backups") == 0


class TestCommand:
    def test_default_is_a_dry_run_and_apply_takes_a_backup_first(
        self, live_server: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("MCP_MEMORY_BACKUP_DIR", str(tmp_path / "backups"))
        db = server._get_db()
        _seed(db, ["User chose option B"])

        monkeypatch.setattr("sys.argv", ["mcp-memory", "utils", "backfill-provenance"])
        cli.main()
        dry_run = json.loads(capsys.readouterr().out)
        assert dry_run["total"] == 1
        assert not (tmp_path / "backups").exists()

        monkeypatch.setattr("sys.argv", ["mcp-memory", "utils", "backfill-provenance", "--apply"])
        cli.main()
        applied = json.loads(capsys.readouterr().out)
        assert applied["applied"] == 1
        assert list((tmp_path / "backups").glob("memory-*.db"))
        assert db.reads.get_entity("proj", "task/t").observations[0].provenance == "user"

        monkeypatch.setattr("sys.argv", ["mcp-memory", "utils", "backfill-provenance", "--revert"])
        cli.main()
        assert json.loads(capsys.readouterr().out)["reverted"] == 1
