---
name: memory-review
description: Audit and clean up the memory graph - fix orphans, consolidate duplicates, trim bloat, and enforce naming conventions. Run periodically or when asked about memory health.
---

# memory-review

Work through this checklist to audit and clean up the memory graph. The `/visualise` endpoint gives a visual overview, but auditors MUST read the graph data via the **read-only memory MCP tools** (`read_graph`, `search_nodes`, `get_entity_with_relations`, `search_related_nodes`), NOT via `curl`/`Read` of `/api/graph`.

**Read the graph via the read-only memory MCP tools** (`read_graph`, `get_entity_with_relations`), not curl. The "MEMORY UPDATE REQUIRED" gate (in mcp-memory's own hook plugin, `hooks/plugin.py`, NOT cline-hooks) never hard-blocks subagents - it applies to the main agent loop only - so auditors run freely regardless of agent type. (Reading via curl is fine on the MAIN thread too, where you can write to satisfy the gate.)

**Approach:** Use subagents for READ-ONLY auditing only - fan out one agent per scope or per concern to enumerate issues and propose fixes. Each returns a structured list of proposed ops; the MAIN THREAD executes all mutations (deletes, migrates, status changes) serially.

**Do NOT let subagents mutate memory.** Subagent memory writes tend to create entities without the paired `create_relations` (producing orphans) and scatter scratch observations into whatever scope is active, polluting the graph under review. Keep mutation in one place (the main thread) where each op is verified against a fresh live read first.

**Verify before every destructive op.** `delete_observations` requires an EXACT string match - re-read the live entity and copy the exact text; paraphrased/truncated strings silently no-op. For `delete_entity`, confirm the entity is genuinely empty/scratch or that its content is preserved elsewhere before deleting. `delete_entity` is blocked while an entity still has incoming relations - delete or re-point those edges first. To remove an empty "ghost" scope, delete its remaining entities then call `delete_project(project)` (it refuses to delete `global` or any scope that still has entities).

## 1. Find orphans and naming violations

`create_entities` now rejects new unprefixed names and any mis-named/second `project` entity at creation time (a name must start with `<entityType>/`, and a `project` entity must be named exactly `project/<scope>`). The checks below therefore target **legacy entities created before that enforcement** - new ones can no longer be made.

For each project scope, identify:
- **Orphan entities** - nodes with zero relations (these float disconnected in the graph)
- **Misused `project` type** - any `project`-type entity OTHER than the single `project/<repo-name>` root. `project` and `user-preferences` are the only types exempt from the relation requirement, so a legacy work item created as `entityType: project` (e.g. an investigation named after its symptom rather than as a `task/`) slips past the server's relation check as an orphan. These are always a mistake: the content belongs in a `task/`, `feature/`, or `pattern/` entity (with a relation), or should be deleted if superseded.
- **Unprefixed entities** - names not starting with a standard prefix (`project/`, `feature/`, `task/`, `user-preferences/`, `pattern/`, `knowledge/`)
- **Duplicate entities** - same concept stored with and without prefix (e.g. `MyProject` and `project/MyProject`)
- **Ghost project scopes** - scopes that exist but contain zero entities (from auto-generated sessions, old renames)

Fix: rename with proper prefix (delete + recreate with relations), link orphans, or delete if stale. For a misused `project` entity, migrate its content to the correct entity type with a relation (or delete if a `task/`/`feature/` already covers it), then delete the rogue `project` entity.

## 2. Consolidate duplicates

Look for:
- Same entity in multiple project scopes (e.g. `user-preferences/` in a project scope that belongs in `global`)
- Unprefixed duplicates of prefixed entities (merge observations, re-point relations, delete the unprefixed one)
- `user-preferences` entities scattered across project scopes - extract useful observations into the main global preferences, then delete the project-scoped copy
- **Near-duplicate observations** within the same entity (same fact phrased two ways) - keep the more precise one
- **Observations that contradict each other** - resolve the contradiction, keep the correct one

## 3. Trim bloated entities

Project and task entities accumulate session-level detail over time. Trim them:
- **`project/` entities** should contain only current-state facts (tooling, paths, config, active TODOs) - not commit SHAs, resolved work detail, or session logs
- **Resolved `task/` entities** should be 1-3 observations max (outcome summary) - not a play-by-play of the implementation. If a feature entity captures the result, the task can have zero observations.
- **`feature/` entities** should describe the current state of the feature, not its development history
- **`user-preferences/` entities** should contain only preferences NOT already encoded in steering files/skills (which are always loaded). Observations duplicating rule file content are pure waste.
- Move reusable learnings to `pattern/` entities in global scope

### Size targets

| Entity type | Ideal obs count | Action if over |
|---|---|---|
| `user-preferences/` | 10-30 | Extract domain-specific obs into `pattern/` entities |
| `project/` | 15-30 | Remove session logs, resolved work, ephemeral status |
| `knowledge/` | 5-20 | Remove implementation details, keep architecture |
| `pattern/` | 3-15 | Split if covering unrelated topics |
| Resolved `task/` | 0-3 | Delete implementation play-by-play |
| `feature/` | 3-10 | Remove development history |

## 4. Extract patterns from mega-entities

When a `user-preferences/` or `project/` entity has observations spanning unrelated domains, extract them into focused `pattern/` entities:
- Technical gotchas (CDK, AWS, DDB) -> `pattern/<service>-gotchas`
- CLI command recipes -> `pattern/<tool>-commands`
- Workflow recipes (CR, deployment, testing) -> `pattern/<workflow>-<aspect>`

Each pattern entity should be independently searchable - someone searching for "CDK alarm" should hit the pattern directly, not wade through a 50-observation preferences blob.

## 5. Clean up stale entities

- Archive or delete resolved tasks that are no longer useful context
- Delete old ticket/CR entities that were one-off investigations
- Archive superseded project entities (e.g. old TS project replaced by Python rewrite)
- Remove observations with:
  - Dates/timestamps (entities have automatic created_at/updated_at)
  - File paths in global scope (belong on project entities in project scope)
  - Changelog entries ("updated X on date Y")
  - Specific commit SHAs (live in git history)
  - Tool-specific workarounds for tools no longer in use

## 6. Verify relations

- Every non-exempt entity should have at least one relation
- `task/` entities must have `implements` relation(s) to the feature(s) they modify - NOT `belongs-to` project
- `feature/` entities must have `belongs-to` relation to their parent project or knowledge hub
- `pattern/` entities should be linked to relevant `user-preferences/` or `project/` entities
- `knowledge/` entities should be linked to relevant projects or preferences
- Use specific relation types (`implements`, `depends-on`) over generic ones (`relates-to`)
- Check for overlapping entities that should be cross-linked (e.g. two patterns covering related topics)

## 6a. Fix star graphs

A common anti-pattern is every task having a `belongs-to` relation directly to the project root, creating a star graph. Fix this by:
- Ensuring feature entities exist for each major area of the project
- Replacing `task belongs-to project` with `task implements feature`
- Tasks are still reachable from the project via feature traversal (feature belongs-to project)
- Only link a task directly to a project if no relevant feature entity exists yet

## 7. Check global vs project scope

- **Global scope** should contain only: user preferences, cross-project patterns, cross-project knowledge
- **Project scope** should contain: detailed project facts, features, tasks, and project-specific preferences
- If a global entity has more than ~20 observations, it's probably bloated - trim or extract patterns
- Observations about the same topic should live on ONE entity, not be scattered across multiple (consolidate overlaps)

## 8. Verify steering file alignment

Check whether observations on `user-preferences/` entities duplicate content already in steering rules or skill files. These are redundant because rules/skills are always loaded into context while memory must be actively searched:
- If an observation restates a rule file -> delete it from memory
- If an observation adds nuance beyond the rule -> keep it
- If a rule file is missing something that memory captures -> consider updating the rule file and deleting the memory observation

After completing the checklist, report what was cleaned up with counts (observations deleted, entities deleted/created, relations fixed).
