"""Condense a traceback down to the part that explains the failure.

A real example that motivated this. A `MissingGreenlet` alert arrived with the
top two thirds occupied by starlette/anyio/sqlalchemy internals — task-group
plumbing, `_execute_context`, `_exec_single_context`, `do_execute` — while the
line that actually says what to do,

    greenlet_spawn has not been called; can't call await_only() here

was cut off the bottom by the 4096-character limit. The alert was long,
detailed, and useless.

Two rules follow from that:

* the **tail is sacred** — the exception type and its message are the point,
  and must never be what gets dropped;
* **library frames are the filler** — a frame inside site-packages rarely
  tells you anything you could act on, while every application frame does.

So this drops library frames from the middle, keeps every application frame,
always keeps the final exception block, and says how many frames it elided
rather than pretending it showed everything.
"""

from __future__ import annotations

import re

#: A frame header line: `  File "...", line N, in name`.
_FRAME_RE = re.compile(r'^\s*File "([^"]+)", line (\d+), in (.+)$')

#: Paths that mean "not our code".
_LIBRARY_MARKERS = (
    "/site-packages/",
    "/dist-packages/",
    "/lib/python",
    "/venv/",
    "\\site-packages\\",
)

#: Never elide fewer than this many trailing lines — the exception and its
#: message live here.
_TAIL_KEEP_LINES = 12


def _is_library(path: str) -> bool:
    return any(marker in path for marker in _LIBRARY_MARKERS)


def _split_frames(lines: list[str]) -> list[tuple[int, int, str | None]]:
    """Group lines into (start, end, path) blocks; path is None for non-frames."""
    blocks: list[tuple[int, int, str | None]] = []
    current_start = 0
    current_path: str | None = None
    started = False

    for index, line in enumerate(lines):
        match = _FRAME_RE.match(line)
        if match:
            if started:
                blocks.append((current_start, index, current_path))
            current_start, current_path, started = index, match.group(1), True
    if started:
        blocks.append((current_start, len(lines), current_path))
    elif lines:
        blocks.append((0, len(lines), None))

    # Anything before the first frame (the "Traceback (most recent call last):"
    # header, exception-group markers) is its own block.
    if blocks and blocks[0][0] > 0:
        blocks.insert(0, (0, blocks[0][0], None))
    return blocks


def condense(text: str, max_chars: int, keep_library: bool = False) -> str:
    """Shrink `text` to fit `max_chars`, dropping library frames first.

    Returns the original when it already fits. Never returns more than
    `max_chars`; if even the tail alone is too long, the tail is cut — losing
    the beginning of the message is far better than losing its end.
    """
    if not text:
        return text
    if len(text) <= max_chars:
        return text

    lines = text.splitlines()

    # 1. Drop library frames from the middle, keeping application frames.
    if not keep_library:
        blocks = _split_frames(lines)
        tail_starts_at = max(0, len(lines) - _TAIL_KEEP_LINES)

        kept: list[str] = []
        elided = 0
        for start, end, path in blocks:
            in_tail = end > tail_starts_at
            if path and _is_library(path) and not in_tail:
                elided += 1
                continue
            kept.extend(lines[start:end])

        if elided:
            marker = f"  ... {elided} library frame(s) hidden ..."
            # Insert after the "Traceback (...)" header when there is one.
            insert_at = 1 if kept and kept[0].lstrip().startswith("Traceback") else 0
            kept.insert(insert_at, marker)
        lines = kept

    condensed = "\n".join(lines)
    if len(condensed) <= max_chars:
        return condensed

    # 2. Still too long: keep the tail, which holds the exception.
    tail = "\n".join(lines[-_TAIL_KEEP_LINES:])
    if len(tail) >= max_chars:
        return tail[-max_chars:]

    head_budget = max_chars - len(tail) - len(_ELISION)
    head = condensed[:head_budget] if head_budget > 0 else ""
    return f"{head}{_ELISION}{tail}"


_ELISION = "\n... middle of traceback omitted ...\n"


def exception_summary(text: str) -> str:
    """The last non-empty line — normally `SomeError: what went wrong`.

    Surfaced near the top of an alert so the cause is readable even when the
    traceback below it had to be shortened.
    """
    for line in reversed(text.splitlines()):
        stripped = line.strip()
        if stripped and not _FRAME_RE.match(line) and not stripped.startswith("^"):
            return stripped
    return ""


def chunk(text: str, size: int) -> list[str]:
    """Split into `size`-char pieces on line boundaries where possible."""
    if len(text) <= size:
        return [text]
    pieces: list[str] = []
    remaining = text
    while len(remaining) > size:
        cut = remaining.rfind("\n", 0, size)
        if cut <= 0:
            cut = size
        pieces.append(remaining[:cut])
        remaining = remaining[cut:].lstrip("\n")
    if remaining:
        pieces.append(remaining)
    return pieces
