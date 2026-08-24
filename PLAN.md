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
- [x] M6 — decay loop closed (s67): `verify --max-completed N` bloat
  watchdog (ERROR + repair command in detail when a Completed section
  holds >N inline entries; ok finding under budget; off by default;
  dir args expand like parse_paths) + `prune --statusline`; loop proof
  pinned by test (flag → prune --write → clean). Motivation: prune sat
  unused since v1.1.0 because nothing ever signaled it was due — the
  live STATE.md grew to ~66 inline entries. (tag v1.3.0)
- [x] M7 — claim attribution (s76): ship-claim and test-count binding
  rebuilt line-scoped. The nearest `projects/<repo>` ref ON THE
  VERSION'S LINE owns the claim, so a long entry's `cd
  ~/projects/other` recipe can never steal a title-shape "reponame
  vX.Y.Z shipped" claim (live false-block hit s75: verify demanded a
  tag in the wrong repo and honest prose had to be reordered to pass).
  Ship verb required on the version's own line (casual mentions on
  verbless lines skipped); EVERY distinct version in an entry is
  checked (was: first only); unattributable claims in multi-repo
  entries downgrade to WARN with pin-it advice — a gate must never
  false-block prose it cannot attribute. Live dogfood: all 30 tag
  claims on real STATE.md bound to the right repos incl. three
  versions inside one entry. (tag v1.4.0)
- [x] M8 — repo exclusions (s93): `verify --ignore NAME` (repeatable) for
  shared-machine repos the journal owner can't gate. Motivated LIVE: the
  operator's own unversioned briefing tool went dirty mid-day and every
  future verify run would error forever — the permanent-wolf class
  reveille v1.1.0 killed for labels, now killed for repo checks before it
  trained anyone to ignore exits. Semantics: ignored names are invisible
  to repo checks AND claim attribution (fallback candidates filtered);
  a ship/test claim LINE-PINNED onto an ignored repo downgrades to an
  info finding so reduced coverage is never silent — narrowing scope may
  hide findings but must never fake them. Live dogfood on real journals:
  ritual's dirty+unpushed findings gone with one flag; without --ignore
  byte-identical behavior (existing tests untouched). (tag v1.5.0)

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
