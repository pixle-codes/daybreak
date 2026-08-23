"""Near-duplicate lesson detection and corpus stats."""

from __future__ import annotations

import re
from datetime import date

_WORD_RE = re.compile(r"[a-z0-9]+")


def normalize(text: str) -> str:
    return " ".join(_WORD_RE.findall(text.lower()))


def _tokens(text: str) -> set[str]:
    return set(_WORD_RE.findall(text.lower()))


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / (len(a) + len(b) - inter)


def find_dupes(entries, jaccard_min: float = 0.45,
               ratio_min: float = 0.88) -> list[dict]:
    """Pairs of near-identical entries. Jaccard prefilter keeps the
    SequenceMatcher calls rare (O(n^2) cheap ops, few expensive ones)."""
    from difflib import SequenceMatcher

    toks = [(e, _tokens(e.text), normalize(e.text)) for e in entries]
    pairs = []
    for i in range(len(toks)):
        ei, ti, ni = toks[i]
        for j in range(i + 1, len(toks)):
            ej, tj, nj = toks[j]
            if _jaccard(ti, tj) < jaccard_min:
                continue
            ratio = SequenceMatcher(None, ni, nj).ratio()
            if ratio >= ratio_min:
                pairs.append({
                    "a": {"path": ei.path, "line": ei.line,
                          "section": ei.section},
                    "b": {"path": ej.path, "line": ej.line,
                          "section": ej.section},
                    "ratio": round(ratio, 3),
                    "excerpt": ni[:120],
                })
    pairs.sort(key=lambda p: (-p["ratio"], p["a"]["path"], p["a"]["line"]))
    return pairs


def stats(entries, today: date | None = None) -> dict:
    today = today or date.today()
    sections: dict[str, int] = {}
    tags: dict[str, int] = {}
    dated = 0
    oldest = None
    total_chars = 0
    next_count = 0
    for e in entries:
        sections[e.section or "(top)"] = sections.get(e.section or "(top)", 0) + 1
        for t in e.tags:
            tags[f"#{t}"] = tags.get(f"#{t}", 0) + 1
        if e.dates:
            dated += 1
            d = max(e.dates)
            if oldest is None or d < oldest:
                oldest = d
        if "NEXT" in e.text:
            next_count += 1
        total_chars += len(e.text)
    age_days = None
    if oldest is not None:
        age_days = (today - oldest).days
    return {
        "files": sorted({e.path for e in entries}),
        "entries": len(entries),
        "total_chars": total_chars,
        "est_tokens": total_chars // 4,
        "dated_entries": dated,
        "dated_fraction": round(dated / len(entries), 2) if entries else 0.0,
        "oldest_dated_entry": oldest.isoformat() if oldest else None,
        "oldest_age_days": age_days,
        "next_pointers": next_count,
        "sections": dict(sorted(sections.items(),
                                key=lambda kv: -kv[1])),
        "tags": dict(sorted(tags.items(), key=lambda kv: -kv[1])),
    }
