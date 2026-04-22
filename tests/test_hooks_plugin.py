"""Tests for the memory hooks plugin."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from mcp_memory.hooks.plugin import (
    MemoryPlugin,
    _find_project_from_path,
    _parse_mcp_arguments,
)


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
