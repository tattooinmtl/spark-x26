# Spark X2.6 — local llama.cpp setup

Everything for running **Spark-X2.6-4B** (XHToken) on this machine, plus a
small coding agent built around it.

```
C:\models\spark26\
├── start_spark26.bat            server / cli / bench launcher
├── start_spark26_agent.bat      coding agent launcher
├── spark26-chat-template.jinja  Spark's chat template (pretty-printed)
├── harness\
│   ├── spark_agent.py           the agent: tools, skills, agent loop
│   ├── tui.py                   terminal UI: banner, input box, footer
│   ├── commands.py              the slash commands
│   ├── webtools.py              web_search, web_fetch, headless browser
│   ├── mcpclient.py             MCP bridge (stdio + streamable http)
│   ├── winproc.py               keeps the agent's llama-server from outliving it
│   ├── gguf_info.py             read GGUF metadata without loading the model
│   ├── validate_template.py     proves the Python template port == llama.cpp's
│   ├── selftest.py              checks that need no model
│   └── skills\                  small skills, loaded on demand
│       ├── read-code\  edit-code\  verify\  debug\  plan\
├── llama\
│   ├── llama-b11873-cpu\        prebuilt CPU release  (default)
│   └── llama-b11721-notecode\   build of the local source tree
├── docs\                       screenshots of the terminal UI
├── logs\                       session transcripts, bench output
├── last-project.txt            the project both launchers open by default
└── workspace\                   fallback project directory
```

## Install

```bat
git clone https://github.com/tattooinmtl/spark-x26.git C:\Users\ThePa\.spark-code26
cd /d C:\Users\ThePa\.spark-code26
install.cmd
```

The installer checks the git checkout, fetches the llama.cpp CPU build,
finds the model (or downloads it from Hugging Face when you let it),
writes the `spark` command into `%USERPROFILE%\.local\bin` and runs the
self-test so a broken install is caught there and not later.

After that:

```bat
spark              start the coding agent
spark ui           start the llama.cpp web UI on :8081
spark bench        run llama-bench
spark update       pull a new version from the git repo
spark doctor       check the machine without loading the model
spark version      print the version
```

Anything after the command goes straight through, so `spark --workspace
C:\work --think low` and `spark update --force` both work.

The model is **not** in the repo (4 GB). The installer looks for
`Spark-X2.6-4B-Q8_0.gguf` in the install folder, then in `C:\models`, and
falls back to any `Spark-X2.6*.gguf` it finds there. `SPARK_MODEL` and
`SPARK_LLAMA_DIR` override both.

## Dev and production

Two folders, on purpose:

| | folder | what it is |
|---|---|---|
| dev | `C:\models\spark26` | where changes are made and pushed |
| production | `C:\Users\ThePa\.spark-code26` | the installed app, updated by `spark update` |

Production is a real git clone, so `spark update` is a fetch plus a
fast-forward merge. It refuses to run when the tree has uncommitted
changes, because a pull that quietly overwrites your work is worse than no
pull at all — commit first, or use `spark update --force`.

Changes made in the dev folder go to GitHub with `git push`; `spark
update` brings them into production. You can also just work in production
itself: it is a normal checkout, and `spark update` will tell you when it
would step on something you have not committed.

## Two different things, on purpose

There are two entry points and they are not the same product.

**`start_spark26.bat`** starts llama-server. Open http://127.0.0.1:8081 and
you get **llama.cpp's own web UI** — a chat box with llama.cpp's built-in
file tools and the MCP proxy. It is good for a quick question about a file
and it is deliberately *not* a coding environment: you are inside llama.cpp's
interface, the model drives one shot at a time, and there are no skills and
no project rules. It is now pointed at your project instead of at the
llama.cpp build folder, so at least it reads and writes the right code.

**`start_spark26_agent.bat`** starts the **coding agent**. It is a separate
program (`harness/spark_agent.py`) that drives the same model with a real
agent loop: skills, project instructions, a persistent conversation, visible
diffs, git awareness, web search and fetch, a headless browser, the tools
published by your MCP servers, a PI.DEV style terminal UI and a written
transcript of every session.

Use the web UI to look at something. Use the agent to work on something.

## The two launchers

```bat
start_spark26.bat                       server on http://127.0.0.1:8081
start_spark26.bat cli                   interactive chat in the window
start_spark26.bat bench                 llama-bench throughput
start_spark26.bat server low            less thinking, faster turns
start_spark26.bat server high b11721    use the source build instead
start_spark26.bat server high b11873 C:\Trains   serve one project
```

Tokens may be given in any order: `server|cli|bench`, `high|low|none`,
`b11873|b11721`. The **4th argument is the project directory** — the server
runs there, and llama.cpp executes its built-in tools (`read_file`,
`write_file`, `exec_shell_command`) in the working directory of the server
process, so the web UI operates on your code rather than on the llama.cpp
build folder. Default: the project used last.

```bat
start_spark26_agent.bat                 interactive session
start_spark26_agent.bat cli             interactive session on the llama-cli engine
start_spark26_agent.bat server --task "add a docstring to foo.py"
start_spark26_agent.bat --workspace C:\work
```

Run it with no arguments and it opens an interactive session: type an
instruction, read the answer, type the next one. The conversation history
carries over between turns, so follow-ups work ("now do the same for
beta.txt"). Type `/help` for the full command list.

Give it a task inline with `--task`, or pipe one in — both run once and exit:

```bat
echo "list the files" | start_spark26_agent.bat
```

The agent launcher passes every extra argument through to `spark_agent.py`,
so `python spark_agent.py --help` lists all of them.

Set `NOPAUSE=1` to stop either launcher from waiting on `pause` at the end —
needed when they are driven from a script or a pipe, where there is no
console key to press.

### They remember the last project

The project you point either launcher at is written to `last-project.txt`.
Next time, `start_spark26.bat` and `start_spark26_agent.bat` both open that
project with no arguments. Passing a project explicitly overrides it, and
the agent launcher (`--workspace`) updates the remembered one.

### Exit codes

| code | meaning |
|---|---|
| 0 | finished normally |
| 1 | the agent hit its step limit, or a tool call failed |
| 2 | bad arguments, or the model / llama.cpp build is missing |

The agent launcher with no arguments opens an interactive session rather than
exiting — the conversation stays alive between instructions.

## What Spark X2.6 actually is

Verified from the GGUF metadata (`harness/gguf_info.py`) and the model card:

| | |
|---|---|
| Architecture | `spark2_5` — **X2.6 is a post-training bump, not a new architecture** |
| Parameters | 4.4B dense, 36 layers, hidden 2560, FFN 10240 |
| Attention | 16 heads / 4 KV heads, head dim 256, **gated** (sigmoid gate on the output) |
| Layer mix | sliding window **512** on 3 of every 4 layers, full attention on every 4th |
| RoPE | partial, 64 of 256 dims, base 5e6 (full) / 1e4 (SWA) |
| Context | native **1,048,576** tokens |
| Vocab | 131,072, GPT-2 BPE, `tokenizer.ggml.pre = spark2_5` |
| Licence | Apache-2.0 |

Because the arch name is `spark2_5`, the existing llama.cpp builds already
support it — no new build needed. The model card asks for **b10828 or later**;
this box has b11873 (prebuilt) and b11721 (source build).

## Tricks this setup uses

**Thinking budget — none / low / high.** The template takes `enable_thinking`
and `reasoning_effort`. Only `high` and `low` are accepted; anything else
raises a template exception, so the launcher refuses other values.

- launcher: `--reasoning on --reasoning-effort low`
- per request: `"chat_template_kwargs": {"enable_thinking": false}`
- the template tells the model which budget it is running with

**Tool calls.** Spark emits GLM-style tags:
`<tool_call>name<arg_key>k</arg_key><arg_value>v</arg_value></tool_call>`.
llama.cpp's autoparser recognises this as `TAG_WITH_TAGGED` (the same family
as GLM-4.6), so `llama-server` returns structured `tool_calls`.
The harness does not rely on that: it parses the tags itself, so it works on
builds whose autoparser has no Spark rule.

**Sampling.** The model card's own numbers are used as the defaults:
temperature 1.0, top_p 0.95, top_k disabled, repetition_penalty 1.0.

**Swapping the quant.** The folder holds a Q8_0 (4.4 GB). `Q4_K_M` from
`bartowski/Spark-X2.6-4B-GGUF` is 2.6 GB and roughly 1.7x faster on CPU,
at a small quality cost. Both fit this box comfortably.

**Speculative decoding.** `Spark-X2.5-1.7B-Q8_0.gguf` is in `C:\models` and
shares the architecture, so it can serve as a draft model for the 4B target.
Not wired up yet — the 1.7B is the older X2.5 checkpoint, so it is a draft for
the architecture, not for this exact model.

**FIM.** The vocabulary carries `<|fim_begin|>`, `<|fim_hole|>` and
`<|fim_end|>`, so llama.cpp's infill mode can complete code in the middle of
a file.

**Prefix caching.** The agent sends the same system prompt and tool block on
every turn. llama-server keeps the prompt cache per slot, so only the new
turn is re-processed. `--cache-ram` bounds it.

**`--swa-full` exists but is off on purpose.** Spark's SWA layers only need
512 tokens of KV, so the default window-limited cache keeps the footprint
small. `--swa-full` would store the full context for those layers too.

## The agent

`harness/spark_agent.py` is a coding agent with two engines:

- **cli** — renders the prompt in Python and pipes it to `llama-cli`, one
  process per turn. This is the literal "wrap the CLI" path. The model is
  reloaded every turn: about 6 s warm, ~55 s cold.
- **server** — same rendering, posted to `llama-server` over the OpenAI API.
  The model stays resident, so a turn costs only the generation.

Both share the tool set, the path jail and the skills system.

**Tools**: `read_file`, `list_dir`, `grep`, `write_file`, `edit_file`,
`run_command`, `git_status`, `git_diff`, `git_log`, `read_skill`, `finish`.
One call per message.

**Git is read-only.** `git_status`, `git_diff` and `git_log` let the agent
see what it changed and how the repo looked before. There is no commit tool
on purpose: committing stays a human decision.

**Edits show their diff.** `write_file` and `edit_file` return a unified diff
of the change, so the agent sees exactly what it did — and so do you.

**The project states its own rules.** If the workspace root has an
`AGENTS.md` (or `CLAUDE.md` / `.agents.md`), it is appended to the system
prompt as project instructions that take precedence over the general rules.
That is what turns this into a coding environment rather than a chat: drop an
`AGENTS.md` into a repo and the agent follows it without being told again.

**Every session is written down.** Each run appends to
`logs/sessions/session-<timestamp>.md` — your instructions, every tool call
with its diff, and the final answer.

**Skills are progressively disclosed.** The system prompt lists only the id
and one-line description of each skill; the body is pulled with `read_skill`
when the task matches. Each skill is deliberately short (under 25 lines) —
a 4B model has no room for a 30 KB skill file, which is why the upstream
llama.cpp `skills/code-review/SKILL.md` is not usable as-is here.

**Path jail.** Every file tool resolves against the workspace root and
refuses to escape it. `run_command` runs in that directory and refuses a
short list of destructive commands.

### The agent's llama-server never outlives the agent

The agent runs its own `llama-server` on port **8082**, separate from the
web UI server on 8081. It has to be gone when the agent is gone, or a
5 GB model stays resident for nothing.

Three things guarantee that:

- `/exit`, `Ctrl+C` and a normal close all run the shutdown path, which
  terminates the server and waits for the port to come free.
- The server runs inside a Windows **job object** with
  `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`. If the agent dies in any other way
  — the console window is closed, the process is killed — Windows kills
  the job, and the server with it.
- A **detached watchdog process** backs that up. The job object cannot be
  set up when the agent is itself inside a job that forbids nesting, and
  in that case the watchdog notices the agent is gone and stops the
  server. It exits as soon as either process ends, so it never lingers.

A server left over from an earlier run is also cleared: port 8082 belongs
to the agent, so when the session ends no `llama-server` may be left
sitting on it. Only a `llama-server` started from this folder's own
llama.cpp build is ever stopped.

Verified on this box: `/exit` leaves port 8082 free with no `llama-server`
process behind, and a hard kill of the agent takes the server down within
a second or two.

## The terminal UI

`harness/tui.py` draws the session. It follows PI.DEV's shape and Claude
Code's border.

- **The title** is the app name in the ANSI Shadow figlet font, `SPARK CODE`
  on the first line and `2.6` centred under it, boxed in violet. If the
  window is too narrow to box it, the art is printed on its own rather than
  wrapped.
- **The input box** is an orange box you type into, with a live cursor:
  arrows, Home/End, Backspace, Ctrl+U (clear), Ctrl+W (delete word) and
  Up/Down through the history all work. The Windows console is read one key
  at a time so the box redraws as you type.
- **The footer** is two dim lines under the box. The first is where you are
  (workspace, git branch, session id). The second carries the small counters
  on the left — speed in tokens per second, tokens in and out, context used
  against the window, the tool count, the turn count — and the provider,
  model and thinking level on the right, the way PI.DEV does it.
- **Context colour** turns yellow past 70% of the window and red past 90%.

The counters come from the engine itself: `llama-server` reports its own
timings and token counts, and the CLI engine's timing footer is parsed the
same way.

## Slash commands

Everything below works in the interactive session. Type `/help` for the list.

| command | what it does |
|---|---|
| `/help` | every command, with a one-line description |
| `/skills` | the skills on offer; `/skills <id>` reads one in full |
| `/providers` | the providers in play: the two local engines, the web providers, the MCP servers |
| `/model` | the model in use and the other `.gguf` files; `/model <file>` switches and reloads it |
| `/reasoning` | the thinking level; `/reasoning none\|low\|high` changes it for the next turn |
| `/doctor` | a health check of the model, the build, the workspace, the tools, the web providers, the MCP servers and the browser |
| `/clear` | clear the conversation and the screen |
| `/compact` | summarise the conversation so far and continue from the summary |
| `/autocompact` | compact automatically once the context passes a threshold (70% by default) |
| `/resume` | the saved sessions; `/resume <number>` restores one |
| `/exit` | leave |
| `/tools` | every tool the model can call |
| `/mcp` | the MCP servers and their tools |
| `/web` | the web providers and whether their keys are set |
| `/browser` | check the headless browser |
| `/status` | what the session is doing right now |
| `/cost` | the token counters |
| `/project` | the workspace; `/project <path>` switches to another one |
| `/diff` | the git diff of the workspace |
| `/history` | the conversation so far |
| `/transcript` | where the session transcript is being written |
| `/about` | what this is and how it is wired |

`/compact` and `/autocompact` are real: the model is asked to summarise the
session, and the summary replaces the history. `/resume` works because every
turn is also written to `logs/sessions/session-<timestamp>.json` next to the
human-readable transcript.

## Web, browser and MCP

The agent can reach out, on both engines.

**Web search and fetch** (`harness/webtools.py`):

- `web_search` — Tavily when `TAVILY_API_KEY` is set, otherwise a DuckDuckGo
  and Bing HTML fallback.
- `web_fetch` — Firecrawl when `FIRECRAWL_API_KEY` is set, otherwise a plain
  HTTP fetch with the HTML reduced to text.
- `browser_navigate`, `browser_text`, `browser_click`, `browser_type`,
  `browser_press`, `browser_screenshot`, `browser_close` — one shared
  headless Chromium page, opened on the first navigate.

Fetches refuse `file:`, `data:` and `javascript:` URLs and any host that
resolves to a private or loopback address, so the agent cannot be talked
into reading the network it sits on. Screenshots land in `logs\.spark`, not
in your project.

**MCP** (`harness/mcpclient.py`): the servers are read from
`C:\Users\ThePa\.minimax\mcp.json` (then `.mavis\mcp.json`, then
`.omni-harness\mcp.json`). Both stdio and streamable-http servers work. The
async MCP SDK is bridged to the synchronous agent over a background event
loop, and every tool is published to the model as `mcp_<server>_<tool>`.

On this box `global-brain` connects and publishes seven tools; `blender` is
configured but its server is not running, and it is reported as such instead
of failing the session.

Turn both off with `--no-web --no-mcp`.

## Verification

Two independent checks, both runnable:

**`harness/validate_template.py`** renders a set of conversations (system + user,
tools, assistant with a tool call, two tool responses in one block, and each
of the three thinking modes) with the harness's Python port of the template
and with llama.cpp's own Jinja renderer through `POST /apply-template`,
then compares them byte for byte.

All 12 comparisons are identical. The port is safe to drive the model with.

**`harness/selftest.py`** covers the parts that do not need the model: the
thinking splitter (the completion shape differs between engines), the Spark
tool-call parser, the llama-cli output extractor, the workspace path jail,
the llama-cli timing footer, the terminal UI (box widths, the footer's
right-alignment, the context colour thresholds, the editing keys), the slash
command registry and dispatch, the web tools, the MCP bridge and the session
files that `/resume` reads. 82 checks, all passing.

Both were run against the real model and the real server.

The web tools were also run live: `web_search` returns real results,
`web_fetch` returns page text, `browser_navigate` drives headless Chromium
and `browser_screenshot` writes a real PNG. The MCP bridge was run against
the real servers on this box.

## Notes for maintenance

**Batch gotcha that already bit this launcher.** cmd does not expand a
tilde-modified reference (`%~1`) when it follows another `%` expansion on
the same line — it drops the `%` and leaves `~1` as literal text, so the
argument silently becomes the text "~1". The launcher therefore builds its
token list from `%*`, not from `%~1`/`%~2`/`%~3`.

**`goto` out of a called subroutine does not stop the script.** `exit /b`
only leaves the call. The classifier therefore reports a bad argument
through a `BADARG` variable and lets the main flow handle it.

**`--flash-attn` takes an optional value** and will swallow the next
argument if given none, so it is always passed as `--flash-attn on`.

## Measured on this box

Ryzen 7 5700U, 8 cores / 16 threads, 28.9 GB RAM, no dGPU, CPU only.

| | |
|---|---|
| Model load | ~6 s warm, ~55 s cold |
| Generation | ~7.5 t/s (8 threads, Q8_0) |
| Prompt processing | ~31.8 t/s |
| Agent turn, server engine | ~5-30 s (model stays resident) |
| Agent turn, cli engine | ~40-70 s (model is reloaded every turn) |

`llama-server` is on port **8081** (the GLM launcher uses 8080).
The agent uses **8082** for its own server.
