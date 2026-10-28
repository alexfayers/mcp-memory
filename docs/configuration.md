# Configuration

Environment variables. The server reads the first table. The hook plugin reads the second, from the agent's environment.

`mcp-memory setup-service` stores these variables from your shell in the service: `MCP_MEMORY_PURGE_*`, `MCP_MEMORY_GC_*`, `MCP_MEMORY_SURFACED_RETENTION_DAYS` and `MCP_DREAM_*`. It keeps `MCP_DREAM_*` values already installed. To set any other variable, edit the plist or unit.

## Server

| Variable | Description | Default |
|---|---|---|
| `MCP_MEMORY_DB_PATH` | Database file | `~/.local/share/mcp-memory/memory.db` |
| `MCP_MEMORY_PORT` | HTTP port | `8000` |
| `MCP_MEMORY_URL` | Base URL that dream passes and the hook plugin use to reach the server. Overrides `MCP_MEMORY_PORT` and the installed service's port | from the port |
| `MCP_MEMORY_MAX_OBSERVATION_CHARS` | Per-entity observation character budget for reads, highest-voted first. Negative means unlimited | `2000` |
| `MCP_MEMORY_STRICT_POLICY` | Reject project-scoped `user-preferences` and direct `task -> project` relations | off |
| `MCP_MEMORY_SWEEP_INTERVAL_SECONDS` | How often the maintenance sweep runs. It also runs once at startup | `43200` |
| `MCP_MEMORY_ARCHIVE_ENABLED` | Sweep: archive resolved entities untouched for 56+ days, unless they were surfaced and then acted on | `true` |
| `MCP_MEMORY_GC_ENABLED` | Sweep: soft-delete orphan entities with a vote score of `-10` or lower and no live incoming relation | off |
| `MCP_MEMORY_PURGE_ENABLED` | Sweep: hard-delete entities that were soft-deleted longer ago than the grace window. This is the only permanent removal of entities | off |
| `MCP_MEMORY_PURGE_GRACE_DAYS` | Grace window for the purge | `30` |
| `MCP_MEMORY_CALL_METRICS_ENABLED` | Record per-call usage metrics (byte sizes and option usage) | `true` |
| `MCP_MEMORY_CALL_METRICS_RETENTION_DAYS` | Days to keep call metrics | `90` |
| `MCP_MEMORY_SURFACED_RETENTION_DAYS` | Days to keep retrieval telemetry. Negative means forever. The ranking eval needs its `used_at` labels | `-1` |
| `MCP_MEMORY_EVAL_CACHE_TTL_SECONDS` | How long a computed ranking-quality report is cached | `300` |
| `MCP_AUTO_VOTE_WINDOW_SECONDS` | How soon after a search an edit counts as an automatic `+1` | `1800` |
| `MCP_AUTO_VOTE_MAX_PER_DAY` | Per-entity daily cap on automatic votes | `3` |

## Hook plugin

| Variable | Description | Default |
|---|---|---|
| `MCP_MEMORY_WORKSPACE_MARKERS` | Comma-separated directory names that mark a multi-package workspace root, so sibling packages share one project scope. Markers from other cline-hooks plugins are used after these | none |
| `MCP_MEMORY_READONLY_AGENTS` | Extra agent types exempt from the memory-update gate | `Explore`, `Plan` |
| `MCP_MEMORY_EDIT_TOOLS` | Extra file-edit tool names, counted at reduced weight toward the gate | `replace_in_file`, `write_to_file` |
| `MCP_MEMORY_WRAPPER_TOOLS` | Extra tools that run other tool calls. Counted as one call, unless their input names a memory write tool | `codemode` |
| `MCP_MEMORY_UNCOUNTED_TOOLS` | Extra bookkeeping tools that are not counted toward the gate | the frontend's bookkeeping, skill and message tools |

Another cline-hooks plugin can add workspace markers by implementing the `memory_workspace_markers` hook. It returns a `frozenset[str]` of directory names:

```python
from cline_hooks.core.plugin import hookimpl

@hookimpl(optionalhook=True)
def memory_workspace_markers(self) -> frozenset[str]:
    return frozenset({".workspace-root"})
```

## Dreaming

Dreaming lets the server curate memory while it is idle. It runs a headless `claude -p` session that connects back to the server, so the `claude` CLI must be on `PATH`. Both tiers are off by default. Set the variables, then run `mcp-memory setup-service` so the service picks them up.

- Light (`MCP_DREAM_ENABLED`): downvotes stale, superseded or duplicated entities and observations. It never deletes.
- Heavy (`MCP_DREAM_HEAVY_ENABLED`): does the same, and also merges duplicate entities (reversible soft-delete) and duplicate observations in one entity (not reversible).

Each tier fires once per idle session. At most one pass runs at a time. The visualiser's dream card has Run light and Run heavy buttons that start a pass on demand, whether or not the tier is enabled.

| Variable | Description | Default |
|---|---|---|
| `MCP_DREAM_ENABLED` | Enable the light tier | off |
| `MCP_DREAM_IDLE_SECONDS` | Idle time before the light pass fires | `1800` |
| `MCP_DREAM_POLL_SECONDS` | How often to check whether it is due | `300` |
| `MCP_DREAM_MODEL` | Fully-qualified model id | `global.anthropic.claude-haiku-4-5-20251001-v1:0` |
| `MCP_DREAM_TIMEOUT` | Timeout per pass, seconds | `300` |
| `MCP_DREAM_MAX_VOTES` | Advisory cap on entities and observations demoted per pass | `15` |
| `MCP_DREAM_HEAVY_ENABLED` | Enable the heavy tier | off |
| `MCP_DREAM_HEAVY_IDLE_SECONDS` | Idle time before the heavy pass fires | `5400` |
| `MCP_DREAM_HEAVY_POLL_SECONDS` | How often to check whether it is due | `900` |
| `MCP_DREAM_HEAVY_MODEL` | Fully-qualified model id. A bare alias maps to Opus | the light model |
| `MCP_DREAM_HEAVY_TIMEOUT` | Timeout per pass, seconds | `600` |
| `MCP_DREAM_HEAVY_MAX_OPS` | Advisory cap on merges and demotions per pass | `10` |
