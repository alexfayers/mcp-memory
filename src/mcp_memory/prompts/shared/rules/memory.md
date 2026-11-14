---
description: Guide {{agent}} on using mcp-memory for persistent memory.
---

# mcp-memory

- `global`: preferences, patterns, cross-project knowledge - never project summaries, repos, paths, configs. `<repo-name>`: the rest. MUST match an entity's scope to its subject before appending.
- Locating a repo or file: MUST check `list_metadata(kind="paths")` and memory before `find`/`grep`/`ls`; MUST register an unmapped repo or worktree via `set_metadata(kind="paths", add=[...])`.
- Server down: MUST run `mcp-memory restart`, ask the user to reload MCP; never run the binary bare or via `launchctl`/`systemctl`.

## Start

Unless a `Startup block: recent memory` is present, `read_graph`. Then `search_nodes` on `global` and `<repo-name>` (keywords, `user-preferences`, project, `pattern/`s, files, ticket IDs, `status="in-progress"`); `get_entity_with_relations` on hits and `project/<repo-name>` (`entityType="task"`); summarize decisions, constraints, pitfalls before planning.

## Working

- MUST write silently as you go, never batched: discoveries, decisions, feedback, facts; failures and insights to `global`.
- MUST record only what code and git can't show: a decision and why, a gotcha, a preference, where something lives, an external outcome (merge, deploy, close). MUST NOT narrate implementation steps or that tests pass. Memory holds the why: for what changed or how it works, MUST read the code and `git log`/`git blame`.
- Each piece of work and user follow-up MUST be its own related `task/` entity, created before code.
- A `task/` MUST link to the `feature/` it changes with `implements`. A `feature/` MUST link to its `project/` with `part-of`. MUST create a missing `feature/` in the same `create_entities` call, with its relations passed inline.
- New information that replaces or contradicts an observation: MUST `search_nodes` for it first, then `edit_observation` it in place. MUST edit or delete a contradicted observation in the same step.
- MUST record a confirmed external change (merge, deploy, close) in the same response; plans MUST include memory updates.
- Subagents MUST NOT write; main writes after a fresh read.
- A memory hook block MUST get a real memory write on the NEXT tool call, never a placeholder shell command.
- MUST render a presented `vote_score` as stars (e.g. ★3), not the raw number.
- Before you {{TOOL_COMPLETE}}: MUST record lasting facts only. MUST record reusable lessons in `global`. MUST set the task `resolved`.

## Recommending

Memory is a snapshot: except `user-preferences`, MUST re-check lists, measurements and source-pointing facts live before quoting, on pushback, or before costly action; surface conflicts rather than pick a side.
