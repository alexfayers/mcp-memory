"""Pure ranking-score arithmetic for search results."""

from __future__ import annotations

from datetime import UTC, datetime
import math

_RECENCY_HALF_LIFE_DAYS = 30.0
_RECENCY_FLOOR = 0.1

# Per-type recency half-lives (days): durable knowledge decays slowly so it is not buried by
# age, while work items decay fast. Unlisted types fall back to _RECENCY_HALF_LIFE_DAYS.
_TYPE_HALF_LIFE_DAYS: dict[str, float] = {
    "task": 14.0,
    "feature": 90.0,
    "project": 180.0,
    "knowledge": 180.0,
    "pattern": 365.0,
    "user-preferences": 365.0,
}

# How long a resolved entity may go untouched before it is eligible for auto-archiving.
ARCHIVE_STALE_DAYS = int(4 * _TYPE_HALF_LIFE_DAYS["task"])

# Usefulness votes nudge ranking by a bounded multiplier 1 + weight*tanh(score/scale), so a
# runaway score cannot dominate BM25 and a downvoted entity sinks but stays findable.
_VOTE_WEIGHT = 0.5
_VOTE_SCALE = 5.0


def score_row(*, rank: float, updated_at: str, entity_type: str, vote_score: int, now: datetime) -> float:
    bm25_score = -float(rank)
    updated_at_dt = datetime.fromisoformat(updated_at).replace(tzinfo=UTC)
    age_days = max((now - updated_at_dt).total_seconds() / 86400, 0)
    half_life = _TYPE_HALF_LIFE_DAYS.get(entity_type, _RECENCY_HALF_LIFE_DAYS)
    decay = -math.log(2) * age_days / half_life
    recency = max(math.exp(decay), _RECENCY_FLOOR)
    vote_multiplier = 1.0 + _VOTE_WEIGHT * math.tanh(vote_score / _VOTE_SCALE)
    return bm25_score * recency * vote_multiplier
