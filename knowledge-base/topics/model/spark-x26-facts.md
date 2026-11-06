---
id: 20261105-spark-x26-facts
title: Spark X2.6 model facts
tags: [model, context, performance]
created: 2026-11-05
updated: 2026-11-05
related: [20261105-chat-template-tool-format, 20261105-kv-cache-prompt-reuse, 20261105-llama-server-settings]
summary: Architecture, context, sampling, thinking levels, benchmarks and the hardware it runs on.
---
# Spark X2.6 model facts

> Summary: Architecture, context, sampling, thinking levels, benchmarks and the hardware it runs on.

## Context
Everything the harness does should fit what this model is good at (tool calling, agent loops) and what it costs (KV memory, prefill time).

## Details
### Identity
- `XHToken/Spark-X2.6-4B`, Apache 2.0, 4.11B params, same architecture as X2.5 (post-training changes only).
- llama.cpp arch `spark2_5`; needs build **b10828+** (we ship b11873).
- Local file: `C:\models\Spark-X2.6-4B-Q8_0.gguf` (~4.4 GB). Vendor benchmarks used **Q4_K_M**.

### Architecture (drives the KV-cache plan)
- 36 layers: **27 sliding-window (window 512) + 9 full-attention**, pattern 3 SWA then 1 full.
- GQA: 16 query heads, **4 KV heads**, head_dim 256. Vocab 131,072.
- KV per token, full layers only, f16: 9 × 4 × 256 × 2 (K+V) × 2 B = **36,864 B/token** → 16K ≈ 0.6 GB, 32K ≈ 1.2 GB, 64K ≈ 2.4 GB (half that with q8_0).
- SWA layers keep only ~512 tokens each unless `--swa-full` (then 3× the full-layer cost).

### Context
- `max_position_embeddings` 1,048,576. Strong to 256K; 512K–1M is uneven (secondary source).
- Harness today uses **16,384** — far below what the model handles.

### Sampling (model card)
- temperature 1.0, top_p 0.95, top_k off, repetition/presence/frequency penalty neutral. Harness matches this.

### Thinking
- Levels: `none`, `low`, `high`. Template default is thinking on, effort high.
- Card examples allow up to 131,072 new tokens — the model expects room to think. Harness `--max-tokens 1024` is far too small (see audit B9).

### Strengths (vendor-reported, unverified here)
- BFCL-V4 71.8, τ²-bench 86.9, MCP-Atlas 62.4, SWE Verified 62.8, IFEval 93.6 — tool calling and agent loops are its strongest area.

### Hardware it runs on (Tattoo's PC)
- Core Ultra 5 225F (10 cores: 6P + 4E, no hyper-threading), **16 GB DDR5 single stick (single channel)**, **RTX 5060 Ti 8 GB**.
- Today the harness runs **CPU only** (`-ngl 0 --device none`, CPU build). Single-channel DDR5-6000 ≈ 48 GB/s → Q8 decode ceiling ≈ 10 t/s; Q4_K_M ≈ 18 t/s (rough upper bounds).
- The GPU (≈448 GB/s) fits Q8 weights + a 64K q8_0 KV cache in 8 GB → several times faster decode and much faster prefill.

## Sources
- [XHToken/Spark-X2.6-4B model card](https://huggingface.co/XHToken/Spark-X2.6-4B)
- [mer.vin summary with config-derived numbers](https://mer.vin/news/spark-x2-6-4b-4b-on-device-agent-model-benchmarks-and-local-setup/)
- Local: `spark26-chat-template.jinja`, `logs/.spark/llama-server.log`

## Related
- [Chat template and tool-call format](chat-template-tool-format.md) — supports: how prompts are laid out.
- [KV cache and prompt reuse](kv-cache-prompt-reuse.md) — follows from: the SWA architecture above.
- [llama-server settings](llama-server-settings.md) — applies: the hardware numbers above.
