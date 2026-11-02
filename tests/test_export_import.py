"""Tests for exporting and importing project memory across databases."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from mcp_memory import cli
from mcp_memory.api_client import ApiError
from mcp_memory.export_import import (
    EXPORT_FORMAT,
    EXPORT_FORMAT_VERSION,
    ImportSummary,
    export_database as build_export,
    import_projects,
    load_export,
)
from mcp_memory.migrations.schema import MIGRATIONS
from mcp_memory.models import Relation
from mcp_memory.storage import open_writable
from tests import soft_delete_store

if TYPE_CHECKING:
    from mcp_memory.storage import Storage
    from tests.conftest import FakeApi


def export_database(db: Storage, output_path: Path) -> None:
    output_path.write_text(json.dumps(build_export(db)), encoding="utf-8")


_OLD_TS = "2020-01-01 00:00:00"


def _make_db(path: Path) -> Storage:
    db = open_writable(path)
    db.entities.create(
        "global",
        [{"name": "user-preferences/x", "entityType": "user-preferences", "observations": ["a"]}],
    )
    db.entities.create(
        "proj",
        [
            {"name": "feature/f", "entityType": "feature", "observations": ["b", "c"]},
            {"name": "task/t", "entityType": "task", "observations": ["d"], "status": "planned"},
        ],
    )
    db.relations.create("proj", [Relation("task/t", "feature/f", "implements")])
    db.projects.set_paths("proj", ["/tmp/proj"])
    db.projects.set_groups("proj", ["tooling"])
    return db


class TestExport:
    def test_writes_valid_json_with_all_projects(self, tmp_path: Path) -> None:
        db = _make_db(tmp_path / "src.db")
        out = tmp_path / "export.json"
        expected_paths = db.projects.paths_for("proj")
        export_database(db, out)
        db.connection.close()

        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["format"] == EXPORT_FORMAT
        assert data["format_version"] == EXPORT_FORMAT_VERSION
        assert data["schema_version"] == max(m.version for m in MIGRATIONS)
        assert "exported_at" in data
        assert set(data["projects"]) == {"global", "proj"}

        proj = data["projects"]["proj"]
        assert proj["paths"] == expected_paths
        assert proj["groups"] == ["tooling"]
        assert {e["name"] for e in proj["entities"]} == {"feature/f", "task/t"}
        assert {(r["source"], r["target"], r["relation_type"]) for r in proj["relations"]} == {
            ("task/t", "feature/f", "implements")
        }

    def test_excludes_soft_deleted_entities(self, tmp_path: Path) -> None:
        db = _make_db(tmp_path / "src.db")
        soft_delete_store(db, "proj", "task/t")
        out = tmp_path / "export.json"
        export_database(db, out)
        db.connection.close()

        data = json.loads(out.read_text(encoding="utf-8"))
        proj = data["projects"]["proj"]
        assert {e["name"] for e in proj["entities"]} == {"feature/f"}
        assert proj["relations"] == []

    def test_preserves_fidelity_fields(self, tmp_path: Path) -> None:
        db = _make_db(tmp_path / "src.db")
        db.entities.vote("proj", "task/t", 2)
        db.observations.vote("proj", "task/t", 3, content="d")
        task = db.reads.get_entity("proj", "task/t")
        with db.connection.transaction():
            db.connection.write("UPDATE observations SET created_at = ? WHERE content = 'd'", (_OLD_TS,))
        out = tmp_path / "export.json"
        export_database(db, out)
        db.connection.close()

        data = json.loads(out.read_text(encoding="utf-8"))
        exported = next(e for e in data["projects"]["proj"]["entities"] if e["name"] == "task/t")
        assert exported["entity_type"] == "task"
        assert exported["status"] == "planned"
        assert exported["vote_score"] == 2
        assert exported["created_at"] == task.created_at
        assert exported["updated_at"] == task.updated_at
        obs = exported["observations"][0]
        assert obs["content"] == "d"
        assert obs["content_hash"] == task.observations[0].content_hash
        assert obs["vote_score"] == 3
        assert obs["created_at"] == _OLD_TS


class TestRoundTrip:
    def test_import_into_fresh_db_reproduces_everything(self, tmp_path: Path) -> None:
        db = _make_db(tmp_path / "src.db")
        db.entities.vote("proj", "task/t", 2)
        db.observations.vote("proj", "task/t", 3, content="d")
        src_task = db.reads.get_entity("proj", "task/t")
        with db.connection.transaction():
            db.connection.write("UPDATE observations SET created_at = ? WHERE content = 'd'", (_OLD_TS,))
        out = tmp_path / "export.json"
        export_database(db, out)
        db.connection.close()

        dest = open_writable(tmp_path / "dest.db")
        import_projects(dest, load_export(out), ["proj"], dry_run=False)

        dest_obs_created = dest.connection.query_one("SELECT created_at FROM observations WHERE content = 'd'")[
            "created_at"
        ]
        assert dest_obs_created == _OLD_TS

        task = dest.reads.get_entity("proj", "task/t")
        assert task.entity_type == "task"
        assert task.status == "planned"
        assert task.vote_score == 2
        assert task.created_at == src_task.created_at
        assert task.updated_at == src_task.updated_at
        assert [(o.content, o.content_hash, o.vote_score) for o in task.observations] == [
            (o.content, o.content_hash, o.vote_score) for o in src_task.observations
        ]

        feature = dest.reads.get_entity("proj", "feature/f")
        assert {o.content for o in feature.observations} == {"b", "c"}

        rels = dest.reads.get_entity_with_relations("proj", "task/t")["relations"]
        assert any(r.source == "task/t" and r.target == "feature/f" and r.relation_type == "implements" for r in rels)
        assert [g for _, g in dest.projects.groups("proj")] == ["tooling"]
        assert dest.projects.paths_for("proj") == []
        dest.connection.close()

    def test_imports_only_named_subset(self, tmp_path: Path) -> None:
        db = _make_db(tmp_path / "src.db")
        out = tmp_path / "export.json"
        export_database(db, out)
        db.connection.close()

        dest = open_writable(tmp_path / "dest.db")
        import_projects(dest, load_export(out), ["global"], dry_run=False)

        assert dest.entities.exists_in("user-preferences/x", "global")
        assert "proj" not in dest.projects.names()
        dest.connection.close()

    def test_absent_project_raises(self, tmp_path: Path) -> None:
        db = _make_db(tmp_path / "src.db")
        out = tmp_path / "export.json"
        export_database(db, out)
        db.connection.close()

        dest = open_writable(tmp_path / "dest.db")
        with pytest.raises(ValueError, match="not found in export file"):
            import_projects(dest, load_export(out), ["nope"], dry_run=False)
        dest.connection.close()


class TestMerge:
    def test_merge_same_type_entity(self, tmp_path: Path) -> None:
        src = _make_db(tmp_path / "src.db")
        src.entities.vote("proj", "feature/f", 3)
        with src.connection.transaction():
            src.connection.write("UPDATE observations SET created_at = ? WHERE content = 'c'", (_OLD_TS,))
        out = tmp_path / "export.json"
        export_database(src, out)
        src.connection.close()

        dest = open_writable(tmp_path / "dest.db")
        dest.entities.create("proj", [{"name": "feature/f", "entityType": "feature", "observations": ["b", "z"]}])
        dest.entities.vote("proj", "feature/f", 1)
        dest.entities.set_status("proj", "feature/f", "in-progress")

        summary = import_projects(dest, load_export(out), ["proj"], dry_run=False)

        feature = dest.reads.get_entity("proj", "feature/f")
        assert {o.content for o in feature.observations} == {"b", "c", "z"}
        assert feature.vote_score == 3
        assert feature.status == "in-progress"
        dest_obs_c_created = dest.connection.query_one("SELECT created_at FROM observations WHERE content = 'c'")[
            "created_at"
        ]
        assert dest_obs_c_created == _OLD_TS
        assert summary.entities_merged == 1
        assert summary.entities_new == 1
        assert summary.observations_new == 2
        assert summary.observations_duplicate == 1
        dest.connection.close()

    def test_merge_different_type_is_skipped_and_rest_proceeds(self, tmp_path: Path) -> None:
        src = _make_db(tmp_path / "src.db")
        out = tmp_path / "export.json"
        export_database(src, out)
        src.connection.close()

        dest = open_writable(tmp_path / "dest.db")
        dest.entities.create("proj", [{"name": "feature/f", "entityType": "task", "observations": ["orig"]}])

        summary = import_projects(dest, load_export(out), ["proj"], dry_run=False)

        assert summary.entities_skipped_type_mismatch == ["feature/f"]
        assert dest.reads.get_entity("proj", "feature/f").entity_type == "task"
        assert {o.content for o in dest.reads.get_entity("proj", "feature/f").observations} == {"orig"}
        assert dest.entities.exists_in("task/t", "proj")
        dest.connection.close()

    def test_relation_dedup_on_reimport(self, tmp_path: Path) -> None:
        src = _make_db(tmp_path / "src.db")
        out = tmp_path / "export.json"
        export_database(src, out)
        src.connection.close()

        dest = open_writable(tmp_path / "dest.db")
        first = import_projects(dest, load_export(out), ["proj"], dry_run=False)
        second = import_projects(dest, load_export(out), ["proj"], dry_run=False)

        assert first.relations_new == 1
        assert second.relations_new == 0
        assert second.relations_duplicate == 1
        rels = dest.reads.get_entity_with_relations("proj", "task/t")["relations"]
        assert len(rels) == 1
        dest.connection.close()

    def test_groups_imported_additively(self, tmp_path: Path) -> None:
        src = _make_db(tmp_path / "src.db")
        out = tmp_path / "export.json"
        export_database(src, out)
        src.connection.close()

        dest = open_writable(tmp_path / "dest.db")
        dest.projects.set_groups("proj", ["existing"])
        import_projects(dest, load_export(out), ["proj"], dry_run=False)

        assert {g for _, g in dest.projects.groups("proj")} == {"existing", "tooling"}
        dest.connection.close()

    def test_paths_not_written_but_noticed(self, tmp_path: Path) -> None:
        src = _make_db(tmp_path / "src.db")
        source_paths = src.projects.paths_for("proj")
        out = tmp_path / "export.json"
        export_database(src, out)
        src.connection.close()

        dest = open_writable(tmp_path / "dest.db")
        summary = import_projects(dest, load_export(out), ["proj"], dry_run=False)

        assert dest.projects.paths_for("proj") == []
        assert summary.path_notices["proj"] == source_paths
        assert source_paths[0] in summary.render()
        dest.connection.close()


class TestDryRun:
    def test_dry_run_matches_real_run_and_leaves_db_unchanged(self, tmp_path: Path) -> None:
        src = _make_db(tmp_path / "src.db")
        out = tmp_path / "export.json"
        export_database(src, out)
        src.connection.close()

        dry_db = open_writable(tmp_path / "dry.db")
        dry = import_projects(dry_db, load_export(out), ["proj"], dry_run=True)
        assert not dry_db.entities.exists_in("task/t", "proj")
        assert "proj" not in dry_db.projects.names()
        dry_db.connection.close()

        real_db = open_writable(tmp_path / "real.db")
        real = import_projects(real_db, load_export(out), ["proj"], dry_run=False)
        real_db.connection.close()

        assert dry.entities_new == real.entities_new
        assert dry.observations_new == real.observations_new
        assert dry.relations_new == real.relations_new
        assert dry.groups_added == real.groups_added
        assert dry.entities_new == 2
        assert dry.relations_new == 1


class TestSchemaGuard:
    def test_newer_schema_refused_with_no_writes(self, tmp_path: Path) -> None:
        src = _make_db(tmp_path / "src.db")
        out = tmp_path / "export.json"
        export_database(src, out)
        src.connection.close()

        data = json.loads(out.read_text(encoding="utf-8"))
        data["schema_version"] = max(m.version for m in MIGRATIONS) + 1
        out.write_text(json.dumps(data), encoding="utf-8")

        with pytest.raises(ValueError, match="newer than this build"):
            load_export(out)

    def test_older_schema_imports_fine(self, tmp_path: Path) -> None:
        src = _make_db(tmp_path / "src.db")
        out = tmp_path / "export.json"
        export_database(src, out)
        src.connection.close()

        data = json.loads(out.read_text(encoding="utf-8"))
        data["schema_version"] = 1
        out.write_text(json.dumps(data), encoding="utf-8")

        dest = open_writable(tmp_path / "dest.db")
        import_projects(dest, load_export(out), ["proj"], dry_run=False)
        assert dest.entities.exists_in("task/t", "proj")
        dest.connection.close()


class TestCli:
    def test_omitting_project_lists_and_calls_nothing(
        self, tmp_path: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
    ) -> None:
        out = tmp_path / "export.json"
        export_database(_make_db(tmp_path / "src.db"), out)

        cli._cmd_import(argparse.Namespace(input_path=str(out), project=None, dry_run=False))

        captured = capsys.readouterr().out
        assert "global" in captured
        assert "proj" in captured
        assert api.calls == []

    def test_export_writes_the_servers_document(
        self, tmp_path: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
    ) -> None:
        out = tmp_path / "export.json"
        api.results["export"] = {"format": EXPORT_FORMAT}
        cli._cmd_export(argparse.Namespace(output_path=str(out)))
        assert json.loads(out.read_text(encoding="utf-8")) == {"format": EXPORT_FORMAT}
        assert str(out) in capsys.readouterr().out

    def test_import_sends_the_file_and_prints_the_summary(
        self, tmp_path: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
    ) -> None:
        out = tmp_path / "export.json"
        export_database(_make_db(tmp_path / "src.db"), out)
        api.results["import"] = asdict(ImportSummary(entities_new=2, dry_run=True))

        cli._cmd_import(argparse.Namespace(input_path=str(out), project="proj, global", dry_run=True))

        ((op, args),) = api.calls
        assert (op, args["projects"], args["dry_run"], args["data"]) == (
            "import",
            ["proj", "global"],
            True,
            load_export(out),
        )
        assert "[dry run] Import summary" in capsys.readouterr().out

    def test_import_failure_exits_with_the_servers_message(
        self, tmp_path: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
    ) -> None:
        out = tmp_path / "export.json"
        export_database(_make_db(tmp_path / "src.db"), out)
        api.results["import"] = ApiError("Project(s) not found in export file: nope")

        with pytest.raises(SystemExit) as excinfo:
            cli._cmd_import(argparse.Namespace(input_path=str(out), project="nope", dry_run=False))

        assert excinfo.value.code == 1
        assert "Project(s) not found in export file: nope" in capsys.readouterr().err
