#!/usr/bin/env python3
"""
Terminal UI for the Spark Agent CLI.

The look is borrowed from PI.DEV (a footer with small dim stats on the left
and "provider model . thinking-level" on the right) and from Claude Code CLI
(an orange input box). The banner is the app title rendered in the ANSI
Shadow figlet font, boxed in violet.

Everything degrades: if the terminal cannot do ANSI (or the window is too
narrow for the title) the same information is printed as plain text.
"""
from __future__ import annotations

import os
import shutil
import sys

# --------------------------------------------------------------------------
# Palette - PI.DEV's dark theme, with Claude Code's orange input border
# --------------------------------------------------------------------------
COLOR = {
    "title": "\x1b[38;2;229;231;235m",
    "accent": "\x1b[38;2;167;139;250m",     # violet
    "border": "\x1b[38;2;217;119;87m",      # orange, Claude Code style
    "muted": "\x1b[38;2;156;163;175m",
    "dim": "\x1b[38;2;107;114;128m",
    "warning": "\x1b[38;2;245;224;90m",     # yellow
    "error": "\x1b[38;2;248;113;113m",
    "success": "\x1b[38;2;134;239;172m",
    "bold": "\x1b[1m",
}
RESET = "\x1b[0m"

ANSI_SHADOW = {
    "a": "███████╗██████╗  █████╗ ██████╗ ██╗  ██╗     ██████╗ ██████╗ ██████╗ ███████╗\n"
         "██╔════╝██╔══██╗██╔══██╗██╔══██╗██║ ██╔╝    ██╔════╝██╔═══██╗██╔══██╗██╔════╝\n"
         "███████╗██████╔╝███████║██████╔╝█████╔╝     ██║     ██║   ██║██║  ██║█████╗  \n"
         "╚════██║██╔═══╝ ██╔══██║██╔══██╗██╔═██╗     ██║     ██║   ██║██║  ██║██╔══╝  \n"
         "███████║██║     ██║  ██║██║  ██║██║  ██╗    ╚██████╗╚██████╔╝██████╔╝███████╗\n"
         "╚══════╝╚═╝     ╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═╝     ╚═════╝ ╚═════╝ ╚═════╝ ╚══════╝",
    "b": "██████╗     ██████╗ \n"
         "╚════██╗   ██╔════╝ \n"
         " █████╔╝   ███████╗ \n"
         "██╔═══╝    ██╔═══██╗\n"
         "███████╗██╗╚██████╔╝\n"
         "╚══════╝╚═╝ ╚═════╝ ",
}

TITLE = "SPARK CODE 2.6"


def enable_ansi() -> bool:
    """Turn on ANSI escape processing for the Windows console.

    Returns True when the terminal can render colour. On failure every
    helper below still works, it just prints without the escapes.
    """
    if not sys.stdout.isatty():
        return False
    if os.name != "nt":
        return True
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
        if mode.value & ENABLE_VIRTUAL_TERMINAL_PROCESSING:
            return True
        ok = kernel32.SetConsoleMode(
            handle, mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING
        )
        return bool(ok)
    except Exception:
        return False


class Theme:
    """Colour helper that knows whether the terminal can show colour."""

    def __init__(self, enabled: bool | None = None):
        self.enabled = enable_ansi() if enabled is None else bool(enabled)

    def paint(self, text: str, *styles: str) -> str:
        if not self.enabled or not styles:
            return text
        prefix = "".join(COLOR.get(name, "") for name in styles)
        return f"{prefix}{text}{RESET}" if prefix else text


THEME = Theme()


def term_width(default: int = 100) -> int:
    try:
        size = shutil.get_terminal_size((default, 24))
        return max(40, size.columns)
    except Exception:
        return default


def display_width(text: str) -> int:
    """Width of a string in terminal cells.

    The title is full of block and box-drawing characters, which are all
    single width, but the ANSI escapes in a coloured string are not.
    """
    plain = strip_ansi(text)
    width = 0
    for char in plain:
        # East Asian wide ranges count as two cells.
        code = ord(char)
        if (
            0x1100 <= code <= 0x115F
            or 0x2E80 <= code <= 0xA4CF
            or 0xAC00 <= code <= 0xD7A3
            or 0xF900 <= code <= 0xFAFF
            or 0xFE30 <= code <= 0xFE6F
            or 0xFF00 <= code <= 0xFF60
            or 0xFFE0 <= code <= 0xFFE6
        ):
            width += 2
        elif code == 0:
            continue
        else:
            width += 1
    return width


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences.

    A CSI sequence ends with a byte in the range 0x40-0x7E, so 'm' (the
    SGR terminator) has to count. Stopping only on uppercase letters would
    swallow the rest of the line.
    """
    out = []
    index = 0
    while index < len(text):
        char = text[index]
        if char == "\x1b":
            end = index + 1
            if end < len(text) and text[end] == "[":
                end += 1
                while end < len(text) and not (0x40 <= ord(text[end]) <= 0x7E):
                    end += 1
                end += 1
            index = end
            continue
        out.append(char)
        index += 1
    return "".join(out)


def pad(text: str, width: int) -> str:
    """Pad to an exact cell width, ignoring any ANSI escapes inside."""
    return text + " " * max(0, width - display_width(text))


def box(lines: list, width: int, color: str = "border", style: str = "rounded") -> list:
    """Wrap lines in a box and return the finished lines."""
    left, right = ("╭", "╮") if style == "rounded" else ("┌", "┐")
    bottom_left, bottom_right = ("╰", "╯") if style == "rounded" else ("└", "┘")
    theme = THEME
    top = theme.paint(left + "─" * (width + 2) + right, color)
    bottom = theme.paint(bottom_left + "─" * (width + 2) + bottom_right, color)
    body = []
    for line in lines:
        body.append(
            theme.paint("│", color) + " " + pad(line, width) + " " + theme.paint("│", color)
        )
    return [top] + body + [bottom]


def banner(width: int | None = None, theme: Theme | None = None) -> str:
    """The boxed app title.

    The title is the ANSI Shadow rendering of SPARK CODE on one line and
    2.6 centred underneath it, boxed in violet.
    """
    theme = theme or THEME
    top = ANSI_SHADOW["a"].split("\n")
    bottom = ANSI_SHADOW["b"].split("\n")
    title_width = max(display_width(line) for line in top)
    indent = " " * max(0, (title_width - display_width(bottom[0])) // 2)
    art = top + [indent + line for line in bottom]
    art_width = max(display_width(line) for line in art)

    available = width or term_width()
    if available < art_width + 4:
        # Too narrow to box it cleanly - print the art on its own.
        return "\n".join(art)

    inner = art_width + 2
    painted = []
    for line in art:
        painted.append(
            theme.paint("│", "accent")
            + " "
            + theme.paint(pad(line, art_width), "title")
            + " "
            + theme.paint("│", "accent")
        )
    return "\n".join(
        [
            theme.paint("╭" + "─" * inner + "╮", "accent"),
            *painted,
            theme.paint("╰" + "─" * inner + "╯", "accent"),
        ]
    )


def rule(width: int, label: str = "", theme: Theme | None = None) -> str:
    theme = theme or THEME
    if not label:
        return theme.paint("─" * width, "dim")
    text = f"── {label} "
    return theme.paint(text + "─" * max(0, width - display_width(text)), "dim")


# --------------------------------------------------------------------------
# The input box
# --------------------------------------------------------------------------
class PromptBox:
    """A Claude Code style input box with a live-editing cursor.

    On Windows the console is read one key at a time so the box can be
    redrawn while you type. Everywhere else (or when stdin is a pipe) the
    caller falls back to plain input().
    """

    def __init__(self, theme: Theme | None = None, width: int | None = None):
        self.theme = theme or THEME
        self.width = width or min(term_width(), 100)
        self.text = ""
        self.cursor = 0
        # Lines in the last drawn block (box plus footer) and the row the
        # cursor was left on inside it. The cursor is deliberately parked
        # inside the box, so the redraw has to walk back to the top of the
        # block from where the cursor is, not from the bottom of it.
        self._drawn = 0
        self._cursor_row = 0
        # A callable returning the footer lines drawn under the box.
        self.footer = lambda: []

    # -- geometry ---------------------------------------------------------
    def _inner(self) -> int:
        """Width of the text area inside the box."""
        return max(20, self.width - 4)

    def _rows(self) -> list:
        """The visible rows of the box as (text, offset) pairs.

        The offset is the position of the row's first character in the
        un-wrapped text, so the caret can be placed exactly even when the
        text has wrapped.
        """
        # Two cells of every row belong to the "> " marker, so the text
        # wraps two cells earlier. pad() never truncates, so a full row
        # would otherwise push the right hand border out past the top and
        # bottom borders.
        inner = max(1, self._inner() - 2)
        return self._wrap(self.text, inner) or [("", 0)]

    @staticmethod
    def _wrap(text: str, inner: int) -> list:
        """Wrap text into rows of at most `inner` characters.

        Returns [(row_text, offset), ...] where offset is the index of the
        row's first character in `text`. A word is broken at the last
        space that fits and the space is dropped, which is exactly why the
        offsets are tracked instead of recomputed.
        """
        rows: list = []
        if not text:
            return rows
        position = 0
        for paragraph in text.split("\n"):
            start = 0
            if paragraph:
                while start < len(paragraph):
                    end = min(start + inner, len(paragraph))
                    if end < len(paragraph):
                        space = paragraph.rfind(" ", start + 1, end)
                        if space > start:
                            end = space
                    rows.append((paragraph[start:end], position + start))
                    start = end
                    if start < len(paragraph) and paragraph[start] == " ":
                        start += 1
            else:
                rows.append(("", position))
            position += len(paragraph) + 1
        return rows

    def _cursor_position(self, rows: list) -> tuple:
        """(row index, column offset) of the caret inside the wrapped rows."""
        for index, (row, offset) in enumerate(rows):
            if self.cursor <= offset + len(row):
                return index, self.cursor - offset
        return max(0, len(rows) - 1), 0

    # -- painting ---------------------------------------------------------
    def paint(self) -> list:
        theme = self.theme
        rows = self._rows()
        inner = self._inner()
        body = []
        for row, _offset in rows:
            body.append(theme.paint(">", "accent") + " " + theme.paint(row, "title"))
        if not body:
            body = [theme.paint(">", "accent") + " "]
        return box(body, inner, color="border")

    def draw(self):
        """Redraw the box (and the footer) with the caret inside it.

        Everything is done with *relative* cursor moves. An absolute move
        would have to know where the block starts on the screen, and the
        block moves as the conversation grows - which is what used to
        throw the caret into the banner.
        """
        lines = self.paint()
        footer = list(self.footer() or [])
        block = lines + footer
        out = sys.stdout
        if self._drawn:
            # The caret is parked inside the box, on content row
            # self._cursor_row. The block starts one line above content
            # row 0, so walk up exactly that far. Walking up the whole
            # block height instead overshoots into whatever is above the
            # box and the erase below wipes it.
            out.write(f"\x1b[{self._cursor_row + 1}A")
        # Column one, and wipe everything from here down so the previous
        # drawing cannot survive underneath the new one.
        out.write("\r\x1b[J")
        for line in block:
            # Explicit CRLF: a bare LF leaves the column where the line
            # ended, and every relative move below counts on the caret
            # being at column one.
            out.write(line + "\r\n")
        rows = self._rows()
        row, column = self._cursor_position(rows)
        # The caret is now one line below the whole block. Walk it back up
        # to the content row it belongs on, then across to just after the
        # "| > " prefix.
        up = len(block) - 1 - row
        if up > 0:
            out.write(f"\x1b[{up}A")
        out.write(f"\x1b[{4 + column}C")
        out.flush()
        self._drawn = len(block)
        self._cursor_row = row

    def finish(self):
        """Leave the finished box on screen and put the caret below it."""
        out = sys.stdout
        if self._drawn:
            # The caret is inside the box. Walk it down to the line under
            # the whole block, so the answer does not land on top of the
            # box or the footer.
            down = self._drawn - self._cursor_row - 1
            if down > 0:
                out.write(f"\x1b[{down}B")
            out.write("\r")
        out.flush()
        self._drawn = 0
        self._cursor_row = 0

    # -- editing ----------------------------------------------------------
    def insert(self, char: str):
        self.text = self.text[: self.cursor] + char + self.text[self.cursor:]
        self.cursor += len(char)

    def backspace(self):
        if self.cursor > 0:
            self.text = self.text[: self.cursor - 1] + self.text[self.cursor:]
            self.cursor -= 1

    def delete(self):
        if self.cursor < len(self.text):
            self.text = self.text[: self.cursor] + self.text[self.cursor + 1:]

    def move(self, delta: int):
        self.cursor = max(0, min(len(self.text), self.cursor + delta))

    def kill_line(self):
        self.text = self.text[self.cursor:]
        self.cursor = 0

    def kill_word(self):
        head = self.text[: self.cursor].rstrip()
        cut = head.rfind(" ") + 1
        self.text = self.text[:cut] + self.text[self.cursor:]
        self.cursor = cut

    def clear(self):
        self.text = ""
        self.cursor = 0


def read_prompt(prompt_box: PromptBox, history: list | None = None) -> str:
    """Read one instruction, with a live box when the console allows it.

    Returns the text, or raises KeyboardInterrupt when the user aborts.
    """
    try:
        import msvcrt  # noqa: PLC0415  (Windows only)
    except ImportError:
        return _plain_prompt(prompt_box)

    if not sys.stdin.isatty():
        return _plain_prompt(prompt_box)
    if not prompt_box.theme.enabled:
        # The console cannot do ANSI, so the cursor moves would print as
        # garbage. Fall back to a plain prompt.
        return _plain_prompt(prompt_box)

    box = prompt_box
    history = history or []
    hist_index = len(history)
    box.draw()

    while True:
        try:
            char = msvcrt.getwch()
        except KeyboardInterrupt:
            raise
        if char in ("\r", "\n"):
            box.finish()
            text = box.text.strip().lstrip("\ufeff").strip()
            if text and history and history[-1] != text:
                history.append(text)
            # The submitted instruction stays on screen, but the next one
            # starts empty - otherwise it would be typed onto the end of
            # the last one.
            box.clear()
            return text
        if char == "\x03":          # Ctrl+C
            box.finish()
            raise KeyboardInterrupt
        if char == "\x1b":          # escape sequence
            nxt = msvcrt.getwch()
            if nxt == "[":
                code = msvcrt.getwch()
                if code == "D":
                    box.move(-1)
                elif code == "C":
                    box.move(1)
                elif code == "H":
                    box.cursor = 0
                elif code == "F":
                    box.cursor = len(box.text)
                elif code in ("A", "B"):
                    if history:
                        if code == "A":
                            hist_index = max(0, hist_index - 1)
                        else:
                            hist_index = min(len(history), hist_index + 1)
                        if hist_index < len(history):
                            box.text = history[hist_index]
                            box.cursor = len(box.text)
                        else:
                            box.clear()
                box.draw()
                continue
            box.draw()
            continue
        if char == "\x08":          # backspace
            box.backspace()
        elif char == "\x00":        # control char prefix (some keys)
            msvcrt.getwch()
            continue
        elif char == "\x15":        # Ctrl+U
            box.kill_line()
        elif char == "\x17":        # Ctrl+W
            box.kill_word()
        elif char == "\x04":        # Ctrl+D
            if not box.text:
                box.finish()
                raise EOFError
            box.delete()
        elif char == "\x12":        # Ctrl+R - not bound, ignore
            continue
        elif ord(char) < 32:
            continue
        else:
            box.insert(char)
        box.draw()


def _plain_prompt(prompt_box: PromptBox) -> str:
    """Fallback when the console cannot do key-at-a-time input."""
    lines = prompt_box.paint()
    sys.stdout.write(lines[0] + "\n")
    try:
        text = input()
    except EOFError:
        raise
    sys.stdout.write(lines[-1] + "\n")
    for line in prompt_box.footer() or []:
        sys.stdout.write(line + "\n")
    # A PowerShell pipe writes UTF-8 with a BOM, so the first line would
    # otherwise start with an invisible character.
    return text.strip().lstrip("\ufeff").strip()


# --------------------------------------------------------------------------
# The status line under the prompt
# --------------------------------------------------------------------------
def format_tokens(count: int) -> str:
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    if count >= 1_000:
        return f"{count / 1_000:.1f}k"
    return str(count)


def status_line(
    width: int,
    stats: dict,
    model: str,
    reasoning: str,
    theme: Theme | None = None,
    provider: str = "local",
) -> str:
    """Left: small dim stats. Right: provider, model and thinking level.

    This is the PI.DEV footer shape: context use turns yellow past 70% and
    red past 90%.
    """
    theme = theme or THEME
    left_parts = []
    if stats.get("tps"):
        left_parts.append(f"{stats['tps']:.1f} t/s")
    if stats.get("prompt_tokens") is not None:
        left_parts.append(f"↑{format_tokens(stats['prompt_tokens'])}")
    if stats.get("output_tokens") is not None:
        left_parts.append(f"↓{format_tokens(stats['output_tokens'])}")
    if stats.get("ctx_tokens") is not None and stats.get("ctx_window"):
        percent = 100.0 * stats["ctx_tokens"] / max(1, stats["ctx_window"])
        chunk = f"ctx {percent:.0f}% ({format_tokens(stats['ctx_tokens'])}/{format_tokens(stats['ctx_window'])})"
        if percent >= 90:
            left_parts.append(theme.paint(chunk, "error"))
        elif percent >= 70:
            left_parts.append(theme.paint(chunk, "warning"))
        else:
            left_parts.append(theme.paint(chunk, "dim"))
    if stats.get("tools") is not None:
        left_parts.append(theme.paint(f"{stats['tools']} tools", "dim"))
    if stats.get("turns") is not None:
        left_parts.append(theme.paint(f"turn {stats['turns']}", "dim"))

    left = " · ".join(left_parts)
    right = f"{provider} {model} · {reasoning}"
    gap = max(1, width - display_width(left) - display_width(right))
    return left + " " * gap + theme.paint(right, "accent")


def context_line(width: int, workspace: str, branch: str, session: str,
                 theme: Theme | None = None) -> str:
    """The line above the status line: where you are."""
    theme = theme or THEME
    left = theme.paint(workspace, "muted")
    if branch:
        left += theme.paint(f" ({branch})", "dim")
    right = theme.paint(f"session {session}", "dim")
    gap = max(1, width - display_width(left) - display_width(right))
    return left + " " * gap + right
