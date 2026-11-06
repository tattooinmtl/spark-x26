#!/usr/bin/env python3
"""
A fake console for the prompt box.

It interprets the escape sequences the box writes into a screen buffer, so
the redraw can be checked without a real terminal: where the caret ends up,
what the screen looks like after typing, and what happens when Enter is
pressed.

The emulator is deliberately strict. A bare LF only moves down (it does not
return to column one), exactly like a real terminal, and absolute cursor
moves are counted so the box cannot quietly jump somewhere else on the
screen.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import tui  # noqa: E402


class FakeScreen:
    """Just enough of a terminal to check the redraw."""

    def __init__(self, width: int = 100, height: int = 40):
        self.width = width
        self.height = height
        self.grid = [[" "] * width for _ in range(height)]
        self.row = 1
        self.col = 1
        self.absolute_moves = 0
        self.text = ""

    # -- writing ----------------------------------------------------------
    def write(self, text: str) -> int:
        self.text += text
        # Python's text mode on Windows turns every LF into a CRLF and
        # leaves a lone CR alone. The emulator is fed the same bytes the
        # console would get.
        text = text.replace("\n", "\r\n")
        index = 0
        while index < len(text):
            char = text[index]
            if char == "\x1b":
                match = re.match(r"\x1b\[([0-9;?]*)([@-~])", text[index:])
                if match:
                    self._escape(match.group(1), match.group(2))
                    index += match.end()
                    continue
                index += 1
                continue
            if char == "\r":
                self.col = 1
            elif char == "\n":
                self.row += 1
                self._scroll()
            elif char == "\x08":
                self.col = max(1, self.col - 1)
            else:
                self._put(char)
            index += 1
        return len(text)

    def flush(self):
        pass

    def isatty(self):
        return True

    def _put(self, char: str):
        if self.col > self.width:
            self.col = 1
            self.row += 1
            self._scroll()
        self.grid[self.row - 1][self.col - 1] = char
        self.col += 1

    def _scroll(self):
        while self.row > self.height:
            self.grid.pop(0)
            self.grid.append([" "] * self.width)
            self.row -= 1

    def _escape(self, params: str, final: str) -> None:
        values = [int(p) for p in params.split(";") if p.isdigit()]
        count = values[0] if values else 1
        if final == "A":
            self.row = max(1, self.row - count)
        elif final == "B":
            self.row = min(self.height, self.row + count)
        elif final == "C":
            self.col = min(self.width, self.col + count)
        elif final == "D":
            self.col = max(1, self.col - count)
        elif final == "H":
            # An absolute move. The box must never use one: it cannot
            # know where the block sits on the screen.
            self.absolute_moves += 1
            self.row = min(self.height, max(1, values[0] if values else 1))
            self.col = min(self.width, max(1, values[1] if len(values) > 1 else 1))
        elif final == "J":
            # Erase from the caret to the end of the screen.
            for row in range(self.row - 1, self.height):
                if row == self.row - 1:
                    for col in range(self.col - 1, self.width):
                        self.grid[row][col] = " "
                else:
                    self.grid[row] = [" "] * self.width
        elif final == "K":
            for col in range(self.col - 1, self.width):
                self.grid[self.row - 1][col] = " "
        # SGR and the rest are ignored.

    # -- inspection --------------------------------------------------------
    def lines(self) -> list:
        return ["".join(row).rstrip() for row in self.grid]

    def line(self, index: int) -> str:
        rows = self.lines()
        return rows[index] if 0 <= index < len(rows) else ""

    def screen(self) -> str:
        return "\n".join(self.lines())


def report(label, got, want, failures: list):
    """Print a check result.

    The report goes to the real stdout: the fake screen has taken over
    sys.stdout while a box is being driven.
    """
    if got == want:
        sys.__stdout__.write(f"[PASS] {label}\n")
    else:
        sys.__stdout__.write(f"[FAIL] {label}\n  got : {got!r}\n  want: {want!r}\n")
        failures.append(label)


class BoxDriver:
    """Drives a PromptBox against the fake screen."""
    def __init__(self, width: int = 100, marker: str | None = None):
        self.screen = FakeScreen(width=width)
        self.box = tui.PromptBox(tui.Theme(enabled=True), width)
        self.box.footer = lambda: ["FOOTER-ONE", "FOOTER-TWO"]
        self.saved_stdout = sys.stdout
        sys.stdout = self.screen
        if marker:
            self.screen.write(marker + "\r\n")
        self.top = self.screen.row
        self.box.draw()

    def type(self, text: str):
        for char in text:
            self.box.insert(char)
        self.box.draw()

    def enter(self):
        self.box.finish()

    def restore(self):
        sys.stdout = self.saved_stdout


def test_draw_and_type():
    """The box is drawn, the caret is in it, and it follows the text."""
    failures = []

    def check(label, got, want):
        report(label, got, want, failures)

    driver = BoxDriver()
    screen = driver.screen
    lines = screen.lines()

    check("tui: the box is on screen", "╭" in screen.screen(), True)
    check("tui: the footer is under the box", "FOOTER-TWO" in screen.screen(), True)
    check(
        "tui: the caret sits just after the marker",
        (screen.row, screen.col),
        (driver.top + 1, 5),
    )
    check("tui: the box starts on the line under the caret", driver.top, 1)
    check(
        "tui: the box top is the first line",
        lines[0].startswith("╭"),
        True,
    )
    check(
        "tui: the caret is on the content row",
        lines[screen.row - 1].startswith("│ >"),
        True,
    )
    check("tui: no absolute cursor moves", screen.absolute_moves, 0)

    # Type something and redraw.
    driver.type("hello")
    lines = screen.lines()
    check("tui: the text is inside the box", "│ > hello" in lines[1], True)
    check(
        "tui: the caret follows the text",
        (screen.row, screen.col),
        (2, 10),
    )
    check("tui: the redraw keeps one box", screen.screen().count("╭"), 1)
    check("tui: the footer survives the redraw", "FOOTER-TWO" in lines, True)

    # A second redraw must not move the block.
    driver.type(" there")
    lines = screen.lines()
    check("tui: the redraw keeps one box", screen.screen().count("╭"), 1)
    check("tui: the redraw keeps the text", "│ > hello there" in lines[1], True)
    check(
        "tui: the redraw keeps the caret on the text",
        (screen.row, screen.col),
        (2, 16),
    )
    check("tui: the footer is still there", "FOOTER-TWO" in lines, True)

    # Backspace and redraw.
    driver.box.backspace()
    driver.box.draw()
    check("tui: backspace redraws", "│ > hello ther" in screen.lines()[1], True)
    check(
        "tui: backspace moves the caret back",
        (screen.row, screen.col),
        (2, 15),
    )

    driver.restore()
    return failures


def test_enter():
    """Enter leaves the block alone and the answer lands below it."""
    failures = []

    def check(label, got, want):
        report(label, got, want, failures)

    driver = BoxDriver()
    screen = driver.screen
    driver.type("hello")
    driver.enter()

    lines = screen.lines()
    check("tui: enter keeps the box", screen.screen().count("╭"), 1)
    check("tui: enter keeps the footer", "FOOTER-TWO" in lines, True)
    check("tui: the caret is below the block", screen.row, 6)
    check("tui: the caret is at the left edge", screen.col, 1)

    # Something printed after Enter has to land on a clean line.
    sys.stdout.write("the answer\n")
    lines = screen.lines()
    check("tui: the answer is on its own line", "the answer" in lines, True)
    check(
        "tui: the answer is below the footer",
        lines.index("the answer") > lines.index("FOOTER-TWO"),
        True,
    )
    check(
        "tui: the answer did not overwrite the footer",
        lines[lines.index("FOOTER-TWO")] == "FOOTER-TWO",
        True,
    )
    check("tui: enter uses no absolute moves", screen.absolute_moves, 0)

    driver.restore()
    return failures


def test_banner_survives():
    """Whatever is above the box must not be wiped by a redraw."""
    failures = []

    def check(label, got, want):
        report(label, got, want, failures)

    driver = BoxDriver(marker="BANNER-MARKER")
    screen = driver.screen
    check("tui: the marker is above the box", screen.line(0), "BANNER-MARKER")

    driver.type("hello")
    driver.type(" world")
    lines = screen.lines()
    check("tui: the marker survives the redraw", lines[0], "BANNER-MARKER")
    check("tui: the box is under the marker", lines[1].startswith("╭"), True)
    check("tui: the text is in the box", "│ > hello world" in lines[2], True)
    check("tui: still one box", screen.screen().count("╭"), 1)
    check(
        "tui: the caret is on the content row",
        (screen.row, screen.col),
        (3, 5 + len("hello world")),
    )

    driver.restore()
    return failures


def test_wrapping():
    """A long text wraps, keeps the borders aligned and the caret in place."""
    failures = []

    def check(label, got, want):
        report(label, got, want, failures)

    driver = BoxDriver(width=60)
    screen = driver.screen
    driver.type("x" * 200)
    lines = screen.lines()

    content = [line for line in lines if line.startswith("│ >")]
    check("tui: a long text wraps", len(content) >= 2, True)

    widths = {tui.display_width(line) for line in lines if line.startswith(("│", "╭", "╰"))}
    check("tui: every box row is the same width", len(widths), 1)
    check("tui: the box is 60 wide", widths.pop(), 60)

    last = content[-1]
    check(
        "tui: the caret is on the last row of the text",
        screen.row,
        lines.index(last) + 1,
    )
    # The visible text of that row, without the border and the padding.
    body = last[4:-1].rstrip()
    check(
        "tui: the caret is at the end of the text",
        screen.col,
        5 + len(body),
    )

    driver.restore()
    return failures


def test_read_prompt():
    """The real key handling path, with a scripted keyboard."""
    failures = []

    def check(label, got, want):
        report(label, got, want, failures)

    msvcrt = fake_keyboard()

    keys = list("hello\r")
    saved_getwch = msvcrt.getwch
    saved_kbhit = getattr(msvcrt, "kbhit", None)
    saved_stdin = sys.stdin
    msvcrt.getwch = lambda: keys.pop(0) if keys else ""
    msvcrt.kbhit = lambda: bool(keys)

    class FakeStdin:
        def isatty(self):
            return True

        def readline(self):
            return ""

    sys.stdin = FakeStdin()
    driver = BoxDriver()
    screen = driver.screen
    try:
        text = tui.read_prompt(driver.box, [])

        check("tui: read_prompt returns the text", text, "hello")
        check("tui: the typed text is in the box",
              "│ > hello" in screen.lines()[1], True)
        check("tui: the box is emptied for the next prompt", driver.box.text, "")
        check("tui: the caret is below the box", screen.row, 6)
        check("tui: the caret is at the left edge", screen.col, 1)
        check("tui: read_prompt uses no absolute moves", screen.absolute_moves, 0)

        # The next prompt starts empty, so a new instruction is not typed
        # onto the end of the last one. The submitted box stays on screen
        # and the new one is drawn below it.
        driver.box.draw()
        lines = screen.lines()
        check("tui: the submitted prompt stays on screen",
              "│ > hello" in lines[1], True)
        check("tui: the next prompt is empty", lines[6][4:-1].strip(), "")
        check("tui: the next box is below the first", lines[5].startswith("╭"), True)
        check("tui: there are two boxes", screen.screen().count("╭"), 2)
    finally:
        msvcrt.getwch = saved_getwch
        if saved_kbhit is not None:
            msvcrt.kbhit = saved_kbhit
        sys.stdin = saved_stdin
        driver.restore()

    return failures


def fake_keyboard():
    """msvcrt, or a stand-in module where it does not exist (not Windows)."""
    try:
        import msvcrt  # noqa: PLC0415  (Windows only)
        return msvcrt
    except ImportError:
        import types  # noqa: PLC0415

        module = types.ModuleType("msvcrt")
        module.getwch = lambda: ""
        module.kbhit = lambda: False
        sys.modules["msvcrt"] = module
        return module


def test_keys():
    """Arrows, history, paste, Esc - as the Windows console sends them."""
    failures = []

    def check(label, got, want):
        report(label, got, want, failures)

    msvcrt = fake_keyboard()
    saved = (msvcrt.getwch, getattr(msvcrt, "kbhit", None), sys.stdin)

    class FakeStdin:
        def isatty(self):
            return True

    def run(keys, history=None):
        queue = list(keys)
        msvcrt.getwch = lambda: queue.pop(0) if queue else "\r"
        msvcrt.kbhit = lambda: bool(queue)
        msvcrt.ungetwch = lambda char: queue.insert(0, char)
        driver = BoxDriver()
        try:
            return tui.read_prompt(driver.box, history), driver
        finally:
            driver.restore()

    sys.stdin = FakeStdin()
    try:
        # Left arrow twice then "X": arrows arrive as "\xe0" + "K".
        text, _ = run(["a", "b", "c", "\xe0", "K", "\xe0", "K", "X", "\r"])
        check("keys: left arrow moves the caret (no a-grave typed)", text, "aXbc")
        text, _ = run(["a", "b", "\x00", "G", "X", "\xe0", "O", "Y", "\r"])
        check("keys: Home and End", text, "XabY")
        text, _ = run(["a", "b", "c", "\xe0", "G", "\xe0", "S", "\r"])
        check("keys: Delete", text, "bc")
        history = []
        run(list("first") + ["\r"], history)
        run(list("second") + ["\r"], history)
        check("keys: history records every instruction", history, ["first", "second"])
        text, _ = run(["\xe0", "H", "\xe0", "H", "\r"], history)
        check("keys: Up recalls the history", text, "first")
        text, _ = run(["d", "r", "\xe0", "H", "\xe0", "P", "\r"], history)
        check("keys: Down returns to the draft", text, "dr")
        text, _ = run(list("one") + ["\r"] + list("two") + ["\r"])
        check("keys: a pasted block keeps its lines", text, "one\ntwo")
        text, _ = run(["a", "\n", "b", "\r"])
        check("keys: Ctrl+J adds a new line", text, "a\nb")
        text, _ = run(["x", "y", "\x1b", "o", "k", "\r"])
        check("keys: Esc clears the box and keeps the next key", text, "ok")
        text, _ = run(["a", "b", "\x7f", "c", "\r"])
        check("keys: Ctrl+Backspace types nothing", "\x7f" in text, False)
    finally:
        msvcrt.getwch = saved[0]
        if saved[1] is not None:
            msvcrt.kbhit = saved[1]
        sys.stdin = saved[2]
    return failures


def main() -> int:
    failures = []
    for test in (
        test_draw_and_type,
        test_enter,
        test_banner_survives,
        test_wrapping,
        test_read_prompt,
        test_keys,
    ):
        print(f"--- {test.__name__}")
        failures.extend(test())
        print()

    if failures:
        print(f"FAILED: {len(failures)} check(s): {', '.join(failures)}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    # The console is cp1252 by default and the box is full of box drawing
    # characters, so the report would not survive it.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.exit(main())
