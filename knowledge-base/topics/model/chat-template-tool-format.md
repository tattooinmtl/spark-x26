---
id: 20261105-chat-template-tool-format
title: Chat template and tool-call format
tags: [model, tools, kv-cache]
created: 2026-11-05
updated: 2026-11-05
related: [20261105-kv-cache-prompt-reuse, 20261105-tools-catalog, 20261105-spark-x26-facts]
summary: Exact prompt layout, tool-call syntax and what in it breaks the KV cache.
---
# Chat template and tool-call format

> Summary: Exact prompt layout, tool-call syntax and what in it breaks the KV cache.

## Context
The harness renders the prompt itself (`SparkTemplate`, a Python port of `spark26-chat-template.jinja`) and posts it to `/completion`. That gives full control of the prefix — the key to cache reuse.

## Details
### Layout
```
<｜start▁of▁sentence｜><|System|>\n you are a helpful assistant.{effort text}
## Tools ... <tools>{one JSON schema per line}</tools>
\n\n{our system prompt}<｜end▁of▁sentence｜>
<｜start▁of▁sentence｜><|User|>...<｜end▁of▁sentence｜>
<｜start▁of▁sentence｜><|Bot|><think>{reasoning}</think>{content}<tool_call>...</tool_call><｜end▁of▁sentence｜>
<｜start▁of▁sentence｜><|Tool|><tool_response>...</tool_response><tool_response>...</tool_response><｜end▁of▁sentence｜>
... generation prompt: <|Bot|><think>   (or </think> when thinking is off)
```

### Tool-call syntax
`<tool_call>NAME<arg_key>k</arg_key><arg_value>v</arg_value>...</tool_call>` — strings raw, other values JSON with `", "` / `": "` separators. **Several calls per message are allowed**, and consecutive tool results share one `<|Tool|>` block.

### What invalidates the whole cache (it all sits in the first block)
- The **thinking level** (the effort sentence is in the system block).
- **Any change to the tool list** (adding MCP or web tools mid-session, reordering).
- Editing the system prompt (workspace switch, memory or instructions reloaded).
→ Fix these at session start; change them only on `/clear`, `/model`, `/reasoning`, `/project`.

### Reasoning is kept in history
Past assistant turns are re-rendered **with** their `<think>` block (llama-server logs "chat template supports preserving reasoning"). Keeping it is cache-friendly but costs context; see the context budget.

### Thinking off
Generation prompt ends in `</think>` so the reply starts directly with content/tool calls — use for cheap steps only if the level is fixed for the session.

## Related
- [KV cache and prompt reuse](kv-cache-prompt-reuse.md) — consequence: why the prefix must stay byte-stable.
- [Tools catalog](../harness/tools-catalog.md) — uses: the tool-call syntax.
- [Spark X2.6 model facts](spark-x26-facts.md) — context: thinking levels.
