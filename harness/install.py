#!/usr/bin/env python3
"""
Set this machine up for Spark Code 2.6.

Run it once, from inside the cloned repo:

    install.cmd

It does five things:

1. checks that this folder really is a git checkout of the project, and
   points it at the right remote;
2. fetches the llama.cpp CPU build if it is not there yet, and the CUDA
   build too when the machine has an NVIDIA GPU (install.cmd --gpu skips
   the question);
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
    if not unpack(zip_path, target):
        return None
    good(f"installed {target}")
    return target if (target / "llama-server.exe").is_file() else None


def unpack(zip_path: Path, target: Path) -> bool:
    """Unpack a llama.cpp release zip into target, then delete the zip."""
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
        return True
    except Exception as exc:
        fail(f"could not unpack {zip_path.name}: {exc}")
        return False
    finally:
        try:
            zip_path.unlink()
        except OSError:
            pass


def _powershell_json(script: str):
    code, out = run_cmd(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], timeout=60)
    if code != 0 or not out.strip():
        return None
    try:
        return json.loads(out.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return None


def has_nvidia_gpu() -> str:
    """The NVIDIA GPU name and memory, or an empty string."""
    code, out = run_cmd(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"], timeout=20)
    return out.strip().splitlines()[0] if code == 0 and out.strip() else ""


def model_size_gb() -> float:
    model = find_model()
    try:
        return model.stat().st_size / 1024 ** 3 if model else 4.4
    except OSError:
        return 4.4


def detect_hardware() -> dict:
    """What this PC has that matters for running the model.

    CPU, RAM, every GPU (with VRAM from nvidia-smi for NVIDIA cards, since
    Windows caps the reported adapter memory at 4 GB) and any NPU.
    """
    info = {"cpu": "", "cores": os.cpu_count() or 0, "threads": os.cpu_count() or 0,
            "ram_gb": 0.0, "gpus": [], "npus": []}
    data = _powershell_json(
        "$c=Get-CimInstance Win32_Processor|Select-Object -First 1 Name,NumberOfCores,NumberOfLogicalProcessors;"
        "$r=(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory;"
        "$g=@(Get-CimInstance Win32_VideoController|ForEach-Object{$_.Name});"
        "$n=@(Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue|"
        "Where-Object{$_.FriendlyName -match 'NPU|AI Boost|Neural'}|ForEach-Object{$_.FriendlyName});"
        "@{cpu=$c.Name;cores=$c.NumberOfCores;threads=$c.NumberOfLogicalProcessors;ram=$r;gpus=$g;npus=$n}"
        "|ConvertTo-Json -Compress"
    ) or {}
    info["cpu"] = (data.get("cpu") or "").strip()
    info["cores"] = int(data.get("cores") or info["cores"])
    info["threads"] = int(data.get("threads") or info["threads"])
    info["ram_gb"] = round(float(data.get("ram") or 0) / 1024 ** 3, 1)
    names = data.get("gpus") or []
    names = [names] if isinstance(names, str) else names
    nvidia = {}
    code, out = run_cmd(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"], timeout=20)
    if code == 0:
        for line in out.strip().splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) == 2 and parts[1].isdigit():
                nvidia[parts[0]] = round(int(parts[1]) / 1024, 1)
    for name in names:
        vendor = "nvidia" if "nvidia" in name.lower() else (
            "amd" if any(k in name.lower() for k in ("amd", "radeon")) else (
                "intel" if "intel" in name.lower() else "other"))
        if "basic display" in name.lower() or "remote" in name.lower():
            continue
        vram = next((v for k, v in nvidia.items() if k.lower() in name.lower() or name.lower() in k.lower()), 0.0)
        info["gpus"].append({"name": name, "vendor": vendor, "vram_gb": vram})
    for name, vram in nvidia.items():
        if not any(name.lower() in g["name"].lower() for g in info["gpus"]):
            info["gpus"].append({"name": name, "vendor": "nvidia", "vram_gb": vram})
    npus = data.get("npus") or []
    info["npus"] = [npus] if isinstance(npus, str) else npus
    return info


def choose_backend(hw: dict) -> tuple:
    """(backend, reason) - cuda, vulkan or cpu - for this hardware."""
    need = model_size_gb() + 1.5          # weights + KV cache + compute buffers
    for gpu in hw["gpus"]:
        if gpu["vendor"] == "nvidia":
            if gpu["vram_gb"] and gpu["vram_gb"] < need:
                return "cuda", f"{gpu['name']} has {gpu['vram_gb']} GB; the model needs ~{need:.1f} GB, so llama.cpp offloads what fits"
            return "cuda", f"{gpu['name']} ({gpu['vram_gb'] or '?'} GB) - every layer on the GPU"
    for gpu in hw["gpus"]:
        if gpu["vendor"] in ("amd", "intel") and "uhd" not in gpu["name"].lower():
            return "vulkan", f"{gpu['name']} - Vulkan build"
    return "cpu", "no usable GPU found - CPU build"


def print_hardware(hw: dict | None = None) -> dict:
    hw = hw or detect_hardware()
    step("this PC")
    good(f"CPU   {hw['cpu'] or '?'}  ({hw['cores']} cores, {hw['threads']} threads)")
    need = model_size_gb() + 2
    (good if hw["ram_gb"] >= need + 4 else warn)(f"RAM   {hw['ram_gb']} GB" + ("" if hw["ram_gb"] >= need + 4 else
        f" - tight for a {model_size_gb():.1f} GB model; a Q4_K_M model is lighter"))
    if hw["gpus"]:
        for gpu in hw["gpus"]:
            good(f"GPU   {gpu['name']}" + (f"  {gpu['vram_gb']} GB" if gpu["vram_gb"] else ""))
    else:
        warn("GPU   none found")
    for npu in hw["npus"]:
        good(f"NPU   {npu}  (llama.cpp has no NPU backend, so it is not used)")
    backend, reason = choose_backend(hw)
    good(f"best engine: {backend.upper()} - {reason}")
    return hw


def release_assets() -> list:
    url = f"https://api.github.com/repos/ggml-org/llama.cpp/releases/tags/{LLAMA_TAG}"
    request = urllib.request.Request(url, headers={"User-Agent": "spark-code26"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return [a["name"] for a in json.loads(response.read().decode("utf-8")).get("assets", [])]


def _cuda_version(name: str) -> tuple:
    part = name.split("cuda-", 1)[1].split("-x64", 1)[0]
    return tuple(int(x) for x in part.split(".") if x.isdigit())


def cuda_assets(assets: list | None = None) -> tuple:
    """(build zip, runtime zip) names of the newest CUDA build in the release.

    The names are read from the release itself instead of being guessed, and
    the newest CUDA version wins: an RTX 50 series card (Blackwell) needs
    CUDA 12.8 or later.
    """
    assets = release_assets() if assets is None else assets
    builds = [a for a in assets if a.startswith(f"llama-{LLAMA_TAG}-bin-win-cuda-") and a.endswith("-x64.zip")]
    if not builds:
        return "", ""
    build = max(builds, key=_cuda_version)
    wanted = _cuda_version(build)
    runtimes = [a for a in assets if a.startswith("cudart-") and "win-cuda-" in a and _cuda_version(a) == wanted]
    return build, (runtimes[0] if runtimes else "")


def vulkan_asset(assets: list | None = None) -> str:
    assets = release_assets() if assets is None else assets
    return next((a for a in assets if a == f"llama-{LLAMA_TAG}-bin-win-vulkan-x64.zip"), "")


BACKEND_DLL = {"cuda": "ggml-cuda.dll", "vulkan": "ggml-vulkan.dll"}


def install_accelerated(args: list, assume_yes: bool = False) -> Path | None:
    """Detect the hardware and install the llama.cpp build that fits it.

    NVIDIA -> CUDA build, other GPU -> Vulkan build, nothing -> the CPU build
    stays. Writes the chosen build into config.json. Returns the build folder.
    """
    hw = print_hardware()
    backend, _reason = choose_backend(hw)
    if backend == "cpu":
        good("staying on the CPU build")
        return install_llama()
    target = ROOT / "llama" / f"llama-{LLAMA_TAG}-{backend}"
    dll = BACKEND_DLL[backend]
    if (target / "llama-server.exe").is_file() and (target / dll).is_file():
        good(f"already there: {target}")
    else:
        if not (assume_yes or "--gpu" in args or "--yes" in args) and not ask(
            f"download the llama.cpp {backend.upper()} build? (several hundred MB)", default=True
        ):
            warn("skipped - the model stays on the CPU")
            return None
        try:
            assets = release_assets()
        except Exception as exc:
            warn(f"could not read the llama.cpp release ({exc}) - the model stays on the CPU")
            return None
        wanted = [a for a in (cuda_assets(assets) if backend == "cuda" else (vulkan_asset(assets),)) if a]
        if not wanted:
            warn(f"release {LLAMA_TAG} has no Windows {backend} build - the model stays on the CPU")
            return None
        base = f"https://github.com/ggml-org/llama.cpp/releases/download/{LLAMA_TAG}"
        for asset in wanted:
            zip_path = ROOT / "logs" / ".install" / asset
            if not download(f"{base}/{asset}", zip_path, asset) or not unpack(zip_path, target):
                return None
        if not (target / dll).is_file():
            warn(f"the {backend} build unpacked without {dll} - the model stays on the CPU")
            return None
        good(f"installed {target} ({', '.join(wanted)})")
    update_config(llama_dir=str(target))
    good("the agent uses it from the next start of the model")
    return target


def update_config(**values) -> None:
    """Merge values into config.json without dropping what is there."""
    path = ROOT / "config.json"
    try:
        config = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, json.JSONDecodeError):
        config = {}
    config.update({k: v for k, v in values.items() if v is not None})
    path.write_text(json.dumps(config, indent=2), encoding="utf-8")


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

    # The prompt box is checked against a fake console, so a redraw that
    # throws the caret out of the box fails the install too.
    step("running the prompt box test")
    code, out = run_cmd([sys.executable, str(HERE / "tui_test.py")], cwd=HERE, timeout=120)
    tail = [line for line in out.splitlines() if line.startswith("[FAIL]")]
    if code == 0:
        good("prompt box test passed")
    else:
        fail("prompt box test failed")
        for line in tail[:10]:
            print(f"    {line}")


def seed_remembered_project() -> None:
    """Carry the project across from an earlier install, if there is one.

    The agent remembers the project it was last used on in a file next to
    the code. A fresh install has none, so the one from a previous
    checkout is copied over when it exists.
    """
    target = ROOT / "last-project.txt"
    if target.exists():
        good(f"the remembered project is already set ({target.read_text(encoding='utf-8').strip()})")
        return
    for earlier in (Path(r"C:\models\spark26"),):
        source = earlier / "last-project.txt"
        if not source.is_file():
            continue
        try:
            value = source.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if not value:
            continue
        try:
            target.write_text(value + "\n", encoding="utf-8")
            good(f"remembered project carried over: {value.strip(chr(34))}")
            return
        except OSError:
            return


def repair(args: list, in_agent: bool = False) -> int:
    """Put a broken install back together without touching your work.

    Non-destructive by default: changed source files are only listed;
    `--restore` puts them back to the committed version.
    """
    problems = 0
    step("repairing the install")
    leftovers = list((ROOT / "logs" / ".install").glob("*.part")) if (ROOT / "logs" / ".install").is_dir() else []
    for part in leftovers:
        try:
            part.unlink()
        except OSError:
            pass
    good(f"removed {len(leftovers)} unfinished download(s)") if leftovers else good("no unfinished downloads")
    removed = 0
    for cache in ROOT.joinpath("harness").rglob("__pycache__"):
        shutil.rmtree(cache, ignore_errors=True)
        removed += 1
    good(f"cleared {removed} Python cache folder(s)")

    if not check_git():
        problems += 1
    else:
        code, out = run_cmd(["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT)
        changed = [line for line in out.splitlines() if line.strip()]
        if changed and "--restore" in args:
            run_cmd(["git", "checkout", "--", "."], cwd=ROOT)
            good(f"restored {len(changed)} changed file(s) to the committed version")
        elif changed:
            warn(f"{len(changed)} source file(s) differ from the committed version:")
            for line in changed[:10]:
                print(f"     {line}")
            print(paint("     /repair --restore puts them back (your changes are lost)", "dim"))

    config = {}
    try:
        config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        warn("config.json missing or unreadable - rebuilding it")
    llama = Path(config.get("llama_dir", "")) if config.get("llama_dir") else None
    if not llama or not (llama / "llama-server.exe").is_file():
        warn("the configured llama.cpp build is missing - reinstalling the CPU build")
        llama = install_llama()
        problems += 0 if llama else 1
    else:
        good(f"llama.cpp build ok: {llama}")
    model = Path(config.get("model", "")) if config.get("model") else None
    if not model or not model.is_file():
        model = find_model()
        if model:
            good(f"model found: {model}")
        else:
            fail("no model found - run: spark install --model")
            problems += 1
    else:
        good(f"model ok: {model}")
    update_config(model=str(model) if model else None, llama_dir=str(llama) if llama else None)
    if not in_agent:
        install_launcher()
        try:
            sys.path.insert(0, str(HERE))
            import winproc  # noqa: PLC0415
            note = winproc.kill_server_on_port(8082, str(llama or ""))
            if note:
                good(f"stopped a leftover llama-server: {note}")
        except Exception:
            pass
    run_selftest()
    print()
    (good if not problems else warn)("repair finished" + ("" if not problems else f" - {problems} problem(s) left"))
    return 0 if not problems else 1


def run(args: list) -> int:
    print()
    print(paint(" SPARK CODE 2.6 ", "bold") + paint(" installer", "dim"))
    print(paint(f" {ROOT}", "dim"))
    print()

    if not check_git():
        return 1
    llama = install_llama()
    accelerated = install_accelerated(args)
    if accelerated is not None:
        # The agent offloads to the GPU when the build has a GPU backend.
        llama = accelerated
    model = install_model("--model" in args)
    write_config(model, llama)
    seed_remembered_project()
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
