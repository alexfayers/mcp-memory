# Configuration

Environment variables. The server reads the first table. The hook plugin reads the second, from the agent's environment.

`mcp-memory setup-service` stores these variables from your shell in the service: `MCP_MEMORY_PURGE_*`, `MCP_MEMORY_GC_*`, `MCP_MEMORY_SURFACED_RETENTION_DAYS`, `MCP_DREAM_*`, `DO_NOT_TRACK` and `LLM_PROMPTS_NO_TELEMETRY`. It keeps `MCP_DREAM_*`, `DO_NOT_TRACK` and `LLM_PROMPTS_NO_TELEMETRY` values already installed. To set any other variable, edit the plist or unit.

## Server

| Variable | Description | Default |
|---|---|---|
| `MCP_MEMORY_DB_PATH` | Database file | `mcp-memory/data/memory.db` in the llm-prompts root (`$LLM_PROMPTS_HOME`, else `$XDG_DATA_HOME/llm-prompts`, else `~/.local/share/llm-prompts`) |
| `MCP_MEMORY_PORT` | HTTP port | `8000` |
| `MCP_MEMORY_URL` | Base URL that dream passes, the hook plugin and the CLI commands use to reach the server. Overrides `MCP_MEMORY_PORT` and the installed service's port | from the port |
| `MCP_MEMORY_NO_SERVICE` | When true (`1`, `true`, `yes` or `on`), `setup-service` and `install` leave launchd and systemd alone. They install no service and remove no retired one | off |
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

### Per-scope retention

`set_metadata` with `kind='retention'` gives one project its own window in days. The window overrides the global default for that project only. A negative window means never.

| Setting | Overrides |
|---|---|
| `purge_grace` | `MCP_MEMORY_PURGE_GRACE_DAYS` |
| `surfaced_retention` | `MCP_MEMORY_SURFACED_RETENTION_DAYS` |
| `orphan_gc` | No global default. Orphan GC spares an entity updated within this many days |

## Hook plugin

| Variable | Description | Default |
|---|---|---|
| `MCP_MEMORY_WORKSPACE_MARKERS` | Comma-separated directory names that mark a multi-package workspace root, so sibling packages share one project scope. Markers from other cline-hooks plugins are used after these | none |
| `MCP_MEMORY_READONLY_AGENTS` | Extra agent types exempt from the memory-update gate | `Explore`, `Plan` |
| `MCP_MEMORY_EDIT_TOOLS` | Extra file-edit tool names, counted at reduced weight toward the gate | `replace_in_file`, `write_to_file` |
| `MCP_MEMORY_WRAPPER_TOOLS` | Extra tools that run other tool calls. Counted as one call, unless their input names a memory write tool | `codemode` |
| `MCP_MEMORY_UNCOUNTED_TOOLS` | Extra bookkeeping tools that are not counted toward the gate | the frontend's bookkeeping, skill and message tools |

Stopping needs a `task/` write only after wide work: a `git commit`, edits to more than one file, a presented plan or a writer delegation. Stop is blocked once per piece of work.

The `MEMORY CHECK` reminder comes after a weighted count of 8 since the first file edit, with a block at 10.

Observation triggers (see `add_observations`) apply to every agent, including subagents and calls nested in a wrapper tool. A `pattern` trigger blocks the first matching call once per agent, and the repeat call runs. A `path` trigger adds its observations after the first read or edit of a matching file, a `mention` trigger adds them when a turn ends, on frontends with a transcript reader, and a `change` trigger adds them before an edit of lines blamed to its commit. An edit that names no replaced text counts every line of the file. The plugin keeps each commit's patch-id next to the database. Each observation is shown once per agent, at most 2 per match, best-voted first. The plugin keeps a copy of the trigger rows next to the database and asks the server at most every 5 seconds whether they changed. It matches only the first 20,000 characters of a call's input or a turn's text.

On each user message, the plugin names up to 2 entities from the task's project and `global` that strongly match it, ranked as `search_nodes` ranks them, each with its first observation. A match is strong when its BM25 score reaches 7 times the square root of the message's word count, and its name (without the type prefix, such as `task/`) or first observation holds at least 2 of the message's words of 3 or more letters, other than common words. A short reply such as "ok thanks" names nothing. Only the first 20,000 characters of a message are matched. Each entity is named once per agent.

Another cline-hooks plugin can add workspace markers by implementing the `memory_workspace_markers` hook. It returns a `frozenset[str]` of directory names:

```python
from cline_hooks.core.plugin import hookimpl

@hookimpl(optionalhook=True)
def memory_workspace_markers(self) -> frozenset[str]:
    return frozenset({".workspace-root"})
```

## Dreaming

Dreaming lets the server curate memory while it is idle. It runs a headless `pi -p` session when the `pi` CLI is on `PATH` and a pi model resolves, else a headless `claude -p` session, and the session connects back to the server. Both tiers are off by default. Set the variables, then run `mcp-memory setup-service` so the service picks them up.

- Light (`MCP_DREAM_ENABLED`): downvotes stale, superseded or duplicated entities and observations. It never deletes.
- Heavy (`MCP_DREAM_HEAVY_ENABLED`): does the same. Each pass first applies the audit's safe steps in code (renames, merges of an unprefixed entity into its prefixed twin, feature relinks, stale archives), then runs the memory-review checklist with reversible tools only (merges, trims, splits, relinks orphans, soft-deletes outright wrong content). It never deletes an entity with live `depends-on` or `implements` edges. While it is on and a `pi` or `claude` CLI is on `PATH`, the hook skips the "MEMORY REVIEW DUE" nudge.

Each tier fires once per idle session. At most one pass runs at a time. The visualiser's dream card has Run light and Run heavy buttons that start a pass on demand, whether or not the tier is enabled.

A running pass records each memory change it makes, and a finished pass records its token usage, turns and cost. `/api/dream` returns them as `progress` and `last_pass.metrics`. A failed agent's exit code, stderr and stdout tail go to the server log.

`mcp-memory dream light|heavy` does the same from the command line by asking the running server to start the pass. It exits non-zero if the server is down or refuses. `--wait` prints each memory change the pass makes as it happens, then the final result, action counts and token usage.

| Variable | Description | Default |
|---|---|---|
| `MCP_DREAM_ENABLED` | Enable the light tier | off |
| `MCP_DREAM_IDLE_SECONDS` | Idle time before the light pass fires | `1800` |
| `MCP_DREAM_POLL_SECONDS` | How often to check whether it is due | `300` |
| `MCP_DREAM_MODEL` | Full `claude-<family>-<version>` model id, not an alias | `claude-sonnet-5-5` (the Bedrock id from `~/.claude/settings.json` `modelOverrides`, when llm-prompts is installed) |
| `MCP_DREAM_TIMEOUT` | Timeout per pass, seconds | `300` |
| `MCP_DREAM_MAX_VOTES` | Advisory cap on entities and observations demoted per pass | `15` |
| `MCP_DREAM_HEAVY_ENABLED` | Enable the heavy tier and turn off the memory-review nudge | off |
| `MCP_DREAM_HEAVY_IDLE_SECONDS` | Idle time before the heavy pass fires | `5400` |
| `MCP_DREAM_HEAVY_POLL_SECONDS` | How often to check whether it is due | `900` |
| `MCP_DREAM_HEAVY_MODEL` | Full `claude-<family>-<version>` model id, not an alias | `claude-opus-5-5` (the Bedrock id from `~/.claude/settings.json` `modelOverrides`, when llm-prompts is installed) |
| `MCP_DREAM_HEAVY_TIMEOUT` | Timeout per pass, seconds | `600` |
| `MCP_DREAM_HEAVY_MAX_OPS` | Advisory cap on merges, demotions, deletes, renames, links and trims per pass | `10` |
| `MCP_DREAM_PI_MODEL_LIGHT` | Pi model for the light tier, as `provider/id` | see below |
| `MCP_DREAM_PI_MODEL_HEAVY` | Pi model for the heavy tier, as `provider/id` | see below |
| `MCP_DREAM_PI_EXTENSIONS` | Comma-separated pi extension paths to load, for a model provider that an extension supplies | none |
| `MCP_DREAM_AGENT_DIR` | The pi dream agent directory | `$XDG_CONFIG_HOME/mcp-memory/dream-agent` |

The claude model variables above apply to `claude -p` only. Pi runs from its own dream agent directory. The server creates it on first use with an `mcp.json` that holds only the memory server, an empty `settings.json`, and links to your pi `auth.json` and `models.json`. Edit `settings.json` freely, the server never overwrites it. The server keeps `mcp.json` memory-only: it rewrites the file's server list to the memory server and sets the `x-mcp-memory-dream` header on it, so any other server you add is removed. Your other settings on the memory server, such as `timeout`, stay. Other top-level keys stay. The pi model for a tier is the first of: its `MCP_DREAM_PI_MODEL_*` variable, the `dreamModelLight` or `dreamModelHeavy` key in the dream agent `settings.json`, then your pi default model. When none is set, or pi cannot find the model, the pass runs through `claude -p` if the `claude` CLI is on `PATH`, and fails otherwise.
