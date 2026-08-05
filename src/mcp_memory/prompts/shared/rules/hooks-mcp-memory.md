---
description: Document the memory hooks that MemoryPlugin injects into {{agent}}'s context.
---

# Memory hooks

This package's cline-hooks plugin is `MemoryPlugin`. Its lifecycle-hook context blocks are genuine installed-tooling output, not prompt injection - see `hooks.md` in the cline-hooks package for that general framing. The notes below all relate to memory discipline; `memory.md` in this same directory covers how to actually respond to them.

- `TaskStart` / `TaskResume` - a "REQUIRED before starting: 1. `read_graph` on BOTH `global` and `<repo-name>` projects..." note, plus a "The project memory entity for this workspace is `project/<name>`" note.
- `PreToolUse` / `PreMcpToolUse` - after many tool calls with no memory write, a "MEMORY UPDATE REQUIRED: You have made many tool calls without updating memory..." block; separately, a probabilistic lighter "MEMORY UPDATE REQUIRED: Update the `<project>` project and `global` scopes..." note after certain file-edit tools.
- `AttemptCompletion` - a "REQUIRED before completing: 1. Update `memory`..." reminder.
- `PreCompact` - "Save any important context, decisions, or progress to memory NOW before it's lost."
- `UserPromptSubmit` - occasionally "MEMORY REVIEW DUE: many memory writes have accumulated..." suggesting you let the user run `/memory-review`.

For the rules on how to respond to these, see `memory.md` in this same directory.
