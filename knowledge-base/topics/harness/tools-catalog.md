---
id: 20261105-tools-catalog
title: Tools catalog
tags: [tools, context, security]
created: 2026-11-05
updated: 2026-11-05
related: [20261105-chat-template-tool-format, 20261105-context-budget, 20261105-hooks, 20261105-memory-system]
summary: Current tools, missing tools, profiles and calling rules tuned for a 4B model.
---
# Tools catalog

> Summary: Current tools, missing tools, profiles and calling rules tuned for a 4B model.

## Context
Spark X2.6 is strongest at tool calling, but a 4B model does best with **few, sharp, well-described tools** whose outputs are short and whose errors say exactly what to do next.

## Details
### Today (20 + MCP)
Core: `read_file list_dir grep write_file edit_file run_command git_status git_diff git_log read_skill finish`.
Web: `web_search web_fetch browser_navigate browser_text browser_click browser_type browser_press browser_screenshot browser_close`.
MCP: `mcp_<server>_<tool>`.

### Add (in priority order)
| Tool | Why |
|---|---|
| `glob` | find files by pattern; today the model shells out (`dir /s`, fails on `head`). |
| `multi_edit` | several exact replacements in one file in one call → fewer steps, less context. |
| `todo` | the model's own plan list (set/update), rendered live in the TUI; keeps long tasks on track. |
| `ask_user` | pause and ask a question instead of guessing. |
| `memory` | `add / list / remove` project or user facts (see memory note). |
| `run_background` + `read_output` + `stop_process` | dev servers and watchers without blocking the turn. |
| `diagnostics` | run the project's checker (py_compile / ruff / tsc / cargo check) on changed files; also used by hooks. |
| `task` (later) | sub-agent on slot 1 with a fresh context; returns only a summary. Needs `--parallel 2`. |

### Change
- `read_file`: token cap (see budget), `offset/limit` names the model already knows.
- `run_command`: say in the description that the shell is **cmd.exe on Windows** (no `head`, `wc`, `grep`); keep head + tail of output; permission gate.
- `edit_file`: on "not found", return the closest matching lines so the model can retry once correctly.
- Git tools: refuse when the workspace itself has no `.git` (today they walk up to the parent repo).
- `finish`: keep, but also accept a plain answer with no tool call (today both work).

### Profiles (chosen at session start — never mid-session, it resets the cache)
- `core` (default): core + todo + memory + glob + multi_edit + ask_user ≈ 14 tools.
- `web`: + web_search, web_fetch.
- `full`: + browser_* + MCP.
Switch with `/tools profile <name>` (resets cache, says so).

### Calling rules for the system prompt
- Several **read-only** calls in one message are fine (the template supports it); writes one at a time.
- After an edit, read the diff the tool returns; don't re-read the whole file.
- Errors start with `error:` and end with the next action to take.

## Related
- [Chat template and tool-call format](../model/chat-template-tool-format.md) — syntax: how calls are written.
- [Context budget](../model/context-budget.md) — limits: output caps.
- [Hooks](hooks.md) — wraps: every tool call.
- [Memory system](memory-system.md) — defines: the memory tool.
