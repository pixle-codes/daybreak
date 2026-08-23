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
| `vX.Y.Z` + ship verb on the same line | tag exists locally (`--remote`: also pushed to origin), checked against the repo named nearest on that line |
| `N tests green` | with `--run-tests`: runs the suite and compares (same line-level attribution) |
| journal freshness (`--max-age-days N`) | newest dated entry is at most N days old |

Honest journals are handled: a ship claim negated in prose ("NO TAG YET")
downgrades to a note instead of failing. Version claims bind to the repo
ref on their own line — so a long entry's `cd ~/projects/other` recipe can
never steal a "reponame vX.Y.Z shipped" title claim. If the version's line
names no repo and the entry mentions several, the claim is reported as an
unverifiable warning (never a failure): write `projects/<repo>` beside the
version to pin it. Casual version mentions on lines without a ship verb
are ignored entirely; every distinct version in an entry gets its own check.

```console
$ daybreak verify ~/journal/STATE.md --projects-root ~/projects
ok   [repo] someproj: clean              (/journal/STATE.md:8)
ok   [repo] someproj: master pushed      (/journal/STATE.md:8)
FAIL [tag]  v1.4.0: claim says shipped/tagged but no local tag ...
summary: 1 errors, 0 warnings     # exit code 1 — CI/hook friendly
```

Exit codes: `0` clean · `1` contradictions found · `2` usage/IO error.

#### Nightly self-check recipe

`verify` is built to run unattended. One cron line catches every silent rot
mode this tool knows about — dirty trees, unpushed work, missing tags,
stale journals:

```console
# crontab -e  — 05:12 daily
12 5 * * * python3 -m daybreak verify ~/journal/*.md --remote --max-age-days 3 --statusline >> ~/daybreak.log 2>&1 || echo "journal check FAILED, see ~/daybreak.log" | mail -s daybreak you@example.com
```

- `--statusline` prints exactly one line (`daybreak OK: ...` /
  `daybreak FAIL: <count> errors — [kind] first-finding`) so logs stay
  greppable; the non-zero exit drives the alert.
- `--remote` adds per-tag push checks (network); drop it if the machine
  is offline at that hour.
- `--max-age-days 3` fails when the journal itself stopped being updated
  (the "agent went quiet" signal) — tune to your session cadence.
- systemd equivalent: a `daybreak.service` (`Type=oneshot`,
  `ExecStart=/usr/bin/env python3 -m daybreak verify ...`) plus an
  `OnCalendar=*-*-* 05:12:00` timer works the same way.

Dogfood loop for interactive sessions: run `verify` right after pushing;
a clean exit is what licenses writing "SHIPPED" into the journal.

### `dupes` — catch repeated lessons

Token-Jaccard prefilter then SequenceMatcher on survivors; flags
near-identical entries even across sections (the "we hit this AGAIN"
failure mode). Exits 1 when pairs are found.

### `stats` — corpus facts as JSON

Entries, token estimate, dated fraction, oldest entry, NEXT-pointer count,
per-section histogram.

### `prune` — decay: archive stale Completed entries

Journals only grow, so session-start cost grows with them. `prune` moves
`## Completed` entries older than a retention window into a sibling
archive file (`STATE.md` → `STATE-archive.md`), byte-spliced so every
other section is untouched. Dry-run by default; `--write` first backs the
journal up to `<name>.prune-bak`, then applies atomically, then appends
the moved blocks verbatim to the archive. Entries without an explicit
`sNN` session marker never move; re-running moves nothing (idempotent).

```sh
$ daybreak prune STATE.md --keep-last 8        # dry-run preview
  [s31] - **fenceline v1.0.0 NEW s31 …
  [s30] - **s30 RESEARCH-DRY SESSION** …
would archive 12 block(s), 5321 bytes smaller
$ daybreak prune STATE.md --keep-last 8 --write
backup /home/me/journal/STATE.md.prune-bak; archive /home/me/journal/STATE-archive.md
```

Selectors: `--keep-last N` (keep newest N session entries) or
`--before sNN` (strictly older than sNN); mutually exclusive. Exit codes:
`0` ok (whether or not blocks moved) · `2` usage/IO error.
Note: `digest`/`dupes`/`stats` remain read-only — `prune` is the one
daybreak command that writes, and only with `--write`.

### The decay loop — `verify --max-completed` finds bloat, `prune` fixes it

Decay only works if something tells you it's due. `verify --max-completed N`
is the watchdog: when a Completed section holds more than N inline entries
it raises an ERROR (exit 1, statusline `FAIL … [bloat] …`) whose detail is
the exact repair command. Under budget it reports ok; without the flag the
check is off. One command pair closes the loop:

```sh
$ daybreak verify STATE.md --max-completed 12 --statusline
daybreak FAIL: 1 errors, 0 warn — [bloat] 34 inline entries: Completed section exceeds --max-completed 12; run: python3 -m daybreak prune STATE.md --write
$ python3 -m daybreak prune STATE.md --keep-last 12 --write --statusline
daybreak PRUNE: archived 22 entries, 51234→18702 bytes
$ daybreak verify STATE.md --max-completed 12 --statusline
daybreak OK: 74 checks, 0 errors, 0 warn
```

Nightly recipe with both rot and bloat covered:

```sh
python3 -m daybreak verify ~/journal/STATE.md --remote \
  --max-age-days 3 --max-completed 12 --statusline \
  || python3 -m daybreak prune ~/journal/STATE.md --keep-last 12 --write
```

`prune --statusline` gives the family one-liner (`would archive …` on a
dry run, `archived N entries, X→Y bytes`, or `nothing to archive`).

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
daybreak prune  JOURNAL [--keep-last N | --before sNN] [--write] [--archive PATH] [--json]
```

`FILE` may be a file or a directory (expands to top-level `*.md`).

## Design notes

- **Read-only by default.** Only `prune --write` modifies anything, and it
  backs up + archives before touching the original; everything else never
  edits your journals.
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
