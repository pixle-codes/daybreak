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


def extract_repo_names(entries) -> list[str]:
    names: list[str] = []
    for e in entries:
        for m in _REPO_RE.finditer(e.text):
            n = m.group(1).rstrip(".")
            if n not in names:
                names.append(n)
        for m in _GH_RE.finditer(e.text):
            n = m.group(2)
            if "/" in n:  # trailing .git / path fragments
                n = n.split("/")[0]
            n = n.removesuffix(".git")
            if n not in names:
                names.append(n)
    return names


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


def _check_ship_claims(entries, projects_root: Path, remote: bool):
    """vX.Y.Z + ship verb + repo ref in the same entry => tag must exist."""
    findings = []
    for e in entries:
        vm = _VERSION_RE.search(e.text)
        if not vm or not _SHIP_VERB_RE.search(e.text):
            continue
        if _NEGATION_RE.search(e.text):
            findings.append(Finding(
                "info", "tag", f"v{vm.group(1)}.{vm.group(2)}.{vm.group(3)}",
                "ship claim explicitly negated in prose; skipped", e.key))
            continue
        rm = _REPO_RE.search(e.text) or _GH_RE.search(e.text)
        if not rm:
            continue
        repo_name = (_REPO_RE.search(e.text).group(1) if _REPO_RE.search(e.text)
                     else _GH_RE.search(e.text).group(2).removesuffix(".git"))
        version = f"v{vm.group(1)}.{vm.group(2)}.{vm.group(3)}"
        repo = projects_root / repo_name
        if not (repo / ".git").exists():
            continue  # repo-level finding already covers this
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


def _check_test_counts(entries, projects_root: Path, run_tests: bool):
    findings = []
    for e in entries:
        m = _TESTS_RE.search(e.text)
        if not m:
            continue
        claimed = int(m.group(1) or m.group(2))
        rm = _REPO_RE.search(e.text) or _GH_RE.search(e.text)
        repo_name = None
        if rm:
            repo_name = (_REPO_RE.search(e.text).group(1) if _REPO_RE.search(e.text)
                         else _GH_RE.search(e.text).group(2).removesuffix(".git"))
        if not run_tests:
            findings.append(Finding(
                "info", "tests", str(claimed),
                f"claimed test count{f' for {repo_name}' if repo_name else ''} "
                f"(use --run-tests to verify)", e.key))
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


def verify(entries, projects_root="~/projects", remote: bool = False,
           run_tests: bool = False, today: date | None = None) -> VerifyReport:
    root = Path(projects_root).expanduser()
    report = VerifyReport()
    for name in extract_repo_names(entries):
        # attribute finding to first entry mentioning it
        where = next((e.key for e in entries
                      if name in e.text), "<memory>:0")
        report.findings.extend(_check_repo(name, root, remote, where))
    report.findings.extend(_check_ship_claims(entries, root, remote))
    report.findings.extend(_check_test_counts(entries, root, run_tests))
    return report
