---
id: 20261105-memory-system
title: Memory system
tags: [memory, context, kv-cache]
created: 2026-11-05
updated: 2026-11-05
related: [20261105-skills, 20261105-hooks, 20261105-context-budget, 20261105-kv-cache-prompt-reuse]
summary: Layers of memory, the memory tool and how it stays cache-friendly.
---
# Memory system

> Summary: Layers of memory, the memory tool and how it stays cache-friendly.

## Context
Today the only memory is `AGENTS.md`/`CLAUDE.md` and saved session JSON. The agent forgets build commands, gotchas and preferences between sessions.

## Details
### Layers
| Layer | File | Written by | Loaded |
|---|---|---|---|
| Project rules | `AGENTS.md` / `CLAUDE.md` | you | session start, system block |
| Project memory | `<project>\.spark\memory.md` | agent via `memory` tool, you | session start, system block |
| User memory | `%USERPROFILE%\.spark-code26\memory\user.md` | agent, you | session start, system block |
| Session | `logs\sessions\*.json` + KV slot file | harness | `/resume`, `spark -c` |

### The `memory` tool
`memory(action=add|list|remove, scope=project|user, text)`. One fact per line, ≤ 200 chars, deduplicated. Writes go to disk immediately, but the **system block is not rebuilt mid-session** (cache) — a new fact is echoed in the tool result, so the model still sees it this session.

### Budget
Memory + AGENTS.md ≤ 1.5K tokens. Over budget → the oldest entries move to `memory-archive.md`, searchable with `grep`.

### Commands
`/memory` (show), `/memory edit` (open in notepad), `/init` (scan the repo and draft AGENTS.md: build, test, lint commands).

## Related
- [Skills](skills.md) — contrast: procedures vs facts.
- [Hooks](hooks.md) — uses: the test command stored here.
- [Context budget](../model/context-budget.md) — limits: memory size.
- [KV cache and prompt reuse](../model/kv-cache-prompt-reuse.md) — reason: no mid-session system rebuild.
