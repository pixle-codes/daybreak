import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

from daybreak.cli import main


def _make_unreadable(path: str):
    os.chmod(path, 0o000)
    def _restore():
        os.chmod(path, 0o644)
        os.unlink(path)
    return _restore


class TestUnreadableInput(unittest.TestCase):
    """Unreadable (EACCES) file args die honest rc2, never traceback rc1."""

    def run_cli(self, args):
        import io
        from contextlib import redirect_stderr, redirect_stdout
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(args)
        return code, out.getvalue(), err.getvalue()

    def unreadable_journal(self) -> str:
        p = self.write_journal("## A\n- one entry\n")
        self.addCleanup(_make_unreadable(p))
        return p

    def write_journal(self, content: str) -> str:
        f = tempfile.NamedTemporaryFile("w", suffix=".md", delete=False)
        f.write(content)
        f.close()
        return f.name

    def test_verify_unreadable_file_arg_rc2(self):
        p = self.unreadable_journal()
        code, _, err = self.run_cli(["verify", p])
        self.assertEqual(code, 2)
        self.assertIn("daybreak:", err)
        self.assertIn(Path(p).name, err)

    def test_digest_unreadable_file_arg_rc2(self):
        p = self.unreadable_journal()
        code, _, err = self.run_cli(["digest", p])
        self.assertEqual(code, 2)
        self.assertIn("daybreak:", err)
        self.assertIn(Path(p).name, err)

    def test_dupes_unreadable_file_arg_rc2(self):
        p = self.unreadable_journal()
        code, _, err = self.run_cli(["dupes", p])
        self.assertEqual(code, 2)
        self.assertIn("daybreak:", err)

    def test_stats_unreadable_file_arg_rc2(self):
        p = self.unreadable_journal()
        code, _, err = self.run_cli(["stats", p])
        self.assertEqual(code, 2)
        self.assertIn("daybreak:", err)

    def test_prune_unreadable_journal_rc2(self):
        p = self.unreadable_journal()
        code, _, err = self.run_cli(["prune", p])
        self.assertEqual(code, 2)
        self.assertIn("daybreak:", err)
        self.assertIn(Path(p).name, err)

    def test_directory_with_unreadable_md_fails_naming_path(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        good = Path(d.name) / "a.md"
        good.write_text("## S\n- file a note\n")
        bad = Path(d.name) / "b.md"
        bad.write_text("## S\n- file b note\n")
        self.addCleanup(_make_unreadable(str(bad)))
        code, _, err = self.run_cli(["stats", d.name])
        self.assertEqual(code, 2)
        self.assertIn("b.md", err)

    def test_subprocess_no_traceback(self):
        p = self.unreadable_journal()
        r = subprocess.run(
            [sys.executable, "-m", "daybreak", "verify", p],
            capture_output=True, text=True, cwd=REPO)
        self.assertEqual(r.returncode, 2)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("daybreak:", r.stderr)


class TestCli(unittest.TestCase):
    def run_cli(self, args):
        import io
        from contextlib import redirect_stderr, redirect_stdout
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(args)
        return code, out.getvalue(), err.getvalue()

    def write_journal(self, content: str) -> str:
        f = tempfile.NamedTemporaryFile("w", suffix=".md", delete=False)
        f.write(content)
        f.close()
        self.addCleanup(os.unlink, f.name)
        return f.name

    def test_missing_file_exit_2(self):
        code, _, err = self.run_cli(["digest", "/no/such/file.md"])
        self.assertEqual(code, 2)
        self.assertIn("daybreak:", err)

    def test_digest_ok_exit_0(self):
        p = self.write_journal("## A\n- NEXT (x): thing #pinned\n")
        code, out, _ = self.run_cli(["digest", p, "--budget", "500",
                                     "--today", "2026-08-23"])
        self.assertEqual(code, 0)
        self.assertIn("thing", out)

    def test_digest_json_shape(self):
        p = self.write_journal("## A\n- NEXT (x): thing #pinned\n")
        code, out, _ = self.run_cli(["digest", p, "--json", "--today",
                                     "2026-08-23"])
        data = json.loads(out)
        self.assertIn("must_keep", data)
        self.assertEqual(data["entries_seen"], 1)

    def test_verify_exit_codes(self):
        # missing repo -> error -> exit 1
        p = self.write_journal("- see projects/definitely-missing-repo-xyz\n")
        code, _, _ = self.run_cli(["verify", p,
                                   "--projects-root", "/tmp/definitely-absent"])
        self.assertEqual(code, 1)
        # clean journal (no claims) -> exit 0
        q = self.write_journal("## Notes\n- nothing checkable here\n")
        code2, out2, _ = self.run_cli(["verify", q])
        self.assertEqual(code2, 0)

    def test_dupes_exit_1_on_finding(self):
        p = self.write_journal(
            "## L\n- same words alpha beta gamma\n"
            "## M\n- same words alpha beta gamma\n")
        code, out, _ = self.run_cli(["dupes", p])
        self.assertEqual(code, 1)
        self.assertIn("near-duplicate pair(s)", out)
        cj, outj, _ = None, None, None
        codej, outj, _ = self.run_cli(["dupes", p, "--json"])
        self.assertEqual(codej, 1)
        self.assertEqual(json.loads(outj)["count"], 1)

    def test_stats_json(self):
        p = self.write_journal("## A\n- one entry\n")
        code, out, _ = self.run_cli(["stats", p])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["entries"], 1)

    def test_directory_input(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        (Path(d.name) / "a.md").write_text("## S\n- file a note\n")
        (Path(d.name) / "b.txt").write_text("ignored")
        code, out, _ = self.run_cli(["stats", d.name])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["entries"], 1)


if __name__ == "__main__":
    unittest.main()
