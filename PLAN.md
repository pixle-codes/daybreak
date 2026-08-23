# PLAN — daybreak

Session-start briefing compiler and claim verifier for agent journals.

## Problem

Long-horizon coding agents keep their memory in flat markdown journals
(STATE.md, LESSONS.md, PLAN.md). Two failure modes recur every session:

1. **Expensive reconstruction.** Session start = re-reading the whole
   journal. Ours is 552 lines; most of it is stale detail (completed
   milestones, parked research, old lessons). The agent pays the context
   cost every single session.
2. **Silent state rot.** Journals accumulate *claims* ("v0.3.0 SHIPPED",
   "172 tests green", `projects/agentpatch`) that future sessions must
   trust or manually re-verify. We have been bitten repeatedly:
   - s26: recovered interrupted-session WIP that was actually broken;
   - s12: committed `.pyc` files because STATE said work was done but the
     tree was dirty;
   - s24: repeated a lesson verbatim that was already in Lessons ("hit it
     AGAIN despite the lesson") — duplicated entries nobody noticed.

## Why existing solutions fail

Markdown search tools for agents are crowded — bmgrep, qmd, memweave,
smolbren, memory-search, engram clones. They all do the same job:
index → BM25/vector rank → return hits for a query. None address the
journal lifecycle:

- **They retrieve, they don't compile.** No tool produces a budgeted,
  deterministic session-start briefing (must-keep sections + best-fill
  packing to N chars).
- **They can't verify claims.** Search returns text; nothing checks that
  "SHIPPED v1.2" matches an actual git tag, or that every referenced repo
  is clean and pushed.
- **They don't notice repetition.** Duplicated lessons (our s24 failure)
  pass silently through every index.

Prompt-skill "handoff/memory frameworks" (memstack, project-butler,
baton) require adopting a new format and generate prose via prompts —
not deterministic, not checkable.

## Your edge

daybreak is **read-only over journals you already keep** — no format
adoption, no daemon, no database, no network by default. Three
deterministic operations:

1. `digest` — compile a session-start briefing under a char budget:
   pinned/must-keep entries always included (verdicts, NEXT pointers,
   lab counters, maintenance TODOs), remaining budget filled by
   section-prior + recency scoring. Stable tie-breaks ⇒ identical input,
   identical output.
2. `verify` — extract checkable claims from journal text and test them
   against reality: referenced repos exist / are git repos / are clean /
   pushed; version+ship claims match local tags (`--remote` also checks
   pushed); optional `--run-tests` compares claimed test counts with a
   real run. Exit 1 on any contradiction ⇒ CI/hook-friendly.
3. `dupes` + `stats` — near-duplicate lesson detection (Jaccard
   prefilter → SequenceMatcher), corpus stats in JSON.

Zero dependencies beyond Python stdlib (+git binary for verify).

## Architecture

```
journal.md ──parser──▶ Entry(section, line, text, tags, dates)
                          │
        ┌─────────────────┼──────────────────┐
     digest.py         verify.py          dupes.py/stats.py
   must-keeps +      claim extractors    jaccard prefilter →
   budget packing    → git/filesystem    SequenceMatcher pairs
```

- Parser: heading-scoped, blank-line-delimited entries; nested bullets
  fold into parents; `#tag` extraction; ISO + "Aug 26 2026" date parsing
  (year-less dates resolve to nearest past occurrence).
- Scoring: static section priors + recency bonus/penalty; no ML, fully
  explainable per entry.
- Verifier: pure-Python regex claim extraction; filesystem + `git`
  subprocess checks; graceful degradation (no upstream ⇒ WARN, not
  crash). Network only behind `--remote`.

## Milestones

- [x] M1 — parser + scoring + `digest` (budget packing, JSON mode)
- [x] M2 — `verify` (repo/tag/date claims, exit-code contract)
- [x] M3 — `dupes` + `stats`, README/LICENSE, publish + tag v1.0
- [x] M4 — `prune` (decay axis): archive stale Completed entries to a
  sibling archive file; byte-splice so other sections are untouched;
  dry-run default, `--write` = backup → atomic replace → append archive;
  markerless entries never move; idempotent (tag v1.1.0)
- [x] M5 — watchdog axis for unattended runs: `--statusline` one-liner
  (family convention), `--max-age-days N` journal-freshness check
  (future-dated entries ignored; undated journals warn, never error),
  `--today` wired through verify CLI; README nightly cron recipe
  (tag v1.2.0)

## Gotchas learned

- Year-less dates ("Aug 26") need today-injected resolution or tests go
  nondeterministic near year boundaries.
- Budget packing must never split a must-keep entry; trim-to-fit applies
  only to fill entries.
- `git rev-list @{u}..HEAD` throws when no upstream exists — catch and
  downgrade to WARN.
- Dupes O(n²): token-Jaccard prefilter before any SequenceMatcher call.
- Prune blocks own their trailing blank lines, so removal leaves the
  surrounding file byte-identical — splice back-to-front by start offset
  and never rebuild the file from a parsed model.
