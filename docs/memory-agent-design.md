# memory-agent: an LLM curation/recall service in front of mcp-memory

Status: **v1 + v2 built** (`recall` and the autonomous `dream` curation pass
shipped and live-verified)

A dedicated MCP server that puts an LLM "brain" in front of the mcp-memory
data store. A caller delegates heavy memory recall to it and receives distilled
findings, keeping the caller's own working context clean. The same service
autonomously grooms the graph while memory is idle.

This document is the design of record. The full decision log, every stress-test
finding, and the measured numbers live in the memory graph (see
[Memory references](#memory-references)).

## Goals

- **Context cleanliness** (primary). A heavy recall reads a lot of graph JSON;
  absorbing that in a throwaway agent context and returning ~a few distilled
  bullets keeps the caller's main context focused. This is the win - not raw
  token saving, which is small per call but accumulates over long sessions.
- **Improve as it is used.** Usage signals (votes) tune ranking over time.
- **Autonomous grooming.** A periodic pass re-ranks stale/duplicate memories
  with no human in the loop.

Non-goals for v1: writing synthesised observations, deletion, team hosting.

## Architecture

Two independent processes over HTTP:

- **mcp-memory** - the existing data store. Pure SQLite-backed FastMCP server.
  No model, no credentials, no network to any model provider. **Unchanged** by
  this work.
- **memory-agent** - a new, additive FastMCP HTTP server (same shape as
  mcp-memory, different port). It is the LLM layer. It spawns an ephemeral
  headless agent per request and returns its result.

```
caller agent ──recall(query)──▶ memory-agent ──spawns──▶ headless `claude -p`
                                     │                          │
                                     │                   (locked to mcp-memory
                                     │                    read tools only)
                                     ▼                          │
                              returns distilled ◀───────────────┘
                              [project/entity] bullets
```

### Why a separate server (not a tool inside mcp-memory)

mcp-memory runs everywhere, constantly, on every device. Baking agent-spawning
into it would contaminate the hottest, most-replicated process with model
credentials, a CLI dependency, and multi-second inference latency on a path
that is otherwise millisecond data reads. A separate, opt-in server:

- keeps the data path pure and fast;
- isolates blast radius (a hung/failed brain never touches the data layer);
- centralises the recall prompt in **one place** (the server config), so it is
  live-updated with zero redistribution - no per-client copies to drift;
- serves **any** MCP client (not tied to one editor) and is remote-ready;
- makes the recursion guard hold by construction (below).

## v1 - `recall` (read-only)

A single tool. This is the whole context-cleanliness win and ships first.

- **Handler MUST be `async def`.** FastMCP runs sync tool handlers directly on
  the event loop with no threadpool, so a blocking multi-second subprocess in a
  sync handler would serialise every concurrent request to *this* server. Use
  `asyncio.create_subprocess_exec`.
- **Spawn:**
  ```
  CLAUDE_CONFIG_DIR=<isolated dir> MCP_TIMEOUT=30000 ENABLE_TOOL_SEARCH=false \
  claude -p "<query + recall ritual>" \
    --model global.anthropic.claude-haiku-4-5-20251001-v1:0 \
    --mcp-config <memory-http.json> \
    --strict-mcp-config \
    --disallowedTools <mutating memory tools + built-in write/exec/read tools> \
    --output-format json < /dev/null
  ```
- **Hermetic config dir (`CLAUDE_CONFIG_DIR`).** The spawn must point at an
  isolated config dir containing a minimal `settings.json` (`permissions.allow:
  ["mcp__memory__*"]`, **no hooks**). Otherwise the subprocess inherits the
  user's `~/.claude` hooks and SessionStart skill injections, whose reminders
  derail the small recall model into reporting the graph unreachable.
  `--settings` does *not* work here: hook arrays merge rather than replace, and
  `--bare` severs the MCP connection. The parent environment is otherwise
  inherited so credentials still resolve.
- **`MCP_TIMEOUT`.** Without it, `claude` starts its first model turn mid-
  handshake and the model reports the memory server "still connecting". Set it
  so the agent blocks until the mcp-memory HTTP connection is established.
- **Tool search off (`ENABLE_TOOL_SEARCH=false`).** The CLI defers MCP tools
  behind a discovery step by default; a small recall model then wastes turns
  "loading schemas" and can wrongly report the graph empty. Disabling it makes
  every memory tool directly callable from the first turn.
- **Fact-completeness steering (prompt).** The recall prompt tells the agent to
  search from several angles, read through all of an entity's observations,
  extract specific facts (numbers, dates, decisions), and prefer newer
  observations on conflict, while keeping the output compact. Note a hard
  ceiling: the CLI persists any large tool result to a file and hands the model
  only a preview, and recall denies the Read tool, so facts buried deep in a very
  large entity may still not surface - the durable fix is memory hygiene (keep
  entities focused) rather than recall tuning.
- **Memory URL is auto-detected.** The spawn config points at the *running*
  mcp-memory port, resolved as `MCP_MEMORY_URL` -> `MCP_MEMORY_PORT` -> the port
  parsed from the installed launchd plist / systemd unit -> the default. The
  server is commonly installed on port 3000, not the code default 8000, so a
  hard-coded default silently points recall at a dead port.
- **Return contract:** free-form bulleted findings, each claim tagged with its
  source entity slug inline as `[project/entity-name]`. Compact enough to keep
  the caller's context clean, but the slugs keep every finding actionable (the
  caller can traverse/vote without re-searching). The recall prompt MUST tell
  the agent to preserve slugs verbatim - a small model will otherwise
  paraphrase identifiers away.
- **Steering:** the tool description must restrict callers to *heavy,
  multi-step* recall. A lookup the caller could do inline in one search call
  must not pay for a full agent spawn.
- **Availability gate:** `recall` is only registered in `tools/list` when the
  `claude` CLI it spawns resolves on `PATH`. When claude is absent the tool is
  hidden rather than advertised and then failing "unavailable" on every call.

`memory-config.json` for the spawn:
```json
{"mcpServers": {"memory": {"type": "http", "url": "http://localhost:3000/mcp"}}}
```

### Safety model (the critical part)

The autonomous surface must not be able to corrupt the shared graph. The
enforcement is a **deny-list, not an allow-list**, for a verified reason:

- The spawned `claude -p` inherits the user's client settings, which already
  grant the whole memory server via a wildcard allow. `--allowedTools` is
  *additive* and cannot subtract from an inherited allow, so an allow-list does
  **not** restrict the subprocess.
- `--disallowedTools` (deny) **overrides** the inherited allow. This is
  live-verified: with the mutating tools denied, an attempt to call
  `create_entities` was blocked and no row was written.

Deny for **v1 recall** (read-only): all 12 mutating memory tools **plus**
`vote_entity`, plus built-in write/exec tools (`Bash`, `Write`, `Edit`,
`NotebookEdit`, `Agent`) **plus** built-in read/web tools (`Read`, `Grep`,
`Glob`, `WebFetch`, `WebSearch`). The read tools must be denied too: otherwise
the agent answers from files on disk and cites file paths instead of the
`[project/entity]` graph slugs the return contract requires (live-observed - it
read this very design doc off disk on the first run).

The 12 mutating memory tools to deny: `create_entities`, `set_project_paths`,
`move_project_entities`, `merge_entities`, `delete_project`, `create_relations`,
`delete_entity`, `delete_relation`, `add_observations`, `delete_observations`,
`set_entity_status`, `restore_entity`. Of these, `delete_entity`,
`delete_relation`, `delete_project` and the overwriting `create_entities` are
hard-destructive; `merge_entities` removes its source only by *soft-delete*, so
that removal is reversible via `restore_entity` until a grace-window purge.

**Recursion guard:** `--strict-mcp-config` points the spawned agent's MCP config
*only* at mcp-memory, so it never sees the memory-agent server and physically
cannot call `recall` (verified: the spawned agent's tool list contained only
`mcp__memory__*`).

### Model pinning

Pass the **fully-qualified** model id
`global.anthropic.claude-haiku-4-5-20251001-v1:0`. The `haiku` alias (and other
short forms) silently remap to an Opus model in this environment - no error,
3-5x the cost. After each call, assert the returned `modelUsage` key contains
the expected model or you may silently pay Opus rates.

### Measured cost/latency (live, Haiku)

- Cost: ~$0.05-0.11 per recall (avg ~$0.09).
- Wall: ~15-33s, dominated by the multi-turn tool-calling loop (not the model),
  so this is roughly the floor regardless of model. Reinforces "heavy recall
  only".
- The loop is bounded by `--max-turns` (`MCP_RECALL_MAX_TURNS`, default 12), which
  caps wall-time and cost by limiting how many sequential tool calls a recall may
  make. This is the lever, not the timeout: the spawn's own hard timeout
  (`MCP_RECALL_TIMEOUT`, default 180s) sits far above the calling client's MCP
  timeout, so a long recall would otherwise complete (and bill) after the client
  has already given up. Capping turns keeps a recall inside the client window;
  lowering the timeout would instead kill legitimate multi-turn recalls mid-flight.

## v2 - `dream` (autonomous curation)

A pass that grooms the graph while it is idle. Off by default (opt-in via
`MCP_DREAM_ENABLED=true`).

- **Trigger: 2 hours of memory inactivity**, where "activity" is *any* memory
  tool call (reads included). Active use resets the timer; grooming only runs in
  a genuine idle window. The dream's own reads/votes count as activity, so it
  cannot spin.
- **Mutation surface: demote-never-delete, downvote-only.** The dream's only
  mutation is `vote_entity` with a vote of `-1` (so its deny-list is the v1
  recall deny-list **minus** `vote_entity`). It demotes stale, superseded, or
  duplicated entities and never casts a positive vote or deletes anything.
  Downvoting sinks an entity in ranking but never removes it - functionally
  equivalent to deletion from the reader's perspective, but reversible. Hard
  deletion stays a rare manual operation. (Entities can now also be *soft*-deleted
  - hidden from reads but kept intact and restorable until a grace-window purge -
  which is how `merge_entities` removes a folded-away duplicate; the light dream
  does not do this, it only votes.)
- **Scope: all projects.** A pass searches and votes across every project scope.
- **Voting is ±1 only.** No graded/weighted votes: the ranking multiplier
  already saturates via `tanh`, and letting the model choose magnitude
  reintroduces calibration risk. The dream prompt tells the agent to skip
  entities already at or below a saturated score, and caps how many it demotes
  per pass (advisory - the cap lives in the prompt, since votes happen inside
  the spawned agent).

### Host: an in-process idle-watcher, not a scheduled job

The dream runs as a background `asyncio` task inside the already-installed
`memory-agent` server, launched by `agent._serve()` alongside
`mcp.run_streamable_http_async()` and cancelled cleanly on shutdown. It polls a
`/api/idle` endpoint on mcp-memory (see below) and spawns one `run_dream_pass()`
when the idle window opens. It reuses the v1 spawn stack verbatim (hermetic
`CLAUDE_CONFIG_DIR`, `MCP_TIMEOUT`, pinned model, auto-detected memory URL). The
watcher starts only when the dream is enabled **and** the `claude` CLI resolves on
`PATH`; otherwise it would poll uselessly and every pass would no-op.

This is **not** started via FastMCP's `lifespan=` parameter:
`streamable_http_app()` wires its own lifespan (`session_manager.run()`) and
hands the user lifespan to the low-level server, which in `stateless_http=True`
mode runs *per request* - a task started there would be spawned and cancelled on
every request. A separate scheduled launchd/systemd unit was also rejected:
there is no scheduling machinery in the repo (the renderers emit only a
`KeepAlive`/`Restart=always` daemon), and a scheduled one-shot would fight that
daemon.

### Sole-writer: no second-writer race

The dream votes by spawning `claude -p`, which calls `mcp__memory__vote_entity`
over HTTP - exactly like v1 recall reads. That vote executes *inside* the
mcp-memory process on its single shared connection; the memory-agent process
never opens the DB. So mcp-memory remains the sole writer, and even a user
returning mid-dream shares the same connection on the same event loop - no lock
contention is possible. The 2h-idle window is a cost/quality knob, not the
safety mechanism.

`PRAGMA busy_timeout` is set on the DB connection as cheap insurance (it guards
the manual `relocate` read-write connection), but it is not load-bearing for the
dream, which never writes the file directly.

### Idle detection

mcp-memory persists a single last-activity timestamp to a marker file next to
the database (throttled, so read-heavy traffic does not hammer the disk) and
exposes it at `GET /api/idle` -> `{last_activity, idle_seconds}`. The watcher
GETs that endpoint using the same resolved memory URL recall uses (with the
`/mcp` suffix stripped, since the route is app-root-mounted). A plain GET does
not go through the activity tracker, so polling does not reset the idle timer;
the dream's own spawned tool calls do. The in-memory marker is seeded from disk
on first read (surviving a restart) and, on a fresh install with no marker,
from the current time so a new server does not immediately groom.

### Surfacing the dream in the visualiser

The dream runs in the memory-agent process, but the graph visualiser is served
by mcp-memory, which does not otherwise know the dream's config or actions. The
two processes share a data directory, so the bridge is a small marker file (the
same pattern as the last-activity marker):

- The agent writes `dream-status.json` next to the database - its config
  snapshot at watcher startup, and the latest pass (wall-clock timestamp,
  success flag, raw audit text, and a best-effort parse of the audit's
  `[project/entity-name] - reason` lines) after each `_dream_tick`. Only the
  most recent pass is kept; the graph's current `vote_score` state is the ground
  truth for what remains demoted, so no history is retained. The dream ritual
  pins the audit to that one-line-per-demotion format so the parse is reliable.
- mcp-memory reads that marker and pairs it with its own live idle time at
  `GET /api/dream`, so the browser gets everything same-origin in one poll (a
  plain route, so polling it records no activity). When the marker is absent
  (disabled or never run), the config fields report as unavailable but live idle
  is still returned.
- The visualiser shows a **dream status card** (enabled/idle/estimated
  time-to-next-pass/last-ran and the last pass's reported demotions, each row
  linking to the entity's inspector) and rings every node with a negative
  `vote_score` in amber as **demoted** - the ground-truth demoted state,
  including manual downvotes. Undo is the inspector's existing `+1` vote button.

### Surfacing recall activity in the visualiser

Recall is surfaced by the same marker bridge, but its shape differs from the
dream's because recall is a **pull** tool - spawned per caller-request, run
concurrently, and leaving no trace on the graph. There is no `vote_score`
ground truth to read back, so the marker itself is the only record of what
recall did, and a single latest-only entry is not enough:

- The agent writes `recall-status.json` next to the database, holding a live
  in-flight `active` count and a **bounded rolling history of the last 20
  finished recalls** (each with a wall-clock timestamp, the query truncated to
  80 characters, a success flag, and the `duration_ms`/`num_turns`/`total_cost_usd`
  the `claude -p` run reported). The query is truncated at the record site for
  compactness and light privacy.
- Recording lives in `recall()` itself, not the shared `_run_isolated_agent`
  spawn helper that recall and the dream both call - only `recall()` has the
  query, and gating it there keeps dream passes from writing recall history.
  The `active` count is incremented before the spawn and decremented in a
  `finally`, so it never leaks on a timeout or error, and it is reset to zero at
  agent startup to bound a stale count a crash could leave mid-recall.
- Because concurrent `recall()` coroutines write the marker while a separate
  process (mcp-memory) reads it, the write is **atomic** (temp file plus
  `os.replace`) rather than the dream's plain write - a torn read would
  otherwise expose corrupt JSON. The reader tolerates a bad read as absent.
- mcp-memory serves it same-origin at `GET /api/recall` (a plain route, so
  polling records no activity). The visualiser shows a **recall feed** stacked
  above the dream card - a live "N running" indicator plus each recent recall
  (time, query, duration/turns/cost, coloured by success). When the marker is
  absent the feed reports empty.

### Deferred beyond v2

- **Synthesised-observation append.** Gated behind observation-level voting
  shipping first: entity-level voting cannot down-rank a single bad observation,
  so auto-append could poison a good entity and compound the model's own
  guesses. See the observation-level-voting task in the memory graph.
- **Team / cross-device HTTP hosting** with authentication.

## Pre-flight gate (both phases)

Some environments require a readiness/credential check to pass before an agent
can be spawned (and spawning when that check would fail risks a hang). This is
handled generically:

- An optional config field holds an **arbitrary pre-flight command**.
- Before any spawn, if configured, run it **with a timeout**.
- Exit 0 -> proceed to spawn. Non-zero or timeout -> return a **generic** error
  to the caller (e.g. "recall unavailable: pre-flight check failed"), log detail
  locally, and do **not** spawn.
- If unconfigured, skip the check and spawn directly.

The command and any environment-specific meaning live entirely in the user's
local config value; the shipped code, config schema, and docs stay generic.

## Distribution

Ships in this repo, reusing existing patterns:

- a `memory-agent` console-script entry point: bare invocation serves (running
  the idle-watcher in-process when enabled); `memory-agent setup-service`
  installs the always-on background service via the shared service-spec machinery
  that already generates a macOS launch agent and a Linux systemd unit;
- client registration: `mcp-memory install claude-code` registers **both** the
  data server (`memory`) and the recall server (`memory-agent`) and adds each
  server's `mcp__<name>__*` allow rule.

The agent server discovers the running mcp-memory port itself (see the URL
resolution above), so no DB path is needed at the agent layer - the dream reads
idle state over HTTP (`/api/idle`) and votes over HTTP, never opening the DB. Its
service env also carries the installing user's `PATH`, because recall spawns the
`claude` CLI and launchd/systemd otherwise run with a minimal `PATH` that would
not find it, plus any `MCP_DREAM_*` settings present at install time.

## Testability

- v1 `recall` is read-only and therefore deterministic: assert the parsed
  `claude -p` JSON result contains the expected `[project/entity]` slugs against
  a known DB state; the subprocess boundary mocks with a recorded JSON fixture.
- A ranking-evaluation harness already exists in `tests/` for the vote/recency
  behaviour v2 relies on.

## Memory references

The authoritative, evolving decision log is in the memory graph:

- `task/autonomous-memory-agent-service` (project **mcp-memory**) - full
  decision log, every grill decision, all stress-test findings, corrected spawn
  command, and measured costs. `depends-on` `task/observation-level-voting-retrieval`.
- `task/observation-level-voting-retrieval` (project **mcp-memory**) -
  prerequisite for the deferred synthesised-append capability.
- `feature/mcp-memory/server` (project **mcp-memory**) - the `vote_entity` tool
  and ranking behaviour this design relies on.
- `pattern/claude-code-custom-subagents` (project **global**) - native subagent
  capabilities considered and why a separate server was chosen instead.
- `pattern/verify-restrictions-by-attempting-forbidden-action` (project
  **global**) - why the deny-list (not allow-list) is the real safety mechanism,
  discovered by attempting a forbidden write.
- `pattern/generic-extension-point-for-environment-specific-needs` (project
  **global**) - the pre-flight command seam.
