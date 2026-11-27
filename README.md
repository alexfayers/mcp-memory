# mcp-memory

![tool output](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/alexfayers/mcp-memory/benchmark-badge/size.json)
![recall@10](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/alexfayers/mcp-memory/benchmark-badge/ranking.json)

An MCP server that gives coding agents a persistent, per-project memory. It stores a knowledge graph in SQLite and serves it over HTTP on localhost.

## Install

Needs Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv tool install "git+https://github.com/alexfayers/mcp-memory.git"
```

## Quick start

```bash
mcp-memory setup-service          # run the server in the background
mcp-memory install claude-code    # point an agent at it
```

`install` also supports `codex`, `antigravity`, `pi`, `opencode` and `kiro`. For any other client, use the URL `http://localhost:8000/mcp`.

Other commands:

```bash
mcp-memory restart                # restart the service
mcp-memory utils audit --all-projects   # graph hygiene report
mcp-memory export <file>          # back up to JSON
mcp-memory import <file>          # merge projects from an export
```

`mcp-memory --help` lists the rest. With no arguments it runs the server in the foreground.

## Config

Set with environment variables. The common ones:

- `MCP_MEMORY_DB_PATH`: database file, default `mcp-memory/data/memory.db` in the llm-prompts root (`$LLM_PROMPTS_HOME`, else `$XDG_DATA_HOME/llm-prompts`, else `~/.local/share/llm-prompts`)
- `MCP_MEMORY_PORT`: HTTP port, default `8000`
- `MCP_MEMORY_NO_SERVICE`: when true, `setup-service` and `install` leave launchd and systemd alone. They install no service and remove no retired one. Use it when you run the server yourself
- `MCP_DREAM_ENABLED`, `MCP_DREAM_HEAVY_ENABLED`: let the server curate memory while idle, off by default

Re-run `mcp-memory setup-service` after changing them so the service picks them up.

## As an llm-prompts overlay

mcp-memory ships rules and skills for [llm-prompts](https://github.com/alexfayers/llm-prompts) and a hook plugin for [cline-hooks](https://github.com/alexfayers/cline-hooks). Add this to `~/.config/llm-prompts/config.toml`, then run `llm-prompts setup`:

```toml
[[tools]]
name = "mcp-memory"
source = "git+https://github.com/alexfayers/mcp-memory.git"
```

## Docs

- [Configuration](docs/configuration.md): all environment variables, the hook plugin and dreaming
- [Commands](docs/commands.md): every command, the service and the visualiser
- [Tools](docs/tools.md): the MCP tools
- [Telemetry](docs/telemetry.md): what is sent
- [Contributing](CONTRIBUTING.md)
