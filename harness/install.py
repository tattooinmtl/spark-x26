#!/usr/bin/env python3
"""
Set this machine up for Spark Code 2.6.

Run it once, from inside the cloned repo:

    install.cmd

It does five things:

1. checks that this folder really is a git checkout of the project, and
   points it at the right remote;
2. fetches the llama.cpp CPU build if it is not there yet;
3. finds the model, or downloads it from Hugging Face when you ask it to;
4. writes the `spark` command into a folder on your PATH;
5. runs the self-test, so a broken install is caught here and not later.
"""
from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
REPO_URL = "https://github.com/tattooinmtl/spark-x26.git"
LLAMA_BUILD = "llama-b11873-cpu"
LLAMA_TAG = "b11873"
LLAMA_ASSET = "llama-b11873-bin-win-cpu-x64.zip"
LLAMA_URL = f"https://github.com/ggml-org/llama.cpp/releases/download/{LLAMA_TAG}/{LLAMA_ASSET}"
MODEL_REPO = "XHToken/Spark-X2.6-4B-GGUF"
MODEL_FILE = "Spark-X2.6-4B-Q8_0.gguf"
MODEL_URL = f"https://huggingface.co/{MODEL_REPO}/resolve/main/{MODEL_FILE}"

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


def run_cmd(command: list, cwd: Path | None = None, timeout: int = 300) -> tuple:
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
        return 124, f"{command[0]} timed out"
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def step(text: str) -> None:
    print(paint("->", "accent"), text)


def good(text: str) -> None:
    print(paint(" ok", "green"), text)


def warn(text: str) -> None:
    print(paint(" !!", "yellow"), text)


def fail(text: str) -> None:
    print(paint(" !!", "red"), text)


def ask(question: str, default: bool = False) -> bool:
    suffix = " [Y/n] " if default else " [y/N] "
    try:
        answer = input(paint(question + suffix, "accent")).strip().lower()
    except EOFError:
        return default
    if not answer:
        return default
    return answer.startswith("y")


def download(url: str, target: Path, label: str) -> bool:
    """Download a file with a progress line."""
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + ".part")
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "spark-code26"})
        with urllib.request.urlopen(request, timeout=60) as response:
            total = int(response.headers.get("Content-Length") or 0)
            done = 0
            last = 0.0
            with partial.open("wb") as handle:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    handle.write(chunk)
                    done += len(chunk)
                    now = time.time()
                    if now - last > 0.5:
                        last = now
                        if total:
                            pct = 100.0 * done / total
                            print(f"\r   {label}: {done / 1024 ** 2:.0f} MB / {total / 1024 ** 2:.0f} MB ({pct:.0f}%)", end="", flush=True)
                        else:
                            print(f"\r   {label}: {done / 1024 ** 2:.0f} MB", end="", flush=True)
        print()
        partial.replace(target)
        return True
    except Exception as exc:
        print()
        warn(f"could not download {label}: {exc}")
        if partial.exists():
            try:
                partial.unlink()
            except OSError:
                pass
        return False


# --------------------------------------------------------------------------
# steps
# --------------------------------------------------------------------------
def check_git() -> bool:
    step("checking the git checkout")
    if not (ROOT / ".git").exists():
        fail(f"{ROOT} is not a git checkout")
        print("   clone it first:")
        print(f"     git clone {REPO_URL} \"{ROOT}\"")
        return False
    code, out = run_cmd(["git", "remote", "get-url", "origin"], cwd=ROOT)
    if code != 0:
        run_cmd(["git", "remote", "add", "origin", REPO_URL], cwd=ROOT)
        good(f"added the remote {REPO_URL}")
    else:
        good(f"remote origin -> {out.strip()}")
    code, out = run_cmd(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT)
    if code == 0:
        good(f"on commit {out.strip()}")
    return True


def install_llama() -> Path | None:
    step("checking the llama.cpp build")
    target = ROOT / "llama" / LLAMA_BUILD
    if (target / "llama-server.exe").is_file():
        good(f"already there: {target}")
        return target
    if not ask(f"download the llama.cpp CPU build ({LLAMA_TAG}, about 23 MB)?", default=True):
        warn("skipped - the agent will not start without it")
        return None
    zip_path = ROOT / "logs" / ".install" / LLAMA_ASSET
    if not zip_path.exists():
        if not download(LLAMA_URL, zip_path, "llama.cpp"):
            return None
    try:
        target.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path) as archive:
            names = archive.namelist()
            # The release zip has one top folder; flatten it.
            prefix = ""
            for name in names:
                head = name.split("/", 1)[0]
                if head and "/" in name:
                    prefix = head + "/"
                    break
            for name in names:
                if name.endswith("/"):
                    continue
                relative = name[len(prefix):] if prefix and name.startswith(prefix) else name
                if not relative or ".." in relative:
                    continue
                destination = target / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(name) as source, destination.open("wb") as sink:
                    shutil.copyfileobj(source, sink)
        good(f"installed {target}")
    except Exception as exc:
        fail(f"could not unpack the llama.cpp build: {exc}")
        return None
    finally:
        try:
            zip_path.unlink()
        except OSError:
            pass
    return target if (target / "llama-server.exe").is_file() else None


def find_model() -> Path | None:
    """Where the model is, if it is anywhere we know about."""
    env = os.environ.get("SPARK_MODEL")
    if env and Path(env).is_file():
        return Path(env)
    local = ROOT / "models" / MODEL_FILE
    if local.is_file():
        return local
    for folder in (Path(r"C:\models"),):
        candidate = folder / MODEL_FILE
        if candidate.is_file():
            return candidate
    for folder in (Path(r"C:\models"),):
        if folder.is_dir():
            matches = sorted(folder.glob("Spark-X2.6*.gguf"))
            if matches:
                return matches[0]
    return None


def install_model(want_model: bool) -> Path | None:
    step("looking for the model")
    found = find_model()
    if found:
        size = found.stat().st_size / (1024 ** 3)
        good(f"{found} ({size:.2f} GB)")
        return found
    warn(f"no {MODEL_FILE} found")
    if not want_model and not ask(
        f"download it now? (about 4 GB from Hugging Face)", default=True
    ):
        warn("skipped - the agent cannot start without the model")
        return None
    target = ROOT / "models" / MODEL_FILE
    if not download(MODEL_URL, target, "model"):
        return None
    good(f"installed {target}")
    return target


def install_launcher() -> Path | None:
    """Put the `spark` command in a folder that is already on the PATH."""
    step("installing the spark command")
    candidates = []
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if not entry:
            continue
        folder = Path(entry)
        if folder.is_dir() and os.access(folder, os.W_OK):
            candidates.append(folder)
    preferred = Path.home() / ".local" / "bin"
    if preferred.is_dir():
        candidates.insert(0, preferred)
    if not candidates:
        fail("no writable folder on the PATH")
        return None

    launcher = candidates[0] / "spark.cmd"
    body = f"""@echo off
rem Spark Code 2.6 - generated by install.cmd, do not edit
setlocal
set "SPARK_HOME={ROOT}"
if not exist "%SPARK_HOME%\\harness\\spark_cli.py" (
    echo [spark] not installed at %SPARK_HOME%
    echo         install it with:
    echo           git clone {REPO_URL} "%SPARK_HOME%"
    echo           cd /d "%SPARK_HOME%" ^&^& install.cmd
    exit /b 1
)
set "PY="
for %%P in ("py -3.12" "py -3" "python") do (
    if not defined PY (
        %%~P -c "import sys" >nul 2>&1 && set "PY=%%~P"
    )
)
if not defined PY (
    echo [spark] no python interpreter found on PATH
    exit /b 1
)
%PY% "%SPARK_HOME%\\harness\\spark_cli.py" %*
exit /b %ERRORLEVEL%
"""
    try:
        launcher.write_text(body, encoding="utf-8", newline="\r\n")
    except OSError as exc:
        fail(f"could not write {launcher}: {exc}")
        return None
    good(f"spark -> {launcher}")
    return launcher


def write_config(model: Path | None, llama: Path | None) -> None:
    config = {}
    if model:
        config["model"] = str(model)
    if llama:
        config["llama_dir"] = str(llama)
    try:
        (ROOT / "config.json").write_text(
            json.dumps(config, indent=2), encoding="utf-8"
        )
        good(f"wrote {ROOT / 'config.json'}")
    except OSError as exc:
        warn(f"could not write config.json: {exc}")


def run_selftest() -> None:
    step("running the self-test")
    code, out = run_cmd([sys.executable, str(HERE / "selftest.py")], cwd=HERE, timeout=600)
    tail = [line for line in out.splitlines() if line.startswith("[FAIL]")]
    if code == 0:
        good("self-test passed")
    else:
        fail("self-test failed")
        for line in tail[:10]:
            print(f"    {line}")


def run(args: list) -> int:
    print()
    print(paint(" SPARK CODE 2.6 ", "bold") + paint(" installer", "dim"))
    print(paint(f" {ROOT}", "dim"))
    print()

    if not check_git():
        return 1
    llama = install_llama()
    model = install_model("--model" in args)
    write_config(model, llama)
    install_launcher()
    run_selftest()

    print()
    print(paint(" done.", "green"))
    print()
    print("   spark              start the coding agent")
    print("   spark ui           start the llama.cpp web UI on :8081")
    print("   spark update       pull a new version from the repo")
    print("   spark doctor       check the machine")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
