"""Tests for path-to-project resolution."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

from mcp_memory.database import DatabaseManager
from mcp_memory.path_resolver import (
    match_project_for_path,
    normalize_path,
    resolve_project_for_path,
)


class TestNormalizePath:
    def test_expands_user(self) -> None:
        assert normalize_path("~") == normalize_path(str(Path.home()))

    def test_resolves_dot_segments(self, tmp_path: Path) -> None:
        nested = tmp_path / "a" / "b"
        nested.mkdir(parents=True)
        assert normalize_path(str(tmp_path / "a" / ".." / "a" / "b")) == normalize_path(str(nested))


class TestMatchProjectForPath:
    def test_exact_match(self) -> None:
        mappings = [("platform", normalize_path("/a/b"))]
        assert match_project_for_path("/a/b", mappings) == "platform"

    def test_nested_descendant_matches(self) -> None:
        mappings = [("platform", normalize_path("/a/b"))]
        assert match_project_for_path("/a/b/c/d", mappings) == "platform"

    def test_sibling_does_not_match(self) -> None:
        mappings = [("platform", normalize_path("/a/b/fo"))]
        assert match_project_for_path("/a/b/foo", mappings) is None

    def test_parent_of_registered_does_not_match(self) -> None:
        mappings = [("cdk", normalize_path("/a/b/c"))]
        assert match_project_for_path("/a/b", mappings) is None

    def test_longest_prefix_wins(self) -> None:
        mappings = [("platform", normalize_path("/a/b")), ("cdk", normalize_path("/a/b/c"))]
        assert match_project_for_path("/a/b/c/x", mappings) == "cdk"

    def test_no_mappings(self) -> None:
        assert match_project_for_path("/a/b", []) is None

    def test_file_target_under_registered_dir(self) -> None:
        mappings = [("platform", normalize_path("/a/b"))]
        assert match_project_for_path("/a/b/src/foo.py", mappings) == "platform"

    @pytest.mark.skipif(sys.platform not in {"darwin", "win32"}, reason="case-insensitive FS only")
    def test_case_insensitive_match_on_case_insensitive_fs(self) -> None:
        mappings = [("platform", normalize_path("/Users/X/Work"))]
        assert match_project_for_path("/users/x/work/proj", mappings) == "platform"

    @pytest.mark.skipif(sys.platform in {"darwin", "win32"}, reason="case-sensitive FS only")
    def test_case_sensitive_no_match_on_linux(self) -> None:
        mappings = [("platform", normalize_path("/srv/Work"))]
        assert match_project_for_path("/srv/work/proj", mappings) is None


class TestResolveProjectForPath:
    def test_missing_db_file_returns_none(self, tmp_path: Path) -> None:
        assert resolve_project_for_path("/anything", db_path=tmp_path / "nope.db") is None

    def test_missing_table_returns_none(self, tmp_path: Path) -> None:
        bare = tmp_path / "bare.db"
        sqlite3.connect(str(bare)).close()
        assert resolve_project_for_path("/anything", db_path=bare) is None

    def test_corrupt_db_returns_none(self, tmp_path: Path) -> None:
        junk = tmp_path / "junk.db"
        junk.write_bytes(b"not a database at all")
        assert resolve_project_for_path("/anything", db_path=junk) is None

    def test_happy_path(self, tmp_path: Path) -> None:
        db_path = tmp_path / "memory.db"
        db = DatabaseManager(db_path)
        repo = tmp_path / "acme-service-infra"
        repo.mkdir()
        db.set_project_paths("platform", [str(repo)])
        db.close()
        assert (
            resolve_project_for_path(str(repo / "lib" / "stack.ts"), db_path=db_path) == "platform"
        )

    def test_unmatched_path_returns_none(self, tmp_path: Path) -> None:
        db_path = tmp_path / "memory.db"
        db = DatabaseManager(db_path)
        repo = tmp_path / "registered"
        repo.mkdir()
        db.set_project_paths("platform", [str(repo)])
        db.close()
        assert resolve_project_for_path(str(tmp_path / "elsewhere"), db_path=db_path) is None
