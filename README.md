# mcp-memory

SQLite-backed persistent memory MCP server with FTS5 search and project scoping.

## Features

- **Project-scoped data** - all tools take a `project` parameter to isolate data per project
- **FTS5 full-text search** - recency-weighted BM25 ranking with porter stemming, time-range filtering
- **Graph traversal** - explore entity relationships with filtering by type
- **Safe observation updates** - append or delete individual observations without overwriting
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

### Running as a service

mcp-memory uses HTTP transport, so it needs to run as a persistent background service. A setup script is included for macOS and Linux:

```bash
# Install with defaults (port 8000, DB at ~/.local/share/mcp-memory/memory.db)
scripts/setup-service.sh

# Custom port and DB path
scripts/setup-service.sh --port 3000 --db-path ~/.memory/memory.db
```

The script auto-detects the platform and generates the appropriate service config.

#### macOS (launchd)

The script creates a plist at `~/Library/LaunchAgents/com.mcp-memory.plist`. To manage:

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

The script creates a user unit at `~/.config/systemd/user/mcp-memory.service`. To manage:

```bash
# Restart
systemctl --user restart mcp-memory

# Stop
systemctl --user stop mcp-memory

# Uninstall
systemctl --user disable --now mcp-memory
rm ~/.config/systemd/user/mcp-memory.service
systemctl --user daemon-reload
```

#### Logs

Logs are written to the same directory as the database file (e.g. `~/.local/share/mcp-memory/mcp-memory.log`).

## Tools

All tools require a `project` parameter to scope data.

### create_entities

Create or update entities with observations. Overwrites all existing observations for an entity - use `add_observations` to append instead. Non-exempt entity types (not `user-preferences` or `pattern`) must include at least one relation.

### add_observations

Append observations to an existing entity without overwriting. Skips duplicates. Throws if the entity does not exist.

### delete_observations

Delete specific observations from an existing entity by exact content match. Returns the count deleted. Throws if the entity does not exist.

### search_nodes

FTS5 full-text search with recency-weighted BM25 ranking. Optional `entity_type`, `status`, and time-range (`start_date`/`end_date`) filters. Date params support relative formats (`7d`, `2w`, `3m`) and ISO dates.

### read_graph

Returns the 10 most recent entities and their relations.

### create_relations

Create relationships between entities. Duplicates are ignored.

### get_entity_with_relations

Get an entity with all its relations and connected entities via graph traversal.

### search_related_nodes

Get an entity with directly related entities, optionally filtered by `entityType` and/or `relationType`.

### set_entity_status

Set or clear the status of an entity. Valid statuses: `planned`, `in-progress`, `blocked`, `resolved`, `archived`.

### delete_entity

Delete an entity and all associated observations and relations.

### delete_relation

Delete a specific relation between two entities.

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
