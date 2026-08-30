---
name: memory-review
description: Audit and clean up the memory graph - fix orphans, consolidate duplicates, trim bloat, and enforce naming conventions. Run periodically or when asked about memory health.
disable-model-invocation: true
---

# memory-review

**MUST run only when explicitly asked, or at a genuine lull (session wind-down, no active task)** - a long, heavyweight audit, invoked only via the user's explicit `/memory-review`. The periodic "review due" hook reminder is a nudge to mention to the user, not an instruction to launch this skill automatically; MUST surface it and let the user decide.

MUST work through this checklist to audit and clean up the memory graph. The `/visualise` endpoint gives a visual overview, but auditors MUST read the graph data via the **read-only memory MCP tools** (`read_graph`, `search_nodes`, `get_entity_with_relations`), NOT via `curl`/`Read` of `/api/graph`.

**MUST start with the deterministic audit:** run `mcp-memory audit --project <scope>` (or `--all-projects`) once up front. It reads the live database read-only and emits one JSON report of every *mechanical* violation - orphans, misused `project`-type entities, unprefixed names, ghost scopes, oversized entities (per the section 3 size targets), `task belongs-to project` relation violations, star-graph tasks, and strongly-downvoted entities:

```json
{"project": "...", "orphans": [...], "misused_project_type": [...], "unprefixed": [...],
 "ghost_scopes": [...], "oversized": [...], "relation_violations": [...],
 "star_graph_tasks": [...], "negative_vote_entities": [...]}
```

MAY add `--propose-plan` to emit, instead of the raw report, a `{"steps": [...]}` list of concrete fix-it tool calls (deterministic ones like `rename_entity` with `needs_review: false`, and judgement-required ones - orphan relinking, deletes, and `consider_split` advisories - with `needs_review: true`). A `trim_observations_to_outcome` step carries `needs_review: false` only when every observation it would drop is a near-duplicate of one being kept - otherwise it carries `needs_review: true`, since the trim is a hard delete with no undo. MUST treat it as a worklist: execute the `needs_review: false` steps as-is and apply judgement to the rest.

This replaces the manual enumeration in the sub-checks below - the audit *finds* the violations deterministically. It does **not** decide fixes: near-duplicate/contradiction detection, what is reusable enough for a `pattern/`, memory-vs-rule-file redundancy, and every merge/delete/rename/relink decision remain your judgement. MUST use the audit as the worklist, then apply the judgement each section describes.

The "MEMORY UPDATE REQUIRED" gate (in mcp-memory's own hook plugin, `hooks/plugin.py`, NOT cline-hooks) never hard-blocks subagents - it applies to the main agent loop only - so auditors run freely regardless of agent type. (Reading via curl is fine on the MAIN thread too, where you can write to satisfy the gate.)

**Approach:** MUST use subagents for READ-ONLY auditing only - fan out one agent per scope or concern to enumerate issues and propose fixes. Each returns a structured list of proposed ops; the MAIN THREAD MUST execute all mutations (deletes, migrates, status changes) serially, each verified against a fresh live read first. Subagent writes tend to create orphans (entities without the paired `create_relations`) and scatter scratch observations into whatever scope is active.

**MUST verify before every destructive op.** `delete_observations` requires an EXACT string match - MUST re-read the live entity and copy the exact text; paraphrased/truncated strings silently no-op. For `delete_entity`, MUST confirm the entity is genuinely empty/scratch or its content is preserved elsewhere. `delete_entity` is blocked while an entity still has incoming relations - MUST delete or re-point those edges first. To remove an empty "ghost" scope, delete its remaining entities then call `delete_project(project)` (it refuses to delete `global` or any scope that still has entities).

**To consolidate duplicates, SHOULD use `merge_entities(project, source, target)` over hand-merging.** It copies the source's observations onto the target (deduped, keeping votes), repoints all the source's relations to the target, keeps the higher vote score, and **soft-deletes** the source - reversibly, so a wrong merge can be undone with `restore_entity(project, source)` until a grace-window purge. It sidesteps the incoming-relation delete block.

**MUST focus on structural hygiene the autonomous "dream" cannot do.** The dream's background curation may already be downvoting obvious noise and merging obvious duplicates (see the "dream" mechanism in the memory rules), but it cannot re-link orphans, rename, split bloated entities, move scopes, or fix relations. MUST NOT spend a review re-downvoting obvious noise or re-merging obvious duplicates; MUST concentrate on the structural work below (orphans, naming, bloat/splitting, relations, scope errors). MUST treat a strongly negative `vote_score` as a review prompt (see step 5), not as work already finished.

## 1. Find orphans and naming violations

`create_entities` now rejects new unprefixed names and any mis-named/second `project` entity at creation time (a name must start with `<entityType>/`, and a `project` entity must be named exactly `project/<scope>`). The checks below therefore target **legacy entities created before that enforcement** - new ones can no longer be made.

The audit's `orphans`, `misused_project_type`, `unprefixed`, and `ghost_scopes` keys enumerate these deterministically:
- **Orphan entities** (`orphans`) - nodes with zero relations (these float disconnected in the graph)
- **Misused `project` type** (`misused_project_type`) - any `project`-type entity OTHER than the single `project/<repo-name>` root. `project` is the only type exempt from the relation requirement, so a legacy work item created as `entityType: project` (e.g. an investigation named after its symptom rather than as a `task/`) slips past the server's relation check as an orphan. This is usually a modeling mistake: MUST migrate the content to a `task/`, `feature/`, or `pattern/` entity (with a relation), or delete it if superseded.
- **Unprefixed entities** (`unprefixed`) - names not starting with a standard prefix (`project/`, `feature/`, `task/`, `user-preferences/`, `pattern/`, `knowledge/`)
- **Ghost project scopes** (`ghost_scopes`) - scopes that exist but contain zero entities (from auto-generated sessions, old renames)

**Duplicate entities** - same concept stored with and without prefix (e.g. `MyProject` and `project/MyProject`) - are a judgement call the audit does not make: MUST cross-reference the `unprefixed` list against prefixed names in the same scope.

Fix (your judgement): MUST rename with proper prefix using `rename_entity` (preserves observations and relations - no delete+recreate needed), link orphans, or delete if stale. For a misused `project` entity, MUST migrate its content to the correct entity type with a relation (a same-scope rename is `rename_entity`, while a cross-scope move is `move_entity_cross_scope`, which drops and returns the entity's relations for you to recreate in the target scope), or delete if a `task/`/`feature/` already covers it, then delete the rogue `project` entity.

## 2. Consolidate duplicates

MUST look for:
- Same entity in multiple project scopes (e.g. `user-preferences/` in a project scope that belongs in `global`) - to relocate an entity to its correct scope, SHOULD use `move_entity_cross_scope(source_project, target_project, name)` rather than hand-recreating it; because relations cannot span scopes it returns the dropped relations (`droppedRelations`) to recreate in the target scope
- Unprefixed duplicates of prefixed entities within one project - SHOULD use `merge_entities(project, source=unprefixed, target=prefixed)` to fold the unprefixed copy into the canonical one reversibly
- `user-preferences` entities scattered across project scopes - MUST extract useful observations into the main global preferences, then delete the project-scoped copy
- **Near-duplicate observations** within the same entity (same fact phrased two ways) - MUST keep the more precise one
- **Observations that contradict each other** - MUST resolve the contradiction, keep the correct one

## 3. Trim bloated entities

Project and task entities accumulate session-level detail over time. MUST trim them:
- **`project/` entities** SHOULD contain only current-state facts (tooling, paths, config, active TODOs) - not commit SHAs, resolved work detail, or session logs
- **Resolved `task/` entities** SHOULD be 1-3 observations max (outcome summary) - not a play-by-play of the implementation. If a feature entity captures the result, the task can have zero observations. SHOULD use `trim_observations_to_outcome(project, name, keep_hashes)` to trim an oversized resolved entity down to the kept observations in one call rather than individual `delete_observations`.
- **`feature/` entities** SHOULD describe the current state of the feature, not its development history
- **`user-preferences/` entities** SHOULD contain only preferences NOT already encoded in steering files/skills (which are always loaded). Observations duplicating rule file content are pure waste.
- SHOULD move reusable learnings to `pattern/` entities in global scope

### Size targets

The audit's `oversized` key lists every entity over its ceiling (with `count` and `threshold`). The ceilings it enforces are the upper bounds below; deciding *what* to trim from an over-ceiling entity is your judgement.

| Entity type | Ideal obs count | Action if over |
|---|---|---|
| `user-preferences/` | 10-30 | Extract domain-specific obs into `pattern/` entities |
| `project/` | 15-30 | Remove session logs, resolved work, ephemeral status |
| `knowledge/` | 5-20 | Remove implementation details, keep architecture |
| `pattern/` | 3-15 | Split if covering unrelated topics |
| Resolved `task/` | 0-3 | Delete implementation play-by-play |
| `feature/` | 3-10 | Remove development history |

## 4. Extract patterns from mega-entities

When a `user-preferences/` or `project/` entity has observations spanning unrelated domains, MUST extract them into focused `pattern/` entities:
- Technical gotchas (CDK, AWS, DDB) -> `pattern/<service>-gotchas`
- CLI command recipes -> `pattern/<tool>-commands`
- Workflow recipes (CR, deployment, testing) -> `pattern/<workflow>-<aspect>`

Each pattern entity SHOULD be independently searchable - someone searching for "CDK alarm" should hit the pattern directly, not wade through a 50-observation preferences blob.

## 5. Clean up stale entities

- SHOULD archive or delete resolved tasks that are no longer useful context
- SHOULD delete old ticket/CR entities that were one-off investigations
- SHOULD archive superseded project entities (e.g. old TS project replaced by Python rewrite)
- MUST treat a strongly negative `vote_score` as a rot signal - the audit's `negative_vote_entities` key lists these. MUST prioritise them for review, and trim or delete them if the downvotes reflect stale or misleading content. A negative score may be the dream's doing (an idle-window demotion) rather than a human judgement, so it is a *prompt to review*, not a verdict: MUST confirm the rot and delete/trim, or cast a `+1` if the entity is still useful and was over-demoted. (An entity that reached the saturation floor while orphaned may already have been reversibly soft-deleted by the startup GC; `restore_entity` brings it back if that was wrong.)
- MUST remove observations with:
  - Dates/timestamps (entities have automatic created_at/updated_at)
  - File paths in global scope (belong on project entities in project scope)
  - Changelog entries ("updated X on date Y")
  - Specific commit SHAs (live in git history)
  - Tool-specific workarounds for tools no longer in use

## 6. Verify relations

The audit flags the two mechanical relation faults for you: `orphans` (entities with zero relations, from section 1) and `relation_violations` (`task belongs-to project` edges). MUST work those keys, then apply the judgement checks below the audit cannot make:
- Every non-exempt entity SHOULD have at least one relation (see `orphans`)
- `task/` entities MUST have `implements` relation(s) to the feature(s) they modify - NOT `belongs-to` project (see `relation_violations`)
- `feature/` entities MUST have `belongs-to` relation to their parent project or knowledge hub
- `pattern/` entities SHOULD be linked to relevant `user-preferences/` or `project/` entities
- `knowledge/` entities SHOULD be linked to relevant projects or preferences
- SHOULD use specific relation types (`implements`, `depends-on`) over generic ones (`relates-to`)
- MUST check for overlapping entities that SHOULD be cross-linked (e.g. two patterns covering related topics)

## 6a. Fix star graphs

A common anti-pattern is every task linking directly to the project root, creating a star graph - the audit's `star_graph_tasks` key lists every `task -> project` edge (both the `belongs-to` violations and any other direct link). MUST fix this by:
- Ensuring feature entities exist for each major area of the project
- Replacing `task belongs-to project` with `task implements feature`
- Tasks are still reachable from the project via feature traversal (feature belongs-to project)
- MAY link a task directly to a project only if no relevant feature entity exists yet

## 7. Check global vs project scope

- **Global scope** SHOULD contain only: user preferences, cross-project patterns, cross-project knowledge
- **Project scope** SHOULD contain: detailed project facts, features, tasks, and project-specific preferences
- If a global entity has more than ~20 observations, it's probably bloated - SHOULD trim or extract patterns
- Observations about the same topic SHOULD live on ONE entity, not be scattered across multiple (consolidate overlaps)

## 8. Verify steering file alignment

MUST check whether observations on `user-preferences/` entities duplicate content already in steering rules or skill files. These are redundant because rules/skills are always loaded into context while memory must be actively searched:
- If an observation restates a rule file -> MUST delete it from memory
- If an observation adds nuance beyond the rule -> MUST keep it
- If a rule file is missing something that memory captures -> SHOULD update the rule file and delete the memory observation

After completing the checklist, MUST report what was cleaned up with counts (observations deleted, entities deleted/created, relations fixed).
