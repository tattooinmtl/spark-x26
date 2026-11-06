---
id: 20261105-decisions-log
title: Decisions log
tags: [plan]
created: 2026-11-05
updated: 2026-11-05
related: [20261105-specs-spark26]
summary: Every plan decision, its status and why.
---
# Decisions log

> Summary: Every plan decision, its status and why.

## Context
Status values: `proposed` (waiting for Tattoo), `approved`, `done`, `dropped`.

## Details
| id | decision | status | why |
|---|---|---|---|
| D1 | Keep raw `/completion` with our own template port (not `/v1/chat/completions`) | done | full control of the prefix → cache reuse; tool format already parsed. |
| D2 | Store raw assistant text and render it verbatim | done | append-only prompts avoid SWA full re-prefill. |
| D3 | Keep past reasoning in history; drop it only in compaction | done | template preserves it; dropping per turn resets the cache. |
| D4 | GPU profile default, CPU fallback | done (auto-detect; CUDA build via install --gpu) | 8 GB VRAM fits Q8 + 64K q8_0 KV; CPU is single-channel bound. Open question: gaming conflicts. |
| D5 | Context 64K (GPU) / 32K (CPU) | done (32K default, --ctx 65536 on GPU) | model is strong to 256K; 16K is the bottleneck today. |
| D6 | `n_predict` 8192 + client thinking budget | done | fixes truncated tool calls and B9. |
| D7 | Tool profiles fixed per session | proposed | tool list is in the cached prefix. |
| D8 | Settings changes that touch the prefix apply on next chat | proposed | avoid silent cache resets. |
| D9 | Header: orange frame, title centred to full width | proposed | user request 2026-11-05. |
| D10 | Plan order: model runtime first, then TUI asks, tools, hooks, skills, memory, safety, polish | approved | user request 2026-11-05. |

## Related
- [Spark Code 2.6 plan](specs-spark26.md) — decided for: this plan.

## Phase 0 implementation notes (2026-11-05)
- Final answers were never added to history (the model could not see its own last answer). Fixed with the raw-history change.
- `http.client` drops `conn.sock` once a streamed response starts; the socket is kept separately so Esc can cut the stream.
- Thinking budget closes the block with `\n</think>\n\n` and continues on the same prompt; the closing text is part of the stored raw text.
- Live log before the change: prefill ~45 t/s, decode ~6.9 t/s on CPU, cache reuse 70-99% per step. Re-measure after.
