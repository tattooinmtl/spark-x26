#!/usr/bin/env python3
"""
Slash commands for the Spark Agent CLI.

Every command is a function registered with @command("/name", "summary").
The handler receives a Context and returns either None (the command printed
what it wanted to say) or a string (the text is printed for it).

The commands are the PI.DEV / Claude Code set: skills, providers, model,
reasoning level, doctor, clear, compact, autocompact, resume, exit and a
handful of extras that are useful for a local coding agent.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import tui

REGISTRY: dict = {}


def command(name: str, summary: str, aliases: tuple = ()):
    """Register a slash command."""
    def wrap(func):
        entry = {"name": name, "summary": summary, "func": func, "aliases": aliases}
        REGISTRY[name] = entry
        for alias in aliases:
            REGISTRY[alias] = dict(entry, name=alias, func=func, aliases=())
        return func
    return wrap


class Context:
    """Everything a command is allowed to touch."""

    def __init__(self, agent, cfg, engine, ws, skills, tools, mcp=None, web=None):
        self.agent = agent
        self.cfg = cfg
        self.engine = engine
        self.ws = ws
        self.skills = skills
        self.tools = tools
        self.mcp = mcp
        self.web = web

    @property
    def theme(self) -> tui.Theme:
        return tui.THEME

    def say(self, text: str = ""):
        print(text)


def _lines(*parts) -> str:
    return "\n".join(str(p) for p in parts)


def _web_status(ctx: Context) -> dict:
    """Which web providers are usable right now.

    Never prints a key, only whether one is present.
    """
    if ctx.web is not None and hasattr(ctx.web, "provider_status"):
        try:
            return ctx.web.provider_status()
        except Exception:
            pass
    status = {}
    for name, env_name in (("tavily", "TAVILY_API_KEY"), ("firecrawl", "FIRECRAWL_API_KEY")):
        configured = bool(os.environ.get(env_name))
        status[name] = {"configured": configured, "detail": env_name if configured else f"{env_name} not set"}
    status["duckduckgo"] = {"configured": True, "detail": "no key needed, used as the search fallback"}
    return status


def _mcp_status(ctx: Context) -> dict:
    """Normalise the MCP bridge status into {server: {...}}."""
    if ctx.mcp is None:
        return {}
    try:
        raw = ctx.mcp.server_status()
    except Exception as exc:
        return {"mcp": {"connected": False, "error": str(exc), "tools": 0, "tool_names": []}}
    if isinstance(raw, dict):
        raw = list(raw.items())
    out = {}
    for name, status in raw:
        status = str(status)
        connected = status.startswith("connected")
        tools = 0
        if "(" in status and "tools)" in status:
            try:
                tools = int(status.split("(")[1].split()[0])
            except (ValueError, IndexError):
                tools = 0
        error = "" if connected or status == "disabled" else status
        out[name] = {
            "connected": connected,
            "error": error,
            "tools": tools,
            "tool_names": [],
        }
    return out


# --------------------------------------------------------------------------
# Help
# --------------------------------------------------------------------------
@command("/help", "show every command", aliases=("/?", "/h"))
def cmd_help(ctx: Context):
    theme = ctx.theme
    theme_ = theme
    out = [theme_.paint("Commands", "bold")]
    seen = set()
    for name in sorted(REGISTRY):
        entry = REGISTRY[name]
        if entry["func"] in seen:
            continue
        seen.add(entry["func"])
        aliases = ""
        if entry["aliases"]:
            aliases = theme_.paint(f"  ({', '.join(entry['aliases'])})", "dim")
        out.append(
            f"  {theme_.paint(name.ljust(14), 'accent')}{entry['summary']}{aliases}"
        )
    out.append("")
    out.append(theme_.paint("Anything that does not start with / is sent to the agent.", "dim"))
    return _lines(*out)


@command("/skills", "list the skills, or show one with /skills <id>")
def cmd_skills(ctx: Context):
    theme = ctx.theme
    wanted = (ctx.agent.current_argument or "").strip()
    if wanted:
        for skill in ctx.skills:
            if skill["id"] == wanted or skill["name"] == wanted:
                return _lines(
                    theme.paint(f"# {skill['id']}", "bold"), "", skill["body"]
                )
        available = ", ".join(s["id"] for s in ctx.skills) or "(none)"
        return theme.paint(f"unknown skill {wanted!r}; available: {available}", "warning")
    if not ctx.skills:
        return theme.paint("no skills found", "dim")
    out = [theme.paint("Skills", "bold")]
    for skill in ctx.skills:
        out.append(
            f"  {theme.paint(skill['id'].ljust(12), 'accent')}{skill['description']}"
        )
    out.append("")
    out.append(theme.paint("Use /skills <id> to read one in full.", "dim"))
    return _lines(*out)


@command("/skill", "show one skill in full: /skill <id>", aliases=())
def cmd_skill(ctx: Context):
    wanted = (ctx.agent.current_argument or "").strip()
    if not wanted:
        return ctx.theme.paint("usage: /skill <id>", "warning")
    for skill in ctx.skills:
        if skill["id"] == wanted or skill["name"] == wanted:
            return _lines(
                ctx.theme.paint(f"# {skill['id']}", "bold"), "", skill["body"]
            )
    available = ", ".join(s["id"] for s in ctx.skills) or "(none)"
    return ctx.theme.paint(f"unknown skill {wanted!r}; available: {available}", "warning")


# --------------------------------------------------------------------------
# Providers, model, reasoning
# --------------------------------------------------------------------------
@command("/providers", "show the providers this agent can talk to")
def cmd_providers(ctx: Context):
    theme = ctx.theme
    out = [theme.paint("Providers", "bold")]
    out.append(
        f"  {theme.paint('local'.ljust(16), 'accent')}"
        f"{theme.paint('model', 'dim')}      {ctx.cfg.model}"
    )
    out.append(
        f"  {theme.paint('local-cli'.ljust(16), 'accent')}"
        f"{theme.paint('model', 'dim')}      {ctx.cfg.model}"
    )
    if ctx.web is not None:
        status = _web_status(ctx)
        for name, info in status.items():
            mark = theme.paint("ok", "success") if info.get("configured") else theme.paint("no key", "dim")
            out.append(f"  {theme.paint(name.ljust(16), 'accent')}{theme.paint('web', 'dim')}      {mark}")
    if ctx.mcp is not None:
        for name, info in _mcp_status(ctx).items():
            mark = (
                theme.paint("connected", "success")
                if info.get("connected")
                else theme.paint(info.get("error", "not running") or "not running", "dim")
            )
            out.append(
                f"  {theme.paint(name.ljust(16), 'accent')}{theme.paint('mcp', 'dim')}        {mark}"
            )
    out.append("")
    out.append(theme.paint("Switch the model with /model, the thinking level with /reasoning.", "dim"))
    return _lines(*out)


def _model_candidates() -> list:
    folder = Path(ctx_models_dir())
    return sorted(folder.glob("*.gguf")) if folder.is_dir() else []


def ctx_models_dir() -> str:
    model = os.environ.get("SPARK_MODEL_DIR")
    if model:
        return model
    return r"C:\models"


@command("/model", "show the model, list the .gguf files, or switch with /model <name>")
def cmd_model(ctx: Context):
    theme = ctx.theme
    argument = (ctx.agent.current_argument or "").strip()
    current = Path(ctx.cfg.model)

    if not argument:
        out = [theme.paint("Current model", "bold"), f"  {current}"]
        candidates = _model_candidates()
        if candidates:
            out.append("")
            out.append(theme.paint("Available", "bold"))
            for candidate in candidates:
                mark = theme.paint("*", "success") if candidate == current else " "
                try:
                    size = candidate.stat().st_size / (1024 ** 3)
                    detail = f"{size:.1f} GB"
                except OSError:
                    detail = ""
                out.append(f" {mark} {theme.paint(candidate.name.ljust(38), 'accent')}{detail}")
            out.append("")
            out.append(theme.paint("Switch with /model <file name>.", "dim"))
        return _lines(*out)

    candidates = _model_candidates()
    target = None
    for candidate in candidates:
        if candidate.name.lower() == argument.lower():
            target = candidate
            break
    if target is None:
        candidate = Path(argument)
        if candidate.is_file():
            target = candidate
    if target is None:
        return theme.paint(f"no model file called {argument!r}", "warning")

    if target == current:
        return theme.paint(f"already using {target.name}", "dim")

    ctx.cfg.model = str(target)
    note = ctx.agent.invalidate_engine()
    message = f"model set to {target.name}"
    if note:
        message += f"; {note}, it is reloaded with the new model on the next turn"
    else:
        message += "; the engine reloads it on the next turn"
    return theme.paint(message, "success")


@command("/reasoning", "show or set the thinking level: none, low, high", aliases=("/think",))
def cmd_reasoning(ctx: Context):
    theme = ctx.theme
    argument = (ctx.agent.current_argument or "").strip().lower()
    if not argument:
        table = {
            "none": "no thinking block, fastest answers",
            "low": "short thinking block",
            "high": "long thinking block, best for hard work",
        }
        out = [theme.paint("Reasoning level", "bold"), f"  current: {ctx.cfg.think}"]
        out.append("")
        for level, detail in table.items():
            mark = theme.paint("*", "success") if ctx.cfg.think == level else " "
            out.append(f" {mark} {theme.paint(level.ljust(6), 'accent')}{theme.paint(detail, 'dim')}")
        out.append("")
        out.append(theme.paint("Set with /reasoning <level>.", "dim"))
        return _lines(*out)

    if argument not in ("none", "low", "high"):
        return theme.paint(f"unknown level {argument!r}; use none, low or high", "warning")
    ctx.cfg.think = argument
    # The thinking level is rendered into the prompt, so it applies to the
    # very next turn - no reload needed.
    return theme.paint(f"reasoning level set to {argument}", "success")


# --------------------------------------------------------------------------
# Doctor
# --------------------------------------------------------------------------
@command("/doctor", "check the environment and report what works")
def cmd_doctor(ctx: Context):
    theme = ctx.theme
    cfg = ctx.cfg
    out = [theme.paint("Spark Agent doctor", "bold")]

    def line(label, value, ok=True, note=""):
        mark = theme.paint("ok", "success") if ok else theme.paint("!!", "warning")
        text = f"  {mark} {theme.paint(label.ljust(18), 'accent')}{value}"
        if note:
            text += theme.paint(f"  {note}", "dim")
        out.append(text)

    # Python
    version = sys.version.split()[0]
    line("python", version, True, sys.executable)

    # Model
    model = Path(cfg.model)
    if model.is_file():
        size = model.stat().st_size / (1024 ** 3)
        line("model", model.name, True, f"{size:.2f} GB")
    else:
        line("model", str(model), False, "missing")

    # llama.cpp
    llama_dir = Path(cfg.llama_dir)
    exe = llama_dir / ("llama-server.exe" if cfg.engine == "server" else "llama-cli.exe")
    if exe.is_file():
        line("llama.cpp", exe.name, True, str(llama_dir))
    else:
        line("llama.cpp", str(llama_dir), False, "executable missing")

    # Engine
    if cfg.engine == "server":
        alive = False
        try:
            import urllib.request

            with urllib.request.urlopen(
                f"http://{cfg.host}:{cfg.port}/health", timeout=2
            ) as response:
                alive = response.status == 200
        except Exception:
            alive = False
        line("engine", f"server on {cfg.host}:{cfg.port}", alive, "model stays resident")
    else:
        line("engine", "cli (one process per turn)", True, "model reloads each turn")

    line("threads", f"{cfg.threads} (batch {cfg.threads_batch})", True)
    line("context", f"{cfg.ctx} tokens", True)
    line("sampling", f"temp {cfg.temp} top_p {cfg.top_p} top_k {cfg.top_k}", True)
    line("reasoning", cfg.think, True)

    # Workspace
    ws = ctx.ws.root
    line("workspace", str(ws), ws.is_dir())
    is_git = (ws / ".git").exists()
    line("git", "repository" if is_git else "not a repository", is_git,
         "" if is_git else "git tools will refuse")
    instructions = [
        name for name in ("AGENTS.md", "CLAUDE.md", ".agents.md") if (ws / name).is_file()
    ]
    line("instructions", ", ".join(instructions) or "none", True,
         "" if instructions else "add AGENTS.md to the project root")

    # Skills and tools
    line("skills", f"{len(ctx.skills)}", True, ", ".join(s["id"] for s in ctx.skills))
    core = [t for t in ctx.tools if not t["function"]["name"].startswith(("web_", "browser_", "mcp_"))]
    web = [t for t in ctx.tools if t["function"]["name"].startswith(("web_", "browser_"))]
    mcp = [t for t in ctx.tools if t["function"]["name"].startswith("mcp_")]
    line("tools", f"{len(core)} core", True, ", ".join(t["function"]["name"] for t in core))
    line("web tools", f"{len(web)}", True, ", ".join(t["function"]["name"] for t in web) or "none")
    line("mcp tools", f"{len(mcp)}", True, ", ".join(t["function"]["name"] for t in mcp) or "none")

    # Web providers
    if ctx.web is not None:
        for name, info in _web_status(ctx).items():
            line(f"web: {name}", "configured" if info.get("configured") else "no API key",
                 bool(info.get("configured")))

    # MCP servers
    if ctx.mcp is not None:
        for name, info in _mcp_status(ctx).items():
            ok = bool(info.get("connected"))
            line(f"mcp: {name}", "connected" if ok else (info.get("error") or "not running"), ok,
                 f"{info.get('tools', 0)} tools")

    # Browser
    try:
        import importlib.util

        has_playwright = importlib.util.find_spec("playwright") is not None
    except Exception:
        has_playwright = False
    line("browser", "playwright available" if has_playwright else "playwright missing", has_playwright)

    # Logs
    logs = Path(cfg.transcript_dir)
    try:
        logs.mkdir(parents=True, exist_ok=True)
        writable = os.access(logs, os.W_OK)
    except OSError:
        writable = False
    line("transcripts", str(logs), writable)

    return _lines(*out)


# --------------------------------------------------------------------------
# Conversation control
# --------------------------------------------------------------------------
@command("/clear", "clear the conversation and the screen", aliases=("/reset", "/new"))
def cmd_clear(ctx: Context):
    ctx.agent.reset()
    if sys.stdout.isatty():
        sys.stdout.write("\x1b[2J\x1b[H")
    return ctx.theme.paint("conversation cleared", "success")


@command("/compact", "summarise the conversation so far and start again with the summary")
def cmd_compact(ctx: Context):
    theme = ctx.theme
    summary = ctx.agent.compact()
    if summary is None:
        return theme.paint("nothing to compact yet", "dim")
    return theme.paint("conversation compacted", "success")


@command("/autocompact", "compact automatically when the context fills: /autocompact [on|off|percent]")
def cmd_autocompact(ctx: Context):
    theme = ctx.theme
    argument = (ctx.agent.current_argument or "").strip().lower()
    agent = ctx.agent
    if not argument:
        state = "on" if agent.autocompact else "off"
        return _lines(
            theme.paint("Autocompact", "bold"),
            f"  {state}  threshold {agent.autocompact_threshold}% of the context window",
            "",
            theme.paint("Set with /autocompact on, off or a percentage.", "dim"),
        )
    if argument in ("on", "true", "yes"):
        agent.autocompact = True
        return theme.paint("autocompact on", "success")
    if argument in ("off", "false", "no"):
        agent.autocompact = False
        return theme.paint("autocompact off", "success")
    try:
        percent = int(argument.rstrip("%"))
    except ValueError:
        return theme.paint(f"unknown argument {argument!r}", "warning")
    if not 10 <= percent <= 95:
        return theme.paint("the threshold must be between 10 and 95 percent", "warning")
    agent.autocompact = True
    agent.autocompact_threshold = percent
    return theme.paint(f"autocompact on, at {percent}% of the context window", "success")


@command("/resume", "list the saved sessions, or restore one: /resume <id>")
def cmd_resume(ctx: Context):
    theme = ctx.theme
    sessions = ctx.agent.list_sessions()
    argument = (ctx.agent.current_argument or "").strip()
    if not argument:
        if not sessions:
            return theme.paint("no saved sessions yet", "dim")
        out = [theme.paint("Saved sessions", "bold")]
        for index, info in enumerate(sessions, 1):
            out.append(
                f"  {theme.paint(str(index).ljust(3), 'accent')}"
                f"{theme.paint(info['id'].ljust(26), 'muted')}"
                f"{theme.paint(info['when'].ljust(20), 'dim')}"
                f"{info['summary']}"
            )
        out.append("")
        out.append(theme.paint("Restore with /resume <number> or /resume <id>.", "dim"))
        return _lines(*out)

    target = None
    if argument.isdigit():
        index = int(argument) - 1
        if 0 <= index < len(sessions):
            target = sessions[index]["path"]
    if target is None:
        for info in sessions:
            if info["id"] == argument:
                target = info["path"]
                break
    if target is None:
        return theme.paint(f"no session called {argument!r}", "warning")
    ok = ctx.agent.load_session(target)
    if not ok:
        return theme.paint(f"could not restore {target}", "warning")
    # The session may have been recorded in another project.
    ctx.ws = ctx.agent.ws
    return theme.paint(f"resumed {Path(target).name}", "success")


@command("/exit", "leave the agent", aliases=("/quit", "/q"))
def cmd_exit(ctx: Context):
    ctx.agent.quit_requested = True
    return ctx.theme.paint("bye", "muted")


@command("/history", "show the conversation so far")
def cmd_history(ctx: Context):
    theme = ctx.theme
    out = [theme.paint("Conversation", "bold")]
    for message in ctx.agent.messages[1:]:
        role = message.get("role")
        if role == "tool":
            continue
        content = message.get("content") or ""
        if not isinstance(content, str):
            content = str(content)
        calls = message.get("tool_calls") or []
        if calls:
            names = ", ".join(c.get("name", "?") for c in calls)
            content = (content + f"  [tools: {names}]").strip()
        out.append(f"  {theme.paint(role.ljust(10), 'accent')}{content[:300]}")
    return _lines(*out)


@command("/status", "show what the session is doing right now")
def cmd_status(ctx: Context):
    theme = ctx.theme
    agent = ctx.agent
    stats = agent.session_stats()
    out = [theme.paint("Session", "bold")]
    out.append(f"  {theme.paint('workspace'.ljust(14), 'accent')}{ctx.ws.root}")
    out.append(f"  {theme.paint('model'.ljust(14), 'accent')}{ctx.cfg.model}")
    out.append(f"  {theme.paint('reasoning'.ljust(14), 'accent')}{ctx.cfg.think}")
    out.append(f"  {theme.paint('engine'.ljust(14), 'accent')}{ctx.cfg.engine}")
    out.append(f"  {theme.paint('messages'.ljust(14), 'accent')}{len(agent.messages)}")
    out.append(f"  {theme.paint('turns'.ljust(14), 'accent')}{stats.get('turns', 0)}")
    out.append(f"  {theme.paint('tokens'.ljust(14), 'accent')}"
               f"in {tui.format_tokens(stats.get('prompt_tokens', 0))} "
               f"out {tui.format_tokens(stats.get('output_tokens', 0))}")
    if stats.get("tps"):
        out.append(f"  {theme.paint('speed'.ljust(14), 'accent')}{stats['tps']:.1f} t/s")
    out.append(f"  {theme.paint('transcript'.ljust(14), 'accent')}{stats.get('transcript') or '(none yet)'}")
    return _lines(*out)


@command("/tools", "list every tool the agent can call")
def cmd_tools(ctx: Context):
    theme = ctx.theme
    out = [theme.paint("Tools", "bold")]
    for tool in ctx.tools:
        function = tool["function"]
        description = " ".join((function.get("description") or "").split())
        out.append(
            f"  {theme.paint(function['name'].ljust(20), 'accent')}"
            f"{theme.paint(description[:96], 'dim')}"
        )
    return _lines(*out)


@command("/mcp", "show the MCP servers and their tools")
def cmd_mcp(ctx: Context):
    theme = ctx.theme
    if ctx.mcp is None:
        return theme.paint("MCP is not loaded", "dim")
    status = _mcp_status(ctx)
    if not status:
        return theme.paint("no MCP servers configured", "dim")
    out = [theme.paint("MCP servers", "bold")]
    for name, info in status.items():
        mark = theme.paint("connected", "success") if info.get("connected") else theme.paint("off", "warning")
        out.append(f"  {theme.paint(name.ljust(16), 'accent')}{mark}")
        if info.get("error"):
            out.append(theme.paint(f"      {info['error']}", "dim"))
        if info.get("tools"):
            out.append(theme.paint(f"      {info['tools']} tools", "dim"))
    return _lines(*out)


@command("/web", "show the web search and fetch providers")
def cmd_web(ctx: Context):
    theme = ctx.theme
    if ctx.web is None:
        return theme.paint("web tools are not loaded", "dim")
    status = _web_status(ctx)
    out = [theme.paint("Web providers", "bold")]
    for name, info in status.items():
        mark = theme.paint("configured", "success") if info.get("configured") else theme.paint("no API key", "warning")
        out.append(f"  {theme.paint(name.ljust(16), 'accent')}{mark}")
        if info.get("detail"):
            out.append(theme.paint(f"      {info['detail']}", "dim"))
    out.append("")
    out.append(theme.paint("The agent calls these as web_search and web_fetch.", "dim"))
    return _lines(*out)


@command("/browser", "check the headless browser")
def cmd_browser(ctx: Context):
    theme = ctx.theme
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            version = browser.version
            browser.close()
        return _lines(
            theme.paint("Browser", "bold"),
            f"  chromium {version}",
            theme.paint("  the agent drives it with the browser_* tools", "dim"),
        )
    except Exception as exc:
        return _lines(
            theme.paint("Browser", "bold"),
            f"  {theme.paint('not available', 'warning')}  {exc}",
        )


@command("/project", "show the workspace, or switch it: /project <path>")
def cmd_project(ctx: Context):
    theme = ctx.theme
    argument = (ctx.agent.current_argument or "").strip()
    if not argument:
        remembered = ""
        try:
            remembered = ctx.agent.read_last_project()
        except Exception:
            pass
        out = [theme.paint("Workspace", "bold"), f"  {ctx.ws.root}"]
        if remembered and remembered != str(ctx.ws.root):
            out.append(theme.paint(f"  last used: {remembered}", "dim"))
        out.append("")
        out.append(theme.paint("Switch with /project <path>.", "dim"))
        return _lines(*out)
    target = Path(argument)
    if not target.is_dir():
        return theme.paint(f"{target} is not a directory", "warning")
    ctx.agent.switch_workspace(target)
    ctx.ws = ctx.agent.ws
    return theme.paint(f"workspace is now {target}", "success")


@command("/diff", "show the git diff of the workspace")
def cmd_diff(ctx: Context):
    try:
        return ctx.agent.git_diff()
    except Exception as exc:
        return ctx.theme.paint(f"git diff failed: {exc}", "warning")


@command("/cost", "show the token counters")
def cmd_cost(ctx: Context):
    theme = ctx.theme
    stats = ctx.agent.session_stats()
    window = ctx.cfg.ctx
    prompt = stats.get("prompt_tokens", 0)
    return _lines(
        theme.paint("Tokens", "bold"),
        f"  prompt in   {tui.format_tokens(prompt)}",
        f"  output out  {tui.format_tokens(stats.get('output_tokens', 0))}",
        f"  context     {100.0 * prompt / max(1, window):.0f}% of {tui.format_tokens(window)}",
        theme.paint("  a local model costs nothing per token", "dim"),
    )


@command("/transcript", "show where the session transcript is written")
def cmd_transcript(ctx: Context):
    stats = ctx.agent.session_stats()
    path = stats.get("transcript")
    if not path:
        return ctx.theme.paint("no transcript yet for this session", "dim")
    return _lines(ctx.theme.paint("Transcript", "bold"), f"  {path}")


@command("/about", "what this agent is and how it is wired")
def cmd_about(ctx: Context):
    theme = ctx.theme
    return _lines(
        theme.paint("SPARK CODE 2.6", "bold"),
        "  A local coding agent for Spark X2.6 on llama.cpp.",
        "",
        f"  model      {ctx.cfg.model}",
        f"  engine     {ctx.cfg.engine}",
        f"  workspace  {ctx.ws.root}",
        f"  tools      {len(ctx.tools)}",
        f"  skills     {len(ctx.skills)}",
        f"  reasoning  {ctx.cfg.think}",
        "",
        theme.paint("  The chat template, the sampling values and the tool tags all come", "dim"),
        theme.paint("  from Spark's own model card, not from llama.cpp defaults.", "dim"),
    )


def dispatch(ctx: Context, line: str) -> str | None:
    """Run a slash command. Returns the text to print, or None."""
    if not line.startswith("/"):
        return None
    parts = line.split(None, 1)
    name = parts[0].lower()
    argument = parts[1].strip() if len(parts) > 1 else ""
    entry = REGISTRY.get(name)
    if entry is None:
        # An unknown slash line is not a command, so it goes to the model
        # as an instruction like any other text.
        return None
    ctx.agent.current_argument = argument
    try:
        result = entry["func"](ctx)
    except Exception as exc:  # noqa: BLE001
        return f"error in {name}: {type(exc).__name__}: {exc}"
    finally:
        ctx.agent.current_argument = ""
    return result


def command_names() -> list:
    return sorted(REGISTRY)
