import os
import subprocess
import tempfile
import unittest
from datetime import date
from pathlib import Path

from daybreak.parser import parse_text
from daybreak.verify import (extract_repo_names, verify, _check_repo,
                              Finding, statusline)

TODAY = date(2026, 8, 23)


def run(args, cwd):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True)


def make_repo(root: Path, name: str, dirty=False, tag=None, commit_file=True):
    repo = root / name
    repo.mkdir(parents=True)
    for a in (("init", "-q"), ("config", "user.email", "t@t"),
              ("config", "user.name", "t")):
        run(["git", *a], repo)
    if commit_file:
        (repo / "f.txt").write_text("x\n")
        run(["git", "add", "-A"], repo)
        run(["git", "commit", "-qm", "init"], repo)
    if tag:
        run(["git", "tag", tag], repo)
    if dirty:
        (repo / "g.txt").write_text("uncommitted\n")
    return repo


class TestExtraction(unittest.TestCase):
    def test_repo_refs(self):
        md = "- work in `projects/agentpatch` and github.com/foo/bar-baz.git\n"
        es = parse_text(md, today=TODAY)
        self.assertEqual(extract_repo_names(es), ["agentpatch", "bar-baz"])

    def test_dedup_names(self):
        md = "- projects/x again projects/x\n"
        es = parse_text(md, today=TODAY)
        self.assertEqual(extract_repo_names(es), ["x"])


class TestVerify(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_missing_repo_is_error(self):
        es = parse_text("- see projects/ghost\n", today=TODAY)
        rep = verify(es, projects_root=self.root, today=TODAY)
        self.assertEqual(len(rep.errors), 1)
        self.assertIn("directory missing", rep.errors[0].detail)
        self.assertEqual(rep.exit_code(), 1)

    def test_dirty_repo_is_error(self):
        make_repo(self.root, "dirtyrepo", dirty=True)
        es = parse_text("- touch projects/dirtyrepo\n", today=TODAY)
        rep = verify(es, projects_root=self.root, today=TODAY)
        kinds = {(f.kind, f.severity) for f in rep.findings}
        self.assertIn(("repo", "error"), kinds)
        self.assertTrue(any("dirty" in f.detail for f in rep.errors))

    def test_clean_repo_ok_no_upstream_warn(self):
        make_repo(self.root, "cleanrepo")
        es = parse_text("- touch projects/cleanrepo\n", today=TODAY)
        rep = verify(es, projects_root=self.root, today=TODAY)
        self.assertEqual(rep.errors, [])
        self.assertEqual(len(rep.warnings), 1)   # no upstream
        self.assertEqual(rep.exit_code(), 0)

    def test_ship_claim_without_tag_is_error(self):
        make_repo(self.root, "tagless")
        md = "- shipped v2.3.4 of projects/tagless today\n"
        es = parse_text(md, today=TODAY)
        rep = verify(es, projects_root=self.root, today=TODAY)
        tag_errors = [f for f in rep.errors if f.kind == "tag"]
        self.assertEqual(len(tag_errors), 1)
        self.assertIn("v2.3.4", tag_errors[0].claim)

    def test_ship_claim_with_tag_is_ok(self):
        make_repo(self.root, "tagged", tag="v1.2.3")
        md = "- released v1.2.3 of projects/tagged\n"
        es = parse_text(md, today=TODAY)
        rep = verify(es, projects_root=self.root, today=TODAY)
        self.assertEqual([f for f in rep.errors if f.kind == "tag"], [])

    def test_negated_ship_claim_downgrades_to_info(self):
        make_repo(self.root, "honest")
        md = ("- pair mode SHIPPED in projects/honest; v0.3.0 in code, "
              "NO TAG YET\n")
        es = parse_text(md, today=TODAY)
        rep = verify(es, projects_root=self.root, today=TODAY)
        tag_findings = [f for f in rep.findings if f.kind == "tag"]
        self.assertEqual(len(tag_findings), 1)
        self.assertEqual(tag_findings[0].severity, "info")

    def test_version_without_verb_ignored(self):
        make_repo(self.root, "quiet")
        md = "- bumped to v9.9.9 in projects/quiet changelog\n"
        es = parse_text(md, today=TODAY)
        rep = verify(es, projects_root=self.root, today=TODAY)
        self.assertEqual([f for f in rep.findings if f.kind == "tag"], [])

    def test_verb_elsewhere_but_not_on_version_line_is_skipped(self):
        # s75 regression class: entry-wide verb steals a casual mention.
        make_repo(self.root, "casual", tag="v1.0.0")
        md = ("- released v1.0.0 of projects/casual\n"
              "  battery mentions v2.0.0 incidentally\n")
        es = parse_text(md, today=TODAY)
        rep = verify(es, projects_root=self.root, today=TODAY)
        tags = [f for f in rep.findings if f.kind == "tag"]
        self.assertEqual([t.claim for t in tags], ["v1.0.0"])

    def test_line_level_binding_beats_first_ref(self):
        # THE s75 bug: first-ref recipe repo must not steal the claim.
        make_repo(self.root, "rightone", tag="v0.9.9")   # first ref HAS tag
        make_repo(self.root, "wrongone")                 # claimed repo does NOT
        md = ("- NEXT: cd ~/projects/rightone && python3 -m rightone\n"
              "  shipped v0.9.9 of projects/wrongone\n")
        es = parse_text(md, today=TODAY)
        rep = verify(es, projects_root=self.root, today=TODAY)
        tag_errors = [f for f in rep.errors if f.kind == "tag"]
        self.assertEqual(len(tag_errors), 1)
        self.assertIn("wrongone", tag_errors[0].detail)

    def test_nearest_ref_on_same_line_wins(self):
        make_repo(self.root, "left")
        make_repo(self.root, "right")
        md = "- compare projects/left and projects/right: shipped v3.2.1\n"
        es = parse_text(md, today=TODAY)
        rep = verify(es, projects_root=self.root, today=TODAY)
        tag_errors = [f for f in rep.errors if f.kind == "tag"]
        self.assertEqual(len(tag_errors), 1)
        self.assertIn("in right", tag_errors[0].detail)

    def test_github_ref_binds_on_line(self):
        make_repo(self.root, "ghrepo")
        md = "- shipped v1.0.0, see github.com/pixle-codes/ghrepo\n"
        es = parse_text(md, today=TODAY)
        rep = verify(es, projects_root=self.root, today=TODAY)
        tag_errors = [f for f in rep.errors if f.kind == "tag"]
        self.assertEqual(len(tag_errors), 1)
        self.assertIn("ghrepo", tag_errors[0].detail)

    def test_multi_repo_no_line_ref_is_warn_never_error(self):
        make_repo(self.root, "alpha")
        make_repo(self.root, "beta")
        md = ("- worked across projects/alpha and projects/beta all day;\n"
              "  later: shipped v5.5.5 finally\n")
        es = parse_text(md, today=TODAY)
        rep = verify(es, projects_root=self.root, today=TODAY)
        tag_findings = [f for f in rep.findings if f.kind == "tag"]
        self.assertEqual(len(tag_findings), 1)
        self.assertEqual(tag_findings[0].severity, "warn")
        self.assertEqual(rep.exit_code(), 0)

    def test_single_repo_entry_fallback_still_checks(self):
        make_repo(self.root, "only")
        md = ("- hacked on projects/only all day\n"
              "  later: shipped v7.7.7\n")
        es = parse_text(md, today=TODAY)
        rep = verify(es, projects_root=self.root, today=TODAY)
        tag_errors = [f for f in rep.errors if f.kind == "tag"]
        self.assertEqual(len(tag_errors), 1)

    def test_duplicate_versions_dedupe(self):
        make_repo(self.root, "dup")
        md = ("- shipped v1.1.1 of projects/dup; shipped v1.1.1 again\n")
        es = parse_text(md, today=TODAY)
        rep = verify(es, projects_root=self.root, today=TODAY)
        tags = [f for f in rep.findings if f.kind == "tag"]
        self.assertEqual(len(tags), 1)

    def test_distinct_versions_each_checked(self):
        make_repo(self.root, "multi")
        md = ("- shipped v1.0.0 then shipped v2.0.0 of projects/multi\n")
        es = parse_text(md, today=TODAY)
        rep = verify(es, projects_root=self.root, today=TODAY)
        tag_errors = [f for f in rep.errors if f.kind == "tag"]
        self.assertEqual({t.claim for t in tag_errors},
                         {"v1.0.0", "v2.0.0"})

    def test_test_count_line_binding(self):
        make_repo(self.root, "counted")
        make_repo(self.root, "other")
        md = ("- swept projects/other too\n"
              "  42 stdlib tests green in projects/counted\n")
        es = parse_text(md, today=TODAY, )
        rep = verify(es, projects_root=self.root,
                     run_tests=False, today=TODAY)
        notes = [f for f in rep.findings if f.kind == "tests"]
        self.assertEqual(len(notes), 1)
        self.assertIn("for counted", notes[0].detail)

    def test_remote_unpushed_tag_still_error_after_binding(self):
        make_repo(self.root, "pushed", tag="v1.5.0")
        md = "- shipped v1.5.0 of projects/pushed\n"
        es = parse_text(md, today=TODAY)
        # no origin remote -> ls-remote fails silently (returns None-ish);
        # pin that this path stays a no-warn ok finding
        rep = verify(es, projects_root=self.root, remote=True, today=TODAY)
        oks = [f for f in rep.findings
               if f.kind == "tag" and f.severity == "ok"]
        self.assertEqual(len(oks), 1)

    def test_test_count_info_without_run_tests_flag(self):
        make_repo(self.root, "tested")
        md = "- 42 stdlib tests green in projects/tested\n"
        es = parse_text(md, today=TODAY)
        rep = verify(es, projects_root=self.root,
                     run_tests=False, today=TODAY)
        notes = [f for f in rep.findings if f.kind == "tests"]
        self.assertEqual(notes[0].severity, "info")

    def test_run_tests_compares_count(self):
        repo = make_repo(self.root, "suite")
        (repo / "tests").mkdir()
        (repo / "tests" / "__init__.py").write_text("")
        (repo / "tests" / "test_a.py").write_text(
            "import unittest\n"
            "class A(unittest.TestCase):\n"
            "    def test_x(self):\n"
            "        self.assertTrue(True)\n")
        md = "- 1 stdlib tests green in projects/suite\n"
        es = parse_text(md, today=TODAY)
        rep = verify(es, projects_root=self.root,
                     run_tests=True, today=TODAY)
        notes = [f for f in rep.findings if f.kind == "tests"]
        self.assertEqual(notes[0].severity, "ok")
        # now claim the wrong number
        es2 = parse_text("- 5 stdlib tests green in projects/suite\n",
                         today=TODAY)
        rep2 = verify(es2, projects_root=self.root,
                      run_tests=True, today=TODAY)
        bad = [f for f in rep2.findings if f.kind == "tests"]
        self.assertEqual(bad[0].severity, "error")

    def test_json_roundtrip(self):
        make_repo(self.root, "any")
        es = parse_text("- projects/any here\n", today=TODAY)
        data = verify(es, projects_root=self.root).to_json()
        self.assertIn("findings", data)
        self.assertIn("summary", data)


class TestFreshness(unittest.TestCase):
    def _md(self, body):
        return parse_text(body, today=TODAY)

    def test_stale_journal_is_error(self):
        es = self._md("- entry from long ago Aug 1 2026\n")
        rep = verify(es, max_age_days=7, today=TODAY)
        fresh = [f for f in rep.findings if f.kind == "fresh"]
        self.assertEqual(len(fresh), 1)
        self.assertEqual(fresh[0].severity, "error")
        self.assertEqual(rep.exit_code(), 1)

    def test_recent_journal_is_ok(self):
        es = self._md(f"- fresh entry {TODAY.isoformat()}\n")
        rep = verify(es, max_age_days=7, today=TODAY)
        fresh = [f for f in rep.findings if f.kind == "fresh"]
        self.assertEqual(fresh[0].severity, "ok")
        self.assertEqual(rep.exit_code(), 0)

    def test_boundary_exactly_max_age_passes(self):
        es = self._md("- boundary entry Aug 16 2026\n")
        rep = verify(es, max_age_days=7, today=TODAY)
        fresh = [f for f in rep.findings if f.kind == "fresh"]
        self.assertEqual(fresh[0].severity, "ok")

    def test_no_dated_entries_warns_not_errors(self):
        es = self._md("- undated entry with no claims at all\n")
        rep = verify(es, max_age_days=7, today=TODAY)
        fresh = [f for f in rep.findings if f.kind == "fresh"]
        self.assertEqual(fresh[0].severity, "warn")
        self.assertEqual(rep.exit_code(), 0)

    def test_future_dates_ignored_for_freshness(self):
        # Sep 20 resolves to a past occurrence (2025) relative to TODAY,
        # so use an explicit future date instead.
        es = self._md("- future entry 2099-01-01\n")
        rep = verify(es, max_age_days=7, today=TODAY)
        fresh = [f for f in rep.findings if f.kind == "fresh"]
        self.assertEqual(fresh[0].severity, "warn")  # nothing dated <= today
        self.assertEqual(rep.exit_code(), 0)

    def test_off_by_default(self):
        es = self._md("- old entry Aug 1 2026\n")
        rep = verify(es, today=TODAY)
        self.assertEqual([f for f in rep.findings if f.kind == "fresh"], [])

    def test_where_points_at_newest_entry(self):
        es = self._md("- old entry Aug 1 2026\n"
                      "- newer entry Aug 20 2026\n")
        rep = verify(es, max_age_days=2, today=TODAY)
        fresh = [f for f in rep.findings if f.kind == "fresh"]
        self.assertTrue(fresh[0].where.endswith(":2"))


class TestStatusline(unittest.TestCase):
    def test_ok_line_when_clean(self):
        es = parse_text("- nothing checkable here\n", today=TODAY)
        line = statusline(verify(es, max_age_days=30, today=TODAY))
        self.assertTrue(line.startswith("daybreak OK:"))
        self.assertNotIn("FAIL", line)

    def test_fail_line_carries_first_error(self):
        es = parse_text("- see projects/ghost\nAug 1 2026 note\n",
                        today=TODAY)
        rep = verify(es, max_age_days=3, today=TODAY)
        line = statusline(rep)
        self.assertTrue(line.startswith("daybreak FAIL:"))
        # one-line contract: counts + FIRST error only (repo sorts before fresh)
        self.assertIn("[repo]", line)
        self.assertNotIn("[fresh]", line)
        self.assertIn("2 errors", line)  # both errors still counted


if __name__ == "__main__":
    unittest.main()
