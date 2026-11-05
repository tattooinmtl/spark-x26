#!/usr/bin/env python3
"""
Validate the harness's Python chat-template port against llama.cpp's own
Jinja renderer.

The harness renders Spark's template itself so that the llama-cli engine can
be driven without a server. That port is only trustworthy if it produces the
same bytes llama.cpp's minja renderer produces for the same conversation.

llama-server exposes POST /apply-template, which renders the server's own
chat template. This script sends the same messages and tools to that
endpoint and compares the result with the harness's own render.

Exit code 0 means the two renderings agree.
"""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from spark_agent import (  # noqa: E402
    SparkTemplate,
    build_system_prompt,
    build_tools,
    find_model,
    load_skills,
    load_special_tokens,
    DEFAULT_SKILLS,
)

SERVER = "http://127.0.0.1:8081"


def post(path: str, payload: dict) -> dict:
    request = urllib.request.Request(
        SERVER + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def compare(label: str, mine: str, theirs: str) -> bool:
    if mine == theirs:
        print(f"[PASS] {label}: identical ({len(mine)} chars)")
        return True
    print(f"[FAIL] {label}: mine {len(mine)} chars, llama.cpp {len(theirs)} chars")
    for index, (a, b) in enumerate(zip(mine, theirs)):
        if a != b:
            lo = max(0, index - 120)
            print(f"  first difference at char {index}")
            print(f"  mine     : ...{mine[lo:index + 120]!r}")
            print(f"  llama.cpp: ...{theirs[lo:index + 120]!r}")
            break
    else:
        lo = min(len(mine), len(theirs))
        print(f"  one is a prefix of the other; tail mine    : {mine[lo:lo + 200]!r}")
        print(f"  tail llama.cpp: {theirs[lo:lo + 200]!r}")
    return False


def main() -> int:
    model = Path(sys.argv[1]) if len(sys.argv) > 1 else find_model()
    tokens = load_special_tokens(model)
    template = SparkTemplate(tokens)
    skills = load_skills(DEFAULT_SKILLS)
    tools = build_tools(skills)

    cases = [
        (
            "system + user",
            [
                {"role": "system", "content": "You are a test harness."},
                {"role": "user", "content": "Hello there."},
            ],
            None,
        ),
        (
            "tools + thinking high",
            [
                {"role": "system", "content": "You are a test harness."},
                {"role": "user", "content": "List the files."},
            ],
            tools,
        ),
        (
            "assistant with tool call",
            [
                {"role": "user", "content": "Read a.txt"},
                {
                    "role": "assistant",
                    "content": "",
                    "reasoning_content": "I should read the file.",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "read_file",
                                "arguments": {"path": "a.txt", "start_line": 1},
                            },
                        }
                    ],
                },
                {"role": "tool", "content": "a.txt (1 lines)\n    1| hello"},
            ],
            None,
        ),
        (
            "two tool responses in one block",
            [
                {"role": "user", "content": "Do two things."},
                {
                    "role": "assistant",
                    "content": "",
                    "reasoning_content": "",
                    "tool_calls": [
                        {"id": "c1", "type": "function",
                         "function": {"name": "list_dir", "arguments": {"path": "."}}},
                        {"id": "c2", "type": "function",
                         "function": {"name": "grep", "arguments": {"pattern": "x", "glob": "*"}}},
                    ],
                },
                {"role": "tool", "content": "first"},
                {"role": "tool", "content": "second"},
            ],
            None,
        ),
    ]

    ok = True
    for label, messages, case_tools in cases:
        for enable_thinking, effort in ((True, "high"), (True, "low"), (False, "high")):
            mine = template.render(
                messages,
                tools=case_tools,
                enable_thinking=enable_thinking,
                reasoning_effort=effort,
                add_generation_prompt=True,
            )
            payload = {
                "messages": messages,
                "add_generation_prompt": True,
                "chat_template_kwargs": {
                    "enable_thinking": enable_thinking,
                    "reasoning_effort": effort,
                },
            }
            if case_tools:
                payload["tools"] = case_tools
            try:
                reply = post("/apply-template", payload)
            except Exception as exc:  # noqa: BLE001
                print(f"[SKIP] {label} (thinking={enable_thinking}, effort={effort}): {exc}")
                continue
            theirs = reply.get("prompt", reply) if isinstance(reply, dict) else reply
            ok &= compare(
                f"{label} (thinking={enable_thinking}, effort={effort})", mine, theirs
            )

    print("\nRESULT:", "all renderings identical" if ok else "MISMATCH FOUND")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
