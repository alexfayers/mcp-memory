---
description: Guide {{agent}} on using mcp-memory for persistent memory.
---

# mcp-memory

- `global`: preferences, patterns, cross-project knowledge - never project summaries, repos, paths, configs. `<repo-name>`: the rest. MUST match an entity's scope to its subject before appending.
- Locating a repo or file: MUST check `list_metadata(kind="paths")` and memory before `find`/`grep`/`ls`; MUST register an unmapped repo or worktree via `set_metadata(kind="paths")`.
- Server down: MUST run `mcp-memory restart`, ask the user to reload MCP; never run the binary bare or via `launchctl`/`systemctl`.

## Start

`read_graph` then `search_nodes` on `global` and `<repo-name>` (keywords, `user-preferences`, project, `pattern/`s, files, ticket IDs, `status="in-progress"`); `get_entity_with_relations` on hits and `project/<repo-name>` (`entityType="task"`); summarize decisions, constraints, pitfalls before planning.

## Working

- MUST write silently as you go, never batched: edits, discoveries, decisions, feedback, facts; failures and insights to `global`.
- Each piece of work and user follow-up MUST be its own related `task/` entity, created before code.
- MUST record a confirmed external change (merge, deploy, close) in the same response; plans MUST include memory updates.
- Subagents MUST NOT write; main writes after a fresh read.
- MUST render a presented `vote_score` as stars (e.g. ★3), not the raw number.
- Before you {{TOOL_COMPLETE}}: record what changed, why, caveats, follow-ups; reusable lessons to `global`; set task `resolved`.

## Recommending

Memory is a snapshot: except `user-preferences`, MUST re-check lists, measurements and source-pointing facts live before quoting, on pushback, or before costly action; surface conflicts rather than pick a side.
