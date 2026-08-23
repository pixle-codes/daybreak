"""prune — journal decay: archive stale Completed entries.

The third memory pillar after digest (retrieval) and dupes (hygiene):
journals only ever grow, so session-start cost grows with them. prune
moves Completed-section entries older than a retention window into a
sibling archive file, byte-spliced so every other section is untouched.

Safety model (pinned by tests):
- Only blocks inside the `## Completed` section can move.
- A block is one top-level `- ` bullet plus its continuation lines up to
  the next bullet or heading (trailing blanks included).
- Blocks without an explicit `sNN` session marker NEVER move.
- Dry-run by default; --write first copies the journal to
  `<name>.prune-bak`, then atomically replaces it, then appends the
  moved blocks verbatim to the archive file.
- Idempotent: moved blocks are gone, so a second run moves nothing.
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

_COMPLETED_RE = re.compile(r"^##\s+completed\s*$", re.IGNORECASE)
_HEADING_RE = re.compile(r"^#{1,6}\s")
_BULLET0_RE = re.compile(r"^- ")
_SESSION_RE = re.compile(r"\bs(\d+)\b")

ARCHIVE_HEADER = (
    "# Archived journal entries\n\n"
    "Moved from the Completed section by `daybreak prune`. "
    "Blocks appear in their original order; text is verbatim.\n"
)


def completed_bounds(lines: list[str]) -> tuple[int, int] | None:
    """(start, end) line range inside the `## Completed` section."""
    start = None
    for idx, ln in enumerate(lines):
        if _COMPLETED_RE.match(ln):
            start = idx + 1
            break
    if start is None:
        return None
    end = len(lines)
    for idx in range(start, len(lines)):
        if _HEADING_RE.match(lines[idx]):
            end = idx
            break
    return start, end


def split_blocks(lines: list[str], lo: int, hi: int) -> list[tuple[int, int]]:
    """Top-level bullet blocks within [lo, hi): (start, end) pairs.

    Each block owns its trailing blank/continuation lines, so removing
    [start, end) never disturbs surrounding bytes.
    """
    blocks = []
    i = lo
    while i < hi:
        if _BULLET0_RE.match(lines[i]):
            j = i + 1
            while j < hi and not _BULLET0_RE.match(lines[j]) \
                    and not _HEADING_RE.match(lines[j]):
                j += 1
            blocks.append((i, j))
            i = j
        else:
            i += 1
    return blocks


def _session_of(head_line: str) -> int | None:
    m = _SESSION_RE.search(head_line)
    return int(m.group(1)) if m else None


def select_stale(blocks: list[tuple[int, int]], lines: list[str],
                 keep_last: int = 10, before: int | None = None) -> list[dict]:
    """Moves as dicts {session, start, end, head}; oldest-first order kept."""
    marked = []
    for start, end in blocks:
        s = _session_of(lines[start])
        if s is not None:
            marked.append((start, end, s))
    if not marked:
        return []
    if before is not None:
        threshold = before - 1          # strictly-older semantics
    else:
        threshold = max(s for _, _, s in marked) - keep_last
    moves = []
    for start, end, s in marked:
        if s <= threshold:
            head = lines[start].strip()
            moves.append({"session": s, "start": start, "end": end,
                          "head": head[:72]})
    return moves


def prune_lines(text: str, keep_last: int = 10,
                before: int | None = None) -> tuple[str, list[dict]]:
    """Return (new_text, moves). new_text == text when moves == []."""
    lines = text.splitlines(keepends=True)
    bounds = completed_bounds(lines)
    if bounds is None:
        return text, []
    blocks = split_blocks(lines, *bounds)
    moves = select_stale(blocks, lines, keep_last=keep_last, before=before)
    if not moves:
        return text, []
    for mv in sorted(moves, key=lambda m: -m["start"]):   # splice back-to-front
        del lines[mv["start"]:mv["end"]]
    return "".join(lines), moves


def _moved_text(text: str, moves: list[dict]) -> str:
    lines = text.splitlines(keepends=True)
    return "".join("".join(lines[m["start"]:m["end"]]) for m in moves)


def apply_prune(journal: Path, new_text: str, moves: list[dict],
                archive_path: Path, old_text: str) -> dict:
    backup = journal.with_name(journal.name + ".prune-bak")
    shutil.copy2(journal, backup)
    tmp = journal.with_name(journal.name + ".prune-tmp")
    tmp.write_text(new_text, encoding="utf-8")
    os.replace(tmp, journal)
    if archive_path.exists():
        with open(archive_path, "a", encoding="utf-8") as fh:
            fh.write(_moved_text(old_text, moves))
    else:
        with open(archive_path, "a", encoding="utf-8") as fh:
            fh.write(ARCHIVE_HEADER)
            fh.write("\n")
            fh.write(_moved_text(old_text, moves))
    return {"backup": str(backup), "archive": str(archive_path),
            "moved": len(moves)}
