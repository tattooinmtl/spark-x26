---
id: 20261105-context-budget
title: Context budget
tags: [context, tools, performance]
created: 2026-11-05
updated: 2026-11-05
related: [20261105-llama-server-settings, 20261105-tools-catalog, 20261105-memory-system]
summary: Token budget per part of the prompt and caps for every tool output.
---
# Context budget

> Summary: Token budget per part of the prompt and caps for every tool output.

## Context
A 4B model degrades when its context fills with noise. Every part of the prompt gets a fixed budget, and the footer shows the totals.

## Details
### Measured today
- Our system prompt ≈ 900 chars; 11 core tool schemas ≈ 3,500 chars; 9 web/browser schemas ≈ 2,800 chars → **≈ 2K tokens** of fixed prefix before MCP tools.

### Budget (32K context; scale ×2 for 64K)
| Part | Budget |
|---|---|
| Fixed prefix (system + tools + skills list) | ≤ 3K |
| Memory + AGENTS.md | ≤ 1.5K |
| Conversation (incl. kept reasoning) | ≤ 19K |
| Reply reserve (`n_predict` + thinking) | 8K |
| Autocompact trigger | 75% |

### Tool output caps (by tokens, not lines)
- `read_file`: ≤ 2,000 tokens per call (today 400 lines ≈ up to 6K tokens); say how to continue.
- `grep`: ≤ 60 hits, 160 chars each. `list_dir`: ≤ 200 entries.
- `run_command`: head 1K + tail 1K tokens (errors are usually at the end; today only the head is kept).
- `web_fetch`: ≤ 3K tokens.

### Compaction order (cheapest first)
1. Elide old tool results → `[result elided, N tokens: re-run read_file to see it]` (one cache reset, many tokens freed).
2. Drop reasoning of finished turns.
3. Summarize (current `/compact`) — run the summary prompt on the elided history so it fits.

## Related
- [llama-server settings](llama-server-settings.md) — sets: the context size.
- [Tools catalog](../harness/tools-catalog.md) — applies: the output caps.
- [Memory system](../harness/memory-system.md) — limited by: the memory budget.
