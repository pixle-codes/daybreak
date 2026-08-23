import os
import subprocess
import tempfile
import unittest
from datetime import date
from pathlib import Path

from daybreak.parser import parse_text
from daybreak.verify import (extract_repo_names, verify, _check_repo,
                             Finding)

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


if __name__ == "__main__":
    unittest.main()
