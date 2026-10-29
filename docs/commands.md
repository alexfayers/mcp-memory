# Commands

`mcp-memory` with no arguments runs the server in the foreground. Run `mcp-memory <command> --help` for flags.

| Command | What it does |
|---|---|
| `setup-service [--port] [--db-path]` | Install the background service |
| `restart` | Restart the service |
| `install <target>` | Register the server with an agent |
| `uninstall copilot [--mcp-config]` | Remove the server from VS Code's `mcp.json` |
| `audit` | Hygiene report on the graph |
| `eval` | Search ranking quality |
| `metrics` | Tool usage report |
| `export` / `import` | Back up or merge projects |
| `migrate-db` | Move the database to the default path |

## install

`mcp-memory install <target>` registers the server at `http://localhost:<port>/mcp`. It takes `--port`, which defaults to `MCP_MEMORY_PORT`, then the installed service's port, then `8000`.

| Target | What it does |
|---|---|
| `claude-code` | runs `claude mcp add` |
| `codex` | runs `codex mcp add` |
| `antigravity` | runs `agy mcp add` |
| `pi` | patches `mcp.json` in `~/.pi/agent` (or `$PI_CODING_AGENT_DIR`) |
| `kiro <agent-config.json>` | patches the given Kiro agent JSON |

## Service

`setup-service` installs a launchd job on macOS or a systemd unit on Linux (needs sudo). It takes `--port` (default 8000) and `--db-path` (default `~/.local/share/mcp-memory/memory.db`). Re-running it without `--port` keeps the installed service's port.

It refuses to replace a `com.mcp-memory` job that launchd loaded from another plist, and on Linux an existing unit while HOME is not your real home.

| | macOS | Linux |
|---|---|---|
| File | `~/Library/LaunchAgents/com.mcp-memory.plist` | `/etc/systemd/system/mcp-memory.service` |
| Restart | `mcp-memory restart` | `mcp-memory restart` |
| Stop | `launchctl bootout "gui/$(id -u)/com.mcp-memory"` | `sudo systemctl stop mcp-memory` |
| Uninstall | stop, then delete the plist | `sudo systemctl disable --now mcp-memory`, delete the unit, `sudo systemctl daemon-reload` |

Logs go to `mcp-memory.log` next to the database.

## audit

```bash
mcp-memory audit --project <scope>
mcp-memory audit --all-projects --propose-plan
```

Read-only. Prints JSON listing orphans, misused `project` entities, unprefixed names, ghost scopes, oversized entities, `task belongs-to project` relations, star-graph tasks and strongly downvoted entities.

`--propose-plan` prints `{"steps": [...]}` of fix-up tool calls instead. A step with `needs_review: false` is deterministic, like `rename_entity`. Orphan relinking, deletes and `consider_split` advisories (entities mixing several scopes) have `needs_review: true`. A `trim_observations_to_outcome` step is `false` only when every observation it drops is a near-duplicate of one it keeps. The trim is a hard delete.

## eval

```bash
mcp-memory eval [--k 10] [--since 7d] [--min-content-tokens N]
```

Read-only. Scores search ranking from recorded retrieval telemetry: mean precision, recall, nDCG and success at `k`, plus MRR. `--since` takes `30m`, `1h`, `7d`, `2w`, `3mo` or an ISO date. `--min-content-tokens` skips queries with fewer words than `N`.

## metrics

```bash
mcp-memory metrics [--since 7d]
```

Prints JSON with, per tool, the call count, mean and median input and output bytes, and how often each option value is used. It also totals bytes and gives an input/output ratio (`null` if no output was recorded). Byte sizes are a proxy for token cost, not real token counts.

## export and import

```bash
mcp-memory export <output_path>

mcp-memory import <input_path>                           # list projects in the file
mcp-memory import <input_path> --project a,b [--dry-run] # merge them in
```

Export writes every live project to one JSON file: entities with type, status, timestamps and votes, observations with hashes and votes, relations, registered paths and groups. Soft-deleted entities and telemetry are left out.

Import is additive and safe to re-run. New entities are inserted as-is. An existing entity of the same type keeps its status and timestamps, takes the higher vote score and gains any missing observations. A name that clashes with a different entity type is skipped and reported. Paths are not imported. The summary prints the source paths so you can register local ones with `set_metadata`.

## migrate-db

```bash
mcp-memory migrate-db [--source <path>]
```

The hook plugin does not inherit `MCP_MEMORY_DB_PATH`, so a service installed with a custom `--db-path` and the plugin read different databases. `migrate-db` stops the service, moves the database to the default path (refusing to overwrite a non-empty target) and repoints the service at it. It finds the current path from the installed service unless you pass `--source`.

## Visualiser

`http://localhost:8000/visualise` shows the memory graph, search, voting, ranking quality, tool usage and dreaming status.
