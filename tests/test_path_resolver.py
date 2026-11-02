"""Tests for path-to-project resolution."""

from __future__ import annotations

from pathlib import Path
import sys

import pytest

from mcp_memory.path_resolver import (
    match_project_for_path,
    normalize_path,
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
