"""Tests for the data models and relation-type normalization."""

from __future__ import annotations

import pytest

from mcp_memory.models import normalize_relation_type


class TestNormalizeRelationType:
    def test_canonical_passes_through(self) -> None:
        assert normalize_relation_type("implements") == "implements"

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("related_to", "relates-to"),
            ("related-to", "relates-to"),
            ("Related-To", "relates-to"),
            ("part_of", "part-of"),
            ("blockedBy", "depends-on"),
            ("finding_in", "relates-to"),
            ("  implements  ", "implements"),
            ("extends", "implements"),
            ("has-overlay", "used-by"),
            ("overlay-for", "used-in"),
            ("assigned_to", "belongs-to"),
            ("owned-by", "belongs-to"),
            ("changes", "implements"),
            ("modifies", "implements"),
            ("modified", "implements"),
            ("verifies", "implements"),
            ("continues", "depends-on"),
            ("replaces", "relates-to"),
            ("discovered_by", "relates-to"),
            ("research_for", "relates-to"),
            ("context_for", "relates-to"),
            ("has_architecture", "part-of"),
            ("has_todos", "relates-to"),
            ("self", "relates-to"),
        ],
    )
    def test_variants_and_aliases(self, raw: str, expected: str) -> None:
        assert normalize_relation_type(raw) == expected

    @pytest.mark.parametrize("raw", ["", "   ", "_", "-"])
    def test_empty_raises(self, raw: str) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            normalize_relation_type(raw)
