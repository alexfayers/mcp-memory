---
description: Guide {{agent}} on using mcp-memory for persistent memory.
---

# Memory Usage with mcp-memory

You have one MCP memory server available: `memory`

An optional second server, `memory-agent`, may also be present. It exposes a single `recall(query)` tool that delegates *heavy, multi-step* recall to a throwaway agent and returns distilled findings, each tagged with its source entity slug `[project/entity]`:

- Use `recall` when answering a question would otherwise mean firing several searches and traversing many entities - it keeps that graph JSON out of your own context while handing back actionable slugs you can then vote on or traverse.
- **Mechanical trip-wire (do not rely on judgement here):** the moment you notice you are about to fire a *second* broad `search_nodes`/`search_all_projects` call for the *same* question, or a single search result is large enough to be truncated/persisted to a file, stop and route the question through `recall` instead. Multiple broad searches for one question is the signal you should have used `recall` from the start.
- It is read-only and each call is a full agent spawn (slower and costlier than a direct tool call), so for a targeted lookup you can do inline, call `search_nodes` directly instead.
- `recall` does **not** replace the mandatory "Before starting a task" search ritual or the cheap session-start scan - those stay on the plain `memory` tools. Reach for `recall` once you know the user's specific ask and answering it needs heavy traversal; it is not the broad "what's here" scan at session start. If `memory-agent` is absent, ignore this note.

All tools require a `project` parameter that scopes data. Use two logical projects:

- `global` - for cross-project knowledge (user preferences, patterns, reusable techniques)
- `<repo-name>` - for project-specific knowledge (e.g. `mcp-memory`)

You can (and should) use these MCP tools in _both_ PLAN and ACT mode.

You _do not_ need to let the user know if/when you are interacting with memory.

Ensure you _always_ update memory as you progress through a task, and just before you complete it. **Do not batch memory updates for the end - persist knowledge as soon as you learn it.**

## When to use which project scope

- Use `project="global"` for:
  - General user preferences (coding style, stack choices, tooling).
  - Reusable patterns and techniques (infra patterns, testing approach, migration strategies).
  - Cross-project knowledge (oncall procedures, ADC partition info, team references).
  - Update after any change (successful or failed - note failures explicitly), any new information gained from the user, and any insight discovered during the task.

- Use `project="<repo-name>"` for:
  - **Everything project-specific** - including the `project/` entity itself, all features, tasks, and architecture.
  - Module/API contracts, invariants, and non-obvious gotchas.
  - Project-specific user preferences that don't apply globally.
  - TODOs, partial work, and context that only matters in this codebase.

- **Do NOT create project summary entities in global scope.** Each project's `project/` entity lives in its own project scope. Global is only for things that span multiple projects.
- **NEVER put workspace-specific facts (repo names, file paths, workspace specific rules, tool configs specific to a repo) into global memory.**
- **Before adding to an entity found via search, check whether its current scope actually matches its subject.** A scope can be wrong for reasons other than the entity being new - e.g. it was created from a fallback/basename scope (a home directory that happens to be a git repo, a generic folder name) rather than the specific project it is actually about. If an entity's content clearly belongs to a different, already-known project scope, move it there (recreate under the correct project with all existing observations/relations preserved, then `delete_entity` the original) before appending more content - don't perpetuate a wrong scope by writing more into it.

### Finding project locations

**CRITICAL - mechanical trip-wire, do not rely on judgement here:** the instant a task needs a project's filesystem location, the first tool call MUST be `get_paths_for_project(project="<repo-name>")` (or `list_project_paths()` to browse all registered mappings, or `get_project_for_path(path=...)` to go the other direction) - BEFORE any `find`, `grep`, `ls`, or other disk search. This includes cases where you already ran a memory search for unrelated content in the same turn and it happened to surface a path in passing (e.g. inside a task observation) - that is not a substitute for the deliberate lookup call. Memory already tracks where projects live on disk; searching the filesystem for a project you could look up is redundant and slower. Only fall back to a disk search if `get_paths_for_project` returns empty.

### If the `memory` server is unavailable

If `memory` is not accessible and you need it for the current task:
1. Start the server: `mcp-memory`
2. Ask the user to reload the MCP connection.
3. Do not continue until `memory` is available.

### Restarting the memory service

**NEVER** manually restart the mcp-memory service (e.g. `launchctl kickstart`, `systemctl restart`). Always use `llm-prompts update`, which handles setup, reinstall, and service restart in one command.

## Before starting a task

For ANY and EVERY task, you **MUST** follow ALL of these steps - no exceptions, no shortcuts!

**CRITICAL: Do NOT respond to the user until ALL steps below are complete.** Skipping steps 3-5 defeats the purpose of having memory. `read_graph` alone is not enough - it only returns recent entities and misses deeper context.

**NOTE:** The session-start skill handles the cross-project task summary using `compact=true` calls. The steps below are for finding context relevant to the user's **specific request** once you know what they need - they are deeper, targeted lookups that happen after the lightweight session scan.

1. **ALWAYS** use `read_graph(project="global")` first - this surfaces recent global entities. Never skip this step.
2. **ALWAYS** use `read_graph(project="<repo-name>")` second - this surfaces recent project entities.
3. **ALWAYS** use `search_nodes(project="global")` to find entities related to the user's request. Search for:
    - Keywords and terms from the user's message (e.g. file names, feature names, ticket IDs)
    - `user-preferences` (always search this - it contains workflow and coding style rules)
    - The current project/repository name
    - Any relevant `pattern/` entities (e.g. `pattern/aws-lambda-debugging`, `pattern/dynamodb-batch-get-retry`) - search for keywords related to the tools/services being used
4. **ALWAYS** use `search_nodes(project="<repo-name>")` to find entities related to:
    - Keywords and terms from the user's message
    - The current file(s) or directory being worked on
    - Any feature or ticket identifiers mentioned in the request
    - use `status="in-progress"` filter on `search_nodes` to find any unfinished task entities for the current project (preferred over text search)
5. **ALWAYS** use `get_entity_with_relations` on every relevant entity found in steps 3-4. This traverses the graph to discover linked context that search alone would miss.
    - **ALWAYS** call `search_related_nodes(project="<repo-name>", name="project/<current-project>", entityType="task")` to find all task entities for the current project.

6. If relevant entities exist:
    - Briefly summarize what is already known before making a plan.
    - Highlight prior decisions, constraints, and pitfalls.

**Searching effectively.** `search_nodes` and `search_all_projects` match **any** of a multi-word query's terms by default (OR), ranking entities that match more terms first - so a multi-keyword query is fine and returns the best matches at the top. Pass `match_all=true` only when you need *every* term present (strict AND). Hyphenated terms (e.g. `auth-service`) are treated as a single adjacent-token phrase and will not match compound words like `authservice`, so prefer the bare keywords (`auth service`). If a search returns nothing, retry with fewer/shorter or differently-spelled keywords before concluding the memory does not exist.

## Entity and observation standards

Use consistent naming and entity types to maximize discoverability:

### Entity naming

Entity names must be unique across all entity types. Always prefix the name with the entity type to prevent collisions:

| What | Entity type | Name format | Example |
|---|---|---|---|
| A repository / codebase | `project` | `project/<repo-name>` | `project/ExampleProject` |
| A feature area or module | `feature` | `feature/<project>/<area>` | `feature/ExampleProject/ticketing` |
| A task or ticket | `task` | `task/<TICKET-ID>-<slug>` | `task/ABC-123-idempotency-simplification` |
| A user preference or style | `user-preferences` | `user-preferences/<alias>-<topic>` | `user-preferences/jdoe-workflow` |
| A reusable pattern | `pattern` | `pattern/<short-noun>` | `pattern/dynamodb-batch-get-retry` |

When first interacting with a workspace, verify the `project/` entity name in memory matches the actual package/repo name. If no entity exists, create one. If the name is wrong (e.g. from a rename), create a new entity with the correct name, migrate observations, and delete the old one.

**CRITICAL: the `project` entity type is reserved for the single repo-root entity, named `project/<repo-name>`.** There is exactly one per project scope. NEVER create a second `project`-type entity for an investigation, incident, feature, or piece of work - that is what `task`, `feature`, and `pattern` are for. Because `project` (and `user-preferences`) are the only types exempt from the relation requirement, using `entityType: project` for a work item is the one way to sneak an orphan past the server's relation check - so a free-floating `project` entity (other than the repo root) is always a mistake. An incident or ticket investigation is a `task/<TICKET-ID>-<slug>`; name it for the ticket, not the symptom (e.g. `task/ABC-123-auth-timeout`, never a symptom-named `project` entity like `auth_timeout_investigation`).

### Task entity discipline

**CRITICAL: In-progress work MUST be tracked as a separate `task/` entity - never as observations on a `project/` entity.** This includes external tickets under investigation - each ticket gets its own `task/` entity with a `belongs-to` relation to the relevant knowledge or project entity. Do not store ticket-specific details as observations on a parent knowledge entity.

- Every `task/` entity MUST have its `status` field set to one of: `planned`, `in-progress`, `blocked`, `resolved`, `archived`
- Use `set_entity_status` or pass `status` in `create_entities` - do NOT add a `STATUS:` text observation
- Tasks MUST link to the feature(s) they modify via `implements` relations - this connects them to the project through the feature graph
- Do not add redundant `belongs-to` project relations on tasks - they are reachable via feature traversal
- Only link a task directly to a project if no relevant feature entity exists yet
- When starting a new piece of work, create the `task/` entity and relation immediately - before writing any code
- When completing a task, call `set_entity_status` with `status="resolved"`
- Do not store implementation details or work-in-progress notes on the `project/` entity

### Entity relations

Memory is a graph database - use `get_entity_with_relations` to traverse linked entities and discover connected context.

**CRITICAL: You MUST call `create_relations` whenever you call `create_entities`.** Relations are the core of the graph model - entities without relations are nearly useless. Always link new entities to existing ones.

Every entity MUST have at least one relation, except `user-preferences` (a global singleton) and the single `project/<repo-name>` root entity. This includes `pattern` entities: a pattern MUST link to what it applies to. The server now rejects any non-exempt entity created without a relation, so always create the relation in the same `create_entities` call (inline `relations`) or immediately after. The exemption exists ONLY so the repo-root can anchor the graph - it is NOT licence to model a work item as a `project` entity to skip the relation. If you catch yourself reaching for `entityType: project` to avoid adding a relation, the type is wrong: it's a `task`, `feature`, or `pattern`, and it needs a relation.

Use relations to link related entities, e.g.:
- task `implements` feature (every task should link to the feature(s) it modifies)
- task `depends-on` task
- task `relates-to` task
- feature `belongs-to` project
- pattern `used-in` project (project-specific pattern)
- pattern `used-by` user-preferences/<alias>-workflow (cross-project/global pattern)

Prefer specific relation types (`implements`, `depends-on`) over generic ones (`relates-to`). A rich graph with meaningful edges is far more useful than a star graph where everything just points at the project root.

### Observation wording

Use entity type to distinguish current facts from past actions:

- **`project` / `feature` observations** - use present tense for current facts: "process_ticket requires relationship_manager"
- **`task` observations** - use past tense for completed actions: "Removed is_tracked and is_processed_or_tracked helpers"
- Do not include rationale in the same observation as the fact - add a separate observation for "why"
- **Never include dates or timestamps in observations** - entities already have automatic `created_at`/`updated_at` timestamps that track when observations were added

### Observation hygiene - what NOT to store

Do NOT add observations that:
- **Duplicate steering rules or skill files** - these are always loaded into context; duplicating them in memory is pure waste
- **Record session logs** ("Session 2026-05-13: did X then Y") - memory is for current-state facts, not changelogs
- **Contain file paths in global scope** - paths belong on project entities in their own project scope
- **Are ephemeral status** ("wthaz working on X as of today") - these rot immediately
- **Describe implementation steps for resolved tasks** - once done, only the outcome matters (1-3 obs max)
- **Reference specific commit SHAs** - git history is authoritative for this
- **Describe tool-specific workarounds for tools no longer in use** - delete when obsolete

When an entity exceeds ~30 observations, it's a signal to extract domain-specific knowledge into focused `pattern/` entities that are independently searchable.

**CRITICAL: Never dump observations onto an unrelated entity for convenience.** Every observation must belong to the entity it describes. If no appropriate entity exists, create one. Misplaced observations destroy discoverability - the whole point of the graph.

## While working

- **Update memory frequently** - after each meaningful step, not just at the end. Triggers include:
  - Completing a file edit or group of related edits
  - Discovering something unexpected (a bug, an API quirk, a design constraint)
  - Making a decision or trade-off
  - Receiving feedback or correction from the user
  - Learning new factual information from any source
  - **User confirms an external state change** (e.g. "PR merged", "deployed", "ticket closed") - update the relevant entity IMMEDIATELY in the same response
- **CRITICAL: Hook reminders appear as `<hook_context>` blocks in the environment details. When you see one, you MUST act on it in your NEXT tool call - before doing anything else. Do NOT defer, skip, or queue it for later.**
- As you discover important facts (architecture decisions, API contracts, subtle bugs, performance findings, etc.), update memory with observations worth persisting.
- Prefer small, precise observations over long narrative text.
- Each observation must be **atomic** - one fact per observation. Never combine multiple distinct facts into a single observation string (e.g. do not write "X was done (Y is also true)" - instead add two separate observations).
- Group related observations under a single entity for the project or feature when possible.
- **IMPORTANT: `create_entities` OVERWRITES all existing observations for an entity.** To append new observations without risk of data loss, use `add_observations` instead. Only use `create_entities` when you need to replace all observations or create a new entity. If you must use `create_entities` on an existing entity, always call `get_entity_with_relations` first to read existing observations and include ALL of them.
- Use `add_observations` to safely append new facts to an existing entity - it deduplicates automatically and throws if the entity doesn't exist.
- Use `delete_observations` to remove specific observations by exact content match - it returns the count deleted and throws if the entity doesn't exist.
- When two entities are duplicates of the same thing, use `merge_entities(project, source, target)` to fold the source into the canonical target - it copies observations (deduped, keeping votes), repoints all relations, keeps the higher vote score, and soft-deletes the source. It is reversible: `restore_entity(project, source)` undoes it until a grace-window purge. Prefer this over manually copying observations and deleting the duplicate.
- **Vote on memories as you retrieve them.** When a search or recall surfaces an entity that genuinely helped, `vote_entity(project, name, 1)`; when one is stale, misleading, or noise, `vote_entity(project, name, -1)`. Votes tune future ranking (useful memories rise, unhelpful ones sink but remain findable) and do not alter content or `updated_at`. Prefer a downvote over `delete_entity` when a memory is unhelpful but not wrong enough to remove.
- **Vote on individual observations, not just whole entities.** `vote_observation(project, entityName, observation, vote)` casts a `+1`/`-1` on a single observation, addressed by its exact content (the same way `delete_observations` identifies one). Upvoted observations surface first within their entity and downvoted ones sink, so a large entity leads with its most useful lines. Choose the level by what is actually good or bad:
  - The **whole entity** is useful/stale -> `vote_entity`.
  - The entity is worth keeping but **one observation** in it is the gold (or is stale noise) -> `vote_observation` (`+1` to float it, `-1` to sink it).
  - An observation is outright **wrong** -> `delete_observations`; downvote only when it is stale-but-not-wrong.
  Like entity votes, an observation vote never changes content or `updated_at`. There is no automatic observation vote (the implicit-usefulness auto-vote is entity-level only), so observation ranking depends entirely on you casting these as you read.
- **A background "dream" pass may also be grooming the graph.** When memory has been idle for a while, autonomous curation passes (opt-in, off by default) run in two tiers. The frequent light tier *only* casts `-1` votes on stale, superseded, or duplicate entities. A rarer heavy tier (opt-in separately) also *merges* clear duplicates within a project via `merge_entities` (a reversible soft-delete of the folded-away source). Neither tier ever upvotes, edits observations, or *hard*-deletes. This is complementary to your own voting, not a replacement:
  - Keep voting as you retrieve. Your `+1`/`-1` reflect what actually helped this session - a signal the dream cannot infer from a cold read.
  - Do not rely on the dream to remove anything: hard **deletion stays a deliberate manual operation.** The heavy tier's merges and a separate opt-in startup GC (which *reversibly soft-deletes* an entity driven to the saturation floor **and** orphaned - never a `project` root or `user-preferences`) are both recoverable until a grace-window purge, so neither is a substitute for deliberate cleanup.
  - If an entity you know is useful has drifted down in ranking, a `+1` corrects it (downvotes only sink an entity; they never remove it and are always reversible). If the heavy tier merged something it should not have, `restore_entity` brings the folded-away source back.

## After completing a task or reaching a milestone

For each significant unit of work (feature implemented, bug fixed, refactor completed), and **BEFORE** you {{TOOL_COMPLETE}}:

1. Using `project="<repo-name>"`:
    - Ensure there is an entity representing this project and, if useful, one for the specific feature/area.
    - Add new observations describing:
      - What changed
      - Why it changed (rationale)
      - Any important consequences, caveats, or follow-up TODOs.
    - Call `set_entity_status` on the `task/` entity with `status="resolved"`.

2. When the knowledge is reusable across projects:
    - Also update `project="global"` with a concise, generalized observation.
    - Avoid project-specific details in global memory; focus on patterns and lessons.

3. If a memory is no longer relevant, was incorrect, or would actively mislead future sessions, use `delete_entity` and/or `delete_relation` to remove it. Use this sparingly - prefer marking things deprecated in text unless the memory would cause harm.

## Answering memory-related questions

When the user asks questions like:

- "What do you already know about this project / file / feature?"
- "What have we decided about X so far?"
- "What did we learn from previous work on this?"

You should:

1. Query `project="<repo-name>"` for the most relevant entities and their observations.
2. Optionally query `project="global"` if broader patterns or preferences might be relevant.
3. Present a concise summary, grouped by entity/topic.
4. Clearly distinguish between project-specific memory and global, cross-project knowledge.

## Memory rules

- When creating an implementation plan, always include memory updates in the plan
- Always share these rules with any subagents
- **Subagents are read-only for memory.** A subagent (Explore, Plan, general-purpose, Task) MAY read the graph (`read_graph`, `search_nodes`, `get_entity_with_relations`, `search_related_nodes`) but MUST NOT mutate it (`create_entities`, `add_observations`, `set_entity_status`, `create_relations`, votes). A subagent returns facts-worth-persisting to its caller; the **main thread** performs every write, after verifying against a fresh read. This keeps mutation in one place - subagent writes tend to create entities without the paired `create_relations` (orphans) and scatter scratch observations into whatever scope is active - and stops plan-mode subagents from tripping write-approval prompts. The "persist as you go" rule above binds the main thread; a subagent persists nothing itself.
- Query memory before starting a task
- Update memory as you go - don't just wait until the end of a task
- **ALL knowledge not stored in memory or prompt files is permanently lost at the end of each session.** This includes things told to you mid-session (e.g. user preferences, model config, tool behaviour). Always persist this kind of information immediately to `project="global"`.
