#!/usr/bin/env python3
"""
The `spark` command.

One entry point for everything Spark Code 2.6 does:

    spark              start the coding agent (the terminal UI)
    spark agent        the same
    spark ui           start the llama.cpp web UI on :8081
    spark bench        run llama-bench
    spark update       check the git repo for a new version and pull it
    spark install      set this machine up
    spark doctor       check the environment without starting the model
    spark version      print the version
    spark help         this text

The command lives in a folder on the PATH (the installer puts it in
%USERPROFILE%\\.local\\bin) and points at wherever the app was installed.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

VERSION = "2.6.1"
REPO_URL = "https://github.com/tattooinmtl/spark-x26.git"
DEFAULT_INSTALL = Path(os.environ.get("SPARK_HOME", Path.home() / ".spark-code26"))

COLORS = {
    "accent": "\x1b[38;2;167;139;250m",
    "green": "\x1b[38;2;134;239;172m",
    "yellow": "\x1b[38;2;245;224;90m",
    "red": "\x1b[38;2;248;113;113m",
    "dim": "\x1b[38;2;107;114;128m",
    "bold": "\x1b[1m",
    "off": "\x1b[0m",
}


def paint(text: str, style: str = "") -> str:
    if not sys.stdout.isatty() or style not in COLORS:
        return text
    return f"{COLORS[style]}{text}{COLORS['off']}"


def run(command: list, cwd: Path | None = None, timeout: int = 120) -> tuple:
    """Run a command and return (returncode, output)."""
    try:
        proc = subprocess.run(
            command,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except FileNotFoundError:
        return 127, f"{command[0]} is not on PATH"
    except subprocess.TimeoutExpired:
        return 124, f"{command[0]} timed out after {timeout}s"
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


# --------------------------------------------------------------------------
# update
# --------------------------------------------------------------------------
def cmd_update(args: list) -> int:
    """Check the git repo for a newer version and pull it."""
    root = ROOT
    if not (root / ".git").exists():
        print(paint("this copy is not a git checkout, so it cannot update itself", "red"))
        print(f"  folder: {root}")
        print("  reinstall from the repo instead:")
        print(f"    git clone {REPO_URL} \"{DEFAULT_INSTALL}\"")
        return 1

    code, out = run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=root)
    branch = out.strip() or "main"
    code, out = run(["git", "status", "--porcelain"], cwd=root)
    dirty = [line for line in out.splitlines() if line.strip()]

    print(paint(f"checking {REPO_URL}", "dim"))
    code, out = run(["git", "fetch", "--quiet", "origin", branch], cwd=root, timeout=300)
    if code != 0:
        print(paint("could not reach the repo", "red"))
        print(out.strip())
        return 1

    code, out = run(["git", "rev-parse", "HEAD"], cwd=root)
    before = out.strip()
    code, out = run(["git", "rev-parse", f"origin/{branch}"], cwd=root)
    after = out.strip()

    if before == after:
        print(paint(f"already up to date ({branch} {before[:8]})", "green"))
        return 0

    if dirty and "--force" not in args:
        print(paint("you have uncommitted changes, so the update was not applied:", "yellow"))
        for line in dirty[:10]:
            print(f"    {line}")
        print(paint("  commit them, or run: spark update --force", "dim"))
        return 1

    code, out = run(["git", "merge", "--ff-only", f"origin/{branch}"], cwd=root)
    if code != 0:
        print(paint("the update could not be applied cleanly", "red"))
        print(out.strip())
        return 1

    code, out = run(["git", "log", "-1", "--pretty=%h %s (%cr)"], cwd=root)
    print(paint(f"updated {before[:8]} -> {out.strip()}", "green"))
    print(paint("restart the agent to pick it up", "dim"))
    return 0


# --------------------------------------------------------------------------
# doctor
# --------------------------------------------------------------------------
def cmd_doctor(args: list) -> int:
    """Check the machine without loading the model."""
    from spark_agent import find_llama_dir, find_model, load_config

    print(paint("Spark Code", "bold"), paint(f" {VERSION}", "dim"))
    ok = True

    model = find_model()
    if model.is_file():
        size = model.stat().st_size / (1024 ** 3)
        print(f"  {paint('ok', 'green')}  model       {model}  ({size:.2f} GB)")
    else:
        ok = False
        print(f"  {paint('!!', 'yellow')}  model       not found: {model}")
        print(paint("      run: spark install --model", "dim"))

    llama = find_llama_dir()
    exe = llama / "llama-server.exe"
    if exe.is_file():
        print(f"  {paint('ok', 'green')}  llama.cpp   {llama}")
    else:
        ok = False
        print(f"  {paint('!!', 'yellow')}  llama.cpp   not found: {llama}")
        print(paint("      run: spark install", "dim"))

    if (ROOT / ".git").exists():
        code, out = run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT)
        code2, out2 = run(["git", "status", "--porcelain"], cwd=ROOT)
        state = "clean" if not out2.strip() else f"{len(out2.splitlines())} changed"
        print(f"  {paint('ok', 'green')}  git         {out.strip()} ({state})")
    else:
        print(f"  {paint('!!', 'yellow')}  git         not a checkout")

    config = load_config()
    if config:
        print(f"  {paint('ok', 'green')}  config      {json.dumps(config)[:90]}")

    for name, tool in (("git", "git"), ("python", sys.executable)):
        code, _ = run([tool, "--version"])
        print(f"  {paint('ok' if code == 0 else '!!', 'green' if code == 0 else 'yellow')}  {name:<11} {tool}")

    try:
        import playwright  # noqa: F401

        print(f"  {paint('ok', 'green')}  browser     playwright installed")
    except Exception:
        print(f"  {paint('--', 'dim')}  browser     playwright not installed")

    print()
    print(paint("  spark            start the coding agent", "dim"))
    print(paint("  spark ui         start the llama.cpp web UI on :8081", "dim"))
    print(paint("  spark update     pull a new version from the repo", "dim"))
    return 0 if ok else 1


# --------------------------------------------------------------------------
# the other commands
# --------------------------------------------------------------------------
def cmd_agent(args: list) -> int:
    """Start the coding agent."""
    command = [sys.executable, str(HERE / "spark_agent.py")] + args
    return subprocess.call(command)


def cmd_ui(args: list) -> int:
    """Start the llama.cpp web UI.

    The launcher takes its arguments in a fixed order - mode, thinking
    level, build, project - so a bare folder in the argument list is put
    where it belongs instead of being read as a thinking level.
    """
    launcher = ROOT / "start_spark26.bat"
    if not launcher.is_file():
        print(paint(f"missing {launcher}", "red"))
        return 1
    project = None
    rest = []
    for argument in args:
        if project is None and os.path.isdir(argument):
            project = argument
        else:
            rest.append(argument)
    command = ["server", "high", "b11873"]
    if project:
        command.append(project)
    command.extend(rest)
    return subprocess.call(["cmd", "/c", str(launcher)] + command)


def cmd_bench(args: list) -> int:
    launcher = ROOT / "start_spark26.bat"
    return subprocess.call(["cmd", "/c", str(launcher), "bench"] + args)


def cmd_install(args: list) -> int:
    import install as installer

    return installer.run(args)


def cmd_version(args: list) -> int:
    print(f"Spark Code {VERSION}")
    code, out = run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT)
    if code == 0 and out.strip():
        print(f"  build {out.strip()}")
    print(f"  installed at {ROOT}")
    return 0


HELP = f"""
{paint('SPARK CODE 2.6', 'bold')} - a local coding agent for Spark X2.6

  {paint('spark', 'accent')}              start the coding agent
  {paint('spark agent', 'accent')}        the same
  {paint('spark ui', 'accent')}           start the llama.cpp web UI on :8081
  {paint('spark bench', 'accent')}        run llama-bench
  {paint('spark update', 'accent')}       pull a new version from the git repo
  {paint('spark install', 'accent')}      set this machine up
  {paint('spark doctor', 'accent')}       check the environment
  {paint('spark version', 'accent')}      print the version
  {paint('spark help', 'accent')}         this text

Everything after the command is passed straight through, so

  spark --workspace C:\\work --think low
  spark update --force

both work. The agent's own commands (/help, /model, /doctor, ...) are
typed inside the agent.
"""


def main(argv: list | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        return cmd_agent([])

    command = argv[0].lower()
    rest = argv[1:]

    if command in ("help", "-h", "--help", "/?"):
        print(HELP)
        return 0
    if command in ("version", "-v", "--version"):
        return cmd_version(rest)
    if command in ("update", "--update", "-u"):
        return cmd_update(rest)
    if command in ("doctor", "--doctor"):
        return cmd_doctor(rest)
    if command in ("install", "--install"):
        return cmd_install(rest)
    if command in ("agent", "--agent"):
        return cmd_agent(rest)
    if command in ("ui", "--ui", "server"):
        return cmd_ui(rest)
    if command in ("bench", "--bench"):
        return cmd_bench(rest)

    # Anything else is the agent's own argument list.
    return cmd_agent(argv)


if __name__ == "__main__":
    sys.exit(main())
