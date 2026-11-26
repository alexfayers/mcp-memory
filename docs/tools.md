# Tools

Most tools take a `project` parameter that scopes the data. Entity types are `project`, `feature`, `task`, `user-preferences`, `pattern` and `knowledge`. A name starts with its type prefix, like `task/<id>`, and the `project` entity is named `project/<project>`. Statuses are `planned`, `in-progress`, `blocked`, `resolved` and `archived`. Relation types are `implements`, `depends-on`, `blocks`, `relates-to`, `belongs-to`, `part-of`, `used-by` and `used-in`. Variants like `related-to` or `extends` are mapped to the closest one and anything else is rejected.

## Read

| Tool | What it does |
|---|---|
| `search_nodes` | FTS5 search. Matches any term by default, or all with `match_all=true`. Filters: `entity_type`, `status` (one or a list), `start`/`end` (`30m`, `7d`, `2w`, `3mo` or ISO dates). Archived entities are hidden unless asked for |
| `search_all_projects` | Same search across every project, grouped by project. `projects` narrows it, `expand_groups=true` adds each project's group siblings, `names_only=true` returns just names and statuses |
| `read_graph` | The 10 most recent entities and their relations |
| `get_entity_with_relations` | One entity with its relations and connected entities, filtered by `entityType` and `relationType` |
| `list_metadata` | `kind` is `projects`, `paths` or `groups`. `project` narrows paths and groups |
| `get_project_for_path` | The project whose registered path contains a filesystem path. The longest match wins, or null |
| `get_group_members` | Other projects in the same group |

Read results order observations best-first by vote and trim them to the character budget. Observations carry a `content_hash` that other tools accept in place of the full text.

## Write

Provenance invariant: a stored observation's provenance only rises (`user` > `observed` > `inferred` > untagged), so `user` is never lost through any write, merge, import, backfill or dream. The only exception is an explicit `edit_observation`, which dreams cannot call. Every provenance change on an existing row goes through one rank-checked update by row ID.

A dream-spawned call may not delete, trim, merge away or downvote a `user` observation, or delete or merge away an entity that holds one. It cannot pass `provenance`. An observation it writes takes the provenance of a live copy of the same text elsewhere in the project, preferring `inferred` and never `user`. It cannot create, rename onto or auto-create a project root over a deleted entity that holds a `user` observation. Orphan GC never reaps an entity that holds a live `user` observation.

| Tool | What it does |
|---|---|
| `create_entities` | Create entities with observations. Fails if a name exists. Every type except `project` needs at least one relation. An entity dict may carry `provenance`, applied to its observations |
| `add_observations` | Append to an existing entity. Duplicates are skipped. Pass `provenance` (`user`, `observed` or `inferred`) once per call to record where the claims come from. A duplicate add raises a less trusted observation to the higher-ranked provenance and never lowers it. Reads show a set provenance as `from`. Pass `triggers` to have the memory hook recall the observations: `pattern` (a regex over a tool call's input JSON, with an optional `tool`, such as `execute_command` or an MCP tool name) blocks the first matching call, `path` (a repo-relative glob) injects them on the first read or edit of a matching file, and `mention` (a regex over the agent's turn text) injects them after the turn |
| `edit_observation` | Edit one observation by `content_hash`, keeping its votes and timestamp. Use `oldText`/`newText` for a partial edit. An edit keeps the provenance unless `provenance` is passed, and keeps the triggers unless `triggers` is passed |
| `delete_observations` | Soft-delete by exact `observations` text and/or `observationHashes`. Returns the count and hashes deleted. Restorable with `restore_observations` until a purge |
| `restore_observations` | Undo a soft delete of observations by `observationHashes` and/or exact `observations` text. A hash with a live copy is skipped. Returns the restored count and hashes |
| `trim_observations_to_outcome` | Soft-delete every observation except those in `keep_hashes` (at least one). Returns the count and hashes trimmed. Restorable with `restore_observations` until a purge |
| `merge_observations` | Fold a duplicate observation (`sourceHash`) into another (`targetHash`) in the same entity. The target keeps the higher vote score. The source is soft-deleted and its hash returned. `restore_observations` brings it back until a purge, and the target keeps the higher score |
| `create_relations` | Create relations. Duplicates are ignored |
| `delete_relation` | Soft-delete one relation. Restorable with `restore_relation` until a purge. Creating it again also brings it back |
| `restore_relation` | Undo a soft delete of a relation (`source`, `target`, `type`) between two live entities. Fails if the relation never existed |
| `set_entity_status` | Set or clear a status. A resolved entity untouched for 56 days auto-archives, see `MCP_MEMORY_ARCHIVE_ENABLED` |
| `vote` | Vote on an entity, or on one observation (`observation` or `observationHash`). A nonzero integer from -3 to 3, returns the new net `vote_score`. Votes shift ranking within bounds but never hide anything |
| `rename_entity` | Rename in place. Relations and observations are kept. A different type prefix changes the type |
| `merge_entities` | Fold a duplicate entity (`source`) into `target` in one project. Observations are copied, relations repointed, the higher vote score kept. The source is soft-deleted |
| `restore_entity` | Undo a soft delete, until a purge removes it. Observations and relations deleted separately before the entity was deleted stay deleted |
| `delete_entity` | Soft-delete an entity, hiding it and its observations and relations. Restorable with `restore_entity` until a purge. Refuses while other live entities relate to it or if it would orphan one |

On its first start, the server tags observations that have no provenance from their wording: `inferred` for `INFERRED`, `Inferred`, `UNVERIFIED` or `Likely` leads and any `hypothesis:`, `observed` for `VERIFIED`, `OBSERVED`, `Verified`, `Source-verified` or `Confirmed` leads, and `user` for `User ...` or `The user ...` leads. The first match wins, and unmatched observations stay unknown. It backs up the database first if anything needs tagging. It marks the run done in the database in the same transaction, so it never runs again for that database, and writes the tagged rows beside the backups.

## Projects

| Tool | What it does |
|---|---|
| `set_metadata` | Change a project's `paths` or `groups` with `add`/`remove` (or `values` to replace the list). A path belongs to one project. When the working directory is under a registered path, that project is the active scope |
| `move_entity_cross_scope` | Move one entity to another project. Its relations are dropped and returned as `droppedRelations` so you can recreate them |
| `move_project_entities` | Move all entities, with observations and relations, between projects. Fails on a name in both |
| `delete_project` | Delete an empty project and its paths. Refuses `global` and projects with entities |
