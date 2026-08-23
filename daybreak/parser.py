"""Markdown journal parser: heading-scoped, blank-line-delimited entries.

Rules (pinned by tests):
- `#`..`######` lines update the section context and never appear in entry text.
- A bullet line (`- ` / `* `) at the shallowest indent of its run opens an entry.
  Deeper-indented bullets and plain continuation lines fold into it.
- A blank line ends the current entry.
- Non-bullet prose outside an entry becomes its own paragraph entry.
- `#tag` tokens are extracted (headings excluded since they are not entries).
- Dates: ISO `2026-08-23` plus `Aug 26`, `Aug 26 2026`, `26 Aug 2026`;
  year-less dates resolve to the most recent past occurrence of that
  day/month relative to `today`.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path

from .model import Entry

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
_BULLET_RE = re.compile(r"^(\s*)[-*]\s+(.*)$")
_TAG_RE = re.compile(r"(?<![\w])#([A-Za-z][\w-]{1,30})\b")
_ISO_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"])}
_MDY_RE = re.compile(
    r"\b(" + "|".join(_MONTHS) + r")[a-z]*\.?\s+(\d{1,2})(?:\s*,?\s*(\d{4}))?\b",
    re.IGNORECASE)
_DMY_RE = re.compile(
    r"\b(\d{1,2})\s+(" + "|".join(_MONTHS) + r")[a-z]*\.?(?:\s*,?\s*(\d{4}))?\b",
    re.IGNORECASE)


def _resolve_month_day(month: int, day: int, year: int | None, today: date) -> date:
    if year is None:
        candidate = date(today.year, month, day)
        if candidate > today:          # future birthday this year -> last year's
            try:
                candidate = date(today.year - 1, month, day)
            except ValueError:
                pass
        return candidate
    return date(year, month, day)


def extract_dates(text: str, today: date) -> tuple:
    found = set()
    for m in _ISO_RE.finditer(text):
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            found.add(date(y, mo, d))
        except ValueError:
            pass
    for m in _MDY_RE.finditer(text):
        mo = _MONTHS[m.group(1).lower()]
        yr = int(m.group(3)) if m.group(3) else None
        try:
            found.add(_resolve_month_day(mo, int(m.group(2)), yr, today))
        except ValueError:
            pass
    for m in _DMY_RE.finditer(text):
        mo = _MONTHS[m.group(2).lower()]
        yr = int(m.group(3)) if m.group(3) else None
        try:
            found.add(_resolve_month_day(mo, int(m.group(1)), yr, today))
        except ValueError:
            pass
    return tuple(sorted(found))


def parse_text(text: str, path: str = "<memory>", today: date | None = None) -> list[Entry]:
    today = today or date.today()
    entries: list[Entry] = []
    section, level = "", 0
    cur_lines: list[str] | None = None
    cur_start = 0
    base_indent = 0

    def flush():
        nonlocal cur_lines
        if cur_lines:
            body = "\n".join(cur_lines).rstrip()
            tags = frozenset(_TAG_RE.findall(body))
            entries.append(Entry(path, section, level, cur_start,
                                 body, tags, extract_dates(body, today)))
        cur_lines = None

    for lineno, raw in enumerate(text.splitlines(), start=1):
        hm = _HEADING_RE.match(raw)
        if hm:
            flush()
            level = len(hm.group(1))
            section = hm.group(2).strip()
            continue
        if not raw.strip():
            flush()
            continue
        bm = _BULLET_RE.match(raw)
        if bm is not None:
            indent, rest = len(bm.group(1)), bm.group(2)
            if cur_lines is None or base_indent < 0 or indent <= base_indent:
                flush()
                cur_lines = [f"- {rest}"]
                cur_start = lineno
                base_indent = indent
            else:
                cur_lines.append(raw.rstrip())
        else:
            if cur_lines is None:
                cur_lines = [raw.rstrip()]
                cur_start = lineno
                base_indent = -1     # paragraph mode
            else:
                cur_lines.append(raw.rstrip())
    flush()
    return entries


def parse_file(path: str | Path, today: date | None = None) -> list[Entry]:
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"no such file: {p}")
    text = p.read_text(encoding="utf-8", errors="replace")
    return parse_text(text, str(p), today)


def parse_paths(paths, today: date | None = None) -> list[Entry]:
    """Parse files; a directory expands to its top-level *.md files."""
    out: list[Entry] = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            md = sorted(p.glob("*.md"))
            if not md:
                raise FileNotFoundError(f"no .md files in directory: {p}")
            for f in md:
                out.extend(parse_file(f, today))
        else:
            out.extend(parse_file(p, today))
    return out
