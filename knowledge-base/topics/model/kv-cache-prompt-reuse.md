---
id: 20261105-kv-cache-prompt-reuse
title: KV cache and prompt reuse
tags: [kv-cache, llama-cpp, performance]
created: 2026-11-05
updated: 2026-11-05
related: [20261105-chat-template-tool-format, 20261105-llama-server-settings, 20261105-spark-x26-facts]
summary: How llama.cpp reuses the cache on this SWA model and how the harness must call it.
---
# KV cache and prompt reuse

> Summary: How llama.cpp reuses the cache on this SWA model and how the harness must call it.

## Context
An agent turn is many model calls (one per tool step) whose prompts share almost everything. If each call reuses the previous KV cache, only the new tool result is prefilled. If not, the whole conversation is re-prefilled every step — on CPU that is the dominant cost.

## Details
### Why this model is special
27 of 36 layers are sliding-window. llama.cpp can roll back full-attention KV in place, but **SWA layers cannot be rolled back**. If a new prompt diverges from the cached one *anywhere before the end*, the server needs a **context checkpoint** at or before that point; without one it logs `forcing full prompt re-processing` (only visible at `-lv 4`) and re-prefills everything. The response is still correct — the only symptom is slow time-to-first-token.

Defaults: `--ctx-checkpoints 32`, `--checkpoint-min-step 8192` (checkpoints at least 8K tokens apart — useless with a 16K context), `--cache-ram 8192` MiB.

### The append-only rule (most important)
Every request must be **the previous request + the tokens the model just generated, byte for byte + the new turn**. Then no rollback is ever needed.

The harness breaks this today (hypothesis, verify with the check below):
- `split_thinking()` and `turn()` call `.strip()` on reasoning and content.
- Tool calls are parsed and **re-serialized** by the template (`json` separators, argument order, whitespace) instead of reusing the raw text.
→ The last assistant message differs from what the model produced → rollback → full re-prefill on every step.

Fix: keep the **raw completion text** for each assistant message and render it verbatim (`raw` field in the message; the template emits `<|Bot|><think>` + raw + `<｜end▁of▁sentence｜>`). Keep the parsed form only for executing tools and for display.

### How to verify
- `/completion` returns `tokens_evaluated` (prompt tokens actually computed) and `tokens_cached`. On step N>1 of a turn, `tokens_evaluated` should be ≈ the size of the new tool result, not the whole prompt.
- Run the server once with `-lv 4` and grep the log for `full prompt re-processing`.
- Show it in the TUI footer: `cache 96%`.

### Server flags
- `--parallel 1`, always send `id_slot: 0` and `cache_prompt: true` (already on).
- `--checkpoint-min-step 512` (or the typical step size) and `--ctx-checkpoints 16` so rare divergences still find a checkpoint; `--cache-ram 2048` because the PC has 16 GB RAM.
- `--swa-full` removes the problem entirely but triples KV memory; only for a short context on GPU.
- Verify each flag exists in b11873 with `llama-server --help` (names changed in May 2026).

### Saving the cache across sessions
`--slot-save-path` + `POST /slots/0?action=save|restore` writes the KV cache to disk. `/resume` can restore the KV instead of re-prefilling.

### Things that legitimately reset the cache
`/clear`, `/compact`, `/model`, `/reasoning`, `/project`, tool-list changes. Batch these; never change them silently per step.

## Sources
- [particula.tech — prompt reprocessing on SWA/hybrid models](https://particula.tech/blog/prompt-reprocessing-swa-hybrid-models-kv-cache)
- [llama-server manual](https://manpages.debian.org/unstable/llama.cpp-tools/llama-server.1.en.html)
- [llama.cpp issue 16491](https://github.com/ggml-org/llama.cpp/issues/16491)

## Related
- [Chat template and tool-call format](chat-template-tool-format.md) — prerequisite: what is in the prefix.
- [llama-server settings](llama-server-settings.md) — applies: the flags above.
- [Spark X2.6 model facts](spark-x26-facts.md) — cause: the SWA layer layout.
