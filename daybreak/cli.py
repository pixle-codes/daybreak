"""daybreak CLI — digest | verify | stats | dupes | prune.

Exit codes: 0 = clean, 1 = findings (verify errors / dupes found),
2 = usage or IO errors.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from . import __version__, digest as _digest, dupes as _dupes, prune as _prune
from .parser import parse_paths
from .verify import check_inline_budget, statusline, verify


def _die(msg: str) -> int:
    print(f"daybreak: {msg}", file=sys.stderr)
    return 2


def cmd_digest(args) -> int:
    try:
        entries = parse_paths(args.files)
    except FileNotFoundError as exc:
        return _die(str(exc))
    if not entries:
        return _die("no entries found in input")
    today = date.fromisoformat(args.today) if args.today else date.today()
    if args.json:
        out = _digest.digest_json(entries, args.budget, today)
        out["entries_seen"] = len(entries)
        print(json.dumps(out, indent=2))
    else:
        print(_digest.build_digest(entries, args.budget, today,
                                   header=not args.no_header))
    return 0


def cmd_verify(args) -> int:
    try:
        today = date.fromisoformat(args.today) if args.today else None
        entries = parse_paths(args.files, today=today)
    except (FileNotFoundError, ValueError) as exc:
        return _die(str(exc))
    report = verify(entries, projects_root=args.projects_root,
                    remote=args.remote, run_tests=args.run_tests,
                    today=today, max_age_days=args.max_age_days,
                    ignore=getattr(args, "ignore", None))
    if getattr(args, "max_completed", None) is not None:
        if args.max_completed < 0:
            return _die("--max-completed must be >= 0")
        report.findings.extend(
            check_inline_budget(args.files, args.max_completed))
    if args.statusline:
        print(statusline(report))
        return report.exit_code()
    if args.json:
        print(json.dumps(report.to_json(), indent=2))
    else:
        for f in report.findings:
            mark = {"error": "FAIL", "warn": "WARN",
                    "info": "note", "ok": "ok"}[f.severity]
            print(f"{mark:4} [{f.kind}] {f.claim}: {f.detail}  ({f.where})")
        s = report.to_json()["summary"]
        print(f"summary: {s['errors']} errors, {s['warnings']} warnings")
    return report.exit_code()


def cmd_stats(args) -> int:
    try:
        entries = parse_paths(args.files)
    except FileNotFoundError as exc:
        return _die(str(exc))
    data = _dupes.stats(entries)
    if args.today:
        data = {**data}  # stats() already used today internally via entries
    print(json.dumps(data, indent=2))
    return 0


def cmd_dupes(args) -> int:
    try:
        entries = parse_paths(args.files)
    except FileNotFoundError as exc:
        return _die(str(exc))
    pairs = _dupes.find_dupes(entries, jaccard_min=args.jaccard,
                              ratio_min=args.ratio)
    if args.json:
        print(json.dumps({"pairs": pairs, "count": len(pairs)}, indent=2))
    else:
        for p in pairs:
            print(f"{p['ratio']:.3f}  {p['a']['path']}:{p['a']['line']} <-> "
                  f"{p['b']['path']}:{p['b']['line']}")
            print(f"      {p['excerpt']}")
        print(f"{len(pairs)} near-duplicate pair(s)")
    return 1 if pairs else 0


def cmd_prune(args) -> int:
    if args.keep_last is not None and args.before is not None:
        return _die("--keep-last and --before are mutually exclusive")
    if args.keep_last is not None and args.keep_last < 1:
        return _die("--keep-last must be >= 1")
    if args.before is not None and args.before < 0:
        return _die("--before must be a session number, sNN")
    journal = Path(args.journal)
    if not journal.is_file():
        return _die(f"no such file: {journal}")
    old_text = journal.read_text(encoding="utf-8", errors="replace")
    new_text, moves = _prune.prune_lines(
        old_text, keep_last=args.keep_last or 10, before=args.before)
    info = None
    if moves and args.write:
        archive = (Path(args.archive) if args.archive else
                   journal.with_name(journal.stem + "-archive" + journal.suffix))
        info = _prune.apply_prune(journal, new_text, moves, archive, old_text)
    if args.statusline:
        if not moves:
            print("daybreak PRUNE: nothing to archive")
        elif info:
            print(f"daybreak PRUNE: archived {len(moves)} entries, "
                  f"{len(old_text)}→{len(new_text)} bytes")
        else:
            print(f"daybreak PRUNE: would archive {len(moves)} entries, "
                  f"{len(old_text) - len(new_text)} bytes smaller")
        return 0
    if args.json:
        print(json.dumps({
            "mode": "write" if args.write else "dry-run",
            "moved": len(moves),
            "blocks": [{k: m[k] for k in ("session", "head")} for m in moves],
            "bytes_before": len(old_text),
            "bytes_after": len(new_text),
        }, indent=2))
    else:
        for m in moves:
            print(f"  [s{m['session']}] {m['head']}")
        label = "archived" if args.write else "would archive"
        print(f"{label} {len(moves)} block(s), "
              f"{len(old_text) - len(new_text)} bytes smaller")
    if not moves:
        if not args.json:
            print("nothing to do")
        return 0
    if not args.write:
        return 0
    if not args.json:
        print(f"backup {info['backup']}; archive {info['archive']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="daybreak",
        description="Session-start briefing compiler and claim verifier "
                    "for agent journals.")
    p.add_argument("--version", action="version",
                   version=f"daybreak {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("digest", help="compile a budgeted session-start briefing")
    d.add_argument("files", nargs="+", help="journal .md files (or a directory)")
    d.add_argument("--budget", type=int, default=4000,
                   help="character budget for entry text (default 4000)")
    d.add_argument("--json", action="store_true", help="structured output")
    d.add_argument("--no-header", action="store_true")
    d.add_argument("--today", help="override today, YYYY-MM-DD (tests)")
    d.set_defaults(func=cmd_digest)

    v = sub.add_parser("verify", help="check journal claims against reality")
    v.add_argument("files", nargs="+")
    v.add_argument("--projects-root", default="~/projects")
    v.add_argument("--remote", action="store_true",
                   help="also check tags are pushed (network)")
    v.add_argument("--run-tests", action="store_true",
                   help="run claimed test suites and compare counts")
    v.add_argument("--max-age-days", type=int, default=None, metavar="N",
                   help="error when the newest dated entry is older than N days")
    v.add_argument("--max-completed", type=int, default=None, metavar="N",
                   help="error when a Completed section holds more than N "
                        "inline entries (bloat watchdog; repair via prune)")
    v.add_argument("--ignore", action="append", default=[], metavar="NAME",
                   help="exclude repo NAME from all checks (repeatable) — "
                        "for shared-machine repos that are not yours to gate")
    v.add_argument("--statusline", action="store_true",
                   help="single-line summary (cron/alerting)")
    v.add_argument("--json", action="store_true")
    v.add_argument("--today", help="override today, YYYY-MM-DD (tests)")
    v.set_defaults(func=cmd_verify)

    s = sub.add_parser("stats", help="corpus statistics (JSON)")
    s.add_argument("files", nargs="+")
    s.add_argument("--today", help="override today, YYYY-MM-DD (tests)")
    s.set_defaults(func=cmd_stats)

    u = sub.add_parser("dupes", help="find near-duplicate entries")
    u.add_argument("files", nargs="+")
    u.add_argument("--jaccard", type=float, default=0.45)
    u.add_argument("--ratio", type=float, default=0.88)
    u.add_argument("--json", action="store_true")
    u.set_defaults(func=cmd_dupes)

    pr = sub.add_parser("prune",
                        help="archive stale Completed entries (decay)")
    pr.add_argument("journal", help="journal .md file (single file)")
    pr.add_argument("--keep-last", type=int, default=None,
                    help="keep the newest N session entries (default 10)")
    pr.add_argument("--before", type=int, default=None, metavar="sNN",
                    help="archive sessions strictly older than sNN")
    pr.add_argument("--write", action="store_true",
                    help="apply changes (default: dry-run)")
    pr.add_argument("--archive", default=None,
                    help="archive file (default: sibling <stem>-archive.md)")
    pr.add_argument("--statusline", action="store_true",
                    help="one-line family-convention summary")
    pr.add_argument("--json", action="store_true")
    pr.set_defaults(func=cmd_prune)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except BrokenPipeError:
        return 0


if __name__ == "__main__":
    sys.exit(main())
