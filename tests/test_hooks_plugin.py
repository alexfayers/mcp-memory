"""Tests for the memory hooks plugin."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from mcp_memory.config import get_db_path
from mcp_memory.database import DatabaseManager
from mcp_memory.hooks.plugin import (
    _EDIT_TOOL_WEIGHT,
    MemoryPlugin,
    _find_project_from_path,
    _is_file_edit,
    _is_memory_read,
    _parse_mcp_arguments,
    _resolve_anchor,
    _safe_project,
    _workspace_entity_note,
)
from mcp_memory.path_resolver import normalize_path

_READ_TOOL_NAMES = [
    "search_nodes",
    "read_graph",
    "list_projects",
    "search_all_projects",
    "get_entity_with_relations",
    "search_related_nodes",
]

_EDIT_TOOL_NAMES = [
    "Edit",
    "Write",
    "MultiEdit",
    "NotebookEdit",
    "replace_in_file",
    "write_to_file",
]


class TestFindProjectFromPath:
    def test_finds_git_repo_from_file(self, tmp_path: Path) -> None:
        repo = tmp_path / "my-project"
        (repo / ".git").mkdir(parents=True)
        (repo / "src").mkdir()
        assert _find_project_from_path(str(repo / "src" / "foo.py")) == "my-project"

    def test_finds_git_repo_from_directory(self, tmp_path: Path) -> None:
        repo = tmp_path / "my-project"
        (repo / ".git").mkdir(parents=True)
        (repo / "src").mkdir()
        assert _find_project_from_path(str(repo / "src")) == "my-project"

    def test_finds_git_repo_at_root(self, tmp_path: Path) -> None:
        repo = tmp_path / "my-project"
        (repo / ".git").mkdir(parents=True)
        assert _find_project_from_path(str(repo)) == "my-project"

    def test_returns_none_when_no_git(self, tmp_path: Path) -> None:
        no_repo = tmp_path / "plain-dir"
        no_repo.mkdir()
        assert _find_project_from_path(str(no_repo)) is None

    def test_returns_none_for_nonexistent_path(self, tmp_path: Path) -> None:
        assert _find_project_from_path(str(tmp_path / "nonexistent" / "file.py")) is None

    def test_finds_nearest_git_repo(self, tmp_path: Path) -> None:
        outer = tmp_path / "outer"
        inner = outer / "inner"
        (outer / ".git").mkdir(parents=True)
        (inner / ".git").mkdir(parents=True)
        assert _find_project_from_path(str(inner / "file.py")) == "inner"


class TestResolveAnchor:
    def test_returns_git_root_when_no_marker_configured(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("MCP_MEMORY_WORKSPACE_MARKERS", raising=False)
        pkg = tmp_path / "workspace" / "src" / "PkgA"
        (pkg / ".git").mkdir(parents=True)
        assert _resolve_anchor(str(pkg / "lib" / "x.py")) == (pkg.resolve(), "PkgA")

    def test_collapses_to_workspace_root_when_marker_found(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MCP_MEMORY_WORKSPACE_MARKERS", ".marker")
        workspace = tmp_path / "workspace"
        (workspace / ".marker").mkdir(parents=True)
        pkg = workspace / "src" / "PkgA"
        (pkg / ".git").mkdir(parents=True)
        assert _resolve_anchor(str(pkg / "lib" / "x.py")) == (workspace.resolve(), "PkgA")

    def test_returns_none_when_no_git_root(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("MCP_MEMORY_WORKSPACE_MARKERS", raising=False)
        plain = tmp_path / "plain"
        plain.mkdir()
        assert _resolve_anchor(str(plain)) is None


class TestSafeProject:
    def test_reuses_project_already_mapped_under_anchor(self, tmp_path: Path) -> None:
        db = DatabaseManager(tmp_path / "memory.db")
        workspace = tmp_path / "workspace"
        (workspace / "src" / "PkgA").mkdir(parents=True)
        db.set_project_paths("platform", [str(workspace)])
        assert _safe_project(workspace, "PkgA", db) == "platform"

    def test_pins_to_existing_project_matching_basename(self, tmp_path: Path) -> None:
        db = DatabaseManager(tmp_path / "memory.db")
        db.create_entities(
            "PkgA", [{"name": "task/x", "entityType": "task", "observations": ["o"]}]
        )
        anchor = tmp_path / "PkgA"
        anchor.mkdir()
        assert _safe_project(anchor, "PkgA", db) == "PkgA"

    def test_reuses_project_already_mapped_below_anchor(self, tmp_path: Path) -> None:
        db = DatabaseManager(tmp_path / "memory.db")
        workspace = tmp_path / "workspace"
        (workspace / "src" / "PkgA").mkdir(parents=True)
        db.set_project_paths("platform", [str(workspace / "src" / "PkgA")])
        assert _safe_project(workspace, "workspace", db) == "platform"

    def test_returns_none_when_name_is_unknown(self, tmp_path: Path) -> None:
        db = DatabaseManager(tmp_path / "memory.db")
        anchor = tmp_path / "BrandNew"
        anchor.mkdir()
        assert _safe_project(anchor, "BrandNew", db) is None


@pytest.fixture(autouse=True)
def _isolate_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point every test at a throwaway database so none touches the real user memory DB."""
    monkeypatch.setenv("MCP_MEMORY_DB_PATH", str(tmp_path / "hooks-test.db"))


def _real_plugin() -> MemoryPlugin:
    """A MemoryPlugin with only the tracker side-effects patched (auto-register left live)."""
    with (
        patch("mcp_memory.hooks.plugin.clear"),
        patch("mcp_memory.hooks.plugin.reset"),
    ):
        return MemoryPlugin()


class TestAutoRegister:
    def _db(self) -> DatabaseManager:
        return DatabaseManager(get_db_path())

    def test_pins_to_existing_project_matching_basename(self, tmp_path: Path) -> None:
        db = self._db()
        db.create_entities(
            "acme", [{"name": "project/acme", "entityType": "project", "observations": ["root"]}]
        )
        repo = tmp_path / "acme"
        (repo / ".git").mkdir(parents=True)
        result = _real_plugin().on_hook("TaskStart", task_id="t1", workspace_roots=[str(repo)])
        assert self._db().get_project_for_path(str(repo)) == "acme"
        assert any("acme" in note for note in result.notes)

    def test_pins_via_existing_sibling_mapping(self, tmp_path: Path) -> None:
        db = self._db()
        repo = tmp_path / "repo"
        (repo / ".git").mkdir(parents=True)
        db.set_project_paths("platform", [str(repo / "sub")])
        _real_plugin().on_hook("TaskStart", task_id="t1", workspace_roots=[str(repo)])
        assert self._db().get_project_for_path(str(repo)) == "platform"

    def test_note_but_no_mint_when_unknown(self, tmp_path: Path) -> None:
        repo = tmp_path / "weird"
        (repo / ".git").mkdir(parents=True)
        result = _real_plugin().on_hook("TaskStart", task_id="t1", workspace_roots=[str(repo)])
        assert "weird" not in self._db().list_projects()
        assert any("set_project_paths" in note for note in result.notes)

    def test_no_clobber_when_already_mapped(self, tmp_path: Path) -> None:
        db = self._db()
        repo = tmp_path / "repo"
        (repo / ".git").mkdir(parents=True)
        db.set_project_paths("platform", [str(repo)])
        _real_plugin().on_hook("TaskStart", task_id="t1", workspace_roots=[str(repo)])
        assert self._db().get_paths_for_project("platform") == [normalize_path(str(repo))]

    def test_home_anchor_is_never_registered(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        home = tmp_path / "home"
        (home / ".git").mkdir(parents=True)
        monkeypatch.setattr(Path, "home", lambda: home)
        db = self._db()
        db.create_entities(
            "home", [{"name": "project/home", "entityType": "project", "observations": ["r"]}]
        )
        _real_plugin().on_hook("TaskStart", task_id="t1", workspace_roots=[str(home)])
        assert self._db().get_project_for_path(str(home)) is None

    def test_collapses_to_workspace_marker_root(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MCP_MEMORY_WORKSPACE_MARKERS", ".marker")
        workspace = tmp_path / "workspace"
        (workspace / ".marker").mkdir(parents=True)
        pkg = workspace / "src" / "PkgA"
        (pkg / ".git").mkdir(parents=True)
        db = self._db()
        db.create_entities(
            "PkgA", [{"name": "project/PkgA", "entityType": "project", "observations": ["r"]}]
        )
        _real_plugin().on_hook("TaskStart", task_id="t1", workspace_roots=[str(pkg)])
        assert self._db().get_project_for_path(str(workspace / "src" / "PkgB")) == "PkgA"

    def test_db_error_does_not_break_task_start(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = tmp_path / "acme"
        (repo / ".git").mkdir(parents=True)

        def _boom(_path: object) -> DatabaseManager:
            raise OSError("db unavailable")

        monkeypatch.setattr("mcp_memory.hooks.plugin.DatabaseManager", _boom)
        result = _real_plugin().on_hook("TaskStart", task_id="t1", workspace_roots=[str(repo)])
        assert result is not None


@pytest.fixture
def plugin() -> MemoryPlugin:
    """Create a fresh MemoryPlugin with tracker functions mocked out."""
    blocked_projects: set[str] = set()

    def _has_blocked(_task_id: str, project: str) -> bool:
        return project in blocked_projects

    def _mark_blocked(_task_id: str, project: str) -> None:
        blocked_projects.add(project)

    with (
        patch("mcp_memory.hooks.plugin.clear"),
        patch("mcp_memory.hooks.plugin.increment"),
        patch("mcp_memory.hooks.plugin.reset"),
        patch("mcp_memory.hooks.plugin.has_scope_blocked", side_effect=_has_blocked),
        patch("mcp_memory.hooks.plugin.mark_scope_blocked", side_effect=_mark_blocked),
        patch("mcp_memory.hooks.plugin.resolve_project_for_path", return_value=None),
        patch.object(MemoryPlugin, "_maybe_auto_register", return_value=None),
    ):
        yield MemoryPlugin()


class TestMemoryPluginScopeTracking:
    def test_initial_scope_is_unknown(self, plugin: MemoryPlugin) -> None:
        assert plugin._project_scope == "unknown"

    def test_task_start_sets_scope_from_git_repo(
        self, plugin: MemoryPlugin, tmp_path: Path
    ) -> None:
        repo = tmp_path / "test-repo"
        (repo / ".git").mkdir(parents=True)
        plugin.on_hook("TaskStart", task_id="t1", workspace_roots=[str(repo)])
        assert plugin._project_scope == "test-repo"

    def test_task_start_falls_back_to_dir_name(self, plugin: MemoryPlugin, tmp_path: Path) -> None:
        no_repo = tmp_path / "plain-workspace"
        no_repo.mkdir()
        plugin.on_hook("TaskStart", task_id="t1", workspace_roots=[str(no_repo)])
        assert plugin._project_scope == "plain-workspace"

    def test_post_tool_use_updates_scope_from_file_path(
        self, plugin: MemoryPlugin, tmp_path: Path
    ) -> None:
        repo_a = tmp_path / "repo-a"
        repo_b = tmp_path / "repo-b"
        (repo_a / ".git").mkdir(parents=True)
        (repo_b / ".git").mkdir(parents=True)

        plugin.on_hook("TaskStart", task_id="t1", workspace_roots=[str(repo_a)])
        assert plugin._project_scope == "repo-a"

        plugin.on_hook(
            "PostToolUse",
            task_id="t1",
            tool_name="write_to_file",
            parameters={"path": str(repo_b / "src" / "file.py")},
            is_state_write=False,
        )
        assert plugin._project_scope == "repo-b"

    def test_post_tool_use_updates_scope_from_claude_code_file_path(
        self, plugin: MemoryPlugin, tmp_path: Path
    ) -> None:
        repo_a = tmp_path / "repo-a"
        repo_b = tmp_path / "repo-b"
        (repo_a / ".git").mkdir(parents=True)
        (repo_b / ".git").mkdir(parents=True)

        plugin.on_hook("TaskStart", task_id="t1", workspace_roots=[str(repo_a)])
        plugin.on_hook(
            "PostToolUse",
            task_id="t1",
            tool_name="Edit",
            parameters={"file_path": str(repo_b / "src" / "file.py")},
            is_state_write=False,
        )
        assert plugin._project_scope == "repo-b"

    def test_post_tool_use_updates_scope_from_working_dir(
        self, plugin: MemoryPlugin, tmp_path: Path
    ) -> None:
        repo = tmp_path / "my-repo"
        (repo / ".git").mkdir(parents=True)

        plugin.on_hook("TaskStart", task_id="t1", workspace_roots=[str(tmp_path)])
        plugin.on_hook(
            "PostToolUse",
            task_id="t1",
            tool_name="execute_command",
            parameters={"working_dir": str(repo)},
            is_state_write=False,
        )
        assert plugin._project_scope == "my-repo"

    def test_scope_preserved_when_no_git_found(self, plugin: MemoryPlugin, tmp_path: Path) -> None:
        repo = tmp_path / "my-repo"
        (repo / ".git").mkdir(parents=True)
        no_git = tmp_path / "no-git"
        no_git.mkdir()

        plugin.on_hook("TaskStart", task_id="t1", workspace_roots=[str(repo)])
        plugin.on_hook(
            "PostToolUse",
            task_id="t1",
            tool_name="execute_command",
            parameters={"working_dir": str(no_git)},
            is_state_write=False,
        )
        assert plugin._project_scope == "my-repo"


class TestMemoryPluginMessages:
    def test_block_message_includes_project_scope(
        self, plugin: MemoryPlugin, tmp_path: Path
    ) -> None:
        repo = tmp_path / "my-project"
        (repo / ".git").mkdir(parents=True)
        plugin.on_hook("TaskStart", task_id="t1", workspace_roots=[str(repo)])

        with patch("mcp_memory.hooks.plugin.should_block", return_value=True):
            result = plugin.on_hook(
                "PreToolUse",
                task_id="t1",
                tool_name="read_file",
                parameters={},
            )
        assert result is not None
        assert result.block is not None
        assert "`my-project`" in result.block

    def test_reminder_message_includes_project_scope(
        self, plugin: MemoryPlugin, tmp_path: Path
    ) -> None:
        repo = tmp_path / "cool-project"
        (repo / ".git").mkdir(parents=True)
        plugin.on_hook("TaskStart", task_id="t1", workspace_roots=[str(repo)])

        plugin._reminder.chance = 1.0
        with patch("random.random", return_value=0.0):
            result = plugin.on_hook(
                "PostToolUse",
                task_id="t1",
                tool_name="write_to_file",
                parameters={"path": str(repo / "file.py")},
                is_state_write=False,
            )
        assert result is not None
        assert len(result.notes) == 1
        assert "`cool-project`" in result.notes[0]


class TestRegisteredPathResolution:
    def test_task_start_prefers_registered_project(
        self, plugin: MemoryPlugin, tmp_path: Path
    ) -> None:
        repo = tmp_path / "acme-service-infra"
        (repo / ".git").mkdir(parents=True)
        with patch("mcp_memory.hooks.plugin.resolve_project_for_path", return_value="platform"):
            plugin.on_hook("TaskStart", task_id="t1", workspace_roots=[str(repo)])
        assert plugin._project_scope == "platform"

    def test_update_from_parameters_uses_resolver(
        self, plugin: MemoryPlugin, tmp_path: Path
    ) -> None:
        repo = tmp_path / "acme-service-infra"
        (repo / ".git").mkdir(parents=True)
        with patch("mcp_memory.hooks.plugin.resolve_project_for_path", return_value="platform"):
            plugin.on_hook(
                "PostToolUse",
                task_id="t1",
                tool_name="write_to_file",
                parameters={"path": str(repo / "lib" / "x.ts")},
                is_state_write=False,
            )
        assert plugin._project_scope == "platform"

    def test_entity_note_signals_override_when_resolved_differs(self, tmp_path: Path) -> None:
        repo = tmp_path / "acme-service-infra"
        repo.mkdir()
        with patch("mcp_memory.hooks.plugin.resolve_project_for_path", return_value="platform"):
            note = _workspace_entity_note([str(repo)])
        assert note is not None
        assert "`project/platform`" in note
        assert "acme-service-infra" in note

    def test_entity_note_no_override_when_resolved_matches_basename(self, tmp_path: Path) -> None:
        repo = tmp_path / "myrepo"
        repo.mkdir()
        with patch("mcp_memory.hooks.plugin.resolve_project_for_path", return_value="myrepo"):
            note = _workspace_entity_note([str(repo)])
        assert note == "The project memory entity for this workspace is `project/myrepo`."


class TestParseMcpArguments:
    def test_parses_json_string_arguments(self) -> None:
        params: dict[str, object] = {
            "tool_name": "add_observations",
            "arguments": '{"project": "my-proj", "entityName": "foo"}',
        }
        result = _parse_mcp_arguments("use_mcp_tool", params)
        assert result["project"] == "my-proj"

    def test_parses_dict_arguments(self) -> None:
        params: dict[str, object] = {
            "tool_name": "add_observations",
            "arguments": {"project": "my-proj"},
        }
        result = _parse_mcp_arguments("use_mcp_tool", params)
        assert result["project"] == "my-proj"

    def test_returns_params_for_direct_call(self) -> None:
        params: dict[str, object] = {"project": "my-proj"}
        result = _parse_mcp_arguments("add_observations", params)
        assert result["project"] == "my-proj"

    def test_handles_invalid_json(self) -> None:
        params: dict[str, object] = {"arguments": "not json"}
        result = _parse_mcp_arguments("use_mcp_tool", params)
        assert result == {}


class TestMemoryPluginScopeValidation:
    def test_blocks_on_first_wrong_project_scope(
        self,
        plugin: MemoryPlugin,
        tmp_path: Path,
    ) -> None:
        repo = tmp_path / "my-repo"
        (repo / ".git").mkdir(parents=True)
        plugin.on_hook("TaskStart", task_id="t1", workspace_roots=[str(repo)])

        result = plugin.on_hook(
            "PreToolUse",
            task_id="t1",
            tool_name="use_mcp_tool",
            parameters={
                "tool_name": "add_observations",
                "arguments": '{"project": "wrong-project", "entityName": "foo"}',
            },
        )
        assert result is not None
        assert result.block is not None
        assert "`wrong-project`" in result.block
        assert "`my-repo`" in result.block

    def test_block_message_tells_agent_to_retry_if_intentional(
        self,
        plugin: MemoryPlugin,
        tmp_path: Path,
    ) -> None:
        repo = tmp_path / "my-repo"
        (repo / ".git").mkdir(parents=True)
        plugin.on_hook("TaskStart", task_id="t1", workspace_roots=[str(repo)])

        result = plugin.on_hook(
            "PreToolUse",
            task_id="t1",
            tool_name="use_mcp_tool",
            parameters={
                "tool_name": "add_observations",
                "arguments": '{"project": "wrong-project"}',
            },
        )
        assert result is not None
        assert result.block is not None
        assert "again" in result.block.lower()

    def test_warns_on_subsequent_wrong_project_scope(
        self,
        plugin: MemoryPlugin,
        tmp_path: Path,
    ) -> None:
        repo = tmp_path / "my-repo"
        (repo / ".git").mkdir(parents=True)
        plugin.on_hook("TaskStart", task_id="t1", workspace_roots=[str(repo)])

        plugin.on_hook(
            "PreToolUse",
            task_id="t1",
            tool_name="use_mcp_tool",
            parameters={
                "tool_name": "add_observations",
                "arguments": '{"project": "wrong-project"}',
            },
        )
        result = plugin.on_hook(
            "PreToolUse",
            task_id="t1",
            tool_name="use_mcp_tool",
            parameters={
                "tool_name": "add_observations",
                "arguments": '{"project": "wrong-project"}',
            },
        )
        assert result is not None
        assert result.block is None
        assert len(result.notes) == 1
        assert "`wrong-project`" in result.notes[0]

    def test_blocks_each_new_wrong_project_once(
        self,
        plugin: MemoryPlugin,
        tmp_path: Path,
    ) -> None:
        repo = tmp_path / "my-repo"
        (repo / ".git").mkdir(parents=True)
        plugin.on_hook("TaskStart", task_id="t1", workspace_roots=[str(repo)])

        result_a = plugin.on_hook(
            "PreToolUse",
            task_id="t1",
            tool_name="use_mcp_tool",
            parameters={
                "tool_name": "add_observations",
                "arguments": '{"project": "project-a"}',
            },
        )
        result_b = plugin.on_hook(
            "PreToolUse",
            task_id="t1",
            tool_name="use_mcp_tool",
            parameters={
                "tool_name": "add_observations",
                "arguments": '{"project": "project-b"}',
            },
        )
        assert result_a is not None
        assert result_a.block is not None
        assert result_b is not None
        assert result_b.block is not None

    def test_allows_correct_project_scope(
        self,
        plugin: MemoryPlugin,
        tmp_path: Path,
    ) -> None:
        repo = tmp_path / "my-repo"
        (repo / ".git").mkdir(parents=True)
        plugin.on_hook("TaskStart", task_id="t1", workspace_roots=[str(repo)])

        result = plugin.on_hook(
            "PreToolUse",
            task_id="t1",
            tool_name="use_mcp_tool",
            parameters={
                "tool_name": "add_observations",
                "arguments": '{"project": "my-repo"}',
            },
        )
        assert result is None

    def test_allows_global_scope(
        self,
        plugin: MemoryPlugin,
        tmp_path: Path,
    ) -> None:
        repo = tmp_path / "my-repo"
        (repo / ".git").mkdir(parents=True)
        plugin.on_hook("TaskStart", task_id="t1", workspace_roots=[str(repo)])

        result = plugin.on_hook(
            "PreToolUse",
            task_id="t1",
            tool_name="use_mcp_tool",
            parameters={
                "tool_name": "create_entities",
                "arguments": '{"project": "global"}',
            },
        )
        assert result is None

    def test_skips_validation_when_scope_unknown(
        self,
        plugin: MemoryPlugin,
    ) -> None:
        result = plugin.on_hook(
            "PreToolUse",
            task_id="t1",
            tool_name="use_mcp_tool",
            parameters={
                "tool_name": "add_observations",
                "arguments": '{"project": "anything"}',
            },
        )
        assert result is None


class TestDerivesScopeFromWorkspaceRoots:
    def test_pre_tool_use_derives_scope_from_workspace_roots(
        self,
        plugin: MemoryPlugin,
        tmp_path: Path,
    ) -> None:
        repo = tmp_path / "my-repo"
        (repo / ".git").mkdir(parents=True)

        plugin.on_hook(
            "PreToolUse",
            task_id="t1",
            tool_name="read_file",
            parameters={},
            workspace_roots=[str(repo)],
        )
        assert plugin._project_scope == "my-repo"

    def test_post_tool_use_derives_scope_from_workspace_roots(
        self,
        plugin: MemoryPlugin,
        tmp_path: Path,
    ) -> None:
        repo = tmp_path / "my-repo"
        (repo / ".git").mkdir(parents=True)

        plugin.on_hook(
            "PostToolUse",
            task_id="t1",
            tool_name="read_file",
            parameters={},
            is_state_write=False,
            workspace_roots=[str(repo)],
        )
        assert plugin._project_scope == "my-repo"

    def test_scope_warning_fires_with_workspace_roots(
        self,
        plugin: MemoryPlugin,
        tmp_path: Path,
    ) -> None:
        repo = tmp_path / "my-repo"
        (repo / ".git").mkdir(parents=True)

        result = plugin.on_hook(
            "PreToolUse",
            task_id="t1",
            tool_name="use_mcp_tool",
            parameters={
                "tool_name": "add_observations",
                "arguments": '{"project": "wrong-project"}',
            },
            workspace_roots=[str(repo)],
        )
        assert result is not None
        assert result.block is not None
        assert "`wrong-project`" in result.block
        assert "`my-repo`" in result.block

    def test_no_warning_when_workspace_roots_empty(
        self,
        plugin: MemoryPlugin,
    ) -> None:
        result = plugin.on_hook(
            "PreToolUse",
            task_id="t1",
            tool_name="use_mcp_tool",
            parameters={
                "tool_name": "add_observations",
                "arguments": '{"project": "anything"}',
            },
            workspace_roots=[],
        )
        assert result is None

    def test_does_not_override_existing_scope(
        self,
        plugin: MemoryPlugin,
        tmp_path: Path,
    ) -> None:
        repo_a = tmp_path / "repo-a"
        repo_b = tmp_path / "repo-b"
        (repo_a / ".git").mkdir(parents=True)
        (repo_b / ".git").mkdir(parents=True)

        plugin.on_hook("TaskStart", task_id="t1", workspace_roots=[str(repo_a)])
        assert plugin._project_scope == "repo-a"

        plugin.on_hook(
            "PreToolUse",
            task_id="t1",
            tool_name="read_file",
            parameters={},
            workspace_roots=[str(repo_b)],
        )
        assert plugin._project_scope == "repo-a"


class TestIsMemoryRead:
    @pytest.mark.parametrize("name", _READ_TOOL_NAMES)
    def test_bare_name_is_read(self, name: str) -> None:
        assert _is_memory_read(name, {})

    @pytest.mark.parametrize("name", _READ_TOOL_NAMES)
    def test_prefixed_claude_code_name_is_read(self, name: str) -> None:
        assert _is_memory_read(f"mcp__memory__{name}", {})

    @pytest.mark.parametrize("name", _READ_TOOL_NAMES)
    def test_use_mcp_tool_form_is_read(self, name: str) -> None:
        assert _is_memory_read("use_mcp_tool", {"tool_name": name})

    def test_write_tool_is_not_read(self) -> None:
        assert not _is_memory_read("mcp__memory__create_entities", {})
        assert not _is_memory_read("use_mcp_tool", {"tool_name": "add_observations"})

    def test_non_memory_tool_is_not_read(self) -> None:
        assert not _is_memory_read("read_file", {})


class TestIsFileEdit:
    @pytest.mark.parametrize("name", _EDIT_TOOL_NAMES)
    def test_bare_name_is_edit(self, name: str) -> None:
        assert _is_file_edit(name)

    @pytest.mark.parametrize("name", _EDIT_TOOL_NAMES)
    def test_prefixed_name_is_edit(self, name: str) -> None:
        assert _is_file_edit(f"mcp__memory__{name}")

    def test_read_tool_is_not_edit(self) -> None:
        assert not _is_file_edit("read_file")
        assert not _is_file_edit("execute_command")


class TestMemoryReadsNotGated:
    @pytest.mark.parametrize("name", _READ_TOOL_NAMES)
    def test_pre_tool_use_read_not_blocked(self, plugin: MemoryPlugin, name: str) -> None:
        with patch("mcp_memory.hooks.plugin.should_block", return_value=True):
            result = plugin.on_hook(
                "PreToolUse",
                task_id="t1",
                tool_name=f"mcp__memory__{name}",
                parameters={},
            )
        assert result is None

    @pytest.mark.parametrize("name", _READ_TOOL_NAMES)
    def test_pre_tool_use_read_via_use_mcp_tool_not_blocked(
        self, plugin: MemoryPlugin, name: str
    ) -> None:
        with patch("mcp_memory.hooks.plugin.should_block", return_value=True):
            result = plugin.on_hook(
                "PreToolUse",
                task_id="t1",
                tool_name="use_mcp_tool",
                parameters={"tool_name": name},
            )
        assert result is None

    @pytest.mark.parametrize("name", _READ_TOOL_NAMES)
    def test_pre_mcp_tool_use_read_not_blocked(self, plugin: MemoryPlugin, name: str) -> None:
        with patch("mcp_memory.hooks.plugin.should_block", return_value=True):
            result = plugin.on_hook(
                "PreMcpToolUse",
                task_id="t1",
                mcp_tool_name=name,
            )
        assert result is None

    def test_non_memory_tool_still_blocked(self, plugin: MemoryPlugin) -> None:
        with patch("mcp_memory.hooks.plugin.should_block", return_value=True):
            result = plugin.on_hook(
                "PreToolUse",
                task_id="t1",
                tool_name="read_file",
                parameters={},
            )
        assert result is not None
        assert result.block is not None


class TestReadOnlyAgentExemption:
    def test_explore_subagent_not_blocked(self, plugin: MemoryPlugin) -> None:
        with patch("mcp_memory.hooks.plugin.should_block", return_value=True):
            result = plugin.on_hook(
                "PreToolUse",
                task_id="t1",
                tool_name="read_file",
                parameters={},
                agent_type="Explore",
            )
        assert result is None

    def test_plan_subagent_not_blocked(self, plugin: MemoryPlugin) -> None:
        with patch("mcp_memory.hooks.plugin.should_block", return_value=True):
            result = plugin.on_hook(
                "PreToolUse",
                task_id="t1",
                tool_name="read_file",
                parameters={},
                agent_type="Plan",
            )
        assert result is None

    def test_main_thread_still_blocked(self, plugin: MemoryPlugin) -> None:
        with patch("mcp_memory.hooks.plugin.should_block", return_value=True):
            result = plugin.on_hook(
                "PreToolUse",
                task_id="t1",
                tool_name="read_file",
                parameters={},
                agent_type="",
            )
        assert result is not None
        assert result.block is not None

    def test_non_allowlisted_subagent_not_blocked(self, plugin: MemoryPlugin) -> None:
        # Any subagent (non-empty agent_type) is never hard-blocked: persistence is a
        # main-thread concern and blocking a subagent only deadlocks it. The block applies
        # to the main loop only.
        with patch("mcp_memory.hooks.plugin.should_block", return_value=True):
            result = plugin.on_hook(
                "PreToolUse",
                task_id="t1",
                tool_name="read_file",
                parameters={},
                agent_type="general-purpose",
            )
        assert result is None

    def test_non_allowlisted_subagent_write_still_scope_checked(
        self, plugin: MemoryPlugin, tmp_path: Path
    ) -> None:
        # Subagents skip the hard block but still get scope-mismatch protection on writes.
        repo = tmp_path / "my-repo"
        (repo / ".git").mkdir(parents=True)
        plugin.on_hook("TaskStart", task_id="t1", workspace_roots=[str(repo)])
        result = plugin.on_hook(
            "PreToolUse",
            task_id="t1",
            tool_name="mcp__memory__add_observations",
            parameters={"project": "wrong-project"},
            agent_type="general-purpose",
        )
        assert result is not None
        assert result.block is not None
        assert "`wrong-project`" in result.block

    def test_pre_mcp_tool_use_non_allowlisted_subagent_not_blocked(
        self, plugin: MemoryPlugin
    ) -> None:
        with patch("mcp_memory.hooks.plugin.should_block", return_value=True):
            result = plugin.on_hook(
                "PreMcpToolUse",
                task_id="t1",
                mcp_tool_name="read_file",
                agent_type="general-purpose",
            )
        assert result is None

    def test_post_tool_use_does_not_increment_for_non_allowlisted_subagent(self) -> None:
        with (
            patch("mcp_memory.hooks.plugin.clear"),
            patch("mcp_memory.hooks.plugin.reset"),
            patch("mcp_memory.hooks.plugin.increment") as mock_increment,
        ):
            plugin = MemoryPlugin()
            result = plugin.on_hook(
                "PostToolUse",
                task_id="t1",
                tool_name="read_file",
                parameters={},
                is_state_write=False,
                agent_type="general-purpose",
            )
        mock_increment.assert_not_called()
        assert result is None

    def test_pre_mcp_tool_use_exempt_for_explore(self, plugin: MemoryPlugin) -> None:
        with patch("mcp_memory.hooks.plugin.should_block", return_value=True):
            result = plugin.on_hook(
                "PreMcpToolUse",
                task_id="t1",
                mcp_tool_name="read_file",
                agent_type="Explore",
            )
        assert result is None

    def test_post_tool_use_does_not_increment_for_explore(self) -> None:
        with (
            patch("mcp_memory.hooks.plugin.clear"),
            patch("mcp_memory.hooks.plugin.reset"),
            patch("mcp_memory.hooks.plugin.increment") as mock_increment,
        ):
            plugin = MemoryPlugin()
            result = plugin.on_hook(
                "PostToolUse",
                task_id="t1",
                tool_name="read_file",
                parameters={},
                is_state_write=False,
                agent_type="Explore",
            )
        mock_increment.assert_not_called()
        assert result is None

    def test_env_var_extends_allowlist(
        self, plugin: MemoryPlugin, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MCP_MEMORY_READONLY_AGENTS", "code-reviewer, security-reviewer")
        with patch("mcp_memory.hooks.plugin.should_block", return_value=True):
            result = plugin.on_hook(
                "PreToolUse",
                task_id="t1",
                tool_name="read_file",
                parameters={},
                agent_type="security-reviewer",
            )
        assert result is None


class TestMemoryReviewNudge:
    def test_state_write_records_review_write(self) -> None:
        with (
            patch("mcp_memory.hooks.plugin.clear"),
            patch("mcp_memory.hooks.plugin.reset"),
            patch("mcp_memory.hooks.plugin.record_write") as mock_record,
        ):
            plugin = MemoryPlugin()
            plugin.on_hook(
                "PostToolUse",
                task_id="t1",
                tool_name="mcp__memory__add_observations",
                parameters={},
                is_state_write=True,
            )
        mock_record.assert_called_once_with()

    def test_user_prompt_emits_nudge_and_resets_when_due(self) -> None:
        with (
            patch("mcp_memory.hooks.plugin.should_nudge", return_value=True),
            patch("mcp_memory.hooks.plugin.reset_review") as mock_reset,
        ):
            plugin = MemoryPlugin()
            result = plugin.on_hook("UserPromptSubmit", task_id="t1")
        assert result is not None
        assert len(result.notes) == 1
        note = result.notes[0]
        assert "memory-review" in note
        assert "user" in note.lower()
        mock_reset.assert_called_once_with()

    def test_user_prompt_no_nudge_when_not_due(self) -> None:
        with (
            patch("mcp_memory.hooks.plugin.should_nudge", return_value=False),
            patch("mcp_memory.hooks.plugin.reset_review") as mock_reset,
        ):
            plugin = MemoryPlugin()
            result = plugin.on_hook("UserPromptSubmit", task_id="t1")
        assert result is None
        mock_reset.assert_not_called()


class TestMemoryReadsDoNotIncrement:
    @pytest.mark.parametrize("name", _READ_TOOL_NAMES)
    def test_post_tool_use_read_does_not_increment(self, name: str) -> None:
        with (
            patch("mcp_memory.hooks.plugin.clear"),
            patch("mcp_memory.hooks.plugin.reset"),
            patch("mcp_memory.hooks.plugin.increment") as mock_increment,
        ):
            plugin = MemoryPlugin()
            plugin.on_hook(
                "PostToolUse",
                task_id="t1",
                tool_name=f"mcp__memory__{name}",
                parameters={},
                is_state_write=False,
            )
        mock_increment.assert_not_called()

    def test_post_tool_use_non_memory_tool_increments(self) -> None:
        with (
            patch("mcp_memory.hooks.plugin.clear"),
            patch("mcp_memory.hooks.plugin.reset"),
            patch("mcp_memory.hooks.plugin.increment") as mock_increment,
        ):
            plugin = MemoryPlugin()
            plugin.on_hook(
                "PostToolUse",
                task_id="t1",
                tool_name="read_file",
                parameters={},
                is_state_write=False,
            )
        mock_increment.assert_called_once_with("t1")


class TestFileEditsReducedWeight:
    @pytest.mark.parametrize("name", _EDIT_TOOL_NAMES)
    def test_post_tool_use_edit_increments_by_weight(self, name: str) -> None:
        with (
            patch("mcp_memory.hooks.plugin.clear"),
            patch("mcp_memory.hooks.plugin.reset"),
            patch("mcp_memory.hooks.plugin.increment") as mock_increment,
        ):
            plugin = MemoryPlugin()
            plugin.on_hook(
                "PostToolUse",
                task_id="t1",
                tool_name=name,
                parameters={},
                is_state_write=False,
            )
        mock_increment.assert_called_once_with("t1", _EDIT_TOOL_WEIGHT)

    def test_post_tool_use_multiedit_updates_scope(
        self, plugin: MemoryPlugin, tmp_path: Path
    ) -> None:
        repo_a = tmp_path / "repo-a"
        repo_b = tmp_path / "repo-b"
        (repo_a / ".git").mkdir(parents=True)
        (repo_b / ".git").mkdir(parents=True)

        plugin.on_hook("TaskStart", task_id="t1", workspace_roots=[str(repo_a)])
        plugin.on_hook(
            "PostToolUse",
            task_id="t1",
            tool_name="MultiEdit",
            parameters={"file_path": str(repo_b / "src" / "file.py")},
            is_state_write=False,
        )
        assert plugin._project_scope == "repo-b"

    def test_pre_tool_use_edit_still_blockable(self, plugin: MemoryPlugin) -> None:
        with patch("mcp_memory.hooks.plugin.should_block", return_value=True):
            result = plugin.on_hook(
                "PreToolUse",
                task_id="t1",
                tool_name="Edit",
                parameters={},
            )
        assert result is not None
        assert result.block is not None

    def test_env_var_extends_edit_tools(
        self, plugin: MemoryPlugin, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MCP_MEMORY_EDIT_TOOLS", "apply_patch, str_replace")
        with (
            patch("mcp_memory.hooks.plugin.clear"),
            patch("mcp_memory.hooks.plugin.reset"),
            patch("mcp_memory.hooks.plugin.increment") as mock_increment,
        ):
            plugin.on_hook(
                "PostToolUse",
                task_id="t1",
                tool_name="apply_patch",
                parameters={},
                is_state_write=False,
            )
        mock_increment.assert_called_once_with("t1", _EDIT_TOOL_WEIGHT)
