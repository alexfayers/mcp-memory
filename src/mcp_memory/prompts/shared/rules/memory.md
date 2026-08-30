---
description: Guide {{agent}} on using mcp-memory for persistent memory.
---

# Memory Usage with mcp-memory

- `memory` MCP server is available. Each tool's schema documents its mechanics; this file is policy only.
- Tools MAY be deferred - `ToolSearch` for `mcp__memory__*` before the first call if unavailable.
- All tools take `project`: `global` (cross-project) or `<repo-name>`. Use in both PLAN and ACT mode; no need to announce it.
- MUST update memory as you progress and before completing, never batched at the end.
- Entity naming, task discipline, relation types and observation hygiene live in the `memory-usage` skill - MUST load it before creating or restructuring entities.
- The optional `memory-agent` server's `recall(query)` absorbs heavy multi-step recall. MUST use it rather than a second broad search on one question, or for a result too large to fit; use `search_nodes` for a targeted lookup. Ignore if absent.

## Project scopes

- `global`: user preferences, reusable patterns, cross-project knowledge. Update after any change (note failures), new user info, or insight gained.
- `<repo-name>`: everything project-specific - the `project/` entity, features, tasks, architecture, API contracts, invariants, gotchas, TODOs.
- MUST NOT put project-summary entities or workspace-specific facts (repo names, paths, workspace rules, tool configs) in `global`.
- MUST verify a found entity's scope matches its subject before appending - a wrong scope usually comes from a fallback/basename scope, not a new entity.
- MUST call `list_metadata(kind="paths", ...)` before needing a project's location, and MUST search memory for any "what/where is X" - both BEFORE any `find`/`grep`/`ls`. Fall back to a live search only once the lookup comes back empty or stale.
- Where `memory` is unavailable, MUST run `mcp-memory restart`, then ask the user to reload the MCP connection and wait. MUST NOT run the `mcp-memory` binary bare, nor `launchctl kickstart`/`systemctl restart` it - it is a managed service, and running the binary leaves a stray process holding its port that dies with the session.

## Before starting a task

MUST follow every step, every task, before responding. `read_graph` alone returns recent entities only.

1. `read_graph` on `global`, then on `<repo-name>`.
2. `search_nodes` on both: message keywords, `user-preferences` (always), the project name, relevant `pattern/` entities, current files, feature and ticket IDs. Prefer a `status="in-progress"` filter over text search.
3. `get_entity_with_relations` on every entity found, and always on `project/<current-project>` with `entityType="task"`.
4. Summarize what is known and highlight prior decisions, constraints and pitfalls before planning.

- A hyphenated term (`auth-service`) is one token and will not match `authservice` - use bare keywords, and SHOULD retry with different ones before concluding nothing exists.

## While working

- MUST update after each meaningful step: an edit or group of edits; an unexpected discovery; a decision or trade-off; user feedback; new factual information.
- MUST persist facts as discovered (architecture, API contracts, bugs, performance) as small atomic observations, one fact each, grouped under one entity where possible.
- Explicit user follow-up work MUST get its own `task/` entity immediately, never bundled or deferred.
- A confirmed external state change (PR merged, deployed, ticket closed) MUST update the entity in the same response.
- Hook reminders arrive as `<hook_context>` blocks - MUST act with the actual write on your NEXT tool call, never a placeholder shell command.
- SHOULD vote as you retrieve - up for helpful, down for stale or misleading.

## After completing a task or milestone

Before you {{TOOL_COMPLETE}}, MUST record the outcome for each significant unit of work and set the `task/` entity `resolved`. The `memory-usage` skill carries the full checklist.

## Before recommending from memory

- A memory's "full list of X" or recorded measurement is a synthesis from when it was written, not a live fact - MUST re-derive it against the live source before quoting, especially on pushback.
- Weight staleness by type: `user-preferences` rarely changes; a `pattern` or `knowledge` entity pointing at a source reflects only what was true when written.
- MUST verify a costly-to-be-wrong-about fact against the live source first - a Haiku delegate confirming one fact is cheap.
- MUST surface any conflict with live observations rather than silently picking a side.

## Memory rules

- MUST include memory updates in implementation plans.
- Subagents are read-only for memory: they MAY read but MUST NOT mutate. They return facts to their caller; the main thread performs every write after a fresh read.
- Knowledge not in memory or prompt files is lost at session end, including anything told mid-session - MUST persist those to `global` immediately.
