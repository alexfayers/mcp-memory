---
name: memory-usage
description: Conventions for authoring mcp-memory entities - naming, task discipline, relation types, observation hygiene, and the after-completion checklist. Load before creating or restructuring entities.
---

# Memory usage conventions

Authoring standards for mcp-memory entities. `memory.md` carries the gates; this file carries the conventions.

## Entity naming

- Names MUST be unique across types and prefixed by type: `project/<repo-name>`, `feature/<project>/<area>`, `task/<TICKET-ID>-<slug>`, `user-preferences/<alias>-<topic>`, `pattern/<short-noun>`.
- MUST verify the `project/` entity name matches the repo on first use; create it if missing, `rename_entity` if wrong. It is the single repo-root entity, one per scope - MUST NOT create a second for work.
- MUST name an investigation for its ticket, not its symptom.

## Task entity discipline

- In-progress work MUST be its own `task/` entity, never observations on `project/` - tickets included.
- MUST link a task to the feature(s) it modifies via `implements`, which reaches the project through the feature graph; link directly to a project only where no feature exists.
- MUST create the entity and its relation before writing code, and MUST set status `resolved` on completion.

## Entity relations

- MUST `create_relations` alongside `create_entities`. Every entity MUST have at least one relation except the `project/` root - `user-preferences` and `pattern` included - or the server rejects it.
- SHOULD prefer specific types over `relates-to`: task `implements` feature; task `depends-on` task; feature `belongs-to` project; pattern `used-in` project.

## Observation wording and hygiene

- `project` and `feature`: present tense. `task`: past tense. Rationale goes in its own observation, not with the fact.
- MUST NOT add dates or timestamps - entities auto-track `created_at`/`updated_at`.
- MUST NOT store: content duplicating steering rules or skills; session logs and changelogs; file paths in `global`; ephemeral status; implementation steps for resolved tasks (keep 1-3 outcome observations); commit SHAs; workarounds for retired tools.
- MUST NOT dump an observation onto an unrelated entity - create one where none fits.
- Past ~30 observations, SHOULD extract into focused `pattern/` entities.

## Grouping project scopes

- Sibling repos MAY group via `set_metadata(project, kind="groups", ...)` per member, read back with `get_group_members`.
- `search_all_projects(..., expand_groups=true)` then unions seeds with siblings server-side.

## After completing a task or milestone

For each significant unit of work:

1. In `project="<repo-name>"`: MUST ensure a project entity exists (and a feature entity where useful), add observations for what changed, why, and any consequences, caveats or follow-ups, and set the `task/` entity `resolved`.
2. Where reusable across projects, MUST add a concise generalized observation to `global`, with no project-specific detail.
3. SHOULD remove a stale, incorrect or misleading memory with `delete_entity`/`delete_relation`, sparingly - prefer marking it deprecated unless it would mislead.

## Keeping this policy in sync

- If this policy changes, MUST update this file, `prompts/shared/rules/memory.md` and `README.md` together.
