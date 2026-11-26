"""Tests for the server's generic /api/call route and its client."""

from __future__ import annotations

import io
import json
from typing import TYPE_CHECKING
from unittest.mock import patch
import urllib.error

import pytest

from mcp_memory import api_client, cli, dream, server
from mcp_memory.api_client import ApiError, call
from mcp_memory.path_resolver import normalize_path
from mcp_memory.storage import open_writable

if TYPE_CHECKING:
    from pathlib import Path

    from mcp_memory.storage import Storage


class TestClient:
    def test_unreachable_server_error_names_the_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MCP_MEMORY_URL", "http://example:4321/mcp")
        with (
            patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")),
            pytest.raises(ApiError, match=r"not reachable at http://example:4321$"),
        ):
            call("project-paths")

    def test_server_error_message_is_passed_on(self) -> None:
        error = urllib.error.HTTPError("u", 400, "Bad Request", {}, io.BytesIO(json.dumps({"error": "bad"}).encode()))  # type: ignore[arg-type]
        with patch("urllib.request.urlopen", side_effect=error), pytest.raises(ApiError, match=r"^bad$"):
            call("import")

    def test_cli_exits_with_the_error_when_the_server_is_down(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as excinfo:
            cli._api("metrics", {"since": None})
        assert excinfo.value.code == 1
        assert "Error: mcp-memory server is not reachable" in capsys.readouterr().err


@pytest.mark.usefixtures("live_server")
class TestRoute:
    def test_unknown_operation_and_missing_argument_are_rejected(self) -> None:
        with pytest.raises(ApiError, match="unknown operation"):
            api_client.call("nope")
        with pytest.raises(ApiError, match="anchor"):
            api_client.call("register-workspace", {"basename": "x"})

    @pytest.mark.parametrize(
        ("value", "backend", "expected"),
        [("true", "/usr/bin/pi", True), ("false", "/usr/bin/pi", False), ("true", None, False)],
    )
    def test_dream_heavy_reports_whether_the_heavy_tier_can_run(
        self, value: str, backend: str | None, expected: bool, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MCP_DREAM_HEAVY_ENABLED", value)
        monkeypatch.setattr(dream, "_pi_bin", lambda: backend)
        monkeypatch.setattr(dream, "_claude_bin", lambda: None)
        assert api_client.call("dream-heavy") is expected

    def test_registering_a_workspace_is_visible_in_project_paths(
        self, tmp_path: Path, store: Storage, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(server, "_db", store)
        store.entities.create("acme", [{"name": "project/acme", "entityType": "project", "observations": ["r"]}])
        anchor = str(tmp_path / "acme")

        assert api_client.call("register-workspace", {"anchor": anchor, "basename": "acme"}) == "acme"
        assert api_client.call("project-paths") == {"acme": [normalize_path(anchor)]}

    def test_export_then_import_round_trips_through_storage(
        self, store: Storage, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store.entities.create("proj", [{"name": "task/t", "entityType": "task", "observations": ["fact"]}])
        monkeypatch.setattr(server, "_db", store)
        document = api_client.call("export")

        dest = open_writable(tmp_path / "dest.db")
        monkeypatch.setattr(server, "_db", dest)
        args = {"data": document, "projects": ["proj"], "dry_run": False}
        assert api_client.call("import", args)["entities_new"] == 1
        assert dest.entities.exists_in("task/t", "proj")

    def test_surface_relevant_returns_each_strong_match_with_its_first_observation(
        self, store: Storage, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(server, "_db", store)
        fillers = [
            {"name": f"task/filler-{index}", "entityType": "task", "observations": ["chore"]} for index in range(20)
        ]
        target = {"name": "task/visualise-rebuild", "entityType": "task", "observations": ["Rebuild the visualiser."]}
        store.entities.create("proj", [*fillers, target])

        args = {"projects": ["proj"], "message": "visualise rebuild", "limit": 5}
        assert api_client.call("surface-relevant", args) == [
            {
                "project": "proj",
                "entity": "task/visualise-rebuild",
                "content": "Rebuild the visualiser.",
            }
        ]

    def test_recall_triggers_send_rows_only_when_the_etag_changes(
        self, store: Storage, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(server, "_db", store)
        store.entities.create("proj", [{"name": "task/t", "entityType": "task", "observations": ["fact"]}])
        store.observations.add("proj", "task/t", ["fact"], triggers=[{"kind": "mention", "value": "deploy"}])

        first = api_client.call("recall-triggers", {"etag": None})
        assert [row["value"] for row in first["rows"]] == ["deploy"]
        assert api_client.call("recall-triggers", {"etag": first["etag"]}) == {"etag": first["etag"]}
        store.observations.vote("proj", "task/t", 1, content="fact")
        assert api_client.call("recall-triggers", {"etag": first["etag"]})["rows"][0]["vote_score"] == 1
