---
name: memory-review
description: Audit and clean up the memory graph - fix orphans, consolidate duplicates, trim bloat, and enforce naming conventions. Run periodically or when asked about memory health.
---

# memory-review

Work through this checklist to audit and clean up the memory graph. Use the `/visualise` endpoint or `/api/graph` to inspect the graph visually.

**Approach:** Use subagents aggressively. Each checklist step can be parallelised - fan out one agent per entity or per concern. Delete operations are independent and safe to run concurrently. After each round, re-read the graph and look for more issues until nothing remains.

## 1. Find orphans and naming violations

For each project scope, identify:
- **Orphan entities** - nodes with zero relations (these float disconnected in the graph)
- **Unprefixed entities** - names not starting with a standard prefix (`project/`, `feature/`, `task/`, `user-preferences/`, `pattern/`, `knowledge/`, `tool/`)
- **Duplicate entities** - same concept stored with and without prefix (e.g. `MyProject` and `project/MyProject`)
- **Ghost project scopes** - scopes that exist but contain zero entities (from auto-generated sessions, old renames)

Fix: rename with proper prefix (delete + recreate with relations), link orphans, or delete if stale.

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
