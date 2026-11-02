"""Tests for the server's generic /api/call route and its client."""

from __future__ import annotations

import io
import json
from typing import TYPE_CHECKING
from unittest.mock import patch
import urllib.error

import pytest

from mcp_memory import api_client, cli, server
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
