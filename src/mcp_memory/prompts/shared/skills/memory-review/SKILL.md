---
name: memory-review
description: Audit and clean up the memory graph - fix orphans, consolidate duplicates, trim bloat, and enforce naming conventions. Run periodically or when asked about memory health.
---

# memory-review

Work through this checklist to audit and clean up the memory graph. Use the `/visualise` endpoint or `/api/graph` to inspect the graph visually.

## 1. Find orphans and naming violations

For each project scope, identify:
- **Orphan entities** - nodes with zero relations (these float disconnected in the graph)
- **Unprefixed entities** - names not starting with a standard prefix (`project/`, `feature/`, `task/`, `user-preferences/`, `pattern/`, `knowledge/`, `tool/`)
- **Duplicate entities** - same concept stored with and without prefix (e.g. `MyProject` and `project/MyProject`)

Fix: rename with proper prefix (delete + recreate with relations), link orphans, or delete if stale.

## 2. Consolidate duplicates

Look for:
- Same entity in multiple project scopes (e.g. `user-preferences/` in a project scope that belongs in `global`)
- Unprefixed duplicates of prefixed entities (merge observations, re-point relations, delete the unprefixed one)
- `user-preferences` entities scattered across project scopes - extract useful observations into the main global preferences, then delete the project-scoped copy

## 3. Trim bloated entities

Project and task entities accumulate session-level detail over time. Trim them:
- **`project/` entities** should contain only current-state facts (tooling, paths, config, active TODOs) - not commit SHAs, resolved work detail, or session logs
- **Resolved `task/` entities** should be a brief summary (what was done, key decisions) - not a play-by-play of the implementation
- **`feature/` entities** should describe the current state of the feature, not its development history
- Move reusable learnings to `pattern/` entities in global scope

## 4. Clean up stale entities

- Archive or delete resolved tasks that are no longer useful context
- Delete old ticket/CR entities that were one-off investigations
- Archive superseded project entities (e.g. old TS project replaced by Python rewrite)

## 5. Verify relations

- Every non-exempt entity should have at least one relation
- `task/` entities must have `implements` relation(s) to the feature(s) they modify - NOT `belongs-to` project
- `feature/` entities must have `belongs-to` relation to their parent project
- `pattern/` entities should be linked to relevant `user-preferences/` or `project/` entities
- `knowledge/` entities should be linked to relevant projects or preferences
- Use specific relation types (`implements`, `depends-on`) over generic ones (`relates-to`)

## 5a. Fix star graphs

A common anti-pattern is every task having a `belongs-to` relation directly to the project root, creating a star graph. Fix this by:
- Ensuring feature entities exist for each major area of the project
- Replacing `task belongs-to project` with `task implements feature`
- Tasks are still reachable from the project via feature traversal (feature belongs-to project)
- Only link a task directly to a project if no relevant feature entity exists yet

## 6. Check global vs project scope

- **Global scope** should contain only: user preferences, cross-project patterns, project summaries (not full detail), and cross-project knowledge
- **Project scope** should contain: detailed project facts, features, tasks, and project-specific preferences
- If a global project entity has more than ~10 observations, it's probably too detailed - trim to summary level

After completing the checklist, report what was cleaned up.
