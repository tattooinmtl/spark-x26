#!/usr/bin/env python3
"""
Spark Agent - a local coding harness built on llama.cpp + Spark X2.6.

The model is driven through its own chat template, so the exact wire format
Spark was trained on is preserved: <|User|>/<|Bot|>/<|Tool|> turns, a
<think> block, and <tool_call>name<arg_key>k</arg_key><arg_value>v</arg_value></tool_call>.

Two engines:
  cli     - renders the prompt in Python and pipes it to llama-cli (one
            process per turn; the model is reloaded each turn, ~6 s warm).
  server  - renders the prompt in Python and posts it to llama-server over
            the OpenAI API (model stays resident between turns).

Both engines share the tool registry, the path jail and the skills system.
Skills are progressively disclosed: the agent always sees only the id and the
one-line description of each skill, and pulls the body with read_skill().

Usage:
    python spark_agent.py [server|cli] [--workspace DIR] [--think high|low|none]
                           [--max-steps N] [--render] [--task TEXT]

Terminal UI:
    The interactive session draws a PI.DEV style footer (small dim stats on
    the left, provider/model/reasoning on the right) under a Claude Code
    style input box, and a boxed SPARK CODE 2.6 title on start.
"""
from __future__ import annotations

import argparse
import difflib
import http.client
import json
import os
import queue
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import commands as slash_commands
import settings as settings_store
import tui
import winproc

DEFAULT_ROOT = HERE.parent
DEFAULT_SKILLS = HERE / "skills"
LAST_PROJECT_FILE = DEFAULT_ROOT / "last-project.txt"
SESSION_DIR = DEFAULT_ROOT / "logs" / "sessions"
CONFIG_FILE = DEFAULT_ROOT / "config.json"

# Where the model and the engine live when the app is installed somewhere
# else than C:\models. The installer writes config.json next to the code,
# and these two functions fall back to a sensible search.
MODEL_GLOB = "Spark-X2.6-4B-Q8_0.gguf"
LLAMA_BUILD = "llama-b11873-cpu"
KNOWN_MODEL_DIRS = (Path(r"C:\models"),)


def load_config() -> dict:
    """The install's own settings, written by the installer."""
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def find_model() -> Path:
    """The model to run, wherever this machine keeps it."""
    env = os.environ.get("SPARK_MODEL")
    if env and Path(env).is_file():
        return Path(env)
    config = load_config()
    if config.get("model") and Path(config["model"]).is_file():
        return Path(config["model"])
    local = DEFAULT_ROOT / "models" / MODEL_GLOB
    if local.is_file():
        return local
    for folder in KNOWN_MODEL_DIRS:
        candidate = folder / MODEL_GLOB
        if candidate.is_file():
            return candidate
    for folder in KNOWN_MODEL_DIRS:
        if folder.is_dir():
            matches = sorted(folder.glob("Spark-X2.6*.gguf"))
            if matches:
                return matches[0]
    return local


def find_llama_dir() -> Path:
    """The llama.cpp build to run, wherever this machine keeps it."""
    env = os.environ.get("SPARK_LLAMA_DIR")
    if env and Path(env).is_dir():
        return Path(env)
    config = load_config()
    if config.get("llama_dir") and Path(config["llama_dir"]).is_dir():
        return Path(config["llama_dir"])
    root = DEFAULT_ROOT / "llama"
    preferred = root / LLAMA_BUILD
    if preferred.is_dir():
        return preferred
    if root.is_dir():
        for child in sorted(root.iterdir()):
            if child.is_dir() and (child / "llama-server.exe").is_file():
                return child
    for folder in KNOWN_MODEL_DIRS:
        candidate = folder / "spark26" / "llama" / LLAMA_BUILD
        if candidate.is_dir():
            return candidate
    return preferred


def load_web_tools():
    """The web and browser tools, if the module is present.

    The module is optional: without it the agent still works, it just has
    no way out to the internet.
    """
    try:
        import webtools

        return webtools
    except Exception as exc:  # pragma: no cover - optional dependency
        print(f"[warn] web tools unavailable: {exc}")
        return None


def use_utf8_console():
    """Make the console able to print whatever a tool returns.

    The Windows console defaults to cp1252, and a search result with an
    emoji in the title (Tavily returns those) would raise
    UnicodeEncodeError the moment the agent prints it.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def load_mcp():
    """The MCP bridge, if the SDK is installed."""
    try:
        import mcpclient

        return mcpclient
    except Exception as exc:  # pragma: no cover - optional dependency
        print(f"[warn] MCP unavailable: {exc}")
        return None


def read_last_project() -> str:
    """The project the launchers used last, so neither has to be told twice."""
    try:
        text = LAST_PROJECT_FILE.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""
    return text.strip('"')


def remember_project(path: Path):
    try:
        LAST_PROJECT_FILE.write_text(f'"{path}"\n', encoding="utf-8")
    except OSError:
        pass

# --------------------------------------------------------------------------
# Chat template constants
#
# The turn markers contain U+FF5C and U+2581, which are easy to mangle when
# typed by hand. They are read from the GGUF instead of being hardcoded.
# --------------------------------------------------------------------------
FALLBACK_TOKENS = {
    "bos": "<\uff5cstart\u2581of\u2581sentence\uff5c>",
    "eos": "<\uff5cend\u2581of\u2581sentence\uff5c>",
    "think_open": "<think>",
    "think_close": "</think>",
}

ZERO_WIDTH = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff"), None)


def strip_invisible(text: str) -> str:
    """Remove zero-width characters.

    llama.cpp marks some internal markers with U+200B and the model
    occasionally emits the tool tags with one wedged inside them, so every
    tag comparison is done on a cleaned copy.
    """
    return text.translate(ZERO_WIDTH)


def estimate_tokens(text: str) -> int:
    """A rough token count for when no tokenizer is available.

    Spark X2.6 has a 131072 entry vocabulary, so English prose lands
    around four characters per token. The real number comes from the
    server's /tokenize endpoint whenever the server engine is running.
    """
    if not text:
        return 0
    return max(1, len(text) // 4)


def strip_bom(text: str) -> str:
    """Drop a leading byte order mark.

    A PowerShell pipe hands over UTF-8 with a BOM, so the first line of a
    piped task would otherwise arrive as "\\ufeff/help" and miss the
    command dispatch.
    """
    return text.lstrip("\ufeff")


def load_special_tokens(model_path: Path) -> dict:
    """Pull the exact turn markers out of the GGUF vocabulary."""
    tokens = dict(FALLBACK_TOKENS)
    try:
        sys.path.insert(0, str(HERE))
        from gguf_info import read_metadata

        _, _, meta = read_metadata(str(model_path))
        vocab = meta.get("tokenizer.ggml.tokens") or []
        pairs = [
            ("bos", "tokenizer.ggml.bos_token_id"),
            ("eos", "tokenizer.ggml.eos_token_id"),
        ]
        for key, id_key in pairs:
            idx = meta.get(id_key)
            if isinstance(idx, int) and 0 <= idx < len(vocab):
                tokens[key] = vocab[idx]
        for i, name in ((3, "think_open"), (4, "think_close")):
            if i < len(vocab):
                tokens[name] = vocab[i]
    except Exception as exc:  # pragma: no cover - metadata is optional
        print(f"[warn] could not read GGUF tokens ({exc}); using defaults")
    return tokens


# --------------------------------------------------------------------------
# Template rendering - a faithful port of Spark's chat_template.jinja
# --------------------------------------------------------------------------
class SparkTemplate:
    def __init__(self, tokens: dict):
        self.bos = tokens["bos"]
        self.eos = tokens["eos"]

    def render_content(self, content) -> str:
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            out = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    out.append(block.get("text", ""))
                else:
                    raise ValueError(f"unsupported content block: {block!r}")
            return "".join(out)
        raise ValueError(f"content must be a string or list of text blocks: {content!r}")

    @staticmethod
    def _normalize_call(call: dict) -> tuple:
        """Accept both the flat internal shape and the OpenAI function shape.

        The template walks tool_call.function.name / .arguments, and raises if
        the arguments are not a mapping, so a JSON string has to be decoded.
        """
        function = call.get("function") or {}
        name = call.get("name") or function.get("name") or ""
        arguments = call.get("arguments")
        if arguments is None:
            arguments = function.get("arguments")
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                arguments = {"value": arguments}
        if not isinstance(arguments, dict):
            arguments = {}
        return name, arguments

    def render(
        self,
        messages: list,
        tools: list | None = None,
        enable_thinking: bool = True,
        reasoning_effort: str = "high",
        add_generation_prompt: bool = True,
    ) -> str:
        if not messages:
            raise ValueError("No messages provided.")
        if enable_thinking and reasoning_effort not in ("high", "low"):
            raise ValueError(
                f"Unexpected reasoning effort {reasoning_effort}. "
                "Supported types are high (default) and low."
            )

        reasoning_instructions = ""
        if enable_thinking:
            reasoning_instructions = (
                "\n`thinking_effort` guides on how much to think in your thinking "
                "channel (not including the response channel), supported values "
                f"include `low` and `high`.\nNow the system is invoked with "
                f"`thinking_effort={reasoning_effort}`."
            )

        default_system = "you are a helpful assistant." + reasoning_instructions

        initial_system = ""
        if messages[0].get("role") == "system":
            initial_system = self.render_content(messages[0].get("content"))

        out = [self.bos + "<|System|>\n" + default_system]

        if tools:
            out.append("\n\n## Tools\nYou have access to the following functions:\n<tools>")
            for tool in tools:
                out.append("\n" + json.dumps(tool["function"], separators=(",", ":"), ensure_ascii=False))
            out.append("\n</tools>")

        if initial_system:
            out.append("\n\n" + initial_system)

        out.append(self.eos)

        for index, message in enumerate(messages):
            role = message.get("role")
            content = message.get("content")

            if role == "system":
                if index != 0:
                    out.append(
                        self.bos + "<|System|>\n" + self.render_content(content) + self.eos
                    )
            elif role == "user":
                out.append(self.bos + "<|User|>" + self.render_content(content) + self.eos)
            elif role == "assistant" and isinstance(message.get("raw"), str):
                # The exact text the model generated, after the exact
                # generation prompt it was given. Re-rendering it from the
                # parsed parts would change whitespace and argument
                # formatting, and the server would have to recompute the
                # whole prompt (this model's sliding-window layers cannot
                # roll back), so it goes back byte for byte.
                out.append(self.bos + "<|Bot|>" + message.get("raw_prefix", "") + message["raw"] + self.eos)
            elif role == "assistant":
                out.append(self.bos + "<|Bot|>")
                reasoning = message.get("reasoning_content")
                if reasoning:
                    out.append("<think>" + reasoning + "</think>")
                else:
                    out.append("</think>")
                body = self.render_content(content)
                if body:
                    out.append(body)
                for call in message.get("tool_calls") or []:
                    name, arguments = self._normalize_call(call)
                    out.append("<tool_call>" + name)
                    for key, value in arguments.items():
                        if isinstance(value, str):
                            rendered = value
                        else:
                            rendered = json.dumps(value, separators=(", ", ": "), ensure_ascii=False)
                        out.append(f"<arg_key>{key}</arg_key><arg_value>{rendered}</arg_value>")
                    out.append("</tool_call>")
                out.append(self.eos)
            elif role == "tool":
                prev_is_tool = index > 0 and messages[index - 1].get("role") == "tool"
                next_is_tool = index + 1 < len(messages) and messages[index + 1].get("role") == "tool"
                if not prev_is_tool:
                    out.append(self.bos + "<|Tool|>")
                out.append("<tool_response>" + self.render_content(content) + "</tool_response>")
                if not next_is_tool:
                    out.append(self.eos)
            else:
                raise ValueError(f"Unsupported message role: {role}")

        if add_generation_prompt:
            out.append(self.bos + "<|Bot|>")
            out.append("<think>" if enable_thinking else "</think>")

        return "".join(out)


# --------------------------------------------------------------------------
# Tool registry
# --------------------------------------------------------------------------
MAX_READ_LINES = 400
MAX_OUTPUT_CHARS = 6000
# About 2,000 tokens: one read_file result may not crowd out the rest of a
# 32K context. The model is told how to read on.
MAX_READ_CHARS = 8000
TEXT_SUFFIXES = {".txt", ".md", ".py", ".rs", ".js", ".ts", ".tsx", ".jsx", ".json",
                 ".toml", ".yaml", ".yml", ".ini", ".cfg", ".bat", ".ps1", ".sh",
                 ".c", ".h", ".cpp", ".hpp", ".java", ".go", ".rb", ".php", ".cs",
                 ".html", ".css", ".sql", ".xml", ".env", ".gitignore"}


class ToolError(Exception):
    pass


class GenerationCancelled(KeyboardInterrupt):
    """The user pressed Esc while the model was generating."""


class Workspace:
    """A path jail: every file operation is confined to one root."""

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def resolve(self, raw: str) -> Path:
        if not raw or not raw.strip():
            raise ToolError("path is empty")
        candidate = Path(raw.strip().strip('"'))
        if not candidate.is_absolute():
            candidate = self.root / candidate
        resolved = Path(os.path.normpath(str(candidate)))
        try:
            resolved.relative_to(self.root)
        except ValueError:
            raise ToolError(
                f"path {resolved} is outside the workspace ({self.root})"
            )
        return resolved


def tool_read_file(ws: Workspace, path: str, start_line: int = 1, end_line: int = 0) -> str:
    target = ws.resolve(path)
    if not target.exists():
        raise ToolError(f"no such file: {path}")
    if target.is_dir():
        raise ToolError(f"{path} is a directory; use list_dir")
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ToolError(f"cannot read {path}: {exc}")

    lines = text.splitlines()
    start = max(1, int(start_line or 1))
    end = int(end_line or 0) or len(lines)
    end = min(end, len(lines))
    if start > len(lines):
        return f"(file has {len(lines)} lines; start_line {start} is past the end)"
    chunk = lines[start - 1:end]
    note = ""
    if len(chunk) > MAX_READ_LINES:
        chunk = chunk[:MAX_READ_LINES]
    rows = []
    used = 0
    for i, line in enumerate(chunk):
        row = f"{start + i:>5}| {line}"
        if rows and used + len(row) + 1 > MAX_READ_CHARS:
            break
        rows.append(row)
        used += len(row) + 1
    shown_end = start + len(rows) - 1
    if shown_end < min(end, len(lines)):
        note = (f"\n... (showing lines {start}-{shown_end} of {len(lines)}; "
                f"call read_file with start_line={shown_end + 1} to continue)")
    body = "\n".join(rows)
    return f"{path} ({len(lines)} lines)\n{body}{note}"


def _diff_lines(before: str, after: str, path: str) -> str:
    """Unified diff of a change, so the agent sees what it actually did."""
    diff = difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile=f"a/{path}",
        tofile=f"b/{path}",
        n=3,
    )
    text = "".join(diff)
    if len(text) > 4000:
        text = text[:4000] + "\n... (diff truncated)\n"
    return text


def tool_write_file(ws: Workspace, path: str, content: str) -> str:
    target = ws.resolve(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    existed = target.exists()
    before = target.read_text(encoding="utf-8", errors="replace") if existed else ""
    target.write_text(content, encoding="utf-8")
    verb = "updated" if existed else "created"
    summary = f"{verb} {path} ({len(content)} chars, {len(content.splitlines())} lines)"
    diff = _diff_lines(before, content, path)
    return f"{summary}\n{diff}" if diff else summary


def tool_edit_file(ws: Workspace, path: str, old_string: str, new_string: str) -> str:
    target = ws.resolve(path)
    if not target.exists():
        raise ToolError(f"no such file: {path}")
    text = target.read_text(encoding="utf-8", errors="replace")
    if old_string not in text:
        raise ToolError(
            "old_string not found in the file. Read the file again and copy the "
            "exact text, including indentation."
        )
    count = text.count(old_string)
    if count > 1:
        raise ToolError(
            f"old_string appears {count} times in {path}; include more surrounding "
            "context so it is unique"
        )
    updated = text.replace(old_string, new_string, 1)
    target.write_text(updated, encoding="utf-8")
    summary = f"edited {path} (1 replacement, {len(new_string)} chars)"
    diff = _diff_lines(text, updated, path)
    return f"{summary}\n{diff}" if diff else summary


def tool_list_dir(ws: Workspace, path: str = ".") -> str:
    target = ws.resolve(path)
    if not target.exists():
        raise ToolError(f"no such directory: {path}")
    if not target.is_dir():
        raise ToolError(f"{path} is not a directory")
    entries = []
    for child in sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
        if child.name in (".git", "node_modules", "__pycache__"):
            continue
        if child.is_dir():
            entries.append(f"  {child.name}/")
        else:
            try:
                size = child.stat().st_size
            except OSError:
                size = 0
            entries.append(f"  {child.name}  ({size} bytes)")
    if not entries:
        return f"{path} is empty"
    return f"{path}/\n" + "\n".join(entries[:200])


def tool_grep(ws: Workspace, pattern: str, path: str = ".", glob: str = "*") -> str:
    try:
        regex = re.compile(pattern)
    except re.error as exc:
        raise ToolError(f"bad regex {pattern!r}: {exc}")
    root = ws.resolve(path)
    if root.is_file():
        files = [root]
    else:
        files = [p for p in root.rglob(glob) if p.is_file() and p.suffix.lower() in TEXT_SUFFIXES]
    hits = []
    for file in files[:400]:
        try:
            for number, line in enumerate(file.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if regex.search(line):
                    try:
                        rel = file.relative_to(ws.root)
                    except ValueError:
                        rel = file
                    hits.append(f"{rel}:{number}: {line.strip()[:200]}")
                    if len(hits) >= 60:
                        break
        except OSError:
            continue
        if len(hits) >= 60:
            break
    if not hits:
        return f"no matches for {pattern!r} under {path}"
    return "\n".join(hits) + (f"\n... ({len(hits)} matches, truncated)" if len(hits) >= 60 else "")


def tool_run_command(ws: Workspace, command: str, timeout: int = 60) -> str:
    if not command or not command.strip():
        raise ToolError("command is empty")
    banned = ("rm -rf /", "format ", "shutdown", "del /f /q c:\\", "rd /s /q c:\\")
    lowered = command.lower()
    for pattern in banned:
        if pattern in lowered:
            raise ToolError(f"refusing to run a destructive command: {command}")
    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=str(ws.root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=int(timeout or 60),
        )
    except subprocess.TimeoutExpired:
        return f"command timed out after {timeout}s: {command}"
    output = (proc.stdout or "") + (("\n[stderr]\n" + proc.stderr) if proc.stderr.strip() else "")
    output = output.strip()
    if len(output) > MAX_OUTPUT_CHARS:
        # Keep both ends: commands print what they did first and the error
        # or the summary last.
        half = MAX_OUTPUT_CHARS // 2
        skipped = len(output) - 2 * half
        output = output[:half] + f"\n... ({skipped} characters skipped) ...\n" + output[-half:]
    return f"$ {command}\nexit code: {proc.returncode}\n{output}"


# --------------------------------------------------------------------------
# Git - read-only. The agent can look, it cannot commit.
# --------------------------------------------------------------------------
def _git(ws: Workspace, args: str, limit: int = 6000) -> str:
    try:
        proc = subprocess.run(
            f"git {args}",
            shell=True,
            cwd=str(ws.root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
    except FileNotFoundError:
        raise ToolError("git is not installed or not on PATH")
    except subprocess.TimeoutExpired:
        raise ToolError("git timed out")
    output = (proc.stdout or "") + (("\n[stderr]\n" + proc.stderr) if proc.stderr.strip() else "")
    if "not a git repository" in output:
        raise ToolError("the workspace is not a git repository")
    output = output.strip()
    if len(output) > limit:
        output = output[:limit] + f"\n... (truncated, {len(output)} chars total)"
    return output or "(no output)"


def tool_git_status(ws: Workspace) -> str:
    return _git(ws, "status --short --branch")


def tool_git_diff(ws: Workspace, path: str = "") -> str:
    target = ""
    if path and path.strip():
        target = ws.resolve(path)
        relative = target.relative_to(ws.root).as_posix()
        return _git(ws, f'diff HEAD -- "{relative}"')
    return _git(ws, "diff HEAD")


def tool_git_log(ws: Workspace, count: int = 10) -> str:
    try:
        number = max(1, min(int(count or 10), 100))
    except (TypeError, ValueError):
        number = 10
    return _git(ws, f"log --oneline -n {number}")


# --------------------------------------------------------------------------
# Skills - progressive disclosure
# --------------------------------------------------------------------------
def load_skills(skills_dir: Path) -> list:
    skills = []
    if not skills_dir.is_dir():
        return skills
    for entry in sorted(skills_dir.iterdir()):
        skill_file = entry / "SKILL.md" if entry.is_dir() else None
        if skill_file is None or not skill_file.is_file():
            continue
        text = skill_file.read_text(encoding="utf-8", errors="replace")
        name, description, body, slash = entry.name, "", text, ""
        if text.startswith("---"):
            parts = text.split("---", 2)
            if len(parts) >= 3:
                front = parts[1]
                body = parts[2].strip()
                for line in front.splitlines():
                    if line.lower().startswith("description:"):
                        description = line.split(":", 1)[1].strip()
                    elif line.lower().startswith("name:"):
                        name = line.split(":", 1)[1].strip()
                    elif line.lower().startswith("command:"):
                        slash = line.split(":", 1)[1].strip()
        skills.append({"id": entry.name, "name": name, "description": description, "body": body,
                       "command": slash})
    return skills


# --------------------------------------------------------------------------
# Tool-call parsing
# --------------------------------------------------------------------------
TOOL_CALL_RE = re.compile(r"<tool_call>(.*?)(?:</tool_call>|$)", re.DOTALL)
ARG_RE = re.compile(r"<arg_key>(.*?)</arg_key>\s*<arg_value>(.*?)</arg_value>", re.DOTALL)
THINK_RE = re.compile(r"<think>(.*?)</think>", re.DOTALL)


def split_thinking(text: str) -> tuple:
    """Return (reasoning, content) with the think block pulled out.

    Two shapes have to be handled. When the engine returns a full reply the
    block is self-contained: "<think>...</think>answer". When the prompt
    already opened the block (the generation prompt ends with "<think>", as
    Spark's template does), the completion only carries the closing tag:
    "reasoning...</think>answer". Splitting on the closing tag alone would
    leave a stray tag in the answer and corrupt the next history turn.
    """
    clean = strip_invisible(text)
    match = THINK_RE.search(clean)
    if match:
        reasoning = match.group(1).strip()
        content = (clean[:match.start()] + clean[match.end():]).strip()
        return reasoning, content

    close = clean.find("</think>")
    if close >= 0:
        reasoning = clean[:close].strip()
        content = clean[close + len("</think>"):].strip()
        return reasoning, content

    # A stray opening tag means the model restarted its reasoning.
    if "<think>" in clean:
        reasoning, _, content = clean.partition("<think>")
        return reasoning.strip(), content.strip()

    return "", clean.strip()


def parse_tool_calls(text: str) -> list:
    """Parse Spark's <tool_call>name<arg_key>..</arg_key>..</arg_value> blocks."""
    clean = strip_invisible(text)
    calls = []
    for block in TOOL_CALL_RE.findall(clean):
        block = block.strip()
        if not block:
            continue
        head, _, tail = block.partition("<arg_key>")
        name = strip_invisible(head).strip().strip("<>").strip()
        name = re.sub(r"[^\w.-]", "", name)
        if not name:
            continue
        arguments = {}
        for key, value in ARG_RE.findall("<arg_key>" + tail):
            key = strip_invisible(key).strip()
            value = strip_invisible(value)
            if not key:
                continue
            try:
                arguments[key] = json.loads(value)
            except (json.JSONDecodeError, TypeError):
                arguments[key] = value
        calls.append({"name": name, "arguments": arguments})
    return calls


# --------------------------------------------------------------------------
# Tool schemas (OpenAI function format - the template dumps tool["function"])
# --------------------------------------------------------------------------
def build_tools(skills: list) -> list:
    skill_ids = ", ".join(skill["id"] for skill in skills)
    skill_menu = "; ".join(f"{s['id']}: {s['description']}" for s in skills)
    skill_tools = []
    if skills:
        skill_tools.append(
            {
                "type": "function",
                "function": {
                    "name": "read_skill",
                    "description": (
                        "Load the full instructions for one skill before you act on a task "
                        f"that matches it. Available skills - {skill_menu}"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "name": {
                                "type": "string",
                                "description": f"Skill id, one of: {skill_ids}",
                            }
                        },
                        "required": ["name"],
                    },
                },
            }
        )

    return [
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read a text file from the workspace with line numbers.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "File path, relative to the workspace root."},
                        "start_line": {"type": "integer", "description": "First line to read (default 1)."},
                        "end_line": {"type": "integer", "description": "Last line to read (default: end of file)."},
                    },
                    "required": ["path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "list_dir",
                "description": "List the files and folders in a workspace directory.",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string", "description": "Directory path (default: workspace root)."}},
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "grep",
                "description": "Search workspace files with a regular expression.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string", "description": "Regular expression to search for."},
                        "path": {"type": "string", "description": "Directory or file to search (default: workspace root)."},
                        "glob": {"type": "string", "description": "File glob filter (default *)."},
                    },
                    "required": ["pattern"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "write_file",
                "description": "Create a file or replace its whole content.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "File path, relative to the workspace root."},
                        "content": {"type": "string", "description": "Full new content of the file."},
                    },
                    "required": ["path", "content"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "edit_file",
                "description": "Replace one exact snippet in a file. old_string must appear exactly once.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "File path, relative to the workspace root."},
                        "old_string": {"type": "string", "description": "Exact text to replace."},
                        "new_string": {"type": "string", "description": "Replacement text."},
                    },
                    "required": ["path", "old_string", "new_string"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "run_command",
                "description": "Run a shell command in the workspace root and return its output.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string", "description": "Command to run."},
                        "timeout": {"type": "integer", "description": "Timeout in seconds (default 60)."},
                    },
                    "required": ["command"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "git_status",
                "description": "Show the git status of the workspace: current branch and changed files.",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "git_diff",
                "description": "Show the git diff of the workspace, or of one file, against HEAD.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Optional file path; omit for every change."},
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "git_log",
                "description": "Show the recent commit history of the workspace.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "count": {"type": "integer", "description": "How many commits to show (default 10)."},
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "finish",
                "description": "Finish the task and report the result to the user.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "summary": {"type": "string", "description": "Short summary of what was done."}
                    },
                    "required": ["summary"],
                },
            },
        },
    ] + skill_tools


PROJECT_INSTRUCTION_FILES = ("AGENTS.md", "CLAUDE.md", ".agents.md")
MAX_INSTRUCTION_CHARS = 6000


def load_project_instructions(root: Path) -> str:
    """Read the project's own agent instructions from the workspace root.

    This is what turns the harness into a coding environment rather than a
    chat: the project states its own rules (build commands, conventions,
    what not to touch) and the agent follows them without being told again
    on every turn.
    """
    parts = []
    for name in PROJECT_INSTRUCTION_FILES:
        candidate = root / name
        if not candidate.is_file():
            continue
        try:
            text = candidate.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            continue
        if not text:
            continue
        if len(text) > MAX_INSTRUCTION_CHARS:
            text = text[:MAX_INSTRUCTION_CHARS] + "\n... (truncated)"
        parts.append(f"--- {name} ---\n{text}")
    return "\n\n".join(parts)


def build_system_prompt(workspace: Path, skills: list, instructions: str = "",
                        personality: str = "") -> str:
    lines = [
        "You are Spark Agent, a coding agent working inside a workspace directory.",
        "",
        f"Workspace: {workspace}",
        "Shell: run_command uses cmd.exe on Windows - use dir, type, findstr, where;"
        " head, tail, wc and grep do not exist there.",
        "",
        "How to work:",
        "1. Look before you edit. Use list_dir, grep and read_file to find the real text.",
        "2. One tool call per message. Wait for the result, then decide the next step.",
        "3. Change files with edit_file (small, exact replacement) or write_file (new file).",
        "4. If a skill below matches the task, call read_skill with its id first and follow it.",
        "5. Verify your change before you claim it works. git_status and git_diff show what changed.",
        "6. When the task is done, call finish with a short summary.",
        "",
        "Available skills:",
    ]
    for skill in skills:
        lines.append(f"- {skill['id']}: {skill['description']}")
    if not skills:
        lines.append("- (none)")
    if personality and personality.strip():
        lines += ["", "Personality and style:", personality.strip()]
    if instructions:
        lines += [
            "",
            "Project instructions (these come from the project itself and take",
            "precedence over the general rules above):",
            "",
            instructions,
        ]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Engines
# --------------------------------------------------------------------------
LLAMA_CLI_FOOTER = re.compile(r"\[\s*Prompt:.*?Generation:.*?\]\s*$", re.DOTALL)


def extract_cli_completion(stdout: str, prompt: str) -> str:
    """Pull the generated text out of llama-cli's stdout.

    llama-cli is an interactive program: it prints a banner, echoes the
    prompt, then the generation, then a stats footer. In --simple-io mode
    most of that goes away, but not all of it, and the prompt echo is the
    only reliable delimiter - the completion itself is whatever follows the
    exact prompt text that was handed to -f.
    """
    text = stdout
    index = text.find(prompt)
    if index >= 0:
        text = text[index + len(prompt):]
    else:
        # The prompt was not echoed; fall back to the generation marker that
        # ends every rendered Spark prompt.
        marker = text.rfind("<|Bot|>")
        if marker >= 0:
            text = text[marker:]
    text = text.replace("[Start thinking]", "").replace("[End thinking]", "")
    # Drop the trailing interactive prompt and exit notice first, so that the
    # stats footer ends up at the end of the string and can be anchored.
    text = text.split("\nExiting...")[0]
    text = re.split(r"\n\s*>\s*$", text)[0]
    text = LLAMA_CLI_FOOTER.sub("", text)
    return text.strip()


SAMPLE_TIMING_RE = re.compile(
    r"sample time\s*=\s*([\d.]+)\s*ms\s*/\s*(\d+)\s*runs?.*?([\d.]+)\s*tokens per second",
    re.DOTALL,
)
PROMPT_TIMING_RE = re.compile(
    r"prompt eval time\s*=\s*([\d.]+)\s*ms\s*/\s*(\d+)\s*tokens", re.DOTALL
)


def parse_cli_timings(text: str) -> dict:
    """Pull the token counters out of llama-cli's timing footer.

    llama-cli prints:
        sample time =  5678.90 ms /  100 runs  ( 56.78 ms per token, 17.61 tokens per second)
        prompt eval time = 1234.56 ms / 250 tokens
    """
    stats = {}
    sample = SAMPLE_TIMING_RE.search(text)
    if sample:
        stats["output_tokens"] = int(sample.group(2))
        stats["tps"] = float(sample.group(3))
    prompt = PROMPT_TIMING_RE.search(text)
    if prompt:
        stats["prompt_tokens"] = int(prompt.group(2))
    return stats


class CliEngine:
    """Renders the prompt and pipes it to llama-cli, one process per turn."""

    name = "cli"

    def __init__(self, cfg):
        self.cfg = cfg
        self.cli = Path(cfg.llama_dir) / "llama-cli.exe"
        self.stats = {}

    streams = False

    def generate(self, prompt: str, n_predict: int | None = None, on_text=None,
                 should_cancel=None, temperature: float | None = None) -> str:
        prompt_file = self.cfg.tmp_dir / "prompt.txt"
        prompt_file.write_text(prompt, encoding="utf-8")
        command = [
            str(self.cli),
            "-m", str(self.cfg.model),
            "-f", str(prompt_file),
            "--ctx-size", str(self.cfg.ctx),
            "--threads", str(self.cfg.threads),
            "--threads-batch", str(self.cfg.threads_batch),
            "--batch-size", str(self.cfg.batch),
            "--ubatch-size", str(self.cfg.ubatch),
            "--cache-type-k", self.cfg.ctk,
            "--cache-type-v", self.cfg.ctv,
            # --flash-attn takes an optional value, so it swallows the next
            # argument unless it is given one explicitly.
            "--flash-attn", "on",
            "--load-mode", self.cfg.load_mode,
            "-ngl", "0",
            "--device", "none",
            "--no-warmup",
            "--no-display-prompt",
            "--simple-io",
            "--temp", str(self.cfg.temp),
            "--top-p", str(self.cfg.top_p),
            "--top-k", str(self.cfg.top_k),
            "--repeat-penalty", str(self.cfg.repeat),
            "-n", str(int(n_predict or self.cfg.max_tokens)),
        ]
        env = dict(os.environ)
        env["LLAMA_ARG_CHAT_TEMPLATE_KWARGS"] = json.dumps(
            {
                "enable_thinking": self.cfg.think != "none",
                "reasoning_effort": self.cfg.think if self.cfg.think != "none" else "high",
            }
        )
        started = time.time()
        proc = subprocess.run(
            command,
            cwd=str(self.cfg.llama_dir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"llama-cli failed ({proc.returncode}):\n{proc.stderr[-2000:]}")
        text = extract_cli_completion(proc.stdout, prompt)
        self.stats = parse_cli_timings(proc.stdout + "\n" + proc.stderr)
        # llama-cli echoes the prompt unless told not to; --no-display-prompt
        # handles that, but strip a leading echo defensively.
        print(f"    [cli turn {time.time() - started:.1f}s]", flush=True)
        return text

    def count_tokens(self, text: str) -> int:
        """No server in the cli engine, so estimate."""
        return estimate_tokens(text)


def gpu_args(cfg) -> list:
    """GPU offload flags for llama-server.

    --ngl auto offloads every layer when the llama.cpp build in use has a
    GPU backend (ggml-cuda.dll or ggml-vulkan.dll), and keeps everything on
    the CPU otherwise.
    """
    value = str(getattr(cfg, "ngl", "auto")).lower()
    if value == "auto":
        folder = Path(cfg.llama_dir)
        has_gpu = any((folder / dll).is_file() for dll in ("ggml-cuda.dll", "ggml-vulkan.dll"))
        value = "all" if has_gpu else "0"
    if value in ("0", "none", "off"):
        return ["-ngl", "0", "--device", "none"]
    return ["-ngl", value]


class ServerEngine:
    """Posts the rendered prompt to llama-server over the OpenAI API."""

    name = "server"

    def __init__(self, cfg):
        self.cfg = cfg
        self.base = f"http://{cfg.host}:{cfg.port}"
        self.process = None
        self.running = False
        self.owned = False
        self.stats = {}
        # The job handle has to stay open for as long as the server should
        # live: closing it kills the server.
        self._job = None

    def start(self):
        if self._probe():
            self.running = True
            self.owned = False
            print(f"[server] reusing llama-server already on {self.base}")
            return
        server = Path(self.cfg.llama_dir) / "llama-server.exe"
        log = (self.cfg.tmp_dir / "llama-server.log").open("w", encoding="utf-8")
        # The harness renders the prompt itself and posts raw completions,
        # so the server's own chat template settings play no part.
        env = dict(os.environ)
        command = [
            str(server),
            "-m", str(self.cfg.model),
            "--host", self.cfg.host,
            "--port", str(self.cfg.port),
            "--ctx-size", str(self.cfg.ctx),
            "--threads", str(self.cfg.threads),
            "--threads-batch", str(self.cfg.threads_batch),
            "--batch-size", str(self.cfg.batch),
            "--ubatch-size", str(self.cfg.ubatch),
            "--cache-type-k", self.cfg.ctk,
            "--cache-type-v", self.cfg.ctv,
            "--flash-attn", "on",
            "--poll", "50",
            "--parallel", "1",
            "--load-mode", self.cfg.load_mode,
            # Prompt reuse on a sliding-window model: keep checkpoints close
            # together so a prompt that differs near its end can resume from
            # one instead of being recomputed from the start.
            "--checkpoint-min-step", str(self.cfg.checkpoint_step),
            "--ctx-checkpoints", str(self.cfg.checkpoints),
            "--cache-ram", str(self.cfg.cache_ram),
            "--slot-save-path", str(Path(self.cfg.tmp_dir) / "slots"),
            *gpu_args(self.cfg),
        ]
        (Path(self.cfg.tmp_dir) / "slots").mkdir(parents=True, exist_ok=True)
        self.process = subprocess.Popen(
            command, cwd=str(self.cfg.llama_dir), stdout=log, stderr=subprocess.STDOUT, env=env
        )
        self.owned = True
        # A job object with KILL_ON_JOB_CLOSE: if the agent dies for any
        # reason - including the console window being closed - Windows
        # kills the server with it, so it never survives as an orphan
        # holding the model in memory.
        self._job = winproc.kill_on_close(self.process)
        # The job object cannot be set up when the agent itself is inside a
        # job that forbids nesting, so a detached watchdog covers that.
        winproc.start_watchdog(self.process.pid, os.getpid(), sys.executable)
        print(f"[server] starting llama-server on {self.base} (pid {self.process.pid})")
        for _ in range(120):
            if self._probe():
                self.running = True
                print("[server] ready")
                return
            if self.process.poll() is not None:
                raise RuntimeError("llama-server exited during startup; see logs/llama-server.log")
            time.sleep(1)
        raise RuntimeError("llama-server did not become ready within 120s")

    def _probe(self) -> bool:
        try:
            with urllib.request.urlopen(f"{self.base}/health", timeout=2) as response:
                return response.status == 200
        except Exception:
            return False

    streams = True

    def generate(self, prompt: str, n_predict: int | None = None, on_text=None,
                 should_cancel=None, temperature: float | None = None) -> str:
        """Stream one completion of an already rendered prompt.

        on_text(piece) is called for every piece of text and with None about
        ten times a second while nothing arrives (for the spinner); when it
        returns True the generation is stopped early (the thinking budget).
        should_cancel() is polled the same way; True aborts the request and
        raises GenerationCancelled. The request runs in a thread so the
        prompt-processing wait can be cancelled too.
        """
        body = json.dumps(
            {
                "prompt": prompt,
                "temperature": float(temperature if temperature is not None else self.cfg.temp),
                "top_p": self.cfg.top_p,
                "top_k": self.cfg.top_k,
                "min_p": float(getattr(self.cfg, "min_p", 0.0)),
                "typical_p": float(getattr(self.cfg, "typical_p", 1.0)),
                "repeat_penalty": self.cfg.repeat,
                "repeat_last_n": int(getattr(self.cfg, "repeat_last_n", 64)),
                "presence_penalty": float(getattr(self.cfg, "presence_penalty", 0.0)),
                "frequency_penalty": float(getattr(self.cfg, "frequency_penalty", 0.0)),
                "seed": int(getattr(self.cfg, "seed", -1)),
                "n_predict": int(n_predict or self.cfg.max_tokens),
                "cache_prompt": True,
                "id_slot": 0,
                "stream": True,
                "stop": [],
            }
        ).encode("utf-8")
        events: queue.Queue = queue.Queue()
        conn = http.client.HTTPConnection(self.cfg.host, self.cfg.port, timeout=self.cfg.timeout)
        # http.client drops conn.sock once a streamed response starts, so
        # the socket is kept here to be able to cut the stream off.
        held: dict = {}

        def worker():
            try:
                conn.request("POST", "/completion", body=body,
                             headers={"Content-Type": "application/json"})
                held["sock"] = conn.sock
                response = conn.getresponse()
                if response.status != 200:
                    detail = response.read().decode("utf-8", "replace")[:2000]
                    events.put(("error", f"llama-server error {response.status}: {detail}"))
                    return
                for line in response:
                    line = line.decode("utf-8", "replace").strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    events.put(("chunk", chunk))
                    if chunk.get("stop"):
                        break
            except Exception as exc:  # noqa: BLE001 - reported to the caller
                events.put(("error", f"{type(exc).__name__}: {exc}"))
            finally:
                events.put(("end", None))

        started = time.time()
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        parts: list = []
        final: dict = {}
        error = ""
        stopped_early = False
        cancelled = False
        while True:
            try:
                kind, item = events.get(timeout=0.1)
            except queue.Empty:
                if on_text is not None:
                    on_text(None)
                if should_cancel is not None and should_cancel():
                    cancelled = True
                    break
                continue
            if kind == "chunk":
                piece = item.get("content") or ""
                if piece:
                    parts.append(piece)
                    if on_text is not None and on_text(piece):
                        stopped_early = True
                        break
                if item.get("stop"):
                    final = item
                if should_cancel is not None and should_cancel():
                    cancelled = True
                    break
            elif kind == "error":
                error = item
            else:
                break
        if cancelled or stopped_early:
            # Closing the socket makes llama-server stop generating.
            sock = held.get("sock") or conn.sock
            try:
                if sock is not None:
                    sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            conn.close()
            # On Windows a thread blocked in recv() is not woken by shutdown
            # or close; it ends by itself when the server stops sending. The
            # shutdown has already told llama-server to stop, so the reader
            # thread (a daemon) is left to finish instead of waited for.
            thread.join(timeout=0.2)
        else:
            conn.close()
        if cancelled:
            raise GenerationCancelled()
        if error and not parts:
            raise RuntimeError(error)

        timings = final.get("timings") or {}
        processed = timings.get("prompt_n")
        cached = timings.get("cache_n")
        if cached is None:
            cached = final.get("tokens_cached")
        stats = {
            "output_tokens": final.get("tokens_predicted") or len(parts),
            "prompt_tokens": final.get("tokens_evaluated") or 0,
            "tps": float(timings.get("predicted_per_second") or 0.0),
            "prompt_tps": float(timings.get("prompt_per_second") or 0.0),
            "stop_type": final.get("stop_type") or ("budget" if stopped_early else ""),
            "stopped_early": stopped_early,
        }
        if isinstance(processed, int) and isinstance(cached, int) and processed + cached > 0:
            stats["prompt_processed"] = processed
            stats["prompt_cached"] = cached
            stats["prompt_tokens"] = processed + cached
            stats["cache_pct"] = 100.0 * cached / (processed + cached)
        self.stats = stats
        if on_text is None:
            print(f"    [server turn {time.time() - started:.1f}s]", flush=True)
        return "".join(parts)

    # -- KV cache on disk ----------------------------------------------------
    def _slot_action(self, action: str, name: str) -> bool:
        try:
            body = json.dumps({"filename": name}).encode("utf-8")
            request = urllib.request.Request(
                f"{self.base}/slots/0?action={action}", data=body,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(request, timeout=120) as response:
                return response.status == 200
        except Exception:
            return False

    def save_slot(self, session_id: str) -> bool:
        """Write the KV cache of this session next to its transcript."""
        if not self.running:
            return False
        ok = self._slot_action("save", f"session-{session_id}.bin")
        folder = Path(self.cfg.tmp_dir) / "slots"
        try:
            files = sorted(folder.glob("session-*.bin"), key=lambda p: p.stat().st_mtime)
            for old in files[:-5]:
                old.unlink()
        except OSError:
            pass
        return ok

    def restore_slot(self, session_id: str) -> bool:
        """Load a saved KV cache so a resumed session is not recomputed."""
        if not (Path(self.cfg.tmp_dir) / "slots" / f"session-{session_id}.bin").is_file():
            return False
        return self._slot_action("restore", f"session-{session_id}.bin")

    # -- token counting ---------------------------------------------------
    def count_tokens(self, text: str) -> int:
        """Exact token count through the server's own tokenizer."""
        if not text:
            return 0
        try:
            body = json.dumps({"content": text}).encode("utf-8")
            request = urllib.request.Request(
                f"{self.base}/tokenize",
                data=body,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(request, timeout=20) as response:
                payload = json.loads(response.read().decode("utf-8"))
            return int(payload.get("tokens") and len(payload["tokens"]) or 0)
        except Exception:
            return estimate_tokens(text)

    def stop(self):
        """Shut the server down so the port is free again.

        The agent's port belongs to the agent: when the session ends, no
        llama-server may be left sitting on it holding the model in memory.
        A server this session started is terminated; one this session
        reused (left over from an earlier run) is stopped by pid.
        """
        if not self.owned:
            # Somebody else's server is on the port - but it is our port,
            # and this session is over, so it goes now rather than being
            # left running for ever.
            note = winproc.kill_server_on_port(self.cfg.port, self.cfg.llama_dir)
            self.running = False
            return note or ""
        if self.process is not None and self.process.poll() is None:
            try:
                self.process.terminate()
                self.process.wait(timeout=10)
            except Exception:
                try:
                    self.process.kill()
                    self.process.wait(timeout=5)
                except Exception:
                    pass
        self.process = None
        self.running = False
        # Closing the job handle kills anything still in it.
        self._job = None
        return ""


# --------------------------------------------------------------------------
# Agent loop
# --------------------------------------------------------------------------
def execute_tool(name: str, arguments: dict, ws: Workspace, skills: list,
                web=None, mcp=None, scratch_dir: str = "") -> str:
    """Run one tool call and return its output as text.

    The web and browser tools and the MCP bridge are dispatched here too,
    so every tool the model can name comes back through one path.
    """
    try:
        if name == "read_file":
            end = arguments.get("end_line", 0)
            return tool_read_file(
                ws,
                arguments.get("path", ""),
                int(arguments.get("start_line", 1) or 1),
                int(end) if str(end).isdigit() else 0,
            )
        if name == "list_dir":
            return tool_list_dir(ws, arguments.get("path", "."))
        if name == "grep":
            return tool_grep(
                ws,
                arguments.get("pattern", ""),
                arguments.get("path", "."),
                arguments.get("glob", "*"),
            )
        if name == "write_file":
            return tool_write_file(ws, arguments.get("path", ""), arguments.get("content", ""))
        if name == "edit_file":
            return tool_edit_file(
                ws,
                arguments.get("path", ""),
                arguments.get("old_string", ""),
                arguments.get("new_string", ""),
            )
        if name == "run_command":
            return tool_run_command(
                ws, arguments.get("command", ""), int(arguments.get("timeout", 60) or 60)
            )
        if name == "git_status":
            return tool_git_status(ws)
        if name == "git_diff":
            return tool_git_diff(ws, arguments.get("path", ""))
        if name == "git_log":
            return tool_git_log(ws, int(arguments.get("count", 10) or 10))
        if name == "read_skill":
            wanted = str(arguments.get("name", "")).strip()
            for skill in skills:
                if skill["id"] == wanted or skill["name"] == wanted:
                    return f"# skill: {skill['id']}\n{skill['body']}"
            available = ", ".join(s["id"] for s in skills) or "(none)"
            raise ToolError(f"unknown skill {wanted!r}; available: {available}")
        if name == "finish":
            return "__FINISH__:" + str(arguments.get("summary", ""))
        if web is not None and (name.startswith("web_") or name.startswith("browser_")):
            arguments = dict(arguments or {})
            if name == "browser_screenshot" and not str(arguments.get("path", "")).strip():
                # Without this the screenshot lands in the current working
                # directory, which is the user's project.
                folder = Path(scratch_dir) if scratch_dir else Path.cwd()
                try:
                    folder.mkdir(parents=True, exist_ok=True)
                    stamp = time.strftime("%H%M%S")
                    arguments["path"] = str(folder / f"browser-shot-{stamp}.png")
                except OSError:
                    pass
            return web.execute_web_tool(name, arguments)
        if mcp is not None and name.startswith("mcp_"):
            return mcp.call_tool(name, arguments)
    except ToolError as exc:
        return f"error: {exc}"
    except Exception as exc:  # noqa: BLE001
        return f"error: {type(exc).__name__}: {exc}"
    return f"error: unknown tool {name!r}"


class SparkAgent:
    """A coding agent with a persistent conversation.

    The message history survives between turns, so a follow-up instruction
    ("now do the same for beta.txt") is answered with the earlier work still
    in context.
    """

    def __init__(self, cfg, engine, ws: Workspace, skills: list, tools: list,
                 template: SparkTemplate, web=None, mcp=None):
        self.cfg = cfg
        self.engine = engine
        self.ws = ws
        self.skills = skills
        self.tools = tools
        self.template = template
        self.web = web
        self.mcp = mcp
        self.instructions = load_project_instructions(ws.root)
        self.transcript = None
        self.messages = [
            {"role": "system", "content": build_system_prompt(ws.root, skills, self.instructions, getattr(cfg, "personality", ""))},
        ]
        # slash command state
        self.current_argument = ""
        self.quit_requested = False
        self.autocompact = True
        self.autocompact_threshold = 70
        self.turns = 0
        self.stats = {}
        self.ctx_tokens = 0
        self.session_id = time.strftime("%Y%m%d-%H%M%S")
        self.session_path = None
        self.last_reasoning = ""

    # ------------------------------------------------------------------
    # conversation
    # ------------------------------------------------------------------
    def system_prompt(self) -> str:
        return build_system_prompt(self.ws.root, self.skills, self.instructions,
                                   getattr(self.cfg, "personality", ""))

    def reset(self):
        self.messages = [
            {"role": "system",
             "content": self.system_prompt()},
        ]
        self.turns = 0
        self.stats = {}
        # A new conversation is a new session: the old one stays on disk for
        # /resume instead of being overwritten by the next save.
        self.session_id = time.strftime("%Y%m%d-%H%M%S")
        self.session_path = None
        self.transcript = None
        self.last_reasoning = ""

    def _record(self, role: str, text: str):
        """Append the exchange to a session transcript next to the logs."""
        if self.transcript is None:
            try:
                folder = Path(self.cfg.transcript_dir)
                folder.mkdir(parents=True, exist_ok=True)
                stamp = time.strftime("%Y%m%d-%H%M%S")
                self.transcript = folder / f"session-{stamp}.md"
                self.transcript.write_text(
                    f"# Spark Agent session\n\nworkspace: {self.ws.root}\n\n", encoding="utf-8"
                )
            except OSError:
                return
        try:
            with self.transcript.open("a", encoding="utf-8") as handle:
                handle.write(f"\n## {role}\n\n{text}\n")
        except OSError:
            pass

    # ------------------------------------------------------------------
    # session files
    # ------------------------------------------------------------------
    def save_session(self) -> str:
        """Write the conversation so /resume can pick it up later."""
        try:
            folder = Path(self.cfg.transcript_dir)
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"session-{self.session_id}.json"
            payload = {
                "id": self.session_id,
                "workspace": str(self.ws.root),
                "model": str(self.cfg.model),
                "reasoning": self.cfg.think,
                "messages": self.messages,
            }
            path.write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")
            self.session_path = path
            return str(path)
        except OSError:
            return ""

    def list_sessions(self) -> list:
        """Every saved session, newest first."""
        out = []
        try:
            folder = Path(self.cfg.transcript_dir)
            if not folder.is_dir():
                return out
            for path in sorted(folder.glob("session-*.json"), reverse=True):
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                messages = payload.get("messages") or []
                first = ""
                for message in messages:
                    if message.get("role") == "user":
                        first = " ".join(str(message.get("content", "")).split())
                        break
                out.append({
                    "id": payload.get("id") or path.stem,
                    "when": time.strftime("%Y-%m-%d %H:%M", time.localtime(path.stat().st_mtime)),
                    "summary": first[:60],
                    "path": str(path),
                })
        except OSError:
            pass
        return out

    def load_session(self, path: str) -> bool:
        """Restore a saved conversation."""
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        messages = payload.get("messages")
        if not isinstance(messages, list) or not messages:
            return False
        self.messages = messages
        self.session_id = payload.get("id") or self.session_id
        workspace = payload.get("workspace")
        if workspace and Path(workspace).is_dir() and workspace != str(self.ws.root):
            self.ws = Workspace(Path(workspace))
            self.instructions = load_project_instructions(self.ws.root)
        return True

    # ------------------------------------------------------------------
    # engine and workspace
    # ------------------------------------------------------------------
    def ensure_engine(self):
        """Start the server again if it was stopped to reload a model."""
        if getattr(self.engine, "name", "") == "server":
            if not getattr(self.engine, "running", False):
                self.engine.start()

    def invalidate_engine(self) -> str:
        """The model changed: reload it on the next turn.

        Returns a note when the change cannot take effect straight away.
        """
        if hasattr(self.engine, "stop"):
            try:
                return self.engine.stop() or ""
            except Exception:
                return ""
        return ""

    def switch_workspace(self, path: Path):
        self.ws = Workspace(Path(path))
        self.instructions = load_project_instructions(self.ws.root)
        if self.messages and self.messages[0].get("role") == "system":
            self.messages[0]["content"] = self.system_prompt()
        remember_project(self.ws.root)

    def read_last_project(self) -> str:
        return read_last_project()

    def git_diff(self) -> str:
        return tool_git_diff(self.ws, "")

    # ------------------------------------------------------------------
    # statistics
    # ------------------------------------------------------------------
    def session_stats(self) -> dict:
        stats = dict(self.stats)
        stats["turns"] = self.turns
        stats["transcript"] = str(self.transcript) if self.transcript else ""
        stats["session"] = self.session_id
        stats["ctx_window"] = self.cfg.ctx
        return stats

    def current_prompt_tokens(self) -> int:
        """How big the conversation is right now, in tokens."""
        try:
            enable_thinking = self.cfg.think != "none"
            prompt = self.template.render(
                self.messages, tools=self.tools, enable_thinking=enable_thinking,
                reasoning_effort=self.cfg.think if enable_thinking else "high",
                add_generation_prompt=True,
            )
            return self.engine.count_tokens(prompt)
        except Exception:
            return estimate_tokens(json.dumps(self.messages, ensure_ascii=False))

    def refresh_context_tokens(self):
        """Recount the conversation, so the footer does not have to.

        The footer is redrawn on every keystroke, so the token count is
        cached here and only refreshed when the conversation changes.
        """
        self.ctx_tokens = self.current_prompt_tokens()
        return self.ctx_tokens

    # ------------------------------------------------------------------
    # compaction
    # ------------------------------------------------------------------
    def compact(self):
        """Replace the conversation with a summary of itself.

        Returns the summary, or None when there was nothing to compact.
        """
        if len(self.messages) <= 2:
            return None
        lines = []
        for message in self.messages[1:]:
            role = message.get("role")
            if role == "tool":
                continue
            content = message.get("content") or ""
            if not isinstance(content, str):
                content = str(content)
            calls = message.get("tool_calls") or []
            if calls:
                content = (content + "\n[tool calls: "
                           + ", ".join(c.get("name", "?") for c in calls) + "]").strip()
            lines.append(f"{role}: {content}")
        if not lines:
            return None

        request = (
            "Summarise this coding session so it can continue after the history is "
            "dropped. Keep what the user asked for, which files were read or "
            "changed, the decisions that were made, the current state of the work, "
            "and what is still open. Be specific about file paths, names and "
            "commands. Reply with the summary only, no preamble.\n\n"
            + "\n\n".join(lines)
        )
        messages = [
            {"role": "system", "content": self.system_prompt()},
            {"role": "user", "content": request},
        ]
        enable_thinking = self.cfg.think != "none"
        prompt = self.template.render(
            messages, tools=None, enable_thinking=enable_thinking,
            reasoning_effort=self.cfg.think if enable_thinking else "high",
            add_generation_prompt=True,
        )
        raw = self.engine.generate(prompt, temperature=getattr(self.cfg, "compact_temp", None))
        _reasoning, content = split_thinking(raw)
        summary = content.strip()
        if not summary:
            return None
        self.messages = [
            {"role": "system", "content": self.system_prompt()},
            {"role": "user", "content": (
                "Summary of the work so far, kept as the only context:\n\n" + summary
            )},
        ]
        self.save_session()
        return summary

    def maybe_autocompact(self) -> bool:
        """Compact when the conversation outgrows the threshold."""
        if not self.autocompact or len(self.messages) <= 2:
            return False
        tokens = self.current_prompt_tokens()
        limit = self.cfg.ctx * (self.autocompact_threshold / 100.0)
        if tokens < limit:
            return False
        print(f"\n[tui] context at {tokens} tokens, compacting")
        summary = self.compact()
        if summary:
            print("[tui] compacted")
        return bool(summary)

    # ------------------------------------------------------------------
    # the turn
    # ------------------------------------------------------------------
    def think_budget(self) -> int:
        """Thinking tokens allowed before the answer is forced."""
        budget = int(getattr(self.cfg, "think_budget", 0) or 0)
        if budget > 0:
            return budget
        return 1024 if self.cfg.think == "low" else 4096

    def _generate(self, prompt: str, enable_thinking: bool) -> tuple:
        """One model call. Returns (raw text, whether it was shown live).

        The raw text is exactly what follows the generation prompt, so it
        can go back into the history byte for byte. When the thinking block
        runs past the budget, the block is closed for the model and the same
        prompt continues - the closing tag is part of the raw text from then
        on, so the history still matches the server's cache.
        """
        engine = self.engine
        if not getattr(engine, "streams", False):
            raw = engine.generate(prompt)
            if enable_thinking and "</think>" not in raw:
                raw += "</think>"
            return raw, False

        view = tui.StreamView(tui.THEME, thinking=enable_thinking)
        budget = self.think_budget() if enable_thinking else 0

        def on_text(piece):
            if piece is None:
                view.tick()
                return False
            view.feed(piece)
            return bool(budget) and view.in_think and view.think_tokens >= budget

        def on_text_no_budget(piece):
            if piece is None:
                view.tick()
            else:
                view.feed(piece)
            return False

        try:
            raw = engine.generate(prompt, on_text=on_text, should_cancel=tui.esc_pressed)
            if engine.stats.get("stopped_early") and view.in_think:
                closing = "\n</think>\n\n"
                view.feed(closing)
                remaining = max(512, self.cfg.max_tokens - view.tokens)
                more = engine.generate(
                    prompt + raw + closing, n_predict=remaining,
                    on_text=on_text_no_budget,
                    should_cancel=tui.esc_pressed,
                )
                raw = raw + closing + more
        finally:
            view.finish()
        if enable_thinking and "</think>" not in raw:
            # The output limit was reached inside the thinking block. Close it,
            # so the reasoning is never mistaken for the answer.
            raw += "</think>"
        return raw, view.content_shown

    def turn(self, task: str) -> str:
        """Run one instruction to completion and return the final answer."""
        self.ensure_engine()
        self.messages.append({"role": "user", "content": task})
        self._record("you", task)
        self.turns += 1
        enable_thinking = self.cfg.think != "none"
        reasoning_effort = self.cfg.think if self.cfg.think != "none" else "high"
        gen_prefix = "<think>" if enable_thinking else "</think>"
        theme = tui.THEME

        for step in range(1, self.cfg.max_steps + 1):
            prompt = self.template.render(
                self.messages, tools=self.tools, enable_thinking=enable_thinking,
                reasoning_effort=reasoning_effort, add_generation_prompt=True,
            )
            raw, shown = self._generate(prompt, enable_thinking)
            reasoning, content = split_thinking(raw)
            calls = parse_tool_calls(content)
            visible = content.split("<tool_call>", 1)[0].strip()
            self.last_reasoning = reasoning or self.last_reasoning

            self.stats = dict(getattr(self.engine, "stats", {}) or {})

            if reasoning and not getattr(self.engine, "streams", False):
                print(theme.paint(f"✻ thought ({estimate_tokens(reasoning)} tokens) · /thinking shows it", "dim"))

            message = {
                "role": "assistant",
                "content": visible if calls else content.strip(),
                "reasoning_content": reasoning,
                "raw": raw,
                "raw_prefix": gen_prefix,
            }

            if not calls:
                answer = content.strip()
                self.messages.append(message)
                if not shown:
                    print(answer or theme.paint("(empty answer)", "dim"))
                if not answer and self.stats.get("stop_type") == "limit":
                    print(theme.paint(f"[stopped] the {self.cfg.max_tokens}-token output limit was reached", "warning"))
                self._record("agent", answer or "(empty)")
                self.save_session()
                return answer

            message["tool_calls"] = calls
            self.messages.append(message)
            if not shown and visible:
                print(visible)

            for call in calls:
                print(theme.paint(f"  ● {call['name']} ", "accent")
                      + theme.paint(json.dumps(call["arguments"], ensure_ascii=False)[:200], "dim"))
                result = execute_tool(
                    call["name"], call["arguments"], self.ws, self.skills,
                    web=self.web, mcp=self.mcp,
                    scratch_dir=str(getattr(self.cfg, "tmp_dir", "")),
                )
                if result.startswith("__FINISH__:"):
                    summary = result[len("__FINISH__:"):].strip()
                    # Close the call so the history stays a valid sequence.
                    self.messages.append({"role": "tool", "content": "done"})
                    print()
                    print(summary)
                    self._record("agent", summary)
                    self.save_session()
                    return summary
                first = result.strip().splitlines()[0] if result.strip() else "(no output)"
                more = len(result.strip().splitlines()) - 1
                print(theme.paint(f"    └ {first[:160]}" + (f"  (+{more} lines)" if more > 0 else ""), "dim"))
                self._record("tool", f"{call['name']} {json.dumps(call['arguments'], ensure_ascii=False)[:200]}\n{result[:800]}")
                self.messages.append({"role": "tool", "content": result})

        print(theme.paint(f"\n[stopped] reached the {self.cfg.max_steps}-step limit", "warning"))
        self.save_session()
        return ""


REPL_HELP = """
Commands:
  /help     show this help
  /reset    clear the conversation and start over
  /exit     leave (Ctrl+C also works)

Anything else is sent to the agent as an instruction.
"""


def git_branch(ws: Workspace) -> str:
    """The current branch, or an empty string when it is not a repo."""
    try:
        proc = subprocess.run(
            "git rev-parse --abbrev-ref HEAD",
            shell=True, cwd=str(ws.root), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=10,
        )
        branch = proc.stdout.strip()
        if proc.returncode != 0 or branch in ("", "HEAD"):
            return ""
        return branch
    except Exception:
        return ""


def model_label(cfg) -> str:
    return Path(cfg.model).stem.replace("Spark-", "").replace(".gguf", "")


def print_status(agent: SparkAgent, width: int) -> list:
    """The PI.DEV style footer under the input box."""
    stats = agent.session_stats()
    stats["ctx_tokens"] = agent.ctx_tokens
    stats["tools"] = len(agent.tools)
    return [
        tui.context_line(width, str(agent.ws.root), git_branch(agent.ws), agent.session_id),
        tui.status_line(width, stats, model_label(agent.cfg), agent.cfg.think),
    ]


def repl(agent: SparkAgent, ctx):
    """Interactive session: type an instruction, keep going."""
    theme = tui.THEME
    width = min(tui.term_width(), 100)
    print(theme.paint("Type /help for the commands, /exit to leave.", "dim"))
    print()
    history = []
    prompt_box = tui.PromptBox(theme, width)
    agent.refresh_context_tokens()
    prompt_box.footer = lambda: print_status(agent, width)
    while not agent.quit_requested:
        try:
            task = tui.read_prompt(prompt_box, history)
        except EOFError:
            print()
            break
        except KeyboardInterrupt:
            print()
            break
        if not task:
            continue
        try:
            result = slash_commands.dispatch(ctx, task)
        except Exception as exc:  # noqa: BLE001
            result = f"error in {task.split()[0]}: {type(exc).__name__}: {exc}"
        if isinstance(result, slash_commands.AgentTask):
            task = result.text
        elif result is not None:
            print(result)
            print()
            agent.refresh_context_tokens()
            continue
        elif task.startswith("/") and task.split()[0].lower() in slash_commands.REGISTRY:
            # The command printed its own output.
            print()
            agent.refresh_context_tokens()
            continue
        try:
            agent.turn(task)
            agent.maybe_autocompact()
        except KeyboardInterrupt:
            print("\n[interrupted]")
        except Exception as exc:  # noqa: BLE001
            print(f"\n[error] {type(exc).__name__}: {exc}")
        agent.refresh_context_tokens()
        print()
    return 0


def run_agent(cfg, engine, ws: Workspace, skills: list, tools: list, template: SparkTemplate,
              task: str, web=None, mcp=None):
    """Run a single instruction in a throwaway agent (one-shot mode)."""
    agent = SparkAgent(cfg, engine, ws, skills, tools, template, web=web, mcp=mcp)
    agent.turn(task)
    agent.refresh_context_tokens()
    width = min(tui.term_width(), 100)
    for line in print_status(agent, width):
        print(line)
    return 0


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def build_parser():
    parser = argparse.ArgumentParser(description="Spark Agent - coding harness for Spark X2.6")
    parser.add_argument("engine", nargs="?", default="server", choices=["server", "cli"])
    parser.add_argument("--workspace", default=str(DEFAULT_ROOT / "workspace"))
    parser.add_argument("--model", default=str(find_model()))
    parser.add_argument("--llama-dir", default=str(find_llama_dir()))
    parser.add_argument("--skills", default=str(DEFAULT_SKILLS))
    parser.add_argument("--transcript-dir", default=str(DEFAULT_ROOT / "logs" / "sessions"))
    parser.add_argument("--template", default=str(DEFAULT_ROOT / "spark26-chat-template.jinja"))
    parser.add_argument("--think", default="high", choices=["high", "low", "none"])
    parser.add_argument("--max-steps", type=int, default=12)
    parser.add_argument("--max-tokens", type=int, default=8192,
                        help="output tokens per model call, thinking included")
    parser.add_argument("--think-budget", type=int, default=0,
                        help="thinking tokens before the answer is forced (0 = 1024 at low, 4096 at high)")
    parser.add_argument("--ctx", type=int, default=32768)
    parser.add_argument("--threads", type=int, default=6,
                        help="generation threads (the Core Ultra 5 225F has 6 P-cores)")
    parser.add_argument("--threads-batch", type=int, default=10)
    parser.add_argument("--ngl", default="auto",
                        help="GPU layers: auto (all when the build has CUDA), 0, a number or all")
    parser.add_argument("--checkpoint-step", type=int, default=512)
    parser.add_argument("--checkpoints", type=int, default=16)
    parser.add_argument("--cache-ram", type=int, default=2048, help="MiB of host RAM for prompt checkpoints")
    parser.add_argument("--batch", type=int, default=2048)
    parser.add_argument("--ubatch", type=int, default=512)
    parser.add_argument("--ctk", default="f16")
    parser.add_argument("--ctv", default="f16")
    parser.add_argument("--load-mode", default="mmap")
    parser.add_argument("--temp", type=float, default=1.0)
    parser.add_argument("--top-p", type=float, default=0.95)
    parser.add_argument("--top-k", type=int, default=0)
    parser.add_argument("--repeat", type=float, default=1.0)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8082)
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--task", default="")
    parser.add_argument("--interactive", action="store_true",
                        help="always start an interactive session, even when stdin is a pipe")
    parser.add_argument("--render", action="store_true", help="print the rendered prompt and exit")
    parser.add_argument("--no-web", action="store_true", help="do not load the web and browser tools")
    parser.add_argument("--no-mcp", action="store_true", help="do not load the MCP servers")
    parser.add_argument("--mcp-config", default="", help="path to an MCP config file")
    return parser


def main(argv=None):
    use_utf8_console()
    cfg = build_parser().parse_args(argv)
    argv_given = list(argv) if argv is not None else sys.argv[1:]
    # Saved /settings values, unless the same setting was given as a flag.
    settings_store.apply_saved(cfg, None, settings_store.explicit_keys(argv_given))

    root = Path(cfg.llama_dir)
    if not (root / "llama-cli.exe").exists() and not (root / "llama-server.exe").exists():
        print(f"[error] no llama.cpp build at {root}")
        return 2
    if not Path(cfg.model).exists():
        print(f"[error] model not found: {cfg.model}")
        return 2

    # The workspace comes from --workspace when it was given; otherwise from
    # the project the launchers used last, so neither launcher has to be
    # told the same path twice.
    argv_list = list(argv) if argv is not None else sys.argv[1:]
    explicit_workspace = "--workspace" in argv_list
    if not explicit_workspace:
        remembered = read_last_project()
        if remembered and Path(remembered).is_dir():
            cfg.workspace = remembered
    if explicit_workspace:
        remember_project(Path(cfg.workspace))

    # The scratch directory (rendered prompts, server log) lives next to the
    # harness, not inside the project, so the agent never leaves untracked
    # files in the repository it is working on.
    cfg.tmp_dir = DEFAULT_ROOT / "logs" / ".spark"
    cfg.tmp_dir.mkdir(parents=True, exist_ok=True)

    tokens = load_special_tokens(Path(cfg.model))
    template = SparkTemplate(tokens)
    skills = load_skills(Path(cfg.skills))
    ws = Workspace(Path(cfg.workspace))

    # --- optional tool providers ---------------------------------------
    web = None
    if not cfg.no_web:
        web = load_web_tools()
    mcp = None
    if not cfg.no_mcp:
        mcp_module = load_mcp()
        if mcp_module is not None:
            try:
                mcp = mcp_module.McpBridge(
                    Path(cfg.mcp_config) if cfg.mcp_config else None
                )
            except Exception as exc:
                print(f"[warn] MCP bridge failed to start: {exc}")
                mcp = None

    tools = build_tools(skills)
    web_tool_count = 0
    if web is not None:
        try:
            web_tools = web.build_web_tools()
            tools.extend(web_tools)
            web_tool_count = len(web_tools)
        except Exception as exc:
            print(f"[warn] web tools unavailable: {exc}")
            web = None
    mcp_tool_count = 0
    if mcp is not None:
        try:
            mcp.connect_all()
            mcp_tools = mcp.list_tools()
            tools.extend(mcp_tools)
            mcp_tool_count = len(mcp_tools)
        except Exception as exc:
            print(f"[warn] MCP tools unavailable: {exc}")

    print(tui.banner(min(tui.term_width(), 100)))
    print()
    print(f"[setup] model      : {cfg.model}")
    print(f"[setup] llama.cpp  : {root}")
    print(f"[setup] workspace  : {ws.root}")
    print(f"[setup] skills     : {', '.join(s['id'] for s in skills) or '(none)'}")
    found = [n for n in PROJECT_INSTRUCTION_FILES if (ws.root / n).is_file()]
    print(
        "[setup] instructions: "
        + (", ".join(found) if found else "(none - add AGENTS.md to the project root)")
    )
    print(f"[setup] thinking   : {cfg.think}")
    print(f"[setup] engine     : {cfg.engine}")
    print(f"[setup] web tools  : {web_tool_count}")
    print(f"[setup] mcp tools  : {mcp_tool_count}")

    task = cfg.task
    interactive = cfg.interactive
    if not task and not interactive and not sys.stdin.isatty():
        # A piped task: read it all and run once.
        task = strip_bom(sys.stdin.read().strip())
    elif not task:
        # A console: stay open and take instructions one at a time.
        interactive = True

    if cfg.render:
        messages = [
            {"role": "system",
             "content": build_system_prompt(ws.root, skills, load_project_instructions(ws.root),
                                                getattr(cfg, "personality", ""))},
            {"role": "user", "content": task or "Say hello."},
        ]
        print(template.render(
            messages, tools=tools,
            enable_thinking=cfg.think != "none",
            reasoning_effort=cfg.think if cfg.think != "none" else "high",
        ))
        return 0

    if not task and not interactive:
        print("No task given.")
        print("  - pass it inline:  --task \"do the thing\"")
        print("  - pipe it in:      echo \"do the thing\" | start_spark26_agent.bat")
        print("  - or run it with no arguments for an interactive session.")
        return 2

    engine = ServerEngine(cfg) if cfg.engine == "server" else CliEngine(cfg)
    if cfg.engine == "server":
        engine.start()

    ctx = slash_commands.Context(
        agent=None, cfg=cfg, engine=engine, ws=ws, skills=skills, tools=tools,
        web=web, mcp=mcp,
    )
    try:
        agent = SparkAgent(cfg, engine, ws, skills, tools, template, web=web, mcp=mcp)
        settings_store.apply_saved(cfg, agent, settings_store.explicit_keys(argv_given))
        ctx.agent = agent
        if interactive:
            return repl(agent, ctx)
        agent.turn(task)
        agent.save_session()
        return 0
    finally:
        if mcp is not None:
            try:
                mcp.close()
            except Exception:
                pass
        if cfg.engine == "server":
            agent_ref = ctx.agent
            if agent_ref is not None and len(agent_ref.messages) > 1 and hasattr(engine, "save_slot"):
                engine.save_slot(agent_ref.session_id)
            note = engine.stop()
            if note:
                print(f"[server] {note}")
            else:
                print("[server] shut down")


if __name__ == "__main__":
    sys.exit(main())
