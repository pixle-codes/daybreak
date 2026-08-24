"""Claim verification: test journal claims against filesystem + git reality.

Claim types extracted from entries:
- repo refs:   `projects/<name>` or `github.com/<owner>/<name>`
- ship claims: vX.Y.Z co-occurring with ship/tag/release verbs in an entry
               that also references a repo
- test counts: "N tests green" / "N passing" (verifiable only via --run-tests)

Severity: ERROR = contradiction with reality (dirty tree, unpushed work,
missing tag/dir). WARN = cannot decide (no upstream, no git binary).
Exit code contract: 0 clean, 1 any ERROR, 2 usage/IO.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from . import prune as _prune

_REPO_RE = re.compile(r"\bprojects/([A-Za-z0-9_.-]+)")
_GH_RE = re.compile(r"github\.com/([A-Za-z0-9-]+)/([A-Za-z0-9_.-]+)")
_VERSION_RE = re.compile(r"\bv?(\d+)\.(\d+)\.(\d+)\b")
_SHIP_VERB_RE = re.compile(
    r"\b(shipped|ship|tagged|tag|released|release|published)\b", re.IGNORECASE)
_TESTS_RE = re.compile(
    r"\b(\d+)\s+(?:stdlib\s+)?(?:unit\s+)?tests?\s+(?:green|passing)\b"
    r"|\b(\d+)\s+passing\b", re.IGNORECASE)
_NEGATION_RE = re.compile(
    r"\b(no tag|not tagged|untagged|unreleased|not yet|pending tag)\b",
    re.IGNORECASE)


@dataclass
class Finding:
    severity: str            # "error" | "warn" | "info" | "ok"
    kind: str                # repo | tag | tests | date
    claim: str
    detail: str
    where: str               # path:line

    def to_json(self) -> dict:
        return {"severity": self.severity, "kind": self.kind,
                "claim": self.claim, "detail": self.detail, "where": self.where}


@dataclass
class VerifyReport:
    findings: list = field(default_factory=list)

    @property
    def errors(self):
        return [f for f in self.findings if f.severity == "error"]

    @property
    def warnings(self):
        return [f for f in self.findings if f.severity == "warn"]

    def exit_code(self) -> int:
        return 1 if self.errors else 0

    def to_json(self) -> dict:
        return {
            "findings": [f.to_json() for f in self.findings],
            "summary": {
                "errors": len(self.errors),
                "warnings": len(self.warnings),
                "ok": sum(1 for f in self.findings if f.severity == "ok"),
            },
        }


def _git(args, cwd, timeout=15):
    if shutil.which("git") is None:
        return None
    try:
        return subprocess.run(["git"] + args, cwd=cwd, timeout=timeout,
                              capture_output=True, text=True)
    except (subprocess.TimeoutExpired, OSError):
        return None


def _norm_gh_name(m) -> str:
    n = m.group(2)
    if "/" in n:  # trailing .git / path fragments
        n = n.split("/")[0]
    return n.removesuffix(".git")


def _repo_refs_with_pos(text: str) -> list[tuple[str, int]]:
    """All repo refs as (name, char_position), sorted by position."""
    out = [(m.group(1).rstrip("."), m.start())
           for m in _REPO_RE.finditer(text)]
    out += [(_norm_gh_name(m), m.start()) for m in _GH_RE.finditer(text)]
    out.sort(key=lambda np: np[1])
    return out


def _line_bounds(text: str, pos: int) -> tuple[int, int]:
    start = text.rfind("\n", 0, pos) + 1
    end = text.find("\n", pos)
    return start, (len(text) if end == -1 else end)


def _bind_repo_on_line(text: str, pos: int) -> str | None:
    """Repo ref on the same line as `pos`, nearest wins (ties: earliest)."""
    lo, hi = _line_bounds(text, pos)
    refs = [(n, p) for n, p in _repo_refs_with_pos(text) if lo <= p < hi]
    if not refs:
        return None
    refs.sort(key=lambda np: (abs(np[1] - pos), np[1]))
    return refs[0][0]


def _distinct(names: list[str]) -> list[str]:
    seen: set[str] = set()
    out = []
    for n in names:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


def extract_repo_names(entries, ignore: list[str] | None = None) -> list[str]:
    skipped = _ignore_set(ignore)
    names: list[str] = []
    for e in entries:
        for n, _ in _repo_refs_with_pos(e.text):
            if n in skipped:
                continue
            if n not in names:
                names.append(n)
    return names


def _ignore_set(ignore: list[str] | None) -> set[str]:
    return {n.strip() for n in (ignore or []) if n.strip()}


def _check_repo(name: str, projects_root: Path, remote: bool, where: str):
    out = []
    repo = projects_root / name
    if not repo.is_dir():
        return [Finding("error", "repo", f"projects/{name}",
                        f"directory missing under {projects_root}", where)]
    if not (repo / ".git").exists():
        return [Finding("error", "repo", f"projects/{name}",
                        "not a git repository", where)]
    status = _git(["status", "--porcelain"], repo)
    if status is None:
        return [Finding("warn", "repo", name, "git unavailable", where)]
    if status.stdout.strip():
        n = len(status.stdout.strip().splitlines())
        out.append(Finding("error", "repo", name,
                           f"dirty working tree ({n} uncommitted files)", where))
    else:
        out.append(Finding("ok", "repo", name, "clean", where))

    head = _git(["rev-parse", "--abbrev-ref", "HEAD"], repo)
    branch = head.stdout.strip() if head and head.returncode == 0 else "?"
    upstream = _git(["rev-list", "--count", "@{u}..HEAD"], repo)
    if upstream is None or upstream.returncode != 0:
        out.append(Finding("warn", "repo", name,
                           f"branch {branch} has no upstream; push state unknown",
                           where))
    else:
        ahead = int(upstream.stdout.strip() or "0")
        if ahead > 0:
            out.append(Finding("error", "repo", name,
                               f"{ahead} unpushed commit(s) on {branch}", where))
        else:
            out.append(Finding("ok", "repo", name, f"{branch} pushed", where))
    return out


def _check_ship_claims(entries, projects_root: Path, remote: bool,
                       ignore: list[str] | None = None):
    """vX.Y.Z + ship verb ON THE VERSION'S OWN LINE => tag must exist.

    Attribution ladder (v1.4.0): the nearest repo ref on the version's
    line wins — title-shape prose ("reponame vX.Y.Z shipped") pins
    exactly, so a `cd ~/projects/other` recipe elsewhere in a long
    entry can never steal the claim. If the line names no repo, fall
    back to the entry's first ref ONLY when unambiguous; multi-repo
    entries stay UNVERIFIED at WARN (a gate must not false-block honest
    prose it cannot attribute — mention projects/<repo> beside the
    version to pin it). Casual version mentions on verbless lines are
    skipped entirely. Names passed via --ignore are invisible to
    attribution AND to line-scoped pins; a pin onto an ignored repo
    downgrades to an info finding so reduced coverage is never silent.
    """
    skipped = _ignore_set(ignore)
    findings = []
    for e in entries:
        text = e.text
        if not _VERSION_RE.search(text) or not _SHIP_VERB_RE.search(text):
            continue
        negated = bool(_NEGATION_RE.search(text))
        refs_all = _distinct([n for n, _ in _repo_refs_with_pos(text)
                              if n not in skipped])
        first_ref = refs_all[0] if refs_all else None
        checked: set[tuple[str, str]] = set()
        for vm in _VERSION_RE.finditer(text):
            version = f"v{vm.group(1)}.{vm.group(2)}.{vm.group(3)}"
            lo, hi = _line_bounds(text, vm.start())
            if not _SHIP_VERB_RE.search(text[lo:hi]):
                continue  # casual mention, not a claim
            if negated:
                findings.append(Finding(
                    "info", "tag", version,
                    "ship claim explicitly negated in prose; skipped", e.key))
                continue
            repo_name = _bind_repo_on_line(text, vm.start())
            if repo_name is not None and repo_name in skipped:
                if (version, repo_name) not in checked:
                    checked.add((version, repo_name))
                    findings.append(Finding(
                        "info", "tag", version,
                        f"claim pinned to projects/{repo_name}; excluded "
                        f"via --ignore", e.key))
                continue
            ambiguous = False
            if repo_name is None:
                if first_ref is None:
                    continue
                ambiguous = len(refs_all) > 1
                repo_name = first_ref
            pair = (version, repo_name)
            if pair in checked:
                continue
            checked.add(pair)
            repo = projects_root / repo_name
            if not (repo / ".git").exists():
                continue  # repo-level finding already covers this
            if ambiguous:
                findings.append(Finding(
                    "warn", "tag", version,
                    f"ship claim unverifiable: entry names "
                    f"{len(refs_all)} repos and none sits on the version's "
                    f"line (closest guess {repo_name}); write "
                    f"projects/<repo> beside {version} to pin", e.key))
                continue
            tag = _git(["tag", "-l", version], repo)
            if tag is None or tag.returncode != 0:
                findings.append(Finding("warn", "tag", version,
                                        f"could not list tags in {repo_name}",
                                        e.key))
            elif not tag.stdout.strip():
                findings.append(Finding("error", "tag", version,
                                        f"claim says shipped/tagged but no local "
                                        f"tag {version} in {repo_name}", e.key))
            else:
                findings.append(Finding("ok", "tag", version,
                                        f"local tag exists in {repo_name}", e.key))
                if remote:
                    ls = _git(["ls-remote", "--tags", "origin", version], repo,
                              timeout=30)
                    if ls and ls.returncode == 0 and not ls.stdout.strip():
                        findings.append(Finding(
                            "error", "tag", version,
                            f"tag exists locally but NOT pushed to origin", e.key))
    return findings


def _check_test_counts(entries, projects_root: Path, run_tests: bool,
                       ignore: list[str] | None = None):
    skipped = _ignore_set(ignore)
    findings = []
    for e in entries:
        m = _TESTS_RE.search(e.text)
        if not m:
            continue
        claimed = int(m.group(1) or m.group(2))
        repo_name = _bind_repo_on_line(e.text, m.start())
        if repo_name is not None and repo_name in skipped:
            findings.append(Finding(
                "info", "tests", str(claimed),
                f"claim pinned to projects/{repo_name}; excluded via "
                f"--ignore", e.key))
            continue
        ambiguous = False
        if repo_name is None:
            refs_all = _distinct([n for n, _ in _repo_refs_with_pos(e.text)
                                  if n not in skipped])
            if len(refs_all) == 1:
                repo_name = refs_all[0]
            elif refs_all:
                ambiguous = True
        if not run_tests:
            findings.append(Finding(
                "info", "tests", str(claimed),
                f"claimed test count{f' for {repo_name}' if repo_name else ''} "
                f"(use --run-tests to verify)", e.key))
            continue
        if ambiguous:
            findings.append(Finding(
                "warn", "tests", str(claimed),
                "cannot attribute test count to one repo unambiguously; "
                "mention projects/<repo> on the same line", e.key))
            continue
        target = projects_root / repo_name if repo_name else None
        if not target or not (target / "tests").is_dir():
            findings.append(Finding("warn", "tests", str(claimed),
                                    "cannot locate tests/ directory", e.key))
            continue
        try:
            proc = subprocess.run(
                ["python3", "-m", "unittest", "discover", "-s", "tests", "-t", "."],
                cwd=target, timeout=120, capture_output=True, text=True)
        except (subprocess.TimeoutExpired, OSError):
            findings.append(Finding("warn", "tests", str(claimed),
                                    "test run failed to execute", e.key))
            continue
        mm = re.search(r"^Ran (\d+) tests?", proc.stderr, re.MULTILINE)
        actual = int(mm.group(1)) if mm else None
        ok = proc.returncode == 0 and actual == claimed
        findings.append(Finding(
            "ok" if ok else "error", "tests", str(claimed),
            f"actual: {actual if actual is not None else 'unknown'} "
            f"(rc={proc.returncode})", e.key))
    return findings


def _check_freshness(entries, max_age_days: int, today: date):
    """Newest dated entry older than max_age_days => error.

    Detects the silent-rot case where the journal itself stopped being
    maintained while everything it claims still looks true. Future-dated
    entries are ignored (they cannot prove freshness). No dated entries
    at all => warn, never error (undated journals are a style, not a lie).
    """
    where = entries[0].key if entries else "<memory>:0"
    dated = [(d, e) for e in entries for d in e.dates if d <= today]
    if not dated:
        return [Finding("warn", "fresh", "journal freshness",
                        "no dated entries found; freshness unknown", where)]
    newest, newest_entry = max(dated, key=lambda de: de[0])
    age = (today - newest).days
    where = newest_entry.key
    if age > max_age_days:
        return [Finding("error", "fresh", f"newest entry {age}d old",
                        f"last journal update {newest.isoformat()} is older "
                        f"than --max-age-days {max_age_days}", where)]
    return [Finding("ok", "fresh", "journal freshness",
                    f"newest entry {age}d old ({newest.isoformat()})", where)]


def check_inline_budget(paths, limit: int):
    """Completed-section bloat watchdog: > limit inline entries => error.

    Closes the decay loop with `prune`: the finding names the exact
    repair command. Journals without a Completed section pass clean.
    Directories expand to their top-level *.md files (parse_paths rule).
    """
    findings = []
    for raw in paths:
        p = Path(raw)
        files = sorted(p.glob("*.md")) if p.is_dir() else [p]
        for f in files:
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                findings.append(Finding("warn", "bloat", str(f),
                                        f"unreadable: {exc}", str(f)))
                continue
            count, lineno = _prune.count_blocks(text)
            where = f"{f}:{lineno}" if lineno else str(f)
            if count > limit:
                findings.append(Finding(
                    "error", "bloat", f"{count} inline entries",
                    f"Completed section exceeds --max-completed {limit}; "
                    f"run: python3 -m daybreak prune {f} --write", where))
            else:
                findings.append(Finding(
                    "ok", "bloat", "inline budget",
                    f"{count} inline entries (max {limit})", where))
    return findings


def statusline(report: VerifyReport) -> str:
    """One-line summary for cron/alerting (family convention)."""
    s = report.to_json()["summary"]
    n = len(report.findings)
    if report.errors:
        first = report.errors[0]
        return (f"daybreak FAIL: {s['errors']} errors, {s['warnings']} warn "
                f"— [{first.kind}] {first.claim}: {first.detail}")
    return f"daybreak OK: {n} checks, {s['errors']} errors, {s['warnings']} warn"


def verify(entries, projects_root="~/projects", remote: bool = False,
           run_tests: bool = False, today: date | None = None,
           max_age_days: int | None = None,
           ignore: list[str] | None = None) -> VerifyReport:
    """--ignore NAME (repeatable): repo refs to exclude from ALL checks.

    For repos on a shared machine that are not the journal owner's to
    gate (an operator's own tool, a foreign checkout). Ignored names
    produce no repo findings and are invisible to claim attribution;
    claims line-pinned onto them downgrade to info so narrowing scope
    never silently fakes coverage.
    """
    root = Path(projects_root).expanduser()
    today = today or date.today()
    report = VerifyReport()
    for name in extract_repo_names(entries, ignore):
        # attribute finding to first entry mentioning it
        where = next((e.key for e in entries
                      if name in e.text), "<memory>:0")
        report.findings.extend(_check_repo(name, root, remote, where))
    report.findings.extend(_check_ship_claims(entries, root, remote, ignore))
    report.findings.extend(
        _check_test_counts(entries, root, run_tests, ignore))
    if max_age_days is not None:
        report.findings.extend(_check_freshness(entries, max_age_days, today))
    return report
