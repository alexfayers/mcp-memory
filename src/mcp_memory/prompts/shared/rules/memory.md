---
description: Guide {{agent}} on using mcp-memory for persistent memory.
---

# mcp-memory

- `global`: preferences, patterns, cross-project knowledge - never project summaries, repos, paths, configs. `<repo-name>`: the rest. MUST match scope to subject before appending.
- Locating a repo or file: MUST check `list_metadata(kind="paths")` and memory before `find`/`grep`/`ls`; MUST register an unmapped repo or worktree via `set_metadata(kind="paths", add=[...])`.
- Server down: MUST run `mcp-memory restart`, ask the user to reload MCP; never run the binary bare or via `launchctl`/`systemctl`.

## Start

Unless a `Startup block: recent memory` is present, `read_graph`. Then `search_nodes` on `global` and `<repo-name>` (keywords, `user-preferences`, project, `pattern/`s, files, ticket IDs, `status="in-progress"`); `get_entity_with_relations` on hits and `project/<repo-name>` (`entityType="task"`); summarize decisions, constraints, pitfalls before planning.

## Working

- MUST write silently as you go, never batched: discoveries, decisions, feedback, facts; cross-project failures and insights to `global`.
- MUST create or update the `feature/` before editing a second file or creating one, yours or a delegate's before you spawn it, in the call or message of your first reads or edits. MUST also create a `task/` for the work in the same call (one per step when planned). A fix to one existing file MAY write both with its final check. A `feature/`'s relation MUST be passed inline in `create_entities` (e.g. `relations:[{target:'project/<project>', type:'part-of'}]`), never as a separate `create_relations` call. Each follow-up MUST be its own `planned` `task/`. A task is one small step (e.g. `task/add-tag-filter`) that `implements` the `feature/`, created in the same `create_entities` call if none fits. As each task completes, MUST add its decisions and set its `status` `resolved` (never as an observation) in the script running the final check, once it passes, or with the last memory write.
- A task MUST hold an instruction carrying only what the user asked, including file names or tools it names (e.g. `Add a tag filter to the list endpoint, with tests`), never how-detail not in the user's ask: file names, test styles or past tense (`Added X`, `tests pass`). It MUST also hold only a decision and why, and an outcome line only for what git can't show: where it landed (PR or CR number, merged, deployed), what is still unverified, or a surprise (plus open follow-ups while `planned` or `in-progress`), never what was edited or tested. With no such outcome, MUST NOT write an outcome line: `resolved` is the outcome. A `feature/` MUST hold the user's stated decisions about the capability with their reasons, its project-specific gotchas, and decisions binding later work. It MAY also hold one caller-view line: what the capability does for its caller, from the caller's side so search finds it (e.g. `Export a report as CSV`). A `feature/` MUST NOT hold a status line, task text or the ask, or implementation detail (file names, internals, test layout). A learning serving no capability MUST NOT become a `feature/`: a standalone fact goes in `knowledge/`. A decision that only explains one step stays on its `task/`. A how-to reusable elsewhere MUST go in a `pattern/`: `search_nodes` first and append to a match, else create it `part-of` its closest broader pattern, creating a missing parent in the same call. Only in this project: project scope, top-level `part-of` `project/<project>`. Across projects: `global` scope, top-level `part-of` `project/global`. MUST NOT make one hub pattern.
- Good: `Decision: keep the cart total server-side - a client price can be stale`. Bad: `Added total field`, `tests pass`.
- New information that replaces or contradicts an observation: MUST `search_nodes` first, then `edit_observation` or delete it in the same step.
- MUST record a confirmed external change (merge, deploy, close) in the same response; plans MUST include memory updates.
- Subagents MUST NOT write, even where a rule or hook says to - they report decisions and why, gotchas and how-tos to their owner. Main writes after a fresh read.
- A memory hook block MUST get a real memory write on the NEXT tool call, never a placeholder.
- MUST render a presented `vote_score` as stars (e.g. ★3).
- Before you {{TOOL_COMPLETE}}: record only what a new session can't cheaply get from code or git (decisions and why, gotchas, user preferences, where things live), not change narration. Place each per the bullets above, including delegates' reports. Create any missing `task/`, a `planned` one per open follow-up, and set each finished one `resolved`.

## Recommending

Memory is a snapshot: except `user-preferences`, MUST re-check lists, measurements and source-pointing facts live before quoting, on pushback, or before costly action; surface conflicts rather than pick a side.
