#!/usr/bin/env python3
"""
Self-test for the harness internals that do not need the model.

Covers the three places where a silent bug would corrupt an agent run:
the thinking splitter (the completion shape depends on the engine), the
Spark tool-call parser, and the workspace path jail.

Run:  python selftest.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from spark_agent import (  # noqa: E402
    SparkTemplate,
    Workspace,
    extract_cli_completion,
    load_project_instructions,
    parse_tool_calls,
    split_thinking,
    tool_edit_file,
    tool_git_diff,
    tool_git_log,
    tool_git_status,
    tool_grep,
    tool_read_file,
    tool_write_file,
    execute_tool,
)

FAILURES = []


def check(label: str, got, want):
    if got == want:
        print(f"[PASS] {label}")
    else:
        print(f"[FAIL] {label}\n  got : {got!r}\n  want: {want!r}")
        FAILURES.append(label)


def test_split_thinking():
    # Self-contained block.
    check(
        "split_thinking: full block",
        split_thinking("<think>plan</think>answer"),
        ("plan", "answer"),
    )
    # The prompt already opened the block, so only the closing tag arrives.
    check(
        "split_thinking: closing tag only",
        split_thinking("plan it out</think>answer"),
        ("plan it out", "answer"),
    )
    # No thinking at all.
    check("split_thinking: none", split_thinking("just an answer"), ("", "just an answer"))
    # Zero-width space inside the tags must not defeat the split.
    check(
        "split_thinking: zero width",
        split_thinking("<think>a</think>b"),
        ("a", "b"),
    )
    # Empty reasoning block.
    check("split_thinking: empty block", split_thinking("<think></think>answer"), ("", "answer"))


def test_parse_tool_calls():
    calls = parse_tool_calls(
        "<tool_call>read_file<arg_key>path</arg_key><arg_value>src/a.rs</arg_value></tool_call>"
    )
    check("parse_tool_calls: one call", len(calls), 1)
    check("parse_tool_calls: name", calls[0]["name"], "read_file")
    check("parse_tool_calls: args", calls[0]["arguments"], {"path": "src/a.rs"})

    two = parse_tool_calls(
        "<tool_call>list_dir<arg_key>path</arg_key><arg_value>.</arg_value></tool_call>"
        "<tool_call>grep<arg_key>pattern</arg_key><arg_value>x</arg_value>"
        "<arg_key>glob</arg_key><arg_value>*.py</arg_value></tool_call>"
    )
    check("parse_tool_calls: two calls", len(two), 2)
    check("parse_tool_calls: second name", two[1]["name"], "grep")
    check("parse_tool_calls: second args", two[1]["arguments"], {"pattern": "x", "glob": "*.py"})

    # A non-string argument arrives as JSON.
    typed = parse_tool_calls(
        "<tool_call>read_file<arg_key>path</arg_key><arg_value>a.txt</arg_value>"
        "<arg_key>start_line</arg_key><arg_value>12</arg_value></tool_call>"
    )
    check("parse_tool_calls: int arg", typed[0]["arguments"]["start_line"], 12)

    # Zero-width characters inside the tags.
    zw = parse_tool_calls(
        "<tool_call>read_file<arg_key>path</arg_key><arg_value>a.txt</arg_value>"
        "</tool_call>"
    )
    check("parse_tool_calls: zero width", zw[0]["name"], "read_file")

    # A tool call with no arguments.
    none = parse_tool_calls("<tool_call>list_dir</tool_call>")
    check("parse_tool_calls: no args", none[0]["arguments"], {})

    # Plain prose must not be mistaken for a tool call.
    check("parse_tool_calls: prose", parse_tool_calls("I will list the files."), [])


def test_workspace_jail():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        ws = Workspace(root)

        tool_write_file(ws, "a.txt", "hello\nworld\n")
        check("read_file: line count", "2 lines" in tool_read_file(ws, "a.txt"), True)

        tool_edit_file(ws, "a.txt", "world", "there")
        check("edit_file: applied", "there" in tool_read_file(ws, "a.txt"), True)

        hits = tool_grep(ws, "there", ".")
        check("grep: found", "a.txt:2" in hits, True)

        # Escaping the workspace must be refused.
        try:
            ws.resolve("..\\..\\Windows\\System32\\config\\SAM")
            print("[FAIL] workspace jail: escape allowed")
            FAILURES.append("workspace jail: escape allowed")
        except Exception:
            print("[PASS] workspace jail: escape refused")

        # An absolute path outside the root must be refused too.
        try:
            ws.resolve("C:\\Windows\\System32\\config\\SAM")
            print("[FAIL] workspace jail: absolute escape allowed")
            FAILURES.append("workspace jail: absolute escape allowed")
        except Exception:
            print("[PASS] workspace jail: absolute escape refused")


def test_template():
    tokens = {
        "bos": "<BOS>",
        "eos": "<EOS>",
        "think_open": "<think>",
        "think_close": "</think>",
    }
    template = SparkTemplate(tokens)
    out = template.render(
        [{"role": "user", "content": "hi"}],
        tools=[{"type": "function", "function": {"name": "f", "description": "d"}}],
        enable_thinking=True,
        reasoning_effort="low",
    )
    check("template: bos", out.startswith("<BOS><|System|>"), True)
    check("template: tool block", "<tools>\n{\"name\":\"f\",\"description\":\"d\"}\n</tools>" in out, True)
    check("template: user turn", "<BOS><|User|>hi<EOS>" in out, True)
    check("template: generation prompt", out.endswith("<BOS><|Bot|><think>"), True)

    # The template only accepts high and low.
    try:
        template.render([{"role": "user", "content": "x"}], reasoning_effort="medium")
        print("[FAIL] template: medium effort accepted")
        FAILURES.append("template: medium effort accepted")
    except ValueError:
        print("[PASS] template: medium effort refused")


def test_extract_cli_completion():
    prompt = "<|Bot|><think>"
    raw = (
        "\nLoading model... \n\n"
        "build      : b11873-b083c384f\nmodel      : x.gguf\nftype      : Q8_0\n\n"
        "available commands:\n  /exit or Ctrl+C     stop or exit\n\n\n"
        "> " + prompt + "\n\n[Start thinking]\n\n"
        "I will read the file.\n[End thinking]\n\nHere is the answer.\n\n"
        "[ Prompt: 37.5 t/s | Generation: 6.4 t/s ]\n\n> \n\nExiting...\n"
    )
    got = extract_cli_completion(raw, prompt)
    check("extract_cli_completion: strips banner", got.startswith("I will read the file."), True)
    check("extract_cli_completion: keeps answer", got.endswith("Here is the answer."), True)
    check("extract_cli_completion: no stats", "Prompt:" not in got, True)
    check("extract_cli_completion: no think markers", "Start thinking" not in got, True)

    # A prompt that was not echoed still yields the tail.
    raw_no_echo = "some output\n\n[ Prompt: 1 t/s | Generation: 2 t/s ]\n\n> \n"
    check(
        "extract_cli_completion: no echo",
        extract_cli_completion(raw_no_echo, "<|Bot|>") == "some output",
        True,
    )


def test_coding_environment():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        ws = Workspace(root)

        # An edit must report the diff, not just "edited".
        tool_write_file(ws, "a.txt", "one\ntwo\n")
        result = tool_edit_file(ws, "a.txt", "two", "three")
        check("edit_file: returns a diff", "--- a/a.txt" in result and "+three" in result, True)

        # Writing over an existing file also reports the diff.
        result2 = tool_write_file(ws, "a.txt", "one\nfour\n")
        check("write_file: returns a diff", "+four" in result2 and "-two" not in result2, True)

        # Project instructions are picked up from the workspace root.
        check("instructions: none without a file", load_project_instructions(root), "")
        (root / "AGENTS.md").write_text("- build with cargo\n", encoding="utf-8")
        found = load_project_instructions(root)
        check("instructions: AGENTS.md loaded", "build with cargo" in found, True)
        check("instructions: labelled", "AGENTS.md" in found, True)

        # A project without instructions still works.
        other = Path(tmp) / "plain"
        other.mkdir()
        check("instructions: absent in a plain project", load_project_instructions(other), "")


def test_git_tools():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        ws = Workspace(root)

        # Outside a repository the tools must say so instead of failing oddly.
        # execute_tool is the path the agent actually uses.
        message = execute_tool("git_status", {}, ws, [])
        check("git_status: outside a repo", "not a git repository" in message, True)
        check("git_status: error is reported, not raised", message.startswith("error:"), True)

        if shutil.which("git") is None:
            print("[SKIP] git tools: git is not installed")
            return

        (root / "a.txt").write_text("hello\n", encoding="utf-8")
        env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e"}
        env.update(os.environ)
        for args in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "first"]):
            subprocess.run(["git", *args], cwd=str(root), capture_output=True, env=env)

        status = tool_git_status(ws)
        check("git_status: shows the branch", "branch" in status or "master" in status or "main" in status, True)
        # A clean tree prints only the branch line and no file entries.
        check(
            "git_status: clean tree",
            all(line.startswith("##") for line in status.splitlines() if line.strip()),
            True,
        )

        log = tool_git_log(ws, 5)
        check("git_log: shows the commit", "first" in log, True)

        (root / "a.txt").write_text("changed\n", encoding="utf-8")
        diff = tool_git_diff(ws)
        check("git_diff: shows the change", "-hello" in diff and "+changed" in diff, True)

        single = tool_git_diff(ws, "a.txt")
        check("git_diff: single path", "+changed" in single, True)


def test_cli_timings():
    """The counters that feed the footer have to come out of llama-cli's log."""
    from spark_agent import parse_cli_timings

    footer = (
        "llama_print_timings:        load time =    1234.56 ms\n"
        "llama_print_timings:     sample time =    5678.90 ms /   100 runs   "
        "(   56.78 ms per token,    17.61 tokens per second)\n"
        "llama_print_timings:    prompt eval time =    1234.56 ms /   250 tokens   "
        "(    4.93 ms per token,   202.51 tokens per second)\n"
    )
    stats = parse_cli_timings(footer)
    check("timings: output tokens", stats.get("output_tokens"), 100)
    check("timings: prompt tokens", stats.get("prompt_tokens"), 250)
    check("timings: tokens per second", round(stats.get("tps", 0), 2), 17.61)
    check("timings: no footer", parse_cli_timings("nothing here"), {})


def test_tui():
    """The footer, the banner and the input box."""
    import tui

    check("tui: strip_ansi", tui.strip_ansi("\x1b[38;2;1;2;3mhello\x1b[0m"), "hello")
    check("tui: display_width ignores colour",
          tui.display_width("\x1b[38;2;1;2;3mhello\x1b[0m"), 5)

    theme = tui.Theme(enabled=False)
    box = tui.PromptBox(theme, 90)
    box.text = "hello world"
    box.cursor = len(box.text)
    lines = box.paint()
    check("tui: box rows are equal width",
          len({tui.display_width(line) for line in lines}), 1)
    check("tui: box is 90 wide", tui.display_width(lines[0]), 90)
    check("tui: the text is inside the box",
          any("hello world" in line for line in lines), True)

    banner = tui.banner(100, theme)
    check("tui: banner has the title", "SPARK" not in banner and "\u2588" in banner, True)
    check("tui: banner is boxed", banner.splitlines()[0].startswith("\u256d"), True)
    check("tui: banner rows are equal width",
          len({tui.display_width(line) for line in banner.splitlines()}), 1)

    stats = {"tps": 7.5, "prompt_tokens": 100, "output_tokens": 20,
             "ctx_tokens": 1000, "ctx_window": 16000, "tools": 20, "turns": 2}
    line = tui.status_line(80, stats, "X2.6", "high", theme)
    check("tui: status line is 80 wide", tui.display_width(line), 80)
    check("tui: status line has the model", "X2.6" in line, True)
    check("tui: status line has the reasoning level", "high" in line, True)

    # Editing
    box = tui.PromptBox(theme, 90)
    box.insert("a")
    box.insert("b")
    box.move(-1)
    box.insert("X")
    check("tui: insert at the cursor", box.text, "aXb")
    box.backspace()
    check("tui: backspace", box.text, "ab")
    box.move(-1)
    box.delete()
    check("tui: delete", box.text, "b")
    box.text = "one two"
    box.cursor = 7
    box.kill_word()
    check("tui: kill word", box.text, "one ")


def test_commands():
    """Every command the user asked for has to exist and dispatch."""
    import commands

    wanted = ["/skills", "/help", "/providers", "/model", "/reasoning", "/doctor",
              "/clear", "/compact", "/autocompact", "/resume", "/exit"]
    missing = [name for name in wanted if name not in commands.REGISTRY]
    check("commands: all the requested commands exist", missing, [])

    class FakeAgent:
        current_argument = ""
        quit_requested = False
        autocompact = True
        autocompact_threshold = 70

        def reset(self):
            self.reset_called = True

    class FakeContext:
        pass

    import tui

    ctx = FakeContext()
    ctx.agent = FakeAgent()
    ctx.theme = tui.Theme(enabled=False)
    ctx.cfg = type("Cfg", (), {"model": "x.gguf", "think": "high", "ctx": 100})()
    ctx.engine = None
    ctx.ws = type("Ws", (), {"root": Path(".")})()
    ctx.skills = []
    ctx.tools = []
    ctx.mcp = None
    ctx.web = None

    check("commands: a plain line is not a command", commands.dispatch(ctx, "hello"), None)
    check("commands: an unknown slash line falls through to the agent",
          commands.dispatch(ctx, "/nosuchcommand"), None)
    help_text = commands.dispatch(ctx, "/help")
    check("commands: /help returns text", bool(help_text), True)
    check("commands: /help lists /doctor", "/doctor" in help_text, True)


def test_web_tools():
    """The web and browser tools build, and they fail safely."""
    import webtools

    tools = webtools.build_web_tools()
    names = [tool["function"]["name"] for tool in tools]
    check("web: the tool list is not empty", len(names) > 0, True)
    check("web: web_search exists", "web_search" in names, True)
    check("web: web_fetch exists", "web_fetch" in names, True)
    check("web: the browser tools exist",
          "browser_navigate" in names and "browser_click" in names, True)
    check("web: every tool has a schema",
          all(tool["function"].get("parameters", {}).get("type") == "object" for tool in tools),
          True)

    result = webtools.execute_web_tool("web_search", {"query": "llama.cpp", "count": 2})
    check("web: web_search returns text", result.startswith("error:") or len(result) > 20, True)
    bad = webtools.execute_web_tool("no_such_tool", {})
    check("web: an unknown tool returns an error, not an exception",
          bad.startswith("error:"), True)


def test_mcp_bridge():
    """The MCP bridge connects and reports its servers."""
    import mcpclient

    bridge = mcpclient.McpBridge()
    try:
        bridge.connect_all()
        status = bridge.server_status()
        check("mcp: the server list is not empty", len(status) > 0, True)
        tools = bridge.list_tools()
        check("mcp: every tool is named mcp_*",
              all(t["function"]["name"].startswith("mcp_") for t in tools), True)
        check("mcp: every tool has a schema",
              all(t["function"].get("parameters", {}).get("type") == "object" for t in tools),
              True)
        missing = bridge.call_tool("mcp_nope_nope", {})
        check("mcp: an unknown tool returns an error, not an exception",
              missing.startswith("error:"), True)
    finally:
        bridge.close()


def test_sessions():
    """The session file that /resume reads has to round-trip."""
    from spark_agent import SparkAgent

    class Cfg:
        transcript_dir = str(Path(tempfile.gettempdir()) / "spark-selftest-sessions")
        model = "x.gguf"
        think = "high"
        ctx = 100
        max_steps = 1

    agent = SparkAgent.__new__(SparkAgent)
    agent.cfg = Cfg()
    agent.session_id = "selftest"
    agent.messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "do the thing"},
    ]
    agent.ws = type("Ws", (), {"root": Path(".")})()
    agent.transcript = None
    agent.turns = 0
    agent.stats = {}
    agent.instructions = ""
    agent.skills = []
    agent.current_argument = ""
    agent.quit_requested = False
    agent.autocompact = True
    agent.autocompact_threshold = 70

    path = agent.save_session()
    check("sessions: the session file is written", bool(path) and Path(path).is_file(), True)
    sessions = agent.list_sessions()
    check("sessions: the session is listed", any(s["id"] == "selftest" for s in sessions), True)

    agent.messages = []
    check("sessions: the session loads back", agent.load_session(path), True)
    check("sessions: the history is restored", len(agent.messages), 2)
    shutil.rmtree(Cfg.transcript_dir, ignore_errors=True)


def test_winproc():
    """The helpers that keep the agent's llama-server from outliving it."""
    import winproc

    check("winproc: this process is alive", winproc.process_alive(os.getpid()), True)
    check("winproc: a dead pid is not alive", winproc.process_alive(999999), False)

    pid = winproc.port_pid(8082)
    check("winproc: the port lookup answers", isinstance(pid, int), True)

    # A port with nothing of ours on it must not be touched.
    note = winproc.kill_server_on_port(8082, str(Path(__file__).resolve().parent.parent))
    check("winproc: killing on a free port does nothing", note, "")

    # A job with KILL_ON_JOB_CLOSE: the handle has to survive.
    import subprocess
    import sys as _sys

    child = subprocess.Popen([_sys.executable, "-c", "import time; time.sleep(30)"])
    job = winproc.kill_on_close(child)
    check("winproc: the kill-on-close job is set up", job is not None, True)
    child.kill()
    try:
        child.wait(timeout=5)
    except Exception:
        pass
    if job is not None:
        import ctypes

        ctypes.windll.kernel32.CloseHandle(job)


def main() -> int:
    test_split_thinking()
    test_parse_tool_calls()
    test_extract_cli_completion()
    test_workspace_jail()
    test_template()
    test_coding_environment()
    test_git_tools()
    test_cli_timings()
    test_tui()
    test_commands()
    test_web_tools()
    test_mcp_bridge()
    test_sessions()
    test_winproc()
    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} check(s): {', '.join(FAILURES)}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
