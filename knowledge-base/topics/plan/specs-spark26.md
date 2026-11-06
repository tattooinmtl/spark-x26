---
id: 20261105-specs-spark26
title: Spark Code 2.6 plan (specs)
tags: [plan, model, tools, hooks, skills, memory, tui]
created: 2026-11-05
updated: 2026-11-05
related: [20261105-audit-2026-11-05, 20261105-decisions-log, 20261105-kv-cache-prompt-reuse, 20261105-llama-server-settings, 20261105-tools-catalog, 20261105-hooks, 20261105-skills, 20261105-memory-system, 20261105-context-budget]
summary: The master plan: phases, order, owners, acceptance checks.
---
# Spark Code 2.6 plan (specs)

> Summary: The master plan: phases, order, owners, acceptance checks.

## Context
Goal: a terminal coding agent that runs **Spark-X2.6-4B locally** and gets the most out of it — fast cached turns, tools and skills sized to its context, hooks, memory — with a TUI on par with Claude Code / Codex CLI / Gemini CLI.
Status: Phase 0 implemented in `C:\models\spark26` (not committed, not in production yet). Tests: `harness\stream_test.py`. Newest requests are at the top. Every phase ends with its acceptance checks passing in `selftest.py` / `tui_test.py` plus a live run.

## Phase 0 — Run the model right — DONE in dev, waiting for a live test on Windows
Notes: [model facts](../model/spark-x26-facts.md), [KV cache](../model/kv-cache-prompt-reuse.md), [server settings](../model/llama-server-settings.md), [context budget](../model/context-budget.md).
1. **Append-only prompts**: store each assistant message's raw completion and render it verbatim; no `.strip()` or re-serializing in history.
   ✔ step 2+ of a turn: `tokens_evaluated` ≈ new tool-result size; no `full prompt re-processing` at `-lv 4`.
2. **Output budget**: `n_predict` 8192 + client-side thinking budget (low 1024 / high 4096). Fixes thinking printed as the answer (audit B9).
   ✔ a 3-file edit task completes at `high` with no truncated tool call.
3. **Streaming** `/completion` (`stream: true`) feeding the TUI; **Esc cancels** (close the connection, keep history consistent).
4. **GPU profile** (CUDA build, `-ngl all`, 64K ctx, q8_0 KV) with automatic CPU fallback (`-t 6 -tb 10`, 32K). `spark doctor` reports which profile is active.
   ✔ `spark bench` numbers recorded in the decisions log for both profiles.
5. **Checkpoint flags** (`--checkpoint-min-step 512 --ctx-checkpoints 16 --cache-ram 2048`), `id_slot 0`; verify every flag against `llama-server --help` for b11873.
6. **Context budget** enforced: token-based tool output caps; footer shows `ctx %` and `cache %`.
7. **KV slot save/restore** for `/resume` and `spark -c`.
8. (open) Try `--spec-type ngram-mod` on edit-heavy tasks; keep only if faster.
9. (open) Quant option: Q4_K_M download offered by the installer for CPU users.

## Phase 0b — In-app maintenance and settings — DONE in dev (2026-11-05)
- `/update` (`/update-spark`, `/upgrade`), `/cuda status|install` (`/gpu`), `/hardware` (`/hw`), `/repair` (`/fix`, `--restore`), `/version`; same logic as `spark update|cuda|hardware|repair` (in `install.py`).
- `/cuda install` reads CPU, RAM, GPUs (VRAM via nvidia-smi) and NPUs, then picks CUDA (NVIDIA), Vulkan (AMD/Intel) or CPU, downloads the matching llama.cpp build, writes config.json and restarts the engine. NPUs are reported; llama.cpp has no NPU backend.
- `/settings` (`/config`): full page, every sampling, thinking, agent and engine value, personality, actions (new chat, edit AGENTS.md, edit personality, clear sessions, reset). `/set <key> <value>`. Saved in config.json; command-line flags win.
- `/reasoning none|low|high|max` (max = high without the harness budget; the model has no medium).
- Grouped `/help`; skills can declare `command:` and become slash commands (`/review`, `/code-review`).
- Fixed: B1 arrows, B2 history, B4 paste, B5 Esc, B6 Ctrl+Backspace, B8 /help name, S1 /clear overwrite, S4 cmd.exe in the system prompt.

## Phase 0c — Use the other llama.cpp tools (proposed)
- `llama-fit-params` in `/cuda install`: fit GPU layers and context to the VRAM instead of guessing.
- `/bench` in-app (`llama-bench`) to compare settings; record results in the decisions log.
- `llama-quantize` / download Q4_K_M for CPU-only PCs (download is better than re-quantizing the Q8).
- `llama-batched-bench` before adding a `task` sub-agent on a second slot.
- Not useful here: the vision CLIs (mtmd, llava, qwen2vl, gemma3), llama-tts (Spark is text-only), rpc-server (one PC is enough for 4B).

## Phase 1 — TUI fixes and layout you asked for (newest)
1. **Header**: an orange-bordered section with the title **centred across the full terminal width**, version and model/workspace line under it inside the same frame; the input box keeps the same orange border.
2. **Title and prompt overlap at the top after load** (audit B10): draw the header once, then a separator, then the prompt; the first prompt draw must not move the cursor above its own block. Needs a screenshot to confirm the cause.
3. **Thinking shown as an artifact** (audit B9): show thinking as one dim folded line (`✻ thinking… 812 tokens`), expandable with Ctrl+O; never as raw text in the answer.
4. **`/settings` screen** (DONE in 0b) (full-screen form, arrow/Tab to move, Enter to edit, Esc to leave, saved to `config.json`):
   - Text boxes: **AGENTS.md** (project), **Personality** (persona text added to the system prompt), user memory.
   - Sampling per use: agent steps, compaction summary, web summaries — temperature, top_p, top_k, repeat penalty, max tokens, thinking level and thinking budget each.
   - Engine: context size, GPU/CPU profile, KV type, threads.
   - Tools profile, permission mode, autocompact %, hooks on/off.
   - Actions: **New chat**, **Clear all sessions** (with confirm), clear input history, open logs folder.
   - Changes that reset the KV cache (personality, AGENTS.md, thinking level, tools) are marked "applies on next chat".
5. Bugs left from the audit: B3 git per keystroke, B7 resize (B1, B2, B4, B5, B6, B8, S1 done in 0b).
   ✔ `tui_test.py` gains a case per bug.

## Phase 2 — Tools sized for the model
Note: [tools catalog](../harness/tools-catalog.md).
Add `glob`, `multi_edit`, `todo` (live checklist in the TUI), `ask_user`, `memory`, `run_background/read_output/stop_process`, `diagnostics`. Tool profiles chosen at session start. cmd.exe stated in tool and system text. Git tools refuse outside a repo (S2). Parallel read-only calls allowed.
✔ prefix ≤ 3K tokens in `core` profile (`spark --render` measured).

## Phase 3 — Hooks
Note: [hooks](../harness/hooks.md). Events, `.spark/hooks.json`, default hooks (permission gate, syntax check after edits, test reminder at Stop, git status at SessionStart).

## Phase 4 — Skills
Note: [skills](../harness/skills.md). Three load locations, ≤ 400-token bodies, new skills (`windows-shell`, `run-tests`, `git-workflow`, `refactor`, `write-tests`, `explain-error`), skills as slash commands.

## Phase 5 — Memory
Note: [memory system](../harness/memory-system.md). Project + user memory files, `memory` tool, `/memory`, `/init`, 1.5K-token budget.

## Phase 6 — Safety
Permission modes (ask / auto-edit / yolo, Shift+Tab), approval for `run_command` and writes, `Workspace.resolve` with real path resolution (symlinks/junctions), argument-list git calls. (Audit H1, H2, M1, M2.)

## Phase 7 — Output and input polish
Markdown + code highlighting, coloured diffs, compact tool lines, Tab completion for `/` and `@file`, Ctrl+R history search, persistent history, `!` shell passthrough.

## Phase 8 — Later
`/undo` checkpoints, plan mode, `task` sub-agent on slot 1, `spark -c`, custom commands, plugin-style MCP presets.

## Roles (max 5 sub-agents per phase)
| Role | Owns |
|---|---|
| engine | `ServerEngine`, launch flags, streaming, cache checks (Phase 0) |
| tui | `tui.py`, header, settings screen, input bugs (Phase 1, 7) |
| tools | `spark_agent.py` tools, profiles, budgets (Phase 2) |
| hooks-skills-memory | Phases 3–5 |
| verifier | runs selftest/tui_test, live session, checks acceptance items; never edits code |

## Open questions
- Screenshot of the title/prompt overlap (Phase 1.2).
- GPU: is the 5060 Ti free while the agent runs, or should CPU stay default?
- Personality: free text only, or presets too?

## Related
- [Audit 2026-11-05](audit-2026-11-05.md) — source of: bug ids used above.
- [Decisions log](decisions-log.md) — records: choices made per phase.
