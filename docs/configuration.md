# Configuration

Environment variables. The server reads the first table. The hook plugin reads the second, from the agent's environment.

`mcp-memory setup-service` stores these variables from your shell in the service: `MCP_MEMORY_PURGE_*`, `MCP_MEMORY_GC_*`, `MCP_MEMORY_SURFACED_RETENTION_DAYS` and `MCP_DREAM_*`. It keeps `MCP_DREAM_*` values already installed. To set any other variable, edit the plist or unit.

## Server

| Variable | Description | Default |
|---|---|---|
| `MCP_MEMORY_DB_PATH` | Database file | `~/.local/share/mcp-memory/memory.db` |
| `MCP_MEMORY_PORT` | HTTP port | `8000` |
| `MCP_MEMORY_URL` | Base URL that dream passes, the hook plugin and the CLI commands use to reach the server. Overrides `MCP_MEMORY_PORT` and the installed service's port | from the port |
| `MCP_MEMORY_MAX_OBSERVATION_CHARS` | Per-entity observation character budget for reads, highest-voted first. Negative means unlimited | `2000` |
| `MCP_MEMORY_DEDUPE_TURNS` | How many tool calls a memory read's observations stay deduplicated as hashes in `seen` within one context window. `0` disables | `40` |
| `MCP_MEMORY_STRICT_POLICY` | Reject project-scoped `user-preferences` and direct `task -> project` relations | off |
| `MCP_MEMORY_SWEEP_INTERVAL_SECONDS` | How often the maintenance sweep runs. It also runs once at startup | `43200` |
| `MCP_MEMORY_ARCHIVE_ENABLED` | Sweep: archive resolved entities untouched for 56+ days, unless they were surfaced and then acted on | `true` |
| `MCP_MEMORY_GC_ENABLED` | Sweep: soft-delete orphan entities with a vote score of `-10` or lower and no live incoming relation | off |
| `MCP_MEMORY_PURGE_ENABLED` | Sweep: hard-delete entities, observations and relations that were soft-deleted longer ago than the grace window. This is the only permanent removal of them | off |
| `MCP_MEMORY_PURGE_GRACE_DAYS` | Grace window for the purge | `30` |
| `MCP_MEMORY_CALL_METRICS_ENABLED` | Record per-call usage metrics (byte sizes and option usage) | `true` |
| `MCP_MEMORY_CALL_METRICS_RETENTION_DAYS` | Days to keep call metrics | `90` |
| `MCP_MEMORY_SURFACED_RETENTION_DAYS` | Days to keep retrieval telemetry. Negative means forever. The ranking eval needs its `used_at` labels | `-1` |
| `MCP_MEMORY_EVAL_CACHE_TTL_SECONDS` | How long a computed ranking-quality report is cached | `300` |
| `MCP_MEMORY_BACKUP_ENABLED` | Back up the database on server start, every interval, and before each heavy dream pass. Also saves each dream agent's prompt and output as `dream-<tier>-<UTC timestamp>.jsonl` beside the backups, pruned with the same retention windows | `true` |
| `MCP_MEMORY_BACKUP_DIR` | Directory holding timestamped database backups | `backups` beside the database |
| `MCP_MEMORY_BACKUP_INTERVAL_SECONDS` | How often the server backs up the database | `21600` |
| `MCP_MEMORY_BACKUP_KEEP_DAILY` | Days that keep their newest backup after the last 24 hours. Every backup from the last 24 hours is kept | `7` |
| `MCP_MEMORY_BACKUP_KEEP_WEEKLY` | ISO weeks that keep their newest backup | `4` |
| `MCP_MEMORY_BACKUP_KEEP_MONTHLY` | Months that keep their newest backup | `6` |
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
- Heavy (`MCP_DREAM_HEAVY_ENABLED`): does the same. Each pass first applies the audit's safe steps in code (renames, merges, observation merges, feature relinks, stale archives), then runs the memory-review checklist with reversible tools only (trims, splits, relinks orphans, soft-deletes outright wrong content). It never deletes an entity with live `depends-on` or `implements` edges.

When `MCP_DREAM_HEAVY_ENABLED` is on, the memory-review nudge is not shown, since the heavy pass runs the review.

Each tier fires once per idle session. At most one pass runs at a time. The visualiser's dream card has Run light and Run heavy buttons that start a pass on demand, whether or not the tier is enabled.

`mcp-memory dream light|heavy` does the same from the command line by asking the running server to start the pass. It exits non-zero if the server is down or refuses. `--wait` prints each memory change the pass makes as it happens, then the final result.

| Variable | Description | Default |
|---|---|---|
| `MCP_DREAM_ENABLED` | Enable the light tier | off |
| `MCP_DREAM_IDLE_SECONDS` | Idle time before the light pass fires | `1800` |
| `MCP_DREAM_POLL_SECONDS` | How often to check whether it is due | `300` |
| `MCP_DREAM_MODEL` | Model id | `claude-sonnet-5-5` (the Bedrock id from `~/.claude/settings.json` `modelOverrides` on Bedrock) |
| `MCP_DREAM_TIMEOUT` | Timeout per pass, seconds | `900` |
| `MCP_DREAM_MAX_VOTES` | Advisory cap on entities and observations demoted per pass | `50` |
| `MCP_DREAM_HEAVY_ENABLED` | Enable the heavy tier | off |
| `MCP_DREAM_HEAVY_IDLE_SECONDS` | Idle time before the heavy pass fires | `5400` |
| `MCP_DREAM_HEAVY_POLL_SECONDS` | How often to check whether it is due | `900` |
| `MCP_DREAM_HEAVY_MODEL` | Model id | `claude-opus-5-5` (the Bedrock id from `~/.claude/settings.json` `modelOverrides` on Bedrock) |
| `MCP_DREAM_HEAVY_TIMEOUT` | Timeout per pass, seconds | `3600` |
| `MCP_DREAM_HEAVY_MAX_OPS` | Advisory cap on merges, demotions, deletes, renames, links and archives per pass | `100` |
