---
id: 20261105-hooks
title: Hooks
tags: [hooks, tools, security]
created: 2026-11-05
updated: 2026-11-05
related: [20261105-tools-catalog, 20261105-memory-system, 20261105-skills]
summary: Hook events, config format and the default hooks to ship.
---
# Hooks

> Summary: Hook events, config format and the default hooks to ship.

## Context
Hooks let the harness (not the model) enforce rules and feed back facts: format after edit, block dangerous commands, run tests before `finish`. Same shape as Claude Code hooks so existing knowledge carries over.

## Details
### Events
`SessionStart`, `UserPromptSubmit`, `PreToolUse` (can block or rewrite args), `PostToolUse` (can append text to the tool result), `PreCompact`, `Stop` (before the final answer; can send the model back to work once).

### Config
`<project>\.spark\hooks.json` and `%USERPROFILE%\.spark-code26\hooks.json` (project wins):
```json
{"PostToolUse": [{"match": "edit_file|write_file|multi_edit",
  "run": "python -m py_compile {path}", "timeout": 30}]}
```
The command gets the event as JSON on stdin. Exit 0 = continue (stdout appended to the tool result, capped at 500 tokens); exit 2 = block, stderr goes back to the model as the reason.

### Default hooks to ship
- PreToolUse `run_command`: the permission gate + denylist (replaces the inline list).
- PostToolUse edits: syntax check for `.py/.json/.js/.ts` — short output only on failure.
- Stop: if files changed and a test command is known (memory/AGENTS.md), remind once to run it.
- SessionStart: inject `git status --short` (≤ 20 lines) as the first user message, not into the system block (keeps the cache prefix stable).

### Rules
- Hook output goes into **messages**, never into the system block.
- Every hook has a timeout and shows as one dim line in the TUI.

## Related
- [Tools catalog](tools-catalog.md) — wraps: the tools.
- [Memory system](memory-system.md) — supplies: the known test command.
- [Skills](skills.md) — see-also: a skill can name a hook it expects.
