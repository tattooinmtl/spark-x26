---
id: 20261105-llama-server-settings
title: llama-server settings
tags: [llama-cpp, performance, model]
created: 2026-11-05
updated: 2026-11-05
related: [20261105-kv-cache-prompt-reuse, 20261105-spark-x26-facts, 20261105-context-budget]
summary: Recommended GPU and CPU launch flags, threads, KV type, quant, speculative decoding.
---
# llama-server settings

> Summary: Recommended GPU and CPU launch flags, threads, KV type, quant, speculative decoding.

## Context
`ServerEngine.start()` hard-codes a CPU-only command (`-ngl 0 --device none`, ctx 16384, threads 8 / batch 16). These are the proposed replacements. All numbers are estimates — benchmark with `spark bench` before adopting.

## Details
### Profile GPU (recommended default)
Needs the CUDA build of llama.cpp (the RTX 5060 Ti is Blackwell — use a CUDA 12.8+ / CUDA 13 Windows release). Auto-fall back to CPU when no GPU build or `nvidia-smi` is missing.
```
llama-server -m Spark-X2.6-4B-Q8_0.gguf --host 127.0.0.1 --port 8082
  -c 65536 -ngl all -fa on -ctk q8_0 -ctv q8_0
  --parallel 1 --checkpoint-min-step 512 --ctx-checkpoints 16 --cache-ram 2048
  --slot-save-path <install>\logs\.spark\slots
  -t 6 --poll 50
```
VRAM ≈ 4.4 GB weights + ~1.2 GB KV (64K, q8_0) + ~0.7 GB compute ≈ 6.3 GB of 8 GB. Close games first; offer a 32K f16 option.

### Profile CPU (fallback)
```
-c 32768 -ngl 0 -fa on -ctk f16 -ctv f16 -t 6 -tb 10 --parallel 1
--checkpoint-min-step 512 --ctx-checkpoints 16 --cache-ram 2048
```
- `-t 6` = the 6 P-cores. Today `-t 8 -tb 16` oversubscribes a 10-core CPU with no hyper-threading.
- Decode is memory-bandwidth bound on one DDR5 channel: offer **Q4_K_M** (≈2.5 GB, ~1.8× faster decode; what the vendor benchmarked). A second RAM stick (dual channel) roughly doubles CPU decode.

### Speculative decoding (no extra model needed)
Coding output repeats text already in context (file contents, edits). Try `--spec-type ngram-mod` or `ngram-simple`; measure tokens/s on an edit-heavy task. Keep it off if it does not help on this build.

### Things to remove
- `LLAMA_ARG_CHAT_TEMPLATE_KWARGS` env: deprecated (server warns) and unused because the harness posts raw `/completion`.
- `--reasoning-format deepseek`: no effect on `/completion`; harmless.

### Request side (`/completion` body)
- `n_predict`: 8192 (not 1024), with a client-side **thinking budget**: stream tokens, and if the think block exceeds the budget (low 1024 / high 4096), stop, append `</think>` and continue the same request prefix.
- `stream: true` for live output; `id_slot: 0`; `cache_prompt: true`.
- Stop on `<｜end▁of▁sentence｜>` explicitly.

## Related
- [KV cache and prompt reuse](kv-cache-prompt-reuse.md) — explains: the checkpoint flags.
- [Spark X2.6 model facts](spark-x26-facts.md) — source of: the memory math.
- [Context budget](context-budget.md) — uses: the chosen context size.
