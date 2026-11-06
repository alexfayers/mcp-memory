"""Tests for the memory hooks plugin."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import AbstractContextManager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
import socket
from socketserver import BaseRequestHandler, TCPServer
import subprocess
import sys
import threading
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

pytest.importorskip("cline_hooks")

from cline_hooks.core.plugin import HooksPlugin, collect_hook_results, hookimpl, load_plugins, plugins_override
from cline_hooks.core.vocabulary import BOOKKEEPING_TOOLS, FILE_EDIT_TOOLS, MESSAGE_TOOLS, SKILL_TOOLS
from cline_hooks.frontends.claude_code.protocol import ClaudeCodeProtocol
from cline_hooks.plugins.startup_blocks import RESERVE_CHARS, StartupBlocksPlugin

from mcp_memory import server
from mcp_memory.api import _recent_memory, _safe_project
from mcp_memory.hooks.plugin import (
    _DATE_PREFIX_WARNING,
    _EDIT_TOOL_WEIGHT,
    _FRUSTRATION_NUDGE_TEMPLATE,
    _MEMORY_COOLDOWN_STEPS,
    ENABLE_FRUSTRATION_CHECK,
    ENABLE_PROFANITY_CHECK,
    MemoryPlugin,
    _build_task_start_context,
    _find_project_from_path,
    _frustration_tier,
    _has_repeated_punct,
    _is_caps_shouting,
    _is_file_edit,
    _is_memory_read,
    _is_memory_server_reachable,
    _is_memory_write,
    _parse_mcp_arguments,
    _resolve_anchor,
    _resolved_project_set,
    _workspace_entity_note,
    resolve_project_for_path,
)
from mcp_memory.hooks.tracker import _MEMORY_BLOCK_THRESHOLD, get_scope, set_reminder_chance, set_scope
from mcp_memory.path_resolver import normalize_path
from mcp_memory.storage import open_writable
from mcp_memory.tool_names import READ_ONLY_TOOLS

if TYPE_CHECKING:
    from cline_hooks.core.plugin import HookResult

    from mcp_memory.storage import Storage
    from tests.conftest import FakeApi

_needs_profanity_check = pytest.mark.skipif(
    not ENABLE_PROFANITY_CHECK,
    reason="profanity detection is gated off by plugin.ENABLE_PROFANITY_CHECK",
)

_needs_frustration_check = pytest.mark.skipif(
    not ENABLE_FRUSTRATION_CHECK,
    reason="frustration nudge is gated off by plugin.ENABLE_FRUSTRATION_CHECK",
)

_READ_TOOL_NAMES = sorted(READ_ONLY_TOOLS)

_REGISTERED_READ_ONLY = {tool.name: tool.annotations.read_only_hint for tool in asyncio.run(server.mcp.list_tools())}

_EDIT_TOOL_NAMES = sorted(FILE_EDIT_TOOLS)

_FRUSTRATION_MARKER = _FRUSTRATION_NUDGE_TEMPLATE.split(" [", 1)[0]


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
    def test_returns_git_root_when_no_marker_configured(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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

    def test_returns_none_when_no_git_root(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MCP_MEMORY_WORKSPACE_MARKERS", raising=False)
        plain = tmp_path / "plain"
        plain.mkdir()
        assert _resolve_anchor(str(plain)) is None


def _marker_contributor(markers: frozenset[str]) -> HooksPlugin:
    class MarkerContributor(HooksPlugin):
        @hookimpl
        def memory_workspace_markers(self) -> frozenset[str]:
            return markers

    return MarkerContributor()


def _workspace_with_package(tmp_path: Path, *markers: str) -> tuple[Path, Path]:
    workspace = tmp_path / "workspace"
    for marker in markers:
        (workspace / marker).mkdir(parents=True)
    pkg = workspace / "src" / "PkgA"
    (pkg / ".git").mkdir(parents=True)
    return workspace.resolve(), pkg


class TestContributedWorkspaceMarkers:
    @pytest.mark.parametrize("marker", [".marker", ".workspace-root"])
    def test_env_var_and_contributed_markers_both_anchor(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, marker: str
    ) -> None:
        monkeypatch.setenv("MCP_MEMORY_WORKSPACE_MARKERS", ".marker")
        workspace, pkg = _workspace_with_package(tmp_path, marker)
        with plugins_override([_real_plugin(), _marker_contributor(frozenset({".workspace-root"}))]):
            assert _resolve_anchor(str(pkg / "lib" / "x.py")) == (workspace, "PkgA")


class TestSafeProject:
    def test_reuses_project_already_mapped_under_anchor(self, tmp_path: Path) -> None:
        db = open_writable(tmp_path / "memory.db")
        workspace = tmp_path / "workspace"
        (workspace / "src" / "PkgA").mkdir(parents=True)
        db.projects.set_paths("platform", [str(workspace)])
        assert _safe_project(workspace, "PkgA", db) == "platform"

    def test_pins_to_existing_project_matching_basename(self, tmp_path: Path) -> None:
        db = open_writable(tmp_path / "memory.db")
        db.entities.create("PkgA", [{"name": "task/x", "entityType": "task", "observations": ["o"]}])
        anchor = tmp_path / "PkgA"
        anchor.mkdir()
        assert _safe_project(anchor, "PkgA", db) == "PkgA"

    def test_reuses_project_already_mapped_below_anchor(self, tmp_path: Path) -> None:
        db = open_writable(tmp_path / "memory.db")
        workspace = tmp_path / "workspace"
        (workspace / "src" / "PkgA").mkdir(parents=True)
        db.projects.set_paths("platform", [str(workspace / "src" / "PkgA")])
        assert _safe_project(workspace, "workspace", db) == "platform"

    def test_returns_none_when_name_is_unknown(self, tmp_path: Path) -> None:
        db = open_writable(tmp_path / "memory.db")
        anchor = tmp_path / "BrandNew"
        anchor.mkdir()
        assert _safe_project(anchor, "BrandNew", db) is None


@pytest.fixture(autouse=True)
def _isolate_plugins() -> Iterator[None]:
    """Keep every test off real plugin entry-point discovery."""
    with plugins_override([]):
        yield


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
    def _task_start(self, workspace: Path) -> HookResult | None:
        return _real_plugin().on_hook("TaskStart", task_id="t1", workspace_roots=[str(workspace)])

    def test_registers_the_repo_root_under_the_project_the_server_names(self, tmp_path: Path, api: FakeApi) -> None:
        repo = tmp_path / "acme"
        (repo / ".git").mkdir(parents=True)
        api.results.update({"project-paths": {}, "register-workspace": "platform"})
        result = self._task_start(repo)
        assert ("register-workspace", {"anchor": str(repo), "basename": "acme"}) in api.calls
        assert result is not None
        assert any("platform" in note for note in result.notes)

    def test_note_but_no_mint_when_unknown(self, tmp_path: Path, api: FakeApi) -> None:
        repo = tmp_path / "weird"
        (repo / ".git").mkdir(parents=True)
        api.results.update({"project-paths": {}, "register-workspace": None})
        result = self._task_start(repo)
        assert result is not None
        assert any("set_metadata" in note and "add=" in note for note in result.notes)

    def test_no_register_when_already_mapped(self, tmp_path: Path, api: FakeApi) -> None:
        repo = tmp_path / "repo"
        (repo / ".git").mkdir(parents=True)
        api.results["project-paths"] = {"platform": [normalize_path(str(repo))]}
        self._task_start(repo)
        assert "register-workspace" not in [op for op, _ in api.calls]

    def test_home_anchor_is_never_registered(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, api: FakeApi
    ) -> None:
        home = tmp_path / "home"
        (home / ".git").mkdir(parents=True)
        monkeypatch.setattr(Path, "home", lambda: home)
        api.results["project-paths"] = {}
        self._task_start(home)
        assert "register-workspace" not in [op for op, _ in api.calls]

    def test_collapses_to_workspace_marker_root(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, api: FakeApi
    ) -> None:
        monkeypatch.setenv("MCP_MEMORY_WORKSPACE_MARKERS", ".marker")
        workspace = tmp_path / "workspace"
        (workspace / ".marker").mkdir(parents=True)
        pkg = workspace / "src" / "PkgA"
        (pkg / ".git").mkdir(parents=True)
        api.results.update({"project-paths": {}, "register-workspace": "PkgA"})
        self._task_start(pkg)
        assert ("register-workspace", {"anchor": str(workspace), "basename": "PkgA"}) in api.calls

    def test_server_error_does_not_break_task_start(self, tmp_path: Path) -> None:
        repo = tmp_path / "acme"
        (repo / ".git").mkdir(parents=True)
        assert self._task_start(repo) is not None


@pytest.fixture
def plugin() -> Iterator[MemoryPlugin]:
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


@pytest.fixture
def live_plugin() -> Iterator[MemoryPlugin]:
    """A MemoryPlugin on the real tracker with a started task and a reachable memory server."""
    with (
        patch.object(MemoryPlugin, "_maybe_auto_register", return_value=None),
        patch("mcp_memory.hooks.plugin._is_memory_server_reachable", return_value=True),
    ):
        plugin = MemoryPlugin()
        plugin.on_hook("TaskStart", task_id="t1", workspace_roots=[])
        yield plugin


def _registered_paths(mapping: dict[Path, str]) -> AbstractContextManager[object]:
    return patch(
        "mcp_memory.hooks.plugin.resolve_project_for_path",
        side_effect=lambda path: next(
            (name for root, name in mapping.items() if Path(path).is_relative_to(root)), None
        ),
    )


class TestMemoryPluginScopeTracking:
    def test_initial_scope_is_unknown(self, plugin: MemoryPlugin) -> None:
        assert get_scope("t1") is None

    def test_task_start_sets_scope_from_git_repo(self, plugin: MemoryPlugin, tmp_path: Path) -> None:
        repo = tmp_path / "test-repo"
        (repo / ".git").mkdir(parents=True)
        plugin.on_hook("TaskStart", task_id="t1", workspace_roots=[str(repo)])
        assert get_scope("t1") == "test-repo"

    def test_task_start_falls_back_to_dir_name(self, plugin: MemoryPlugin, tmp_path: Path) -> None:
        no_repo = tmp_path / "plain-workspace"
        no_repo.mkdir()
        plugin.on_hook("TaskStart", task_id="t1", workspace_roots=[str(no_repo)])
        assert get_scope("t1") == "plain-workspace"

    def test_post_tool_use_updates_scope_from_file_path(self, plugin: MemoryPlugin, tmp_path: Path) -> None:
        repo_a = tmp_path / "repo-a"
        repo_b = tmp_path / "repo-b"
        (repo_a / ".git").mkdir(parents=True)
        (repo_b / ".git").mkdir(parents=True)

        plugin.on_hook("TaskStart", task_id="t1", workspace_roots=[str(repo_a)])
        assert get_scope("t1") == "repo-a"

        with _registered_paths({repo_b: "repo-b"}):
            plugin.on_hook(
                "PostToolUse",
                task_id="t1",
                tool_name="write_to_file",
                parameters={"path": str(repo_b / "src" / "file.py")},
            )
        assert get_scope("t1") == "repo-b"

    def test_post_tool_use_updates_scope_from_read_path(self, plugin: MemoryPlugin, tmp_path: Path) -> None:
        repo_a = tmp_path / "repo-a"
        repo_b = tmp_path / "repo-b"
        (repo_a / ".git").mkdir(parents=True)
        (repo_b / ".git").mkdir(parents=True)

        plugin.on_hook("TaskStart", task_id="t1", workspace_roots=[str(repo_a)])
        with _registered_paths({repo_b: "repo-b"}):
            plugin.on_hook(
                "PostToolUse",
                task_id="t1",
                tool_name="read_file",
                parameters={"path": str(repo_b / "src" / "file.py")},
            )
        assert get_scope("t1") == "repo-b"

    def test_post_tool_use_updates_scope_from_working_dir(self, plugin: MemoryPlugin, tmp_path: Path) -> None:
        repo = tmp_path / "my-repo"
        (repo / ".git").mkdir(parents=True)

        plugin.on_hook("TaskStart", task_id="t1", workspace_roots=[str(tmp_path)])
        with _registered_paths({repo: "my-repo"}):
            plugin.on_hook(
                "PostToolUse",
                task_id="t1",
                tool_name="execute_command",
                parameters={"working_dir": str(repo)},
            )
        assert get_scope("t1") == "my-repo"

    def test_scope_preserved_when_unregistered_repo_entered(self, plugin: MemoryPlugin, tmp_path: Path) -> None:
        repo = tmp_path / "my-repo"
        other = tmp_path / "other-repo"
        (repo / ".git").mkdir(parents=True)
        (other / ".git").mkdir(parents=True)

        plugin.on_hook("TaskStart", task_id="t1", workspace_roots=[str(repo)])
        plugin.on_hook(
            "PostToolUse",
            task_id="t1",
            tool_name="execute_command",
            parameters={"working_dir": str(other)},
        )
        assert get_scope("t1") == "my-repo"

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
        )
        assert get_scope("t1") == "my-repo"

    def test_task_start_under_home_repo_falls_back_to_dir_name(
        self, plugin: MemoryPlugin, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        home = tmp_path / "home"
        (home / ".git").mkdir(parents=True)
        (home / "sub").mkdir()
        monkeypatch.setattr(Path, "home", lambda: home)

        plugin.on_hook("TaskStart", task_id="t1", workspace_roots=[str(home / "sub")])
        assert get_scope("t1") == "sub"


class TestSessionScopeIsolation:
    def test_scope_isolated_between_sessions(self, plugin: MemoryPlugin, tmp_path: Path) -> None:
        repo_a = tmp_path / "repo-a"
        repo_b = tmp_path / "repo-b"
        (repo_a / ".git").mkdir(parents=True)
        (repo_b / ".git").mkdir(parents=True)
        plugin.on_hook("TaskStart", task_id="t1", workspace_roots=[str(repo_a)])
        plugin.on_hook("TaskStart", task_id="t2", workspace_roots=[str(repo_b)])

        result = plugin.on_hook(
            "PreToolUse",
            task_id="t1",
            tool_name="use_mcp_tool",
            parameters={
                "tool_name": "create_entities",
                "arguments": '{"project": "repo-a"}',
            },
        )
        assert result is None

    def test_file_path_scope_does_not_leak_to_other_session(self, plugin: MemoryPlugin, tmp_path: Path) -> None:
        repo_a = tmp_path / "repo-a"
        repo_b = tmp_path / "repo-b"
        (repo_a / ".git").mkdir(parents=True)
        (repo_b / ".git").mkdir(parents=True)
        plugin.on_hook("TaskStart", task_id="t1", workspace_roots=[str(repo_a)])
        plugin.on_hook("TaskStart", task_id="t2", workspace_roots=[str(repo_a)])
        plugin.on_hook(
            "PostToolUse",
            task_id="t1",
            tool_name="replace_in_file",
            parameters={"path": str(repo_b / "src" / "file.py")},
        )

        result = plugin.on_hook(
            "PreToolUse",
            task_id="t2",
            tool_name="use_mcp_tool",
            parameters={
                "tool_name": "create_entities",
                "arguments": '{"project": "repo-a"}',
            },
        )
        assert result is None

    def test_scope_survives_new_plugin_instance(self, plugin: MemoryPlugin, tmp_path: Path) -> None:
        repo_a = tmp_path / "repo-a"
        (repo_a / ".git").mkdir(parents=True)
        plugin.on_hook("TaskStart", task_id="t1", workspace_roots=[str(repo_a)])

        result = MemoryPlugin().on_hook(
            "PreToolUse",
            task_id="t1",
            tool_name="use_mcp_tool",
            parameters={
                "tool_name": "create_entities",
                "arguments": '{"project": "other"}',
            },
        )
        assert result is not None
        assert result.block is not None
        assert "`repo-a`" in result.block

    def test_pre_mcp_uses_session_scope_without_workspace_roots(self, plugin: MemoryPlugin, tmp_path: Path) -> None:
        repo_a = tmp_path / "repo-a"
        (repo_a / ".git").mkdir(parents=True)
        plugin.on_hook("TaskStart", task_id="t1", workspace_roots=[str(repo_a)])

        result = plugin.on_hook(
            "PreMcpToolUse",
            task_id="t1",
            mcp_tool_name="create_entities",
            mcp_arguments='{"project": "other"}',
        )
        assert result is not None
        assert result.block is not None
        assert "`repo-a`" in result.block


class TestMemoryPluginMessages:
    def test_block_message_includes_project_scope(self, plugin: MemoryPlugin, tmp_path: Path) -> None:
        repo = tmp_path / "my-project"
        (repo / ".git").mkdir(parents=True)
        plugin.on_hook("TaskStart", task_id="t1", workspace_roots=[str(repo)])

        with (
            patch("mcp_memory.hooks.plugin.should_block", return_value=True),
            patch("mcp_memory.hooks.plugin._is_memory_server_reachable", return_value=True),
        ):
            result = plugin.on_hook(
                "PreToolUse",
                task_id="t1",
                tool_name="execute_command",
                parameters={},
            )
        assert result is not None
        assert result.block is not None
        assert "`my-project`" in result.block

    def test_reminder_message_includes_project_scope(self, plugin: MemoryPlugin, tmp_path: Path) -> None:
        repo = tmp_path / "cool-project"
        (repo / ".git").mkdir(parents=True)
        plugin.on_hook("TaskStart", task_id="t1", workspace_roots=[str(repo)])

        set_reminder_chance("t1", 1.0)
        with patch("random.random", return_value=0.0):
            result = plugin.on_hook(
                "PostToolUse",
                task_id="t1",
                tool_name="write_to_file",
                parameters={"path": str(repo / "file.py")},
            )
        assert result is not None
        assert len(result.notes) == 1
        assert "`cool-project`" in result.notes[0]

    def test_reminder_cooldown_is_per_session(self, plugin: MemoryPlugin, tmp_path: Path) -> None:
        repo = tmp_path / "cool-project"
        (repo / ".git").mkdir(parents=True)
        plugin.on_hook("TaskStart", task_id="t1", workspace_roots=[str(repo)])
        plugin.on_hook("TaskStart", task_id="t2", workspace_roots=[str(repo)])

        def _edit(task_id: str) -> object:
            return plugin.on_hook(
                "PostToolUse",
                task_id=task_id,
                tool_name="write_to_file",
                parameters={"path": str(repo / "file.py")},
            )

        with patch("random.random", return_value=1.0):
            for _ in range(_MEMORY_COOLDOWN_STEPS):
                _edit("t2")
        with patch("random.random", return_value=0.0):
            assert _edit("t1") is not None
        with patch("random.random", return_value=0.5):
            assert _edit("t2") is not None

    def test_reminder_and_block_tell_the_model_to_edit_a_replaced_observation(self, plugin: MemoryPlugin) -> None:
        with (
            patch("mcp_memory.hooks.plugin.should_block", return_value=True),
            patch("mcp_memory.hooks.plugin.should_warn", return_value=True),
            patch("mcp_memory.hooks.plugin._is_memory_server_reachable", return_value=True),
        ):
            blocked = plugin.on_hook("PreToolUse", task_id="t1", tool_name="execute_command", parameters={})
            reminded = plugin.on_hook("PostToolUse", task_id="t1", tool_name="execute_command", parameters={})
        assert blocked is not None
        assert reminded is not None
        assert "edit_observation" in (blocked.block or "")
        assert "edit_observation" in reminded.notes[0]
        assert "set the task's status" in (blocked.block or "")
        assert "Don't record what you changed" in reminded.notes[0]

    def test_completion_reminder_routes_facts_and_limits_the_outcome_line(self, plugin: MemoryPlugin) -> None:
        result = plugin.on_hook("AttemptCompletion", task_id="t1")
        assert result is not None
        note = result.notes[0]
        assert "`feature/`" in note
        assert "`pattern/`" in note
        assert "own `planned` `task/`" in note
        assert "git can't show" in note
        assert "no outcome line" in note

    def test_set_entity_status_clears_the_block(self, plugin: MemoryPlugin) -> None:
        with (
            patch("mcp_memory.hooks.plugin.should_block", return_value=True),
            patch("mcp_memory.hooks.plugin._is_memory_server_reachable", return_value=True),
        ):
            result = plugin.on_hook(
                "PreToolUse",
                task_id="t1",
                tool_name="use_mcp_tool",
                parameters={"tool_name": "set_entity_status", "arguments": "{}"},
            )
        assert result is None or result.block is None

    @pytest.mark.parametrize("warn", [False, True])
    def test_reminder_fires_for_any_tool_type_once_the_soft_threshold_is_reached(
        self, plugin: MemoryPlugin, warn: bool
    ) -> None:
        with patch("mcp_memory.hooks.plugin.should_warn", return_value=warn), patch("random.random", return_value=1.0):
            result = plugin.on_hook("PostToolUse", task_id="t1", tool_name="web_fetch", parameters={})
        assert (result is not None) is warn


class TestMemoryPluginServerDownGating:
    def test_blocks_when_server_reachable_and_over_threshold(self, plugin: MemoryPlugin) -> None:
        with (
            patch("mcp_memory.hooks.plugin.should_block", return_value=True),
            patch("mcp_memory.hooks.plugin._is_memory_server_reachable", return_value=True),
        ):
            result = plugin.on_hook(
                "PreToolUse",
                task_id="t1",
                tool_name="execute_command",
                parameters={},
            )
        assert result is not None
        assert result.block is not None

    def test_does_not_block_when_server_unreachable(self, plugin: MemoryPlugin) -> None:
        with (
            patch("mcp_memory.hooks.plugin.should_block", return_value=True),
            patch("mcp_memory.hooks.plugin._is_memory_server_reachable", return_value=False),
        ):
            result = plugin.on_hook(
                "PreToolUse",
                task_id="t1",
                tool_name="execute_command",
                parameters={},
            )
        assert result is not None
        assert result.block is None
        assert len(result.notes) == 1
        assert "unavailable" in result.notes[0].lower()

    def test_no_reachability_check_when_below_threshold(self, plugin: MemoryPlugin) -> None:
        with (
            patch("mcp_memory.hooks.plugin.should_block", return_value=False),
            patch("mcp_memory.hooks.plugin._is_memory_server_reachable") as mock_reachable,
        ):
            result = plugin.on_hook(
                "PreToolUse",
                task_id="t1",
                tool_name="execute_command",
                parameters={},
            )
        assert result is None
        mock_reachable.assert_not_called()


class TestRegisteredPathResolution:
    def test_task_start_prefers_registered_project(self, plugin: MemoryPlugin, tmp_path: Path) -> None:
        repo = tmp_path / "acme-service-infra"
        (repo / ".git").mkdir(parents=True)
        with patch("mcp_memory.hooks.plugin.resolve_project_for_path", return_value="platform"):
            plugin.on_hook("TaskStart", task_id="t1", workspace_roots=[str(repo)])
        assert get_scope("t1") == "platform"

    def test_update_from_parameters_uses_resolver(self, plugin: MemoryPlugin, tmp_path: Path) -> None:
        repo = tmp_path / "acme-service-infra"
        (repo / ".git").mkdir(parents=True)
        with patch("mcp_memory.hooks.plugin.resolve_project_for_path", return_value="platform"):
            plugin.on_hook(
                "PostToolUse",
                task_id="t1",
                tool_name="write_to_file",
                parameters={"path": str(repo / "lib" / "x.ts")},
            )
        assert get_scope("t1") == "platform"

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


class TestResolveProjectForPath:
    def test_matches_registered_paths_from_the_server(self, tmp_path: Path, api: FakeApi) -> None:
        repo = tmp_path / "acme-service-infra"
        api.results["project-paths"] = {"platform": [normalize_path(str(repo))]}
        assert resolve_project_for_path(str(repo / "lib" / "stack.ts")) == "platform"
        assert resolve_project_for_path(str(tmp_path / "elsewhere")) is None

    def test_server_error_returns_none(self) -> None:
        assert resolve_project_for_path("/anything") is None


class TestResolvedProjectSet:
    def test_empty_workspace_roots_returns_global_only(self) -> None:
        assert _resolved_project_set([]) == ["global"]

    def test_group_siblings_follow_global_and_repo(self, tmp_path: Path, api: FakeApi) -> None:
        api.results["group-members"] = ["cline-hooks", "other"]
        repo = tmp_path / "llm-prompts"
        repo.mkdir()
        assert _resolved_project_set([str(repo)]) == ["global", "llm-prompts", "cline-hooks", "other"]
        assert ("group-members", {"project": "llm-prompts"}) in api.calls

    def test_server_error_falls_back_to_global_and_repo(self, tmp_path: Path) -> None:
        repo = tmp_path / "acme"
        repo.mkdir()
        assert _resolved_project_set([str(repo)]) == ["global", "acme"]


def _task_start_notes(*plugins: HooksPlugin, roots: list[str], **kwargs: object) -> list[str]:
    with (
        plugins_override([_real_plugin(), *plugins]),
        patch("cline_hooks.core.protocol._active_protocol", ClaudeCodeProtocol()),
    ):
        return collect_hook_results(load_plugins(), "TaskStart", task_id="t1", workspace_roots=roots, **kwargs).notes


class TestRecentMemoryStartupBlock:
    @pytest.fixture(autouse=True)
    def _serve_recent_memory(self, api: FakeApi, store: Storage) -> None:
        api.results["recent-memory"] = lambda args: _recent_memory(store, args["project"], args["left"])

    def test_lists_recent_entities_of_global_and_repo(self, tmp_path: Path, store: Storage) -> None:
        db = store
        db.entities.create("global", [{"name": "p/one", "entityType": "pattern", "observations": ["first fact"]}])
        db.entities.create("acme", [{"name": "task/two", "entityType": "task", "observations": ["second fact"]}])
        notes = _task_start_notes(StartupBlocksPlugin(), roots=[str(tmp_path / "acme")])
        assert any("- p/one (pattern): first fact\n\n## acme\n- task/two (task): second" in n for n in notes)

    def test_budget_keeps_names(self, tmp_path: Path, store: Storage) -> None:
        names = [f"task/{number}" for number in range(5)]
        entities = [{"name": name, "entityType": "task", "observations": ["x" * 70]} for name in names]
        store.entities.create("acme", entities)
        spec = replace(ClaudeCodeProtocol.frontend_spec, max_context_chars=RESERVE_CHARS + 400)
        with patch.object(ClaudeCodeProtocol, "frontend_spec", spec):
            notes = _task_start_notes(StartupBlocksPlugin(), roots=[str(tmp_path / "acme")])
        (note,) = (n for n in notes if n.startswith("Startup block"))
        assert all(f"- {name} (task)" in note for name in names)
        assert note.count("x" * 70) < len(names)

    def test_no_workspace_gives_no_block(self) -> None:
        assert not any(note.startswith("Startup block") for note in _task_start_notes(StartupBlocksPlugin(), roots=[]))


class TestBuildTaskStartContext:
    def test_includes_workspace_entity_note(self, tmp_path: Path) -> None:
        repo = tmp_path / "acme"
        repo.mkdir()
        parts = _build_task_start_context([str(repo)])
        assert any("project/acme" in part for part in parts)

    def test_includes_numbered_instructions(self, tmp_path: Path) -> None:
        repo = tmp_path / "acme"
        repo.mkdir()
        parts = _build_task_start_context([str(repo)])
        instructions = next(part for part in parts if "Session-start guidance" in part)
        assert instructions.startswith("Session-start guidance:\n")
        for step in ("1. ", "2. ", "3. ", "4. "):
            assert step in instructions

    def test_task_scan_step_is_conditional_on_outstanding_work_ask(self, tmp_path: Path) -> None:
        repo = tmp_path / "acme"
        repo.mkdir()
        parts = _build_task_start_context([str(repo)])
        instructions = next(part for part in parts if "Session-start guidance" in part)
        assert "REQUIRED before starting" not in instructions
        assert "Only if the opening message asks about outstanding work" in instructions

    def test_project_list_reflects_resolved_scopes(self, tmp_path: Path, api: FakeApi) -> None:
        api.results["group-members"] = ["cline-hooks"]
        repo = tmp_path / "llm-prompts"
        repo.mkdir()
        parts = _build_task_start_context([str(repo)])
        instructions = next(part for part in parts if "Session-start guidance" in part)
        assert "`global`, `llm-prompts`, `cline-hooks`" in instructions


class TestIsMemoryServerReachable:
    def test_true_when_listening(self, monkeypatch: pytest.MonkeyPatch) -> None:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        monkeypatch.setattr(
            "mcp_memory.hooks.plugin.get_memory_url",
            lambda: f"http://127.0.0.1:{port}/mcp",
        )
        try:
            assert _is_memory_server_reachable() is True
        finally:
            listener.close()

    def test_false_when_nothing_listening(self, monkeypatch: pytest.MonkeyPatch) -> None:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        listener.close()
        monkeypatch.setattr(
            "mcp_memory.hooks.plugin.get_memory_url",
            lambda: f"http://127.0.0.1:{port}/mcp",
        )
        assert _is_memory_server_reachable() is False


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


class TestMemoryPluginDatePrefixNudge:
    def test_flags_today_prefixed_observation_in_add_observations(
        self,
        plugin: MemoryPlugin,
    ) -> None:
        today = datetime.now(tz=UTC).strftime("%Y-%m-%d")
        result = plugin.on_hook(
            "PreToolUse",
            task_id="t1",
            tool_name="add_observations",
            parameters={
                "project": "global",
                "entityName": "foo",
                "observations": [f"{today}: fact"],
            },
        )
        assert result is not None
        assert any("REDUNDANT DATE PREFIX" in note for note in result.notes)

    def test_flags_today_prefixed_new_text_in_edit_observation(
        self,
        plugin: MemoryPlugin,
    ) -> None:
        today = datetime.now(tz=UTC).strftime("%Y-%m-%d")
        result = plugin.on_hook(
            "PreToolUse",
            task_id="t1",
            tool_name="edit_observation",
            parameters={
                "project": "global",
                "entityName": "foo",
                "observationHash": "abcd1234",
                "newText": f"{today}: fact",
            },
        )
        assert result is not None
        assert any("REDUNDANT DATE PREFIX" in note for note in result.notes)

    def test_does_not_flag_yesterday_prefixed_observation(
        self,
        plugin: MemoryPlugin,
    ) -> None:
        yesterday = (datetime.now(tz=UTC) - timedelta(days=1)).strftime("%Y-%m-%d")
        result = plugin.on_hook(
            "PreToolUse",
            task_id="t1",
            tool_name="add_observations",
            parameters={
                "project": "global",
                "entityName": "foo",
                "observations": [f"{yesterday}: fact"],
            },
        )
        assert result is None

    def test_does_not_flag_date_without_separator(
        self,
        plugin: MemoryPlugin,
    ) -> None:
        today = datetime.now(tz=UTC).strftime("%Y-%m-%d")
        result = plugin.on_hook(
            "PreToolUse",
            task_id="t1",
            tool_name="add_observations",
            parameters={
                "project": "global",
                "entityName": "foo",
                "observations": [f"{today} fact"],
            },
        )
        assert result is None

    def test_flags_today_prefixed_observation_in_nested_create_entities(
        self,
        plugin: MemoryPlugin,
    ) -> None:
        today = datetime.now(tz=UTC).strftime("%Y-%m-%d")
        result = plugin.on_hook(
            "PreToolUse",
            task_id="t1",
            tool_name="create_entities",
            parameters={
                "project": "global",
                "entities": [
                    {
                        "name": "foo",
                        "entityType": "note",
                        "observations": [f"{today}: fact"],
                    }
                ],
            },
        )
        assert result is not None
        assert any("REDUNDANT DATE PREFIX" in note for note in result.notes)

    def test_flags_today_prefixed_observation_via_use_mcp_tool_json_string(
        self,
        plugin: MemoryPlugin,
    ) -> None:
        today = datetime.now(tz=UTC).strftime("%Y-%m-%d")
        result = plugin.on_hook(
            "PreToolUse",
            task_id="t1",
            tool_name="use_mcp_tool",
            parameters={
                "tool_name": "add_observations",
                "arguments": json.dumps({
                    "project": "global",
                    "entityName": "foo",
                    "observations": [f"{today}: fact"],
                }),
            },
        )
        assert result is not None
        assert any("REDUNDANT DATE PREFIX" in note for note in result.notes)

    def test_never_flags_merge_observations(
        self,
        plugin: MemoryPlugin,
    ) -> None:
        today = datetime.now(tz=UTC).strftime("%Y-%m-%d")
        result = plugin.on_hook(
            "PreToolUse",
            task_id="t1",
            tool_name="merge_observations",
            parameters={
                "project": "global",
                "entityName": "foo",
                "sourceHash": f"{today}: fact",
                "targetHash": "abcd1234",
            },
        )
        assert result is None

    def test_never_flags_delete_observations(
        self,
        plugin: MemoryPlugin,
    ) -> None:
        today = datetime.now(tz=UTC).strftime("%Y-%m-%d")
        result = plugin.on_hook(
            "PreToolUse",
            task_id="t1",
            tool_name="delete_observations",
            parameters={
                "project": "global",
                "entityName": "foo",
                "observations": [f"{today}: fact"],
            },
        )
        assert result is None

    def test_message_renders_count_and_first_example(
        self,
        plugin: MemoryPlugin,
    ) -> None:
        today = datetime.now(tz=UTC).strftime("%Y-%m-%d")
        result = plugin.on_hook(
            "PreToolUse",
            task_id="t1",
            tool_name="add_observations",
            parameters={
                "project": "global",
                "entityName": "foo",
                "observations": [f"{today}: first fact", f"{today}: second fact"],
            },
        )
        assert result is not None
        assert len(result.notes) == 1
        note = result.notes[0]
        assert "2 observation(s)" in note
        assert f"{today}: first fact" in note

    def test_combines_repeated_scope_warning_with_date_prefix_note(
        self,
        plugin: MemoryPlugin,
        tmp_path: Path,
    ) -> None:
        today = datetime.now(tz=UTC).strftime("%Y-%m-%d")
        repo = tmp_path / "my-repo"
        (repo / ".git").mkdir(parents=True)
        plugin.on_hook("TaskStart", task_id="t1", workspace_roots=[str(repo)])

        plugin.on_hook(
            "PreToolUse",
            task_id="t1",
            tool_name="use_mcp_tool",
            parameters={
                "tool_name": "add_observations",
                "arguments": '{"project": "wrong-project", "entityName": "foo", "observations": ["irrelevant"]}',
            },
        )
        result = plugin.on_hook(
            "PreToolUse",
            task_id="t1",
            tool_name="use_mcp_tool",
            parameters={
                "tool_name": "add_observations",
                "arguments": json.dumps({
                    "project": "wrong-project",
                    "entityName": "foo",
                    "observations": [f"{today}: fact"],
                }),
            },
        )
        assert result is not None
        assert result.block is None
        assert len(result.notes) == 2
        assert any("`wrong-project`" in note for note in result.notes)
        assert any("REDUNDANT DATE PREFIX" in note for note in result.notes)

    def test_block_precedence_preserved_with_date_prefix_present(
        self,
        plugin: MemoryPlugin,
        tmp_path: Path,
    ) -> None:
        today = datetime.now(tz=UTC).strftime("%Y-%m-%d")
        repo = tmp_path / "my-repo"
        (repo / ".git").mkdir(parents=True)
        plugin.on_hook("TaskStart", task_id="t1", workspace_roots=[str(repo)])

        result = plugin.on_hook(
            "PreToolUse",
            task_id="t1",
            tool_name="use_mcp_tool",
            parameters={
                "tool_name": "add_observations",
                "arguments": json.dumps({
                    "project": "wrong-project",
                    "entityName": "foo",
                    "observations": [f"{today}: fact"],
                }),
            },
        )
        assert result is not None
        assert result.block is not None
        assert "`wrong-project`" in result.block
        assert result.notes == [_DATE_PREFIX_WARNING.format(count=1, example=f"{today}: fact")]


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
        assert get_scope("t1") == "my-repo"

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
            workspace_roots=[str(repo)],
        )
        assert get_scope("t1") == "my-repo"

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
        assert get_scope("t1") == "repo-a"

        plugin.on_hook(
            "PreToolUse",
            task_id="t1",
            tool_name="read_file",
            parameters={},
            workspace_roots=[str(repo_b)],
        )
        assert get_scope("t1") == "repo-a"


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


class TestEveryRegisteredToolIsClassified:
    @pytest.mark.parametrize(("name", "read_only"), sorted(_REGISTERED_READ_ONLY.items()))
    def test_registered_tool_is_classified_by_its_annotation(self, name: str, read_only: bool) -> None:
        assert _is_memory_read(f"mcp__memory__{name}", {}) is read_only
        assert _is_memory_write(f"mcp__memory__{name}", {}) is not read_only


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
    def test_pre_tool_use_read_via_use_mcp_tool_not_blocked(self, plugin: MemoryPlugin, name: str) -> None:
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
        assert result is None or result.block is None

    def test_non_memory_tool_still_blocked(self, plugin: MemoryPlugin) -> None:
        with (
            patch("mcp_memory.hooks.plugin.should_block", return_value=True),
            patch("mcp_memory.hooks.plugin._is_memory_server_reachable", return_value=True),
        ):
            result = plugin.on_hook(
                "PreToolUse",
                task_id="t1",
                tool_name="execute_command",
                parameters={},
            )
        assert result is not None
        assert result.block is not None


class TestContextWindowInjection:
    def _call(self, plugin: MemoryPlugin, tool: str = "search_nodes", task_id: str = "t1") -> HookResult | None:
        plugin.on_hook("PreToolUse", tool_name="use_mcp_tool", parameters={}, task_id=task_id)
        return plugin.on_hook("PreMcpToolUse", mcp_tool_name=tool, mcp_arguments={"query": "q"}, task_id=task_id)

    def test_deduped_read_gets_stable_window_and_other_tools_nothing(self, plugin: MemoryPlugin) -> None:
        first = self._call(plugin).updated_input
        plugin.on_hook("PreToolUse", task_id="t1", tool_name="read_file", parameters={})
        second = self._call(plugin).updated_input
        assert first["query"] == "q"
        assert first["context_window_id"] == second["context_window_id"]
        assert second["context_turn"] == first["context_turn"] + 2
        assert self._call(plugin, tool="list_metadata") is None

    def test_subagent_and_new_task_start_get_new_windows(self) -> None:
        plugin = _real_plugin()
        main = self._call(plugin).updated_input["context_window_id"]
        assert self._call(plugin, task_id="t1:agent1").updated_input["context_window_id"] != main
        plugin.on_hook("TaskStart", task_id="t1", workspace_roots=[], source="compact")
        assert self._call(plugin).updated_input["context_window_id"] != main


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
        with (
            patch("mcp_memory.hooks.plugin.should_block", return_value=True),
            patch("mcp_memory.hooks.plugin._is_memory_server_reachable", return_value=True),
        ):
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
                agent_id="sub1",
            )
        assert result is None

    def test_non_allowlisted_subagent_write_still_scope_checked(self, plugin: MemoryPlugin, tmp_path: Path) -> None:
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

    def test_pre_mcp_tool_use_non_allowlisted_subagent_not_blocked(self, plugin: MemoryPlugin) -> None:
        with patch("mcp_memory.hooks.plugin.should_block", return_value=True):
            result = plugin.on_hook(
                "PreMcpToolUse",
                task_id="t1",
                mcp_tool_name="read_file",
                agent_type="general-purpose",
                agent_id="sub1",
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
                agent_type="general-purpose",
                agent_id="sub1",
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
                agent_type="Explore",
            )
        mock_increment.assert_not_called()
        assert result is None

    def test_env_var_extends_allowlist(self, plugin: MemoryPlugin, monkeypatch: pytest.MonkeyPatch) -> None:
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


class TestSubagentIdentification:
    def test_agent_id_without_agent_type_is_subagent(self, plugin: MemoryPlugin) -> None:
        with patch("mcp_memory.hooks.plugin.should_block", return_value=True):
            result = plugin.on_hook(
                "PreToolUse",
                task_id="t1",
                tool_name="execute_command",
                parameters={},
                agent_id="abc123",
            )
        assert result is None

    def test_agent_type_without_agent_id_is_not_subagent(self, plugin: MemoryPlugin) -> None:
        with (
            patch("mcp_memory.hooks.plugin.should_block", return_value=True),
            patch("mcp_memory.hooks.plugin._is_memory_server_reachable", return_value=True),
        ):
            result = plugin.on_hook(
                "PreToolUse",
                task_id="t1",
                tool_name="execute_command",
                parameters={},
                agent_type="main",
            )
        assert result is not None
        assert result.block is not None


class TestMemoryReviewNudge:
    def test_memory_write_resets_counter(self) -> None:
        with (
            patch("mcp_memory.hooks.plugin.clear"),
            patch("mcp_memory.hooks.plugin.reset") as mock_reset,
            patch("mcp_memory.hooks.plugin.record_write") as mock_record,
        ):
            plugin = MemoryPlugin()
            plugin.on_hook(
                "PostToolUse",
                task_id="t1",
                tool_name="mcp__memory__create_entities",
                parameters={"project": "global", "entities": []},
            )
        mock_reset.assert_called_once_with("t1")
        mock_record.assert_called_once_with()

    def test_use_mcp_tool_memory_write_resets_counter(self) -> None:
        with (
            patch("mcp_memory.hooks.plugin.clear"),
            patch("mcp_memory.hooks.plugin.reset") as mock_reset,
            patch("mcp_memory.hooks.plugin.record_write"),
        ):
            MemoryPlugin().on_hook(
                "PostToolUse",
                task_id="t1",
                tool_name="use_mcp_tool",
                parameters={"server_name": "memory", "tool_name": "create_entities", "arguments": {}},
            )
        mock_reset.assert_called_once_with("t1")

    def test_state_write_flag_on_non_memory_tool_does_not_reset_counter(self) -> None:
        with (
            patch("mcp_memory.hooks.plugin.clear"),
            patch("mcp_memory.hooks.plugin.reset") as mock_reset,
            patch("mcp_memory.hooks.plugin.increment") as mock_increment,
            patch("mcp_memory.hooks.plugin.has_edited", return_value=True),
        ):
            MemoryPlugin().on_hook(
                "PostToolUse", task_id="t1", tool_name="read_file", parameters={}, is_state_write=True
            )
        mock_reset.assert_not_called()
        mock_increment.assert_called_once_with("t1")

    def test_user_prompt_emits_nudge_and_resets_when_due(self) -> None:
        with (
            patch("mcp_memory.hooks.plugin.should_nudge", return_value=True),
            patch("mcp_memory.hooks.plugin.reset_review") as mock_reset,
        ):
            plugin = MemoryPlugin()
            result = plugin.on_hook("UserPromptSubmit", task_id="t1")
        assert result is not None
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
        assert result is not None
        assert not any("memory-review" in note for note in result.notes)
        mock_reset.assert_not_called()


class TestProfanityNudge:
    def _note(self, message: str, verdict: bool, monkeypatch: pytest.MonkeyPatch) -> str:
        """Fire the nudge with a mocked profanity verdict and return the frustration note."""
        monkeypatch.setattr("better_profanity.profanity.contains_profanity", lambda _message: verdict)
        with patch("mcp_memory.hooks.plugin.should_nudge", return_value=False):
            plugin = MemoryPlugin()
            result = plugin.on_hook("UserPromptSubmit", task_id="t1", message=message)
        assert result is not None
        return next(note for note in result.notes if _FRUSTRATION_MARKER in note)

    @_needs_profanity_check
    @_needs_frustration_check
    def test_fires_when_profanity_detected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        note = self._note("this is broken", True, monkeypatch)
        assert "[elevated]" in note
        assert "vote=2" in note
        assert "profanity" in note

    @_needs_profanity_check
    @_needs_frustration_check
    def test_fires_every_time_with_no_debounce(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("better_profanity.profanity.contains_profanity", lambda _message: True)
        with patch("mcp_memory.hooks.plugin.should_nudge", return_value=False):
            plugin = MemoryPlugin()
            first = plugin.on_hook("UserPromptSubmit", task_id="t1", message="forget this")
            second = plugin.on_hook("UserPromptSubmit", task_id="t1", message="forget this")
        assert first is not None
        assert any(_FRUSTRATION_MARKER in note for note in first.notes)
        assert second is not None
        assert any(_FRUSTRATION_MARKER in note for note in second.notes)

    def test_absent_when_negative_and_not_shouting(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("better_profanity.profanity.contains_profanity", lambda _message: False)
        with patch("mcp_memory.hooks.plugin.should_nudge", return_value=False):
            plugin = MemoryPlugin()
            result = plugin.on_hook("UserPromptSubmit", task_id="t1", message="can you check the parser")
        assert result is None or not any(_FRUSTRATION_MARKER in note for note in result.notes)

    @_needs_frustration_check
    def test_fires_on_repeated_punct_when_negative_and_not_shouting(self, monkeypatch: pytest.MonkeyPatch) -> None:
        note = self._note("really???", False, monkeypatch)
        assert "[mild]" in note
        assert "vote=1" in note
        assert "repeated punctuation" in note

    def test_repeated_punct_false_positive_does_not_fire_nudge(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("better_profanity.profanity.contains_profanity", lambda _message: False)
        with patch("mcp_memory.hooks.plugin.should_nudge", return_value=False):
            plugin = MemoryPlugin()
            result = plugin.on_hook("UserPromptSubmit", task_id="t1", message="self.config.value.thing")
        assert result is None or not any(_FRUSTRATION_MARKER in note for note in result.notes)

    @pytest.mark.parametrize(
        "message",
        [
            "oh for fricks sake",
            "this is fricking broken",
            "what the frick",
            "stop being so darn useless",
            "that's flipping wrong again",
            "sheesh",
            "what in tarnation",
            "oh gosh",
            "gadzooks",
            "geez",
            "oh jeez",
        ],
    )
    @_needs_frustration_check
    def test_fires_on_minced_oath_when_negative_and_not_shouting(
        self, message: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        note = self._note(message, False, monkeypatch)
        assert "[mild]" in note
        assert "vote=1" in note
        assert "a minced oath" in note

    @pytest.mark.parametrize(
        "message",
        [
            "can you check the parser",
            "the sparse matrix is fine",
            "add sodium to the fixture",
            "there is a danger here",
            "checking the darning logic",
            "the heckler tests are flaky",
            "use a goshawk image",
            "that geezer wrote the original parser",
            "freaking out about the parser",
        ],
    )
    def test_word_boundaries_do_not_false_trigger(self, message: str, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("better_profanity.profanity.contains_profanity", lambda _message: False)
        with patch("mcp_memory.hooks.plugin.should_nudge", return_value=False):
            plugin = MemoryPlugin()
            result = plugin.on_hook("UserPromptSubmit", task_id="t1", message=message)
        assert result is None or not any(_FRUSTRATION_MARKER in note for note in result.notes)

    @pytest.mark.parametrize(
        "message",
        [
            "can you check the parser",
            "the sparse matrix is fine",
            "add sodium to the fixture",
            "this legacy code is horrible",
            "the tests failed badly",
            "this class needs a method",
        ],
    )
    def test_real_classifier_no_false_positive(self, message: str) -> None:
        with patch("mcp_memory.hooks.plugin.should_nudge", return_value=False):
            plugin = MemoryPlugin()
            result = plugin.on_hook("UserPromptSubmit", task_id="t1", message=message)
        assert result is None or not any(_FRUSTRATION_MARKER in note for note in result.notes)

    @_needs_frustration_check
    def test_minced_oath_alone_is_mild(self, monkeypatch: pytest.MonkeyPatch) -> None:
        note = self._note("geez", False, monkeypatch)
        assert "[mild]" in note
        assert "vote=1" in note
        assert "a minced oath" in note

    @_needs_frustration_check
    def test_caps_shouting_alone_is_elevated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        note = self._note("WHY IS THIS STILL BROKEN", False, monkeypatch)
        assert "[elevated]" in note
        assert "vote=2" in note
        assert "all-caps shouting" in note

    @_needs_frustration_check
    def test_caps_plus_punct_is_elevated_and_names_both(self, monkeypatch: pytest.MonkeyPatch) -> None:
        note = self._note("WHY IS THIS BROKEN!!!", False, monkeypatch)
        assert "[elevated]" in note
        assert "vote=2" in note
        assert "all-caps shouting" in note
        assert "repeated punctuation" in note

    @_needs_frustration_check
    def test_minced_plus_punct_is_elevated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        note = self._note("geez really???", False, monkeypatch)
        assert "[elevated]" in note
        assert "vote=2" in note

    @_needs_profanity_check
    @_needs_frustration_check
    def test_profanity_plus_caps_is_strong(self, monkeypatch: pytest.MonkeyPatch) -> None:
        note = self._note("WHY IS THIS BROKEN", True, monkeypatch)
        assert "[strong]" in note
        assert "vote=3" in note

    @_needs_profanity_check
    @_needs_frustration_check
    def test_all_three_signals_is_strong_and_names_all(self, monkeypatch: pytest.MonkeyPatch) -> None:
        note = self._note("WHY IS THIS BROKEN!!!", True, monkeypatch)
        assert "[strong]" in note
        assert "vote=3" in note
        assert "profanity" in note
        assert "all-caps shouting" in note
        assert "repeated punctuation" in note

    @_needs_frustration_check
    def test_two_mild_signals_do_not_reach_strong(self, monkeypatch: pytest.MonkeyPatch) -> None:
        note = self._note("geez really???", False, monkeypatch)
        assert "[strong]" not in note

    @_needs_frustration_check
    def test_lone_minced_oath_does_not_reach_elevated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        note = self._note("geez", False, monkeypatch)
        assert "[mild]" in note
        assert "[elevated]" not in note

    @_needs_profanity_check
    @_needs_frustration_check
    def test_tier_fires_every_time_with_no_debounce(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("better_profanity.profanity.contains_profanity", lambda _message: True)
        with patch("mcp_memory.hooks.plugin.should_nudge", return_value=False):
            plugin = MemoryPlugin()
            first = plugin.on_hook("UserPromptSubmit", task_id="t1", message="WHY IS THIS BROKEN")
            second = plugin.on_hook("UserPromptSubmit", task_id="t1", message="WHY IS THIS BROKEN")
        assert first is not None
        assert any("[strong]" in note for note in first.notes)
        assert second is not None
        assert any("[strong]" in note for note in second.notes)


class TestHasRepeatedPunct:
    @pytest.mark.parametrize(
        "message",
        [
            "really???",
            "what?!",
            "wow!!",
            "this is broken again!!!",
            "seriously!?",
            "what?!?!",
            "why!!!",
        ],
    )
    def test_true_for_repeated_runs(self, message: str) -> None:
        assert _has_repeated_punct(message)

    @pytest.mark.parametrize(
        "message",
        [
            "wow!",
            "does this work?",
            "self.config.value.thing",
            "add a, b, c, and d",
            "hmm... let me think",
        ],
    )
    def test_false_otherwise(self, message: str) -> None:
        assert not _has_repeated_punct(message)


class TestIsCapsShouting:
    @pytest.mark.parametrize("message", ["WHY IS THIS STILL BROKEN", "WHAT", "API", "WHAT?!"])
    def test_true_for_sustained_caps(self, message: str) -> None:
        assert _is_caps_shouting(message)

    @pytest.mark.parametrize(
        "message",
        [
            "call the API please",
            "does the SQL query work",
            "really???",
            "Can you take a look at the parser please",
            "upgrade to 1.2.3 please",
            "check /usr/local/lib/python3.12/site-packages",
            "does the JSON parser handle this",
            "I",
            "A",
        ],
    )
    def test_false_otherwise(self, message: str) -> None:
        assert not _is_caps_shouting(message)


class TestFrustrationTier:
    @pytest.mark.parametrize(
        ("weight", "expected"),
        [
            (1, ("mild", 1)),
            (2, ("elevated", 2)),
            (3, ("elevated", 2)),
            (4, ("strong", 3)),
            (5, ("strong", 3)),
        ],
    )
    def test_tier_for_weight(self, weight: int, expected: tuple[str, int]) -> None:
        assert _frustration_tier(weight) == expected


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
            )
        mock_increment.assert_not_called()

    def test_post_tool_use_non_memory_tool_increments(self) -> None:
        with (
            patch("mcp_memory.hooks.plugin.clear"),
            patch("mcp_memory.hooks.plugin.reset"),
            patch("mcp_memory.hooks.plugin.increment") as mock_increment,
            patch("mcp_memory.hooks.plugin.has_edited", return_value=True),
        ):
            plugin = MemoryPlugin()
            plugin.on_hook(
                "PostToolUse",
                task_id="t1",
                tool_name="read_file",
                parameters={},
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
            )
        mock_increment.assert_called_once_with("t1", _EDIT_TOOL_WEIGHT)

    def test_post_tool_use_edit_updates_scope(self, plugin: MemoryPlugin, tmp_path: Path) -> None:
        repo_a = tmp_path / "repo-a"
        repo_b = tmp_path / "repo-b"
        (repo_a / ".git").mkdir(parents=True)
        (repo_b / ".git").mkdir(parents=True)

        plugin.on_hook("TaskStart", task_id="t1", workspace_roots=[str(repo_a)])
        with _registered_paths({repo_b: "repo-b"}):
            plugin.on_hook(
                "PostToolUse",
                task_id="t1",
                tool_name="replace_in_file",
                parameters={"path": str(repo_b / "src" / "file.py")},
            )
        assert get_scope("t1") == "repo-b"

    def test_pre_tool_use_edit_still_blockable(self, plugin: MemoryPlugin) -> None:
        with (
            patch("mcp_memory.hooks.plugin.should_block", return_value=True),
            patch("mcp_memory.hooks.plugin._is_memory_server_reachable", return_value=True),
        ):
            result = plugin.on_hook(
                "PreToolUse",
                task_id="t1",
                tool_name="replace_in_file",
                parameters={},
            )
        assert result is not None
        assert result.block is not None

    def test_env_var_extends_edit_tools(self, plugin: MemoryPlugin, monkeypatch: pytest.MonkeyPatch) -> None:
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
            )
        mock_increment.assert_called_once_with("t1", _EDIT_TOOL_WEIGHT)

    def test_wrapper_tool_is_gated_and_counted_as_one_call(self, plugin: MemoryPlugin) -> None:
        parameters = {"code": "await tools.mcp__semble__search({})"}
        with (
            patch("mcp_memory.hooks.plugin.should_block", return_value=True),
            patch("mcp_memory.hooks.plugin._is_memory_server_reachable", return_value=True),
        ):
            blocked = plugin.on_hook("PreToolUse", task_id="t1", tool_name="codemode", parameters=parameters)
        with (
            patch("mcp_memory.hooks.plugin.increment") as mock_increment,
            patch("mcp_memory.hooks.plugin.has_edited", return_value=True),
        ):
            plugin.on_hook("PostToolUse", task_id="t1", tool_name="codemode", parameters=parameters)
        assert blocked is not None
        assert blocked.block is not None
        mock_increment.assert_called_once_with("t1")

    @pytest.mark.parametrize("tool_name", ["codemode", "run_script"])
    def test_wrapper_tool_naming_a_memory_write_is_neither_gated_nor_counted(
        self, plugin: MemoryPlugin, monkeypatch: pytest.MonkeyPatch, tool_name: str
    ) -> None:
        monkeypatch.setenv("MCP_MEMORY_WRAPPER_TOOLS", "batch, run_script")
        parameters = {"code": "await tools.mcp__memory__add_observations({})"}
        with (
            patch("mcp_memory.hooks.plugin.should_block", return_value=True),
            patch("mcp_memory.hooks.plugin._is_memory_server_reachable", return_value=True),
            patch("mcp_memory.hooks.plugin.increment") as mock_increment,
        ):
            gated = plugin.on_hook("PreToolUse", task_id="t1", tool_name=tool_name, parameters=parameters)
            plugin.on_hook("PostToolUse", task_id="t1", tool_name=tool_name, parameters=parameters)
        assert gated is None
        mock_increment.assert_not_called()

    @pytest.mark.parametrize("tool_name", [*sorted(BOOKKEEPING_TOOLS | SKILL_TOOLS | MESSAGE_TOOLS), "other_tool"])
    def test_bookkeeping_tool_is_not_counted_but_still_gated(
        self, plugin: MemoryPlugin, monkeypatch: pytest.MonkeyPatch, tool_name: str
    ) -> None:
        monkeypatch.setenv("MCP_MEMORY_UNCOUNTED_TOOLS", "note_tool, other_tool")
        with (
            patch("mcp_memory.hooks.plugin.has_edited", return_value=True),
            patch("mcp_memory.hooks.plugin.should_block", return_value=True),
            patch("mcp_memory.hooks.plugin._is_memory_server_reachable", return_value=True),
            patch("mcp_memory.hooks.plugin.increment") as mock_increment,
        ):
            gated = plugin.on_hook("PreToolUse", task_id="t1", tool_name=tool_name, parameters={})
            plugin.on_hook("PostToolUse", task_id="t1", tool_name=tool_name, parameters={})
        assert gated is not None
        assert gated.block is not None
        mock_increment.assert_not_called()

    @pytest.mark.parametrize(
        ("hook_name", "call"),
        [
            ("PreToolUse", {"tool_name": "use_mcp_tool", "parameters": {"tool_name": "search", "arguments": "{}"}}),
            ("PreMcpToolUse", {"mcp_tool_name": "search", "mcp_arguments": "{}"}),
            ("PostToolUse", {"tool_name": "use_mcp_tool", "parameters": {"tool_name": "search", "arguments": "{}"}}),
        ],
    )
    def test_call_made_inside_a_wrapper_is_neither_gated_nor_counted(
        self, plugin: MemoryPlugin, hook_name: str, call: dict[str, object]
    ) -> None:
        with (
            patch("mcp_memory.hooks.plugin.should_block", return_value=True),
            patch("mcp_memory.hooks.plugin._is_memory_server_reachable", return_value=True),
            patch("mcp_memory.hooks.plugin.increment") as mock_increment,
        ):
            result = plugin.on_hook(hook_name, task_id="t1", parent_tool_call_id="call-1", **call)
        assert result is None
        mock_increment.assert_not_called()


class TestMemoryGateWaitsForFirstFileEdit:
    def test_lookup_only_session_gets_no_reminder_and_no_block(self, live_plugin: MemoryPlugin) -> None:
        set_reminder_chance("t1", 1.0)
        with patch("random.random", return_value=0.0):
            for _ in range(_MEMORY_BLOCK_THRESHOLD * 2):
                for hook in ("PostToolUse", "PreToolUse"):
                    assert live_plugin.on_hook(hook, task_id="t1", tool_name="execute_command", parameters={}) is None

    def test_session_is_gated_once_a_file_edit_is_made(self, live_plugin: MemoryPlugin) -> None:
        live_plugin.on_hook("PostToolUse", task_id="t1", tool_name="write_to_file", parameters={})
        with patch("random.random", return_value=1.0):
            for _ in range(_MEMORY_BLOCK_THRESHOLD):
                live_plugin.on_hook("PostToolUse", task_id="t1", tool_name="execute_command", parameters={})
        blocked = live_plugin.on_hook("PreToolUse", task_id="t1", tool_name="execute_command", parameters={})
        assert blocked is not None
        assert blocked.block is not None


def _post(plugin: MemoryPlugin, tool_name: str, parameters: dict[str, object], **kwargs: object) -> None:
    plugin.on_hook("PostToolUse", task_id="t1", tool_name=tool_name, parameters=parameters, **kwargs)


def _write_entity(plugin: MemoryPlugin, kind: str, **kwargs: object) -> None:
    entity = {"name": f"{kind}/proj/thing", "entityType": kind, "observations": ["x"]}
    _post(plugin, "mcp__memory__create_entities", {"project": "proj", "entities": [entity]}, **kwargs)


class TestFeatureBeforeFirstEdit:
    @pytest.fixture(autouse=True)
    def _blocking(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MCP_MEMORY_FEATURE_FIRST_BLOCK", "1")

    @staticmethod
    def _edit(
        plugin: MemoryPlugin,
        tool_name: str = "replace_in_file",
        parameters: dict[str, object] | None = None,
        **kwargs: object,
    ) -> HookResult | None:
        return plugin.on_hook("PreToolUse", task_id="t1", tool_name=tool_name, parameters=parameters or {}, **kwargs)

    def _assert_blocked(self, plugin: MemoryPlugin, *args: object, **kwargs: object) -> str:
        blocked = self._edit(plugin, *args, **kwargs)  # type: ignore[arg-type]
        assert blocked is not None
        assert blocked.block is not None
        return blocked.block

    def test_edit_with_no_path_is_blocked_until_a_feature_is_written(self, live_plugin: MemoryPlugin) -> None:
        block = self._assert_blocked(live_plugin)
        assert "feature/" in block
        assert "This edit names no file" in block
        _write_entity(live_plugin, "feature")
        assert self._edit(live_plugin) is None

    def test_editing_one_existing_file_again_is_not_blocked(self, live_plugin: MemoryPlugin) -> None:
        edit = {"path": "/work/proj/a.py"}
        for _ in range(2):
            assert self._edit(live_plugin, parameters=edit, workspace_roots=["/work/proj"]) is None

    def test_editing_a_second_file_is_blocked_until_a_feature_is_written(self, live_plugin: MemoryPlugin) -> None:
        roots = ["/work/proj"]
        assert self._edit(live_plugin, parameters={"path": "/work/proj/a.py"}, workspace_roots=roots) is None
        second = {"parameters": {"path": "/work/proj/b.py"}, "workspace_roots": roots}
        assert "feature/" in self._assert_blocked(live_plugin, **second)
        _write_entity(live_plugin, "feature")
        assert self._edit(live_plugin, **second) is None

    def test_creating_a_file_is_blocked_but_overwriting_an_existing_first_file_is_not(
        self, live_plugin: MemoryPlugin, tmp_path: Path
    ) -> None:
        roots = [str(tmp_path)]
        self._assert_blocked(live_plugin, "write_to_file", {"path": str(tmp_path / "new.py")}, workspace_roots=roots)
        existing = tmp_path / "old.py"
        existing.write_text("x")
        assert self._edit(live_plugin, "write_to_file", {"path": str(existing)}, workspace_roots=roots) is None

    def test_block_shows_the_inline_relation_and_task_in_one_create_entities_call(
        self, live_plugin: MemoryPlugin
    ) -> None:
        block = self._assert_blocked(live_plugin)
        assert "relations:[{target:'project/<p>', type:'part-of'}]" in block
        assert "not as a separate create_relations call" in block
        assert "`task/` that `implements` the `feature/`" in block
        assert "in the script running the final check, once it passes, or with the last memory write" in block

    def test_first_read_warns_about_the_feature_once_before_any_edit(self, live_plugin: MemoryPlugin) -> None:
        def read() -> HookResult | None:
            return live_plugin.on_hook("PostToolUse", task_id="t1", tool_name="read_file", parameters={})

        first = read()
        assert first is not None
        assert "feature/" in first.notes[0]
        first_sentence = first.notes[0].split(". ")[0]
        assert "`feature/`" in first_sentence
        assert "`task/`" in first_sentence
        assert "never in a turn of its own" in first_sentence
        assert "A fix to one existing file may instead write both with its final check" in first.notes[0]
        assert "is blocked" not in first.notes[0]
        assert read() is None

    def test_prompt_warns_about_the_feature_once_per_task(self, live_plugin: MemoryPlugin) -> None:
        first = live_plugin.on_hook("UserPromptSubmit", task_id="t1", message="add a tag filter")
        assert first is not None
        assert "feature/" in first.notes[0]
        assert live_plugin.on_hook("UserPromptSubmit", task_id="t1", message="and more") is None

    def test_subagent_prompt_gets_no_feature_note(self, live_plugin: MemoryPlugin) -> None:
        assert live_plugin.on_hook("UserPromptSubmit", task_id="t1", message="add a filter", agent_id="sub-1") is None

    def test_prompt_note_carries_the_same_rules_as_the_block(self, live_plugin: MemoryPlugin) -> None:
        block = self._assert_blocked(live_plugin)
        note = live_plugin.on_hook("UserPromptSubmit", task_id="t1", message="add a tag filter")
        assert note is not None
        for rule in (
            "never holds a status line, task text or the ask",
            "implementation detail (file names, internals, test layout)",
            "one caller-view line",
            "user's stated decisions about the capability with their reasons",
            "never as a `feature/` observation",
            "`knowledge/`",
        ):
            assert rule in block
            assert rule in note.notes[0]

    def test_feature_written_inside_a_script_lifts_the_gate_for_an_edit_in_the_same_script(
        self, live_plugin: MemoryPlugin
    ) -> None:
        _write_entity(live_plugin, "feature", parent_tool_call_id="c1")
        assert self._edit(live_plugin, "write_to_file", parent_tool_call_id="c1") is None

    def test_first_read_after_the_prompt_note_gets_no_repeat(self, live_plugin: MemoryPlugin) -> None:
        live_plugin.on_hook("UserPromptSubmit", task_id="t1", message="add a tag filter")
        assert live_plugin.on_hook("PostToolUse", task_id="t1", tool_name="read_file", parameters={}) is None

    def test_prompt_and_first_read_after_a_feature_write_get_no_warning(self, live_plugin: MemoryPlugin) -> None:
        _write_entity(live_plugin, "feature")
        assert live_plugin.on_hook("UserPromptSubmit", task_id="t1", message="add a tag filter") is None
        assert live_plugin.on_hook("PostToolUse", task_id="t1", tool_name="read_file", parameters={}) is None

    @pytest.mark.parametrize(
        "command",
        [
            "echo hi > notes.md",
            "echo hi >> notes.md",
            "echo hi | tee notes.md",
            "echo hi | sudo tee -a notes.md",
            "sed -i 's/a/b/' notes.md",
            "sed -n -i -e 's/a/b/' notes.md",
            "python3 -c \"open('notes.md', 'w').write('x')\"",
            "python - <<'EOF'\nfrom pathlib import Path\nPath('notes.md').write_text('x')\nEOF",
            "cat > notes.md <<'EOF'\nhello\nEOF",
            "cat <<EOF > notes.md\nhello\nEOF",
            "echo hi > /work/proj/notes.md",
            "cp old.py notes.md",
            "sudo mv -f old.py notes.md",
            "cp -t notes old.py",
            "python - <<'EOF'\nfrom pathlib import Path\npath = Path('notes.md')\npath.write_text('x')\nEOF",
        ],
    )
    def test_shell_file_write_is_blocked_until_a_feature_is_written(
        self, live_plugin: MemoryPlugin, command: str
    ) -> None:
        write = {"parameters": {"command": command}, "workspace_roots": ["/work/proj"]}
        assert "feature/" in self._assert_blocked(live_plugin, "execute_command", **write)
        _write_entity(live_plugin, "feature")
        assert self._edit(live_plugin, "execute_command", **write) is None

    @pytest.mark.parametrize(
        "command",
        [
            "rg tee src",
            "cat notes.md",
            "make test > /dev/null 2>&1",
            "make test &> /dev/null",
            "echo oops >&2",
            "rg 'a>b' src",
            'echo "a > b"',
            "cat <<EOF\nx > y\nEOF",
            "echo hi > /tmp/scratch.txt",
            "printf x 2> err.log",
            "echo hi | tee /dev/null",
            "git -P log --format=%h | head",
            "cp notes.md /tmp/backup.md",
            "cp -r src /dev/null",
        ],
    )
    def test_shell_command_without_a_workspace_file_write_is_not_gated(
        self, live_plugin: MemoryPlugin, command: str
    ) -> None:
        result = self._edit(
            live_plugin, "execute_command", parameters={"command": command}, workspace_roots=["/work/proj"]
        )
        assert result is None

    def test_shell_and_edit_writes_share_the_one_free_existing_file(
        self, live_plugin: MemoryPlugin, tmp_path: Path
    ) -> None:
        first, second = tmp_path / "a.txt", tmp_path / "b.txt"
        first.write_text("x")
        second.write_text("x")
        roots = [str(tmp_path)]
        assert (
            self._edit(live_plugin, "execute_command", {"command": "echo hi >> a.txt"}, workspace_roots=roots) is None
        )
        assert self._edit(live_plugin, parameters={"path": str(first)}, workspace_roots=roots) is None
        shell = {"command": "sed -i s/x/y/ b.txt"}
        block = self._assert_blocked(live_plugin, "execute_command", shell, workspace_roots=roots)
        assert f"This writes a second file, `{second}`" in block

    def test_block_names_a_created_file(self, live_plugin: MemoryPlugin, tmp_path: Path) -> None:
        shell = {"command": "echo hi > new.md"}
        block = self._assert_blocked(live_plugin, "execute_command", shell, workspace_roots=[str(tmp_path)])
        assert f"This creates `{tmp_path / 'new.md'}`" in block

    def test_python_write_to_a_path_held_in_a_variable_counts_as_a_write_to_an_unnamed_file(
        self, live_plugin: MemoryPlugin, tmp_path: Path
    ) -> None:
        (tmp_path / "a.py").write_text("x")
        command = (
            "cat >> a.py <<'EOF'\nx = 1\nEOF\n"
            "python - <<'EOF'\npath = 'b.py'\nwith open(path, 'w') as handle:\n    handle.write('y')\nEOF"
        )
        block = self._assert_blocked(
            live_plugin, "execute_command", {"command": command}, workspace_roots=[str(tmp_path)]
        )
        assert "This edit names no file" in block

    def test_shell_write_to_a_gitignored_file_is_not_gated(self, live_plugin: MemoryPlugin, tmp_path: Path) -> None:
        subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)  # ruff: ignore[start-process-with-partial-path]
        (tmp_path / ".gitignore").write_text("out.txt\n")
        write = {"parameters": {"command": "echo hi > out.txt"}, "workspace_roots": [str(tmp_path)]}
        assert self._edit(live_plugin, "execute_command", **write) is None

    @pytest.mark.parametrize("tool_name", ["execute_command", "read_file", "plan_mode_respond"])
    def test_only_file_edits_are_gated(self, live_plugin: MemoryPlugin, tool_name: str) -> None:
        assert self._edit(live_plugin, tool_name) is None

    def test_feature_write_inside_a_wrapper_lifts_the_gate(self, live_plugin: MemoryPlugin) -> None:
        arguments = {"project": "proj", "entityName": "feature/proj/thing", "observations": ["x"]}
        _post(live_plugin, "mcp__memory__add_observations", arguments, parent_tool_call_id="call-1")
        assert self._edit(live_plugin, "write_to_file", parent_tool_call_id="call-2") is None

    def test_failed_feature_write_does_not_lift_the_gate(self, live_plugin: MemoryPlugin) -> None:
        error = json.dumps({"error": "Entity type 'feature' requires at least one relation"})
        _write_entity(live_plugin, "feature", tool_result=error)
        self._assert_blocked(live_plugin)

    def test_task_write_does_not_lift_the_gate(self, live_plugin: MemoryPlugin) -> None:
        _post(
            live_plugin, "mcp__memory__set_entity_status", {"project": "proj", "name": "task/x", "status": "resolved"}
        )
        self._assert_blocked(live_plugin)

    def test_edit_is_allowed_with_a_note_when_the_server_is_down(self, live_plugin: MemoryPlugin) -> None:
        with patch("mcp_memory.hooks.plugin._is_memory_server_reachable", return_value=False):
            result = self._edit(live_plugin)
        assert result is not None
        assert result.block is None
        assert "unreachable" in result.notes[0]

    def test_subagent_edit_is_not_gated(self, live_plugin: MemoryPlugin) -> None:
        assert self._edit(live_plugin, agent_id="sub-1", is_teammate=False) is None


class TestFeatureFirstIsANoteByDefault:
    def test_a_second_file_edit_gets_the_note_once_and_is_never_blocked(self, live_plugin: MemoryPlugin) -> None:
        def edit(name: str) -> HookResult | None:
            return live_plugin.on_hook(
                "PreToolUse",
                task_id="t1",
                tool_name="replace_in_file",
                parameters={"path": f"/work/proj/{name}"},
                workspace_roots=["/work/proj"],
            )

        assert edit("a.py") is None
        note = edit("b.py")
        assert note is not None
        assert note.block is None
        assert "This writes a second file" in note.notes[0]
        assert "feature/" in note.notes[0]
        assert edit("c.py") is None


class TestGotchaNudgeAfterWorkaround:
    _NUDGE = "If you just worked around something a new session would hit too, add it as a gotcha on the feature now."

    @staticmethod
    def _fail(plugin: MemoryPlugin, tool_name: str, **kwargs: object) -> None:
        plugin.on_hook("ToolFailed", task_id="t1", tool_name=tool_name, parameters={}, **kwargs)

    @staticmethod
    def _succeed(plugin: MemoryPlugin) -> HookResult | None:
        return plugin.on_hook("PostToolUse", task_id="t1", tool_name="execute_command", parameters={})

    def test_a_shell_failure_then_success_gets_the_nudge_once(self, live_plugin: MemoryPlugin) -> None:
        self._fail(live_plugin, "execute_command")
        first = self._succeed(live_plugin)
        assert first is not None
        assert first.notes == [self._NUDGE]
        assert self._succeed(live_plugin) is None

    def test_a_non_shell_failure_gets_no_nudge(self, live_plugin: MemoryPlugin) -> None:
        self._fail(live_plugin, "read_file")
        assert self._succeed(live_plugin) is None

    def test_a_subagent_failure_gets_no_nudge(self, live_plugin: MemoryPlugin) -> None:
        self._fail(live_plugin, "execute_command", agent_id="sub-1")
        assert self._succeed(live_plugin) is None


def _commit(plugin: MemoryPlugin) -> None:
    _post(plugin, "replace_in_file", {})
    _post(plugin, "execute_command", {"command": "git commit -m x"})


class TestTasksRequiredAtCompletion:
    @pytest.mark.parametrize("hook", ["AttemptCompletion", "Stop"])
    def test_finishing_after_edits_without_a_task_is_blocked_until_one_is_written(
        self, live_plugin: MemoryPlugin, hook: str
    ) -> None:
        _commit(live_plugin)
        blocked = live_plugin.on_hook(hook, task_id="t1")
        assert blocked is not None
        assert blocked.block is not None
        assert "task/" in blocked.block
        assert "git can't show" in blocked.block
        assert "no outcome line" in blocked.block

        _write_entity(live_plugin, "task")
        result = live_plugin.on_hook(hook, task_id="t1")
        assert result is None or result.block is None

    def test_failed_task_write_does_not_lift_the_stop_block(self, live_plugin: MemoryPlugin) -> None:
        _commit(live_plugin)
        _write_entity(live_plugin, "task", tool_result=json.dumps({"error": "Target entity not found"}))
        blocked = live_plugin.on_hook("Stop", task_id="t1")
        assert blocked is not None
        assert blocked.block is not None

    @pytest.mark.parametrize(
        ("tool", "arguments"),
        [
            ("create_entities", {"project": "proj", "entities": [{"name": "task/a", "observations": []}]}),
            ("create_entities", {"project": "proj", "entities": [{"name": "feature/proj/a", "observations": ["x"]}]}),
            ("create_entities", {"project": "other", "entities": [{"name": "task/a", "observations": ["x"]}]}),
            ("add_observations", {"project": "proj", "entityName": "task/a", "observations": [" "]}),
            ("set_entity_status", {"project": "proj", "name": "task/a", "status": None}),
            ("set_entity_status", {"project": "proj", "name": "feature/proj/a", "status": "resolved"}),
            ("rename_entity", {"project": "proj", "old_name": "task/a", "new_name": "task/b"}),
        ],
    )
    def test_a_write_that_records_no_task_for_this_work_does_not_lift_the_stop_block(
        self, live_plugin: MemoryPlugin, tool: str, arguments: dict[str, object]
    ) -> None:
        set_scope("t1", "proj")
        _commit(live_plugin)
        _post(live_plugin, f"mcp__memory__{tool}", arguments)
        blocked = live_plugin.on_hook("Stop", task_id="t1")
        assert blocked is not None
        assert blocked.block is not None

    @pytest.mark.parametrize(
        ("tool", "arguments"),
        [
            ("add_observations", {"project": "proj", "entityName": "task/a", "observations": ["decision"]}),
            ("set_entity_status", {"project": "proj", "name": "task/a", "status": "resolved"}),
        ],
    )
    def test_updating_a_task_in_the_work_s_project_lifts_the_stop_block(
        self, live_plugin: MemoryPlugin, tool: str, arguments: dict[str, object]
    ) -> None:
        set_scope("t1", "proj")
        _commit(live_plugin)
        _post(live_plugin, f"mcp__memory__{tool}", arguments)
        assert live_plugin.on_hook("Stop", task_id="t1") is None

    @pytest.mark.parametrize("hook", ["AttemptCompletion", "Stop"])
    def test_finishing_after_a_small_edit_needs_no_task(self, live_plugin: MemoryPlugin, hook: str) -> None:
        _post(live_plugin, "replace_in_file", {})
        result = live_plugin.on_hook(hook, task_id="t1")
        assert result is None or result.block is None

    @pytest.mark.parametrize(
        "command", ["git commit -m x", "cd sub && git -C repo commit --amend", "git add . && git commit"]
    )
    def test_a_commit_makes_the_work_need_a_task(self, live_plugin: MemoryPlugin, command: str) -> None:
        _post(live_plugin, "replace_in_file", {})
        _post(live_plugin, "execute_command", {"command": command})
        blocked = live_plugin.on_hook("Stop", task_id="t1")
        assert blocked is not None
        assert "task/" in (blocked.block or "")

    @pytest.mark.parametrize("tool", ["plan_mode_respond", "exit_plan_mode"])
    def test_a_plan_makes_the_following_edit_need_a_task(self, live_plugin: MemoryPlugin, tool: str) -> None:
        _post(live_plugin, tool, {})
        assert live_plugin.on_hook("Stop", task_id="t1") is None
        _post(live_plugin, "replace_in_file", {})
        blocked = live_plugin.on_hook("Stop", task_id="t1")
        assert blocked is not None
        assert "task/" in (blocked.block or "")

    def test_a_command_that_only_mentions_commit_is_not_wide_work(self, live_plugin: MemoryPlugin) -> None:
        _post(live_plugin, "replace_in_file", {})
        _post(live_plugin, "execute_command", {"command": "git log --grep commit"})
        assert live_plugin.on_hook("Stop", task_id="t1") is None

    def test_stop_without_edits_does_nothing(self, live_plugin: MemoryPlugin) -> None:
        assert live_plugin.on_hook("Stop", task_id="t1") is None

    def test_stop_is_not_blocked_when_the_server_is_down(self, live_plugin: MemoryPlugin) -> None:
        _commit(live_plugin)
        with patch("mcp_memory.hooks.plugin._is_memory_server_reachable", return_value=False):
            assert live_plugin.on_hook("Stop", task_id="t1") is None

    def test_subagent_stop_is_not_blocked(self, live_plugin: MemoryPlugin) -> None:
        _commit(live_plugin)
        assert live_plugin.on_hook("Stop", task_id="t1", agent_id="sub-1") is None


class TestWorkLifecycle:
    @staticmethod
    def _stop(plugin: MemoryPlugin) -> HookResult | None:
        return plugin.on_hook("Stop", task_id="t1")

    def _assert_stop_blocks(self, plugin: MemoryPlugin) -> str:
        blocked = self._stop(plugin)
        assert blocked is not None
        assert blocked.block is not None
        return blocked.block

    def test_next_piece_of_work_is_gated_again_after_a_stop_that_passes_with_a_task(
        self, live_plugin: MemoryPlugin
    ) -> None:
        _commit(live_plugin)
        _write_entity(live_plugin, "task")
        assert self._stop(live_plugin) is None

        _commit(live_plugin)
        self._assert_stop_blocks(live_plugin)

    def test_next_prompt_resets_work_finished_in_a_forced_turn(self, live_plugin: MemoryPlugin) -> None:
        _commit(live_plugin)
        _write_entity(live_plugin, "task")
        live_plugin.on_hook("UserPromptSubmit", task_id="t1", message="now something else")

        _commit(live_plugin)
        assert self._stop(live_plugin) is not None

    def test_next_prompt_keeps_work_that_still_lacks_its_feature(self, live_plugin: MemoryPlugin) -> None:
        _write_entity(live_plugin, "task")
        self._edit(live_plugin, "a.py")
        self._edit(live_plugin, "b.py")
        live_plugin.on_hook("UserPromptSubmit", task_id="t1", message="carry on")
        assert "`feature/`" in self._assert_stop_blocks(live_plugin)

    def test_compaction_keeps_the_work_state(self, live_plugin: MemoryPlugin) -> None:
        _commit(live_plugin)
        live_plugin.on_hook("TaskStart", task_id="t1", workspace_roots=[], source="compact")
        self._assert_stop_blocks(live_plugin)

    def test_lead_delegating_work_is_gated_at_stop_but_a_subagent_is_not(self, live_plugin: MemoryPlugin) -> None:
        _post(live_plugin, "spawn_agent", {}, agent_id="sub-1")
        assert self._stop(live_plugin) is None

        _post(live_plugin, "spawn_agent", {})
        self._assert_stop_blocks(live_plugin)

    @pytest.mark.parametrize("key", ["subagent_type", "definition"])
    def test_delegating_to_a_read_only_agent_is_not_work(self, live_plugin: MemoryPlugin, key: str) -> None:
        _post(live_plugin, "spawn_agent", {key: "Explore"})
        assert self._stop(live_plugin) is None

    @staticmethod
    def _edit(plugin: MemoryPlugin, name: str) -> None:
        kwargs = {"task_id": "t1", "tool_name": "replace_in_file", "parameters": {"path": f"/work/proj/{name}"}}
        plugin.on_hook("PreToolUse", workspace_roots=["/work/proj"], **kwargs)
        plugin.on_hook("PostToolUse", workspace_roots=["/work/proj"], **kwargs)

    def test_a_small_fix_that_ends_without_a_task_does_not_make_the_next_fix_wide(
        self, live_plugin: MemoryPlugin
    ) -> None:
        self._edit(live_plugin, "a.py")
        assert self._stop(live_plugin) is None

        self._edit(live_plugin, "b.py")
        assert self._stop(live_plugin) is None

    def test_stop_asks_for_a_feature_when_work_past_one_file_wrote_only_a_task(self, live_plugin: MemoryPlugin) -> None:
        _write_entity(live_plugin, "task")
        self._edit(live_plugin, "a.py")
        self._edit(live_plugin, "b.py")
        assert "`feature/`" in self._assert_stop_blocks(live_plugin)

        _write_entity(live_plugin, "feature")
        assert self._stop(live_plugin) is None

    def test_stop_asks_for_a_feature_when_a_shell_command_wrote_past_one_file(self, live_plugin: MemoryPlugin) -> None:
        _write_entity(live_plugin, "task")
        command = "python - <<'EOF'\npath = 'a.py'\nopen(path, 'w').write('x')\nEOF"
        kwargs = {"task_id": "t1", "tool_name": "execute_command", "parameters": {"command": command}}
        live_plugin.on_hook("PreToolUse", workspace_roots=["/work/proj"], **kwargs)
        live_plugin.on_hook("PostToolUse", workspace_roots=["/work/proj"], **kwargs)
        assert "`feature/`" in self._assert_stop_blocks(live_plugin)

    def test_stop_asks_for_the_task_and_the_feature_in_one_turn(self, live_plugin: MemoryPlugin) -> None:
        self._edit(live_plugin, "a.py")
        self._edit(live_plugin, "b.py")
        block = self._assert_stop_blocks(live_plugin)
        assert "`task/`" in block
        assert "new one" in block

    def test_stop_does_not_ask_for_a_feature_after_a_one_file_edit(self, live_plugin: MemoryPlugin) -> None:
        _write_entity(live_plugin, "task")
        self._edit(live_plugin, "a.py")
        self._edit(live_plugin, "a.py")
        assert self._stop(live_plugin) is None

    def test_second_piece_of_work_needs_its_own_feature(self, live_plugin: MemoryPlugin) -> None:
        _write_entity(live_plugin, "feature")
        _write_entity(live_plugin, "task")
        self._edit(live_plugin, "a.py")
        self._edit(live_plugin, "b.py")
        assert self._stop(live_plugin) is None

        live_plugin.on_hook("UserPromptSubmit", task_id="t1", message="now add another thing")
        _write_entity(live_plugin, "task")
        self._edit(live_plugin, "c.py")
        self._edit(live_plugin, "d.py")
        assert "`feature/`" in self._assert_stop_blocks(live_plugin)

    def test_stop_forces_at_most_one_turn_per_piece_of_work(self, live_plugin: MemoryPlugin) -> None:
        _commit(live_plugin)
        assert self._stop(live_plugin) is not None
        assert self._stop(live_plugin) is None


_PI_EDIT_EVENT = {"hook_event_name": "tool_call", "tool_name": "edit", "tool_input": {"path": "a.py", "edits": []}}
_PI_SECOND_EDIT_EVENT = {
    "hook_event_name": "tool_call",
    "tool_name": "edit",
    "tool_input": {"path": "b.py", "edits": []},
}
_PI_FEATURE = {"name": "feature/proj/thing", "entityType": "feature", "observations": ["x"]}
_HOOK_BLOCKED_EXIT_CODE = 2


class TestFeatureFirstThroughTheHookBinary:
    """Drives the real `cline_hooks` entry point with the pi bridge's JSON payloads."""

    @pytest.fixture
    def hook_env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, str]]:
        """Environment for the hook process, with a listening port standing in for the memory server."""
        with TCPServer(("127.0.0.1", 0), BaseRequestHandler) as server_socket:
            threading.Thread(target=server_socket.serve_forever, daemon=True).start()
            monkeypatch.setenv("MCP_MEMORY_URL", f"http://127.0.0.1:{server_socket.server_address[1]}/mcp")
            monkeypatch.setenv("CLINE_HOOKS_FRONTEND", "pi")
            monkeypatch.setenv("MCP_MEMORY_FEATURE_FIRST_BLOCK", "1")
            monkeypatch.setenv("CLINE_HOOKS_DATA_DIR", str(tmp_path / "hooks"))
            yield dict(os.environ)
            server_socket.shutdown()

    @staticmethod
    def _run(env: dict[str, str], **payload: object) -> int:
        """Run one bridge event through the hook entry point and return its exit code."""
        cwd = str(Path(env["MCP_MEMORY_DB_PATH"]).parent)
        identity = {"session_id": "lead", "cwd": cwd, "session_name": "", "agent_id": ""}
        result = subprocess.run(
            [sys.executable, "-m", "cline_hooks"],
            input=json.dumps({**identity, **payload}),
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        return result.returncode

    def _write_feature(self, env: dict[str, str], tool_result: str) -> None:
        self._run(
            env,
            hook_event_name="tool_result",
            tool_name="mcp__memory__create_entities",
            tool_input={"project": "proj", "entities": [_PI_FEATURE]},
            tool_response={"success": True, "result": tool_result},
        )

    def test_lead_second_file_edit_is_blocked_until_a_feature_write_succeeds(self, hook_env: dict[str, str]) -> None:
        assert self._run(hook_env, **_PI_EDIT_EVENT) == 0
        assert self._run(hook_env, **_PI_SECOND_EDIT_EVENT) == _HOOK_BLOCKED_EXIT_CODE

        self._write_feature(hook_env, json.dumps({"error": "Entity type 'feature' requires at least one relation"}))
        assert self._run(hook_env, **_PI_SECOND_EDIT_EVENT) == _HOOK_BLOCKED_EXIT_CODE

        self._write_feature(hook_env, json.dumps({"created": 1}))
        assert self._run(hook_env, **_PI_SECOND_EDIT_EVENT) == 0

    def test_subagent_edits_are_not_gated_by_the_lead_but_need_an_identity(self, hook_env: dict[str, str]) -> None:
        for event in (_PI_EDIT_EVENT, _PI_SECOND_EDIT_EVENT):
            assert self._run(hook_env, session_id="child", agent_id="run-1", **event) == 0
            assert self._run(hook_env, session_id="child-2", session_name="worker#deadbeef", **event) == 0
        assert self._run(hook_env, session_id="child-3", **_PI_EDIT_EVENT) == 0
        assert self._run(hook_env, session_id="child-3", **_PI_SECOND_EDIT_EVENT) == _HOOK_BLOCKED_EXIT_CODE
