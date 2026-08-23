"""Static scoring: section priors + recency. Deterministic, explainable."""

from __future__ import annotations

import re
from datetime import date

# (regex on section text, weight) — first match wins; default 0.0
_PRIORS = [
    (re.compile(r"owner verdict", re.I), 3.0),
    (re.compile(r"^maintenance\b|^maintenance todo", re.I), 2.8),
    (re.compile(r"^lab$", re.I), 2.5),
    (re.compile(r"active projects?", re.I), 1.6),
    (re.compile(r"ideas? backlog", re.I), 0.4),
    (re.compile(r"lessons learned", re.I), -0.5),
    (re.compile(r"completed", re.I), -0.6),
    (re.compile(r"parked|blacklist", re.I), -0.8),
]
_DEFAULT_PRIOR = 0.0

_NEXT_RE = re.compile(r"\bNEXT\b")
_DO_FIRST_RE = re.compile(r"\bDO FIRST\b", re.IGNORECASE)

RECENCY_BONUS = 0.8        # dated within this many days of today
RECENCY_FRESH_DAYS = 21
RECENCY_PENALTY = -0.4     # older than this many days
RECENCY_STALE_DAYS = 120


def prior(entry) -> float:
    for rx, w in _PRIORS:
        if rx.search(entry.section):
            return w
    return _DEFAULT_PRIOR


def is_must_keep(entry) -> bool:
    """Entries that must survive budget packing."""
    if "#pinned" in entry.tags:
        return True
    if prior(entry) >= 2.5:
        return True
    head = entry.text[:200]
    if _DO_FIRST_RE.search(head):
        return True
    # NEXT pointers only count as must-keep in forward-looking sections
    if _NEXT_RE.search(head) and prior(entry) >= 1.6:
        return True
    return False


def score(entry, today: date) -> float:
    s = prior(entry)
    if "#pinned" in entry.tags:
        s += 1.0
    if _NEXT_RE.search(entry.text[:200]):
        s += 1.2
    elif _NEXT_RE.search(entry.text):
        s += 0.6
    if entry.dates:
        age = (today - max(entry.dates)).days
        if age <= RECENCY_FRESH_DAYS:
            s += RECENCY_BONUS
        elif age > RECENCY_STALE_DAYS:
            s += RECENCY_PENALTY
    return round(s, 4)
