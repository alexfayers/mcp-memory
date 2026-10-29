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

| Tool | What it does |
|---|---|
| `create_entities` | Create entities with observations. Fails if a name exists. Every type except `project` needs at least one relation |
| `add_observations` | Append to an existing entity. Duplicates are skipped |
| `edit_observation` | Edit one observation by `content_hash`, keeping its votes and timestamp. Use `oldText`/`newText` for a partial edit |
| `delete_observations` | Delete by exact `observations` text and/or `observationHashes` |
| `trim_observations_to_outcome` | Delete every observation except those in `keep_hashes` (at least one) |
| `merge_observations` | Fold a duplicate observation (`sourceHash`) into another (`targetHash`) in the same entity. The target keeps the higher vote score. The source is hard-deleted |
| `create_relations` | Create relations. Duplicates are ignored |
| `delete_relation` | Delete one relation |
| `set_entity_status` | Set or clear a status. A resolved entity untouched for 56 days auto-archives, see `MCP_MEMORY_ARCHIVE_ENABLED` |
| `vote` | Vote on an entity, or on one observation (`observation` or `observationHash`). A nonzero integer from -3 to 3, returns the new net `vote_score`. Votes shift ranking within bounds but never hide anything |
| `rename_entity` | Rename in place. Relations and observations are kept. A different type prefix changes the type |
| `merge_entities` | Fold a duplicate entity (`source`) into `target` in one project. Observations are copied, relations repointed, the higher vote score kept. The source is soft-deleted |
| `restore_entity` | Undo a soft delete, until a purge removes it |
| `delete_entity` | Permanently delete an entity with its observations and relations. Refuses while other entities relate to it or if it would orphan one |

## Projects

| Tool | What it does |
|---|---|
| `set_metadata` | Change a project's `paths` or `groups` with `add`/`remove` (or `values` to replace the list). A path belongs to one project. When the working directory is under a registered path, that project is the active scope |
| `move_entity_cross_scope` | Move one entity to another project. Its relations are dropped and returned as `droppedRelations` so you can recreate them |
| `move_project_entities` | Move all entities, with observations and relations, between projects. Fails on a name in both |
| `delete_project` | Delete an empty project and its paths. Refuses `global` and projects with entities |
