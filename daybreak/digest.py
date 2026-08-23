"""Budgeted session-start briefing compiler.

Guarantees (pinned by tests):
- must-keep entries are always present, never trimmed below 60 chars;
- total output never exceeds the budget (except a single unavoidable
  must-keep larger than the whole budget);
- deterministic: identical input + today => identical output;
- fill entries may be trimmed to fit, marked with an ellipsis.
"""

from __future__ import annotations

from datetime import date

from . import score as _score

_ELLIPSIS = " …"
_MIN_FILL = 80
_OVERHEAD = 32  # per-entry framing cost in the rendered block


def _render(entry, trim_to: int | None = None) -> str:
    text = entry.text
    if trim_to is not None and len(text) > trim_to:
        keep = max(trim_to - len(_ELLIPSIS), 0)
        text = text[:keep].rstrip() + _ELLIPSIS
    return f"[{entry.key}] {text}"


def select(entries, budget: int, today: date) -> tuple[list, list]:
    """Return (must_keep, fills_chosen). Pure selection; no rendering."""
    ranked = sorted(entries, key=lambda e: (-_score.score(e, today), e.path, e.line))
    must = [e for e in entries if _score.is_must_keep(e)]
    must.sort(key=lambda e: (e.path, e.line))
    must_keys = {e.key for e in must}
    fills = [e for e in ranked if e.key not in must_keys]
    chosen = []
    used = sum(len(e.text) + _OVERHEAD for e in must)
    for e in fills:
        cost = len(e.text) + _OVERHEAD
        if used + cost <= budget:
            chosen.append((e, None))
            used += cost
        else:
            room = budget - used - _OVERHEAD
            if room >= _MIN_FILL:
                chosen.append((e, room))
                used = budget
            break
    return must, chosen


def build_digest(entries, budget: int = 4000, today: date | None = None,
                 header: bool = True) -> str:
    """Rendered markdown briefing. Budget applies to entry text only;
    the one-line status header sits outside the accounting."""
    today = today or date.today()
    must, fills = select(entries, budget, today)

    # Must-keeps always pass whole: if they alone exceed the budget the
    # effective budget grows to fit them (documented behaviour).
    must_cost = sum(len(e.text) + _OVERHEAD for e in must)
    eff_budget = max(budget, must_cost)

    lines: list[str] = []
    if header:
        n_full = sum(1 for _, t in fills if t is None)
        n_trim = sum(1 for _, t in fills if t is not None)
        lines.append(f"# daybreak digest — {len(must)} must-keep + "
                     f"{n_full} full / {n_trim} trimmed fill entries "
                     f"(budget {eff_budget})")
    for e in must:
        lines.append(_render(e, None))
    for e, trim in fills:
        lines.append(_render(e, trim))
    return "\n".join(lines)


def digest_json(entries, budget: int, today: date | None = None) -> dict:
    today = today or date.today()
    must, fills = select(entries, budget, today)
    return {
        "budget": budget,
        "today": today.isoformat(),
        "must_keep": [e.to_json() | {"must": True} for e in must],
        "fills": [
            e.to_json() | {"must": False, "trimmed_at": t} for e, t in fills
        ],
    }
