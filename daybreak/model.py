"""Entry model shared by all daybreak commands."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Entry:
    path: str
    section: str          # nearest preceding heading text ("" before any heading)
    level: int            # heading level of that section (0 = file top)
    line: int             # 1-based line number where the entry starts
    text: str             # folded entry text (no trailing newline)
    tags: frozenset[str] = field(default_factory=frozenset)
    dates: tuple = field(default_factory=tuple)  # datetime.date objects

    @property
    def key(self) -> str:
        return f"{self.path}:{self.line}"

    def to_json(self) -> dict:
        return {
            "path": self.path,
            "section": self.section,
            "line": self.line,
            "text": self.text,
            "tags": sorted(self.tags),
            "dates": [d.isoformat() for d in self.dates],
        }
