# daybreak

**Session-start briefing compiler and claim verifier for agent journals.**

Long-lived coding agents keep their memory in flat markdown journals —
`STATE.md`, `LESSONS.md`, `PLAN.md`. Two things go wrong every session:

1. **Reconstruction is expensive.** The agent re-reads the whole journal
   (ours was 552 lines ≈ 9,000 tokens) when only a fraction is live.
2. **Claims rot silently.** "SHIPPED v1.2", "42 tests green",
   "`projects/foo`" — future sessions trust them or burn time re-checking.
   We once recovered an *interrupted session's WIP that was actually broken*
   because the journal said work was further along than reality.

daybreak fixes both with three deterministic, read-only commands over the
journals you already keep. No new format, no daemon, no database,
no network by default.

## What it does

### `digest` — compile a budgeted session-start briefing

Must-keep entries always survive (pinned tags, OWNER VERDICT / Lab /
Maintenance sections, DO FIRST lines, NEXT pointers in forward-looking
sections); the remaining character budget is filled by section-prior +
recency scoring. Identical input ⇒ identical output.

```console
$ daybreak digest ~/journal/STATE.md --budget 2200
# daybreak digest — 5 must-keep + 0 full / 1 trimmed fill entries (budget 2200)
[/home/me/journal/STATE.md:140] - Session counter: 3 (s23-s27 all non-lab; NEXT SESSION s28 IS THE FIRST TRUE
  LAB SESSION — do not start it with anything else). No Lab repo shipped yet;
  ...
```

On our real journal: **133 entries ≈ 9,900 tokens → a 2,200-char briefing**
that still contains every verdict, lab counter, and NEXT pointer.

`--json` returns the structured selection (`must_keep[]`, `fills[]`
with trim points) for programmatic use.

### `verify` — check journal claims against reality

Extracts checkable claims and tests them:

| Claim | Check |
|---|---|
| `projects/<name>` / `github.com/<owner>/<name>` | directory exists · is a git repo · tree clean · commits pushed |
| `vX.Y.Z` + ship verb + repo ref | tag exists locally (`--remote`: also pushed to origin) |
| `N tests green` | with `--run-tests`: runs the suite and compares |

Honest journals are handled: a ship claim negated in prose ("NO TAG YET")
downgrades to a note instead of failing.

```console
$ daybreak verify ~/journal/STATE.md --projects-root ~/projects
ok   [repo] someproj: clean              (/journal/STATE.md:8)
ok   [repo] someproj: master pushed      (/journal/STATE.md:8)
FAIL [tag]  v1.4.0: claim says shipped/tagged but no local tag ...
summary: 1 errors, 0 warnings     # exit code 1 — CI/hook friendly
```

Exit codes: `0` clean · `1` contradictions found · `2` usage/IO error.

### `dupes` — catch repeated lessons

Token-Jaccard prefilter then SequenceMatcher on survivors; flags
near-identical entries even across sections (the "we hit this AGAIN"
failure mode). Exits 1 when pairs are found.

### `stats` — corpus facts as JSON

Entries, token estimate, dated fraction, oldest entry, NEXT-pointer count,
per-section histogram.

## Install

Zero dependencies beyond Python 3.10+ stdlib; `verify` shells out to `git`.

```sh
git clone https://github.com/pixle-codes/daybreak
daybreak digest --help          # or: python3 -m daybreak --help
```

No install step: run from the checkout (`python3 -m daybreak …`) or put
`daybreak/` on your `PYTHONPATH`.

## Usage

```sh
daybreak digest FILE... [--budget N] [--json] [--no-header]
daybreak verify FILE... [--projects-root DIR] [--remote] [--run-tests] [--json]
daybreak dupes  FILE... [--jaccard F] [--ratio F] [--json]
daybreak stats  FILE...
```

`FILE` may be a file or a directory (expands to top-level `*.md`).

## Design notes

- **Read-only.** daybreak never edits your journals.
- **Deterministic.** Stable sort keys `(score, path, line)`; injectable
  clock via `--today` makes output reproducible near boundaries.
- **Budget contract.** Must-keeps are never trimmed below legibility; if
  musts alone exceed the budget the effective budget grows to fit them
  (documented, tested behaviour).
- **Graceful degradation.** No git upstream ⇒ WARN not crash; no git
  binary ⇒ WARN; network only behind explicit `--remote`.

## Scope & limits

- Scoring is heuristic priors, not ML — tuned for STATE.md-style journals
  (Active / Completed / Lessons / Verdict sections); override by tagging
  entries `#pinned`.
- Dupes detection is O(n²) after prefiltering — fine to a few thousand
  entries.
- `verify` understands `projects/<name>` and GitHub URL references; other
  claim shapes are ignored rather than guessed at.

## License

MIT
