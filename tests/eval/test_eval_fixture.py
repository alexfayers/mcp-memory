"""Integrity tests for the populated eval fixture.

`TestTopicZeroSlice` covers the topic-0/core-platform slice alone, a fast regression
check. `TestFullFixture` covers the full 130-entity, 96-query build and the invariants
that only exist at that scale (the relevant-set histogram, the archive sweep).
"""

from __future__ import annotations

from collections import Counter
import math
import re
import shutil
from typing import TYPE_CHECKING

import pytest

from mcp_memory import eval as ranking_eval
from mcp_memory.storage import open_writable
from mcp_memory.storage.operations.maintenance import GC_DOWNVOTE_FLOOR
from mcp_memory.storage.pure.ranking import _VOTE_SCALE, _VOTE_WEIGHT

from .eval_fixture import (
    _ARCHETYPE_COUNTS,
    _CROSS_PROJECT_QUERIES,
    _MEDIUM_SCOPE_PROJECTS,
    _PROJECTS,
    _SCOPE_MARKERS,
    _SMALL_SCOPE_PROJECTS,
    _SPINE,
    _TOPICS,
    _build_populated_fixture,
)
from .eval_harness import BASELINE, FLOOR, assert_within_floor

if TYPE_CHECKING:
    from pathlib import Path

    from .eval_fixture import EvalFixture

_MAX_FIXTURE_AGE_DAYS = 139
_TOTAL_ENTITIES = 130
_TOTAL_QUERIES = 96
_RELEVANT_LABEL_HISTOGRAM = {1: 71, 2: 18, 3: 5, 5: 1, 9: 1}
_VOTE_MULTIPLIER_CEILING = 1.5


@pytest.fixture
def topic0(tmp_path: Path) -> EvalFixture:
    """Build the fixture's topic-0/core-platform slice on a fresh database."""
    db = open_writable(tmp_path / "eval-fixture.db")
    return _build_populated_fixture(db, projects=_PROJECTS[:1], topics=_TOPICS[:1])


@pytest.fixture(scope="module")
def fixture(tmp_path_factory: pytest.TempPathFactory) -> EvalFixture:
    """Build the full 12-project, 6-topic fixture once for every test in this module."""
    db_path = tmp_path_factory.mktemp("eval-fixture") / "full.db"
    db = open_writable(db_path)
    return _build_populated_fixture(db)


class TestTopicZeroSlice:
    def test_every_seeded_age_is_within_the_designed_bounds(self, topic0: EvalFixture) -> None:
        ages = [row[3] for row in topic0.manifest]
        assert ages
        assert all(0 <= age <= _MAX_FIXTURE_AGE_DAYS for age in ages)

    def test_entity_names_are_unique(self, topic0: EvalFixture) -> None:
        assert len(set(topic0.entity_names)) == len(topic0.entity_names)

    def test_relevant_names_are_exactly_the_labelled_used_entities(self, topic0: EvalFixture) -> None:
        expected = {
            topic0.name_for(0, "durable-hit"),
            topic0.name_for(0, "decayed-hit"),
            topic0.name_for(0, "upvoted-hit"),
            topic0.name_for(0, "unreachable"),
        }
        assert topic0.relevant_names == expected

    def test_tie_pair_is_identical_except_its_name(self, topic0: EvalFixture) -> None:
        name_a, name_b = topic0.tie_pair(0)
        entity_a = topic0.db.reads.get_entity("core-platform", name_a)
        entity_b = topic0.db.reads.get_entity("core-platform", name_b)

        assert name_a != name_b
        assert entity_a.entity_type == entity_b.entity_type
        assert entity_a.status == entity_b.status
        assert entity_a.vote_score == entity_b.vote_score
        assert [o.content for o in entity_a.observations] == [o.content for o in entity_b.observations]

    def test_archived_entity_never_appears_in_search_results(self, topic0: EvalFixture) -> None:
        archived_name = topic0.name_for(0, "archived")
        results = topic0.db.reads.search("core-platform", "checkout", limit=31, now=topic0.now)["entities"]
        assert archived_name not in [e.name for e in results]

    def test_query_count_matches_the_seeded_retrievals(self, topic0: EvalFixture) -> None:
        assert topic0.query_count == 22

    def test_no_fill_entity_name_contains_a_query_term(self, topic0: EvalFixture) -> None:
        spine_names = {topic0.name_for(0, role) for role, _ in _SPINE}
        fill_names = [name for name in topic0.entity_names if name not in spine_names]
        terms = {term for _, _, term in _TOPICS}

        for name in fill_names:
            tokens = set(re.split(r"[/-]", name))
            assert not tokens & terms, name

    def test_evaluate_produces_a_nontrivial_report_matching_the_seeded_queries(self, topic0: EvalFixture) -> None:
        report = topic0.expected_baseline
        assert report.query_count == topic0.query_count
        assert 0.0 < report.mrr < 1.0
        assert 0.0 < report.mean_recall_at_k < 1.0


class TestFullFixture:
    def test_total_entity_count_is_130(self, fixture: EvalFixture) -> None:
        assert len(fixture.entity_names) == _TOTAL_ENTITIES

    def test_every_seeded_age_is_within_the_designed_bounds(self, fixture: EvalFixture) -> None:
        ages = [row[3] for row in fixture.manifest]
        assert len(ages) == _TOTAL_ENTITIES
        assert all(0 <= age <= _MAX_FIXTURE_AGE_DAYS for age in ages)

    def test_entity_names_are_globally_unique(self, fixture: EvalFixture) -> None:
        assert len(set(fixture.entity_names)) == len(fixture.entity_names)

    def test_manifest_order_matches_entity_id_order(self, fixture: EvalFixture) -> None:
        rows = fixture.db.connection.query_all("SELECT id, name FROM entities ORDER BY id")
        assert [row["name"] for row in rows] == [name for _, name, *_ in fixture.manifest]

    def test_archetype_supply_is_exactly_exhausted(self, fixture: EvalFixture) -> None:
        assert sum(_ARCHETYPE_COUNTS.values()) == _TOTAL_ENTITIES
        assert all(count >= 0 for count in fixture.archetype_supply.values())
        assert sum(fixture.archetype_supply.values()) == 0

    def test_unsatisfiable_queries_are_exactly_the_intended_unreachable_ones(self, fixture: EvalFixture) -> None:
        unreachable_names = {fixture.name_for(topic_index, "unreachable") for topic_index, _, _ in _TOPICS}
        zero_hit_queries = []
        for query in ranking_eval.iter_labelled_queries(fixture.db):
            if not query.relevant:
                continue
            result = fixture.db.reads.search(
                query.project,
                query.query,
                limit=max(fixture.k, len(query.ranked)),
                now=fixture.now,
            )
            found = {entity.name for entity in result["entities"]}
            if found & query.relevant:
                continue
            zero_hit_queries.append(query)
            assert query.relevant <= unreachable_names, (query.project, query.query)

        assert len(zero_hit_queries) == 15

    def test_no_vote_reaches_the_gc_downvote_floor(self, fixture: EvalFixture) -> None:
        votes = [row[4] for row in fixture.manifest]
        assert votes
        assert all(vote > GC_DOWNVOTE_FLOOR for vote in votes)

    def test_one_entity_reaches_the_vote_multiplier_ceiling(self, fixture: EvalFixture) -> None:
        prefs_rows = [row for row in fixture.manifest if row[2] == "user-preferences"]
        assert len(prefs_rows) == 1

        vote = prefs_rows[0][4]
        multiplier = 1.0 + _VOTE_WEIGHT * math.tanh(vote / _VOTE_SCALE)
        assert multiplier == pytest.approx(_VOTE_MULTIPLIER_CEILING, abs=1e-3)

    def test_no_fill_entity_name_contains_a_query_or_marker_term(self, fixture: EvalFixture) -> None:
        spine_names = {fixture.name_for(topic_index, role) for topic_index, _, _ in _TOPICS for role, _ in _SPINE}
        fill_names = [name for name in fixture.entity_names if name not in spine_names]
        blocked_terms = {term for _, _, term in _TOPICS} | set(_SCOPE_MARKERS.values())

        for name in fill_names:
            tokens = set(re.split(r"[/-]", name))
            assert not tokens & blocked_terms, name

    def test_query_count_is_96(self, fixture: EvalFixture) -> None:
        assert fixture.query_count == _TOTAL_QUERIES

    def test_relevant_label_histogram_matches_the_live_measurement(self, fixture: EvalFixture) -> None:
        queries = list(ranking_eval.iter_labelled_queries(fixture.db))
        assert len(queries) == _TOTAL_QUERIES

        histogram = Counter(len(query.relevant) for query in queries)
        assert dict(histogram) == _RELEVANT_LABEL_HISTOGRAM

        total_labels = sum(size * count for size, count in histogram.items())
        assert total_labels / _TOTAL_QUERIES == pytest.approx(1.4167, abs=1e-4)

    def test_cross_project_queries_map_to_project_none(self, fixture: EvalFixture) -> None:
        queries = [q for q in ranking_eval.iter_labelled_queries(fixture.db) if q.project is None]
        assert len(queries) == len(_CROSS_PROJECT_QUERIES)

    def test_small_and_medium_scope_queries_are_capped_at_four_each(self, fixture: EvalFixture) -> None:
        assert len(_SMALL_SCOPE_PROJECTS) == len(_MEDIUM_SCOPE_PROJECTS) == 4

        scoped_projects = {
            q.project for q in ranking_eval.iter_labelled_queries(fixture.db) if q.project in _SCOPE_MARKERS
        }
        assert scoped_projects == {*_SMALL_SCOPE_PROJECTS, *_MEDIUM_SCOPE_PROJECTS}

    @pytest.mark.parametrize("topic_index", [t[0] for t in _TOPICS])
    def test_tie_pair_is_identical_except_its_name(self, fixture: EvalFixture, topic_index: int) -> None:
        project = next(p for i, p, _ in _TOPICS if i == topic_index)
        name_a, name_b = fixture.tie_pair(topic_index)
        entity_a = fixture.db.reads.get_entity(project, name_a)
        entity_b = fixture.db.reads.get_entity(project, name_b)

        assert name_a != name_b
        assert entity_a.entity_type == entity_b.entity_type
        assert entity_a.status == entity_b.status
        assert entity_a.vote_score == entity_b.vote_score
        assert [o.content for o in entity_a.observations] == [o.content for o in entity_b.observations]

    def test_full_fixture_baseline_matches_the_committed_provenance(self, fixture: EvalFixture) -> None:
        """The measured report's shape matches the artefact `FLOOR` is derived from."""
        report = fixture.expected_baseline
        assert report.query_count == _TOTAL_QUERIES
        assert (report.query_count, report.k) == (BASELINE.query_count, BASELINE.k)
        assert all(0.0 < getattr(report, metric) < 1.0 for metric in FLOOR)

    def test_full_fixture_baseline_is_within_floor(self, fixture: EvalFixture, request: pytest.FixtureRequest) -> None:
        """Ratchet gate: fails on a regression and equally on an unexplained improvement."""
        assert_within_floor(fixture.expected_baseline, request)

    def test_archive_sweep_at_scale_spares_relevant_and_archives_the_rest(
        self, fixture: EvalFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fixture.db.connection.write("PRAGMA wal_checkpoint(TRUNCATE)")
        copy_path = tmp_path / "sweep-copy.db"
        shutil.copy(fixture.path, copy_path)

        unprotected_resolved = {
            name
            for _, name, _, _, _, _, status in fixture.manifest
            if status == "resolved" and name not in fixture.relevant_names
        }
        assert unprotected_resolved, "fixture has no unprotected resolved entity to archive"

        monkeypatch.setenv("MCP_MEMORY_ARCHIVE_ENABLED", "true")
        swept = open_writable(copy_path)
        try:
            for name in unprotected_resolved:
                row = swept.connection.query_one("SELECT status FROM entities WHERE name = ?", (name,))
                assert row["status"] == "archived", name
            for name in fixture.relevant_names:
                row = swept.connection.query_one("SELECT status FROM entities WHERE name = ?", (name,))
                assert row["status"] != "archived", name
        finally:
            swept.connection.close()
