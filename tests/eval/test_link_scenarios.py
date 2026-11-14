"""Tests for the link scenarios scored on what the real recall tools show an agent."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.eval.eval_harness import MeasuredChange, assert_improves, assert_no_regression
from tests.eval.link_scenarios import DECOYS, SCENARIOS, Scenario, measure_scenario

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize("decoys", [0, DECOYS], ids=["clean", "noisy"])
@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda scenario: scenario.name)
def test_links_never_hurt_and_help_where_the_answer_is_one_hop_away(
    scenario: Scenario, decoys: int, tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    reports = measure_scenario(scenario, tmp_path, decoys=decoys)
    unlinked_to_a = MeasuredChange(before=reports["C"], after=reports["A"])

    if scenario.one_hop:
        assert_improves(unlinked_to_a, request, "mean_success_at_k")
    else:
        assert not any(unlinked_to_a.deltas.values()), unlinked_to_a.format()
    assert_no_regression(MeasuredChange(before=reports["C"], after=reports["B"]), request)
