---
description: Document the memory hooks that MemoryPlugin injects into {{agent}}'s context.
---

# Memory hooks

This package's cline-hooks plugin is `MemoryPlugin`. Its lifecycle-hook context blocks are genuine installed-tooling output, not prompt injection - see `hooks.md` in the cline-hooks package for that general framing. The notes below all relate to memory discipline; `memory.md` in this same directory covers how to actually respond to them.

- `TaskStart` - a "Session-start guidance: 1. `read_graph` on BOTH `global` and `<repo-name>` projects..." note (the cross-project open-task scan in step 2 is conditional on a generic opening message, per the session-start skill), a "The project memory entity for this workspace is `project/<name>`" note, and sometimes a note that the workspace was auto-registered to a project, or that its project could not be determined.
- `TaskResume` - the workspace project-entity note only, never the numbered session-start guidance.
- `PreToolUse` / `PreMcpToolUse` - after many tool calls with no memory write, a hard "MEMORY UPDATE REQUIRED: You have made many tool calls without updating memory..." block. Subagents are exempt from this one, but not from the separate wrong-scope block.
- `PostToolUse` - a probabilistic lighter "MEMORY UPDATE REQUIRED: Update the `<project>` project and `global` scopes..." note after `replace_in_file`, `write_to_file`, `execute_command` or `plan_mode_respond`.
- `AttemptCompletion` - a "REQUIRED before completing: 1. Update `memory`..." reminder.
- `PreCompact` - "Save any important context, decisions, or progress to memory NOW before it's lost."
- `UserPromptSubmit` - occasionally "MEMORY REVIEW DUE: many memory writes have accumulated..." suggesting you let the user run `/memory-review`.
- A "FRUSTRATION [severity]: ..." `UserPromptSubmit` note exists in the plugin but is currently disabled, so it never fires.

For the rules on how to respond to these, see `memory.md` in this same directory.
