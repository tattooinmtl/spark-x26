---
id: 20261105-skills
title: Skills
tags: [skills, context]
created: 2026-11-05
updated: 2026-11-05
related: [20261105-tools-catalog, 20261105-memory-system, 20261105-context-budget]
summary: How skills load, size limits, where they live and which to add.
---
# Skills

> Summary: How skills load, size limits, where they live and which to add.

## Context
Skills already use progressive disclosure: only `id: description` lines sit in the system prompt and the body loads via `read_skill`. That is the right design for a small context — keep it.

## Details
### Rules
- Body ≤ 400 tokens, written as numbered steps that name the exact tools to call.
- Description says **when** to use it ("Use when a test fails…"), ≤ 20 words.
- The skills list is part of the cached prefix: loaded once at session start.

### Where they load from (later ones override by id)
1. `harness\skills\` (built-in)
2. `%USERPROFILE%\.spark-code26\skills\`
3. `<project>\.spark\skills\`

### Existing
`read-code`, `edit-code`, `verify`, `debug`, `plan` (16–21 lines each — good size).

### Add
- `windows-shell` — cmd.exe equivalents (`findstr`, `dir /s /b`, `type`, `where`), PowerShell one-liners.
- `run-tests` — find and run the project's tests, read only the failures.
- `git-workflow` — status/diff review before finishing; never commit unless asked.
- `refactor` — rename/move safely with grep for every call site first.
- `write-tests`, `explain-error`.

### Slash-command skills
A skill with `command: /name` in its frontmatter also becomes a slash command that sends its body as the instruction (custom commands, like Claude Code's `.claude/commands`).

## Related
- [Tools catalog](tools-catalog.md) — skills call: these tools.
- [Memory system](memory-system.md) — contrast: memory is facts, skills are procedures.
- [Context budget](../model/context-budget.md) — limits: size of the skills list.
