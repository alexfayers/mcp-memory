# mcp-memory

SQLite-backed persistent memory MCP server with FTS5 search and project scoping.

## Features

- **Project-scoped data** - all tools take a `project` parameter to isolate data per project
- **FTS5 full-text search** - recency-weighted BM25 ranking with porter stemming, time-range filtering
- **Implicit-usefulness ranking** - an entity edited soon after a search surfaced it earns an automatic upvote, so ranking self-tunes from observed use without relying on explicit votes
- **Graph traversal** - explore entity relationships with filtering by type
- **Safe observation updates** - append or delete individual observations without overwriting
- **Observation-level voting** - up/down-vote individual observations so an entity's most useful lines surface first and stale ones sink, not just whole-entity votes
- **Entity status tracking** - track entity lifecycle with status fields
- **Migration framework** - automatic schema upgrades
- **HTTP transport** - single server instance shared across all clients via streamable-http

## Installation

```bash
uv tool install "git+https://github.com/alexfayers/mcp-memory.git"
```

### As an overlay

mcp-memory ships prompt rules and skills for [llm-prompts](https://github.com/alexfayers/llm-prompts) and a hook plugin for [cline-hooks](https://github.com/alexfayers/cline-hooks). Add it to your `~/.config/llm-prompts/config.toml`:

```toml
[[tools]]
name = "mcp-memory"
source = "git+https://github.com/alexfayers/mcp-memory.git"
standalone = true
overlays_for = ["llm-prompts", "cline-hooks"]
```

Then run `llm-prompts setup` to install everything.

### Configuration

| Environment variable | Description | Default |
|---|---|---|
| `MCP_MEMORY_DB_PATH` | Database file path | `~/.local/share/mcp-memory/memory.db` |
| `MCP_MEMORY_PORT` | HTTP server port | `8000` |
| `MCP_MEMORY_READONLY_AGENTS` | Extra agent types exempt from the memory-update gate (comma-separated) | `Explore`, `Plan` |
| `MCP_MEMORY_EDIT_TOOLS` | Extra file-edit tool names counted at reduced weight toward the gate (comma-separated) | `Edit`, `Write`, `MultiEdit`, `NotebookEdit`, `replace_in_file`, `write_to_file` |
| `MCP_MEMORY_GC_ENABLED` | On startup, soft-delete downvoted orphan entities (score at/below `-10` with no live incoming relation). Reversible; the purge below is the only permanent removal. | off |
| `MCP_MEMORY_PURGE_ENABLED` | On startup, hard-delete soft-deleted entities older than the grace window | off |
| `MCP_MEMORY_PURGE_GRACE_DAYS` | How long a soft-deleted entity is retained before it may be purged | `30` |

### MCP client config

```json
{
  "mcpServers": {
    "memory": {
      "url": "http://localhost:8000/mcp"
    }
  }
}
```

### Agent installation

Automatically configure the MCP server for your agent:

```bash
mcp-memory install kiro <agent-config.json>   # patches Kiro agent JSON
mcp-memory install claude-code                # adds via `claude mcp add`
```

### Running as a service

mcp-memory uses HTTP transport, so it needs to run as a persistent background service. A setup command is included for macOS and Linux:

```bash
# Install with defaults (port 8000, DB at ~/.local/share/mcp-memory/memory.db)
mcp-memory setup-service

# Custom port (DB stays at the default unless --db-path is given)
mcp-memory setup-service --port 3000
```

The command auto-detects the platform and generates the appropriate service config.

#### Migrating an existing database to the default location

The hook plugin and the server are separate processes, and the plugin does not inherit
`MCP_MEMORY_DB_PATH`. If a service was set up with a custom `--db-path`, the two read different
databases. To consolidate onto the default location and repoint the service:

```bash
mcp-memory migrate-db
```

It auto-detects the current path from the installed service, stops the service, moves the
database (refusing to overwrite a non-empty target), and regenerates the service config at the
default path. Pass `--source <path>` to override detection.

#### macOS (launchd)

The command creates a plist at `~/Library/LaunchAgents/com.mcp-memory.plist`. To manage:

```bash
# Restart
launchctl kickstart -k "gui/$(id -u)/com.mcp-memory"

# Stop
launchctl bootout "gui/$(id -u)/com.mcp-memory"

# Uninstall
launchctl bootout "gui/$(id -u)/com.mcp-memory"
rm ~/Library/LaunchAgents/com.mcp-memory.plist
```

#### Linux (systemd)

The command creates a system unit at `/etc/systemd/system/mcp-memory.service` (requires sudo). To manage:

```bash
# Restart
sudo systemctl restart mcp-memory

# Stop
sudo systemctl stop mcp-memory

# Uninstall
sudo systemctl disable --now mcp-memory
sudo rm /etc/systemd/system/mcp-memory.service
sudo systemctl daemon-reload
```

#### Logs

Logs are written to the same directory as the database file (e.g. `~/.local/share/mcp-memory/mcp-memory.log`).

## Tools

All tools require a `project` parameter to scope data.

### create_entities

Create or update entities with observations. Overwrites all existing observations for an entity - use `add_observations` to append instead. Non-exempt entity types (not `user-preferences` or `project`) must include at least one relation. Entity names must start with their type prefix (e.g. `task/`, `feature/`), and a `project` entity must be named exactly `project/<project>` (one root per scope).

### add_observations

Append observations to an existing entity without overwriting. Skips duplicates. Throws if the entity does not exist.

### delete_observations

Delete specific observations from an existing entity by exact content match. Returns the count deleted. Throws if the entity does not exist. For an observation that is stale but not wrong enough to remove, prefer `vote_observation` (downvote to sink it) over deletion.

### search_nodes

FTS5 full-text search with recency- and vote-weighted BM25 ranking. Recency decay is type-aware (durable types such as `pattern` and `knowledge` decay far slower than `task`), and usefulness votes (see `vote_entity`) nudge results up or down within bounds. Multi-word queries match entities containing *any* of the terms by default (entities matching more terms rank first); pass `match_all=true` to require *all* terms. Optional `entity_type`, `status`, and time-range (`start_date`/`end_date`) filters. Date params support relative formats (`7d`, `2w`, `3m`) and ISO dates. Within each returned entity, observations are ordered best-first by their own votes (see `vote_observation`).

### read_graph

Returns the 10 most recent entities and their relations.

### create_relations

Create relationships between entities. Duplicates are ignored. Relation types are normalized (camelCase/underscore to hyphen, lowercased) and must resolve to the canonical vocabulary: `implements`, `depends-on`, `blocks`, `relates-to`, `belongs-to`, `part-of`, `used-by`, `used-in`. Common variants and synonyms are mapped automatically (e.g. `related-to` and `extends` become `relates-to` and `implements`); anything that does not resolve to a canonical type is rejected.

### get_entity_with_relations

Get an entity with all its relations and connected entities via graph traversal.

### search_related_nodes

Get an entity with directly related entities, optionally filtered by `entityType` and/or `relationType`.

### set_entity_status

Set or clear the status of an entity. Valid statuses: `planned`, `in-progress`, `blocked`, `resolved`, `archived`.

### vote_entity

Record a `+1` (useful) or `-1` (stale/unhelpful) usefulness vote on an entity as you retrieve it. Votes accumulate into a net `vote_score` that nudges search ranking within bounds - useful memories surface higher, unhelpful ones sink but stay findable - without changing the entity's content or `updated_at`. Returns the new net `vote_score`.

In addition to these explicit votes, the server casts a deterministic `+1` on its own when an entity that a search surfaced is edited within a short window (default 30 minutes, `MCP_AUTO_VOTE_WINDOW_SECONDS`) - treating "searched, then acted on" as observed usefulness. Auto-votes are idempotent per surfacing and capped per entity per day (`MCP_AUTO_VOTE_MAX_PER_DAY`, default 3); the surfacings are also retained for 30 days to measure ranking quality (precision@k / MRR).

### vote_observation

Record a `+1` or `-1` usefulness vote on a single observation of an entity, addressed by its exact content (like `delete_observations`). Upvoted observations surface first within the entity and downvoted ones sink, so a fat entity leads with its most useful lines. This is a light alternative to `delete_observations` when an observation is stale but not wrong enough to remove, and complements `vote_entity`, which ranks whole entities. Voting does not change content or `updated_at`. Returns the observation's new net `vote_score`.

### delete_entity

Delete an entity and all associated observations and relations.

### delete_relation

Delete a specific relation between two entities.

### merge_entities

Fold a duplicate entity (`source`) into its canonical twin (`target`) within one project. The source's observations are copied onto the target (deduplicated, keeping their votes), all the source's relations are repointed to the target (self-loops and duplicates dropped), the target keeps the higher of the two vote scores, and the source is **soft-deleted**. The merge is reversible: `restore_entity` brings the source back until a grace-window purge removes it. Both entities must already exist in the same project.

### restore_entity

Restore a soft-deleted entity, making it visible to reads again. Soft-deleted entities (e.g. the loser of a `merge_entities` call) are hidden from all reads but kept intact until a grace-window purge (off by default, `MCP_MEMORY_PURGE_ENABLED`; window `MCP_MEMORY_PURGE_GRACE_DAYS`, default 30). Restore reverses the hiding while the entity still exists.

### list_projects

List all project names in the knowledge graph. Takes no parameters.

### search_all_projects

Search entities across all projects in a single call. Returns results grouped by project name. Same FTS5 search and filters as `search_nodes` (including `match_all`) but without the `project` parameter.

### get_paths_for_project

Return the filesystem path(s) registered to a project, or an empty list if the project is unknown or has no registered paths. Read-only - does not create the project.

### get_paths_for_entity

Find which project(s) contain an entity with the given name and return their registered filesystem paths, grouped by project. Entity names are unique only within a project, so the same name may appear in several projects; a matching project with no registered path is still listed with an empty paths list. Returns an empty `matches` list if no entity has that name.

## Development

```bash
# Run all checks (lint, type-check, test)
just

# Individual commands
just lint        # ruff check + format
just type-check  # mypy
just test        # pytest
```

## Related

- [llm-prompts](https://github.com/alexfayers/llm-prompts) - cross-agent rules, workflows, and skills
- [cline-hooks](https://github.com/alexfayers/cline-hooks) - lifecycle hooks framework for AI coding assistants
