"""Token-efficiency payload-size measurement tests.

Measures the serialized-JSON byte size of tool responses on seeded synthetic databases -
a deterministic proxy for token cost. Coupled to no live state.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from mcp_memory import payload
from mcp_memory.database import DatabaseManager
from mcp_memory.models import Relation

from . import SeedEntity, seed

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def db(tmp_path: Path) -> DatabaseManager:
    """Create a fresh database for each test."""
    return DatabaseManager(tmp_path / "payload.db")


class TestPayloadSize:
    def test_payload_size_of_empty_result_is_small(self) -> None:
        size = payload.payload_size({"entities": [], "relations": []})
        assert size > 0
        assert size < 60

    def test_payload_size_grows_with_more_entities(self, tmp_path: Path) -> None:
        small_db = DatabaseManager(tmp_path / "small.db")
        seed(small_db, "proj", [SeedEntity("task/one", ["alpha beta"])])
        small_size = payload.payload_size(small_db.search_nodes("proj", "alpha", limit=10))

        large_db = DatabaseManager(tmp_path / "large.db")
        seed(
            large_db,
            "proj",
            [SeedEntity(f"task/n-{i}", ["alpha beta"]) for i in range(5)],
        )
        large_size = payload.payload_size(large_db.search_nodes("proj", "alpha", limit=10))

        assert large_size > small_size

    def test_payload_size_serializes_dataclasses(self, db: DatabaseManager) -> None:
        seed(db, "proj", [SeedEntity("task/a", ["observation content"])])
        size = payload.payload_size(db.search_nodes("proj", "observation", limit=10))
        assert size > 0

    def test_payload_size_raises_on_unserializable(self) -> None:
        with pytest.raises(TypeError):
            payload.payload_size(object())

    def test_payload_size_counts_utf8_bytes_not_chars(self) -> None:
        ascii_size = payload.payload_size({"o": "aaaa"})
        multibyte_size = payload.payload_size({"o": "éééé"})
        assert multibyte_size > ascii_size


class TestCompactSavings:
    def test_compact_saves_bytes_when_observations_present(self, db: DatabaseManager) -> None:
        long_obs = "cache eviction ttl strategy for the distributed layer " * 5
        seed(
            db,
            "proj",
            [SeedEntity(f"task/cache-{i}", [long_obs]) for i in range(3)],
        )

        result = payload.compact_savings(db, "proj", "cache")

        assert result.compact_bytes < result.full_bytes
        assert result.saved_bytes > 0
        assert 0 < result.ratio <= 1

    def test_compact_savings_ratio_is_fraction_saved(self, db: DatabaseManager) -> None:
        long_obs = "cache eviction ttl strategy for the distributed layer " * 5
        seed(
            db,
            "proj",
            [SeedEntity(f"task/cache-{i}", [long_obs]) for i in range(3)],
        )

        result = payload.compact_savings(db, "proj", "cache")

        assert result.saved_bytes == result.full_bytes - result.compact_bytes
        assert result.ratio == pytest.approx(result.saved_bytes / result.full_bytes)

    def test_compact_savings_zero_when_no_results(self, db: DatabaseManager) -> None:
        seed(db, "proj", [SeedEntity("task/pytest", ["pytest fixture teardown"])])

        result = payload.compact_savings(db, "proj", "quantum cryptography lattice")

        assert result.saved_bytes == 0
        assert result.ratio == 0.0


def _long_observations() -> list[str]:
    return ["cache eviction ttl strategy for the distributed layer " * 6 for _ in range(10)]


class TestObservationBudget:
    def test_budget_cuts_bytes_versus_unlimited(self, db: DatabaseManager) -> None:
        seed(db, "proj", [SeedEntity("task/cache", _long_observations())])

        budgeted = payload.payload_size(
            db.search_nodes("proj", "cache", max_observation_chars=2000)
        )
        unlimited = payload.payload_size(db.search_nodes("proj", "cache", max_observation_chars=-1))

        assert budgeted < unlimited

    def test_budget_is_middle_ground_between_compact_and_unlimited(
        self, db: DatabaseManager
    ) -> None:
        seed(db, "proj", [SeedEntity("task/cache", _long_observations())])

        compact = payload.payload_size(db.search_nodes("proj", "cache", compact=True))
        budgeted = payload.payload_size(
            db.search_nodes("proj", "cache", max_observation_chars=2000)
        )
        unlimited = payload.payload_size(db.search_nodes("proj", "cache", max_observation_chars=-1))

        assert compact < budgeted < unlimited

    def test_zero_budget_is_smallest_non_compact_result(self, db: DatabaseManager) -> None:
        seed(db, "proj", [SeedEntity("task/cache", _long_observations())])

        compact = payload.payload_size(db.search_nodes("proj", "cache", compact=True))
        zero = payload.payload_size(db.search_nodes("proj", "cache", max_observation_chars=0))
        default = payload.payload_size(db.search_nodes("proj", "cache"))

        assert compact < zero < default

    def test_get_entity_with_relations_budget_cuts_bytes(self, db: DatabaseManager) -> None:
        obs = _long_observations()
        seed(
            db,
            "proj",
            [
                SeedEntity("task/primary", obs),
                SeedEntity("task/related-a", obs),
                SeedEntity("task/related-b", obs),
            ],
        )
        db.create_relations(
            "proj",
            [
                Relation("task/primary", "task/related-a", "relates-to"),
                Relation("task/primary", "task/related-b", "relates-to"),
            ],
        )

        budgeted = payload.payload_size(
            db.get_entity_with_relations("proj", "task/primary", max_observation_chars=2000)
        )
        unlimited = payload.payload_size(
            db.get_entity_with_relations("proj", "task/primary", max_observation_chars=-1)
        )

        assert budgeted < unlimited


class TestPerToolPayloads:
    def test_per_tool_payloads_one_entry_per_tool(self, db: DatabaseManager) -> None:
        seed(db, "proj", [SeedEntity("task/a", ["needle"])])
        mapping = {
            "search_nodes": db.search_nodes("proj", "needle", limit=10),
            "read_graph": db.read_graph("proj"),
        }

        result = payload.per_tool_payloads(mapping)

        assert len(result) == 2
        assert {p.tool for p in result} == {"search_nodes", "read_graph"}
        assert all(p.bytes > 0 for p in result)

    def test_per_tool_payloads_sorted_by_tool_name(self, db: DatabaseManager) -> None:
        seed(db, "proj", [SeedEntity("task/a", ["needle"])])
        mapping = {
            "search_nodes": db.search_nodes("proj", "needle", limit=10),
            "get_entity_with_relations": db.search_nodes("proj", "needle", limit=10),
            "read_graph": db.read_graph("proj"),
        }

        result = payload.per_tool_payloads(mapping)

        tools = [p.tool for p in result]
        assert tools == sorted(tools)

    def test_per_tool_payloads_empty_mapping(self) -> None:
        assert payload.per_tool_payloads({}) == []
