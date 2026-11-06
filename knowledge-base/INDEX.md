# Spark Code 2.6 — Knowledge Base Index

## How to use this KB (for agents)
1. Read this index first. Pick notes by their descriptions; open only those.
2. Prefer opening 1–3 specific notes over scanning everything.
3. Cite the note `id` when you use information from it.
4. The plan lives in `20261105-specs-spark26`; start there for "what's next".

## How to update this KB (for agents)
- New idea → new atomic note. Same idea changed → edit in place and bump `updated`.
- Any create / rename / delete MUST update this registry and the topic's `_topic.md` in the same change.
- Use only tags from the controlled vocabulary below; add a tag here before using it.

## Controlled tags
`model`, `llama-cpp`, `kv-cache`, `context`, `tools`, `hooks`, `skills`, `memory`, `tui`, `security`, `plan`, `audit`, `performance`

## Registry
### model
- `20261105-spark-x26-facts` — **Spark X2.6 model facts** — `topics/model/spark-x26-facts.md` — Architecture, context, sampling, thinking levels, benchmarks and the hardware it runs on.
- `20261105-chat-template-tool-format` — **Chat template and tool-call format** — `topics/model/chat-template-tool-format.md` — Exact prompt layout, tool-call syntax and what in it breaks the KV cache.
- `20261105-kv-cache-prompt-reuse` — **KV cache and prompt reuse** — `topics/model/kv-cache-prompt-reuse.md` — How llama.cpp reuses the cache on this SWA model and how the harness must call it.
- `20261105-llama-server-settings` — **llama-server settings** — `topics/model/llama-server-settings.md` — Recommended GPU and CPU launch flags, threads, KV type, quant, speculative decoding.
- `20261105-context-budget` — **Context budget** — `topics/model/context-budget.md` — Token budget per part of the prompt and caps for every tool output.

### harness
- `20261105-tools-catalog` — **Tools catalog** — `topics/harness/tools-catalog.md` — Current tools, missing tools, profiles and calling rules tuned for a 4B model.
- `20261105-hooks` — **Hooks** — `topics/harness/hooks.md` — Hook events, config format and the default hooks to ship.
- `20261105-skills` — **Skills** — `topics/harness/skills.md` — How skills load, size limits, where they live and which to add.
- `20261105-memory-system` — **Memory system** — `topics/harness/memory-system.md` — Layers of memory, the memory tool and how it stays cache-friendly.

### plan
- `20261105-specs-spark26` — **Spark Code 2.6 plan (specs)** — `topics/plan/specs-spark26.md` — The master plan: phases, order, owners, acceptance checks.
- `20261105-audit-2026-11-05` — **Audit 2026-11-05** — `topics/plan/audit-2026-11-05.md` — Bugs, security issues and TUI gaps found in the code audit.
- `20261105-decisions-log` — **Decisions log** — `topics/plan/decisions-log.md` — Every plan decision, its status and why.
