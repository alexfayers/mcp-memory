# Token-efficiency validation

Observation budgeting (`max_observation_chars`) trims the observations returned by search and
entity-lookup tools to a cumulative character budget, saving tokens. Budgeting shapes
observations *after* ranking has already selected and ordered rows, so it cannot change ranking
quality. This document records how to prove that manually against real telemetry.

## Sign convention

- unset / `None` -> configured default (`MCP_MEMORY_MAX_OBSERVATION_CHARS`, default 2000)
- negative (e.g. `-1`) -> unlimited / full detail (the escape hatch)
- `0` -> single highest-voted observation only, plus an omission sentinel
- `N > 0` -> budget observations to `N` cumulative content characters

## The regression is already guarded by a permanent test

`tests/test_ranking_eval.py::TestBudgetingIsOrthogonalToRanking` runs on every `just test` and
locks in that search returns identical entity names and order across budget values, and that
`evaluate()` produces non-trivial metrics on a meaningful fixture. That seeded test is what
actually guards against future regressions. The manual A/B below is belt-and-suspenders on top of
it, catching any fixture-vs-real-data distribution differences against live telemetry.

## Frozen-snapshot A/B procedure

Ranking metrics MUST be validated against a single **frozen** database snapshot with the code
toggled - never a live before-run compared to a later live after-run. The `surfaced_entities`
telemetry table grows between runs, so two live runs score different query sets and any metric
delta is confounded by that growth, not by the code change. Only a frozen snapshot isolates the
code change as the single variable.

1. Freeze a snapshot of the live database:

   ```sh
   cp "$(uv run python -c 'from mcp_memory.config import get_db_path; print(get_db_path())')" /tmp/mem-snapshot.db
   ```

2. Record baseline metrics on the snapshot with the code change in place:

   ```sh
   MCP_MEMORY_DB_PATH=/tmp/mem-snapshot.db uv run mcp-memory eval --k 5 --min-content-tokens 2
   ```

3. Revert the code change and re-run the **same** command against the **same** snapshot:

   ```sh
   git stash
   MCP_MEMORY_DB_PATH=/tmp/mem-snapshot.db uv run mcp-memory eval --k 5 --min-content-tokens 2
   ```

   The two metric reports MUST be identical - budgeting is orthogonal to ranking.

4. Restore the code change:

   ```sh
   git stash pop
   ```

## Payload measurement

Quantify the bytes saved by budgeting over a few representative queries against the same frozen
snapshot, comparing unlimited (`-1`) to the configured default:

```sh
MCP_MEMORY_DB_PATH=/tmp/mem-snapshot.db uv run python - <<'PY'
from mcp_memory.database import DatabaseManager
from mcp_memory.config import get_db_path
from mcp_memory.payload import payload_size

db = DatabaseManager(get_db_path())
for project, query in [("global", "user preferences"), ("mcp-memory", "budgeting")]:
    full = payload_size(db.search_nodes(project, query, max_observation_chars=-1))
    default = payload_size(db.search_nodes(project, query))
    print(f"{project!r} {query!r}: full={full} default={default} saved={full - default}")
PY
```
