import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from daybreak.cli import main
from daybreak.prune import count_blocks


def build_journal(sessions):
    parts = [
        "# Builder State\n",
        "\n",
        "## Active projects\n",
        "- active entry s99\n",
        "  wrapped active line\n",
        "\n",
        "## Completed\n",
    ]
    for s in sessions:
        parts.append(f"- **s{s} product session — thing v1.0** (tagged): notes\n")
        parts.append("  continuation detail\n")
        parts.append("\n")
    parts.append("## Lab\n")
    parts.append("- lab log line\n")
    return "".join(parts)


def run_main(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = main(argv)
    return rc, out.getvalue(), err.getvalue()


class TestCountBlocks(unittest.TestCase):
    def test_counts_completed_bullets_only(self):
        n, lineno = count_blocks(build_journal([1, 2, 3]))
        self.assertEqual(n, 3)
        self.assertEqual(lineno, 7)

    def test_active_bullets_not_counted(self):
        text = build_journal([])
        n, _ = count_blocks(text)
        self.assertEqual(n, 0)

    def test_no_completed_section(self):
        n, lineno = count_blocks("# x\n- bullet\n")
        self.assertEqual((n, lineno), (0, None))


class TestVerifyInlineBudget(unittest.TestCase):
    def test_over_budget_is_error_naming_repair(self):
        with tempfile.TemporaryDirectory() as td:
            j = Path(td) / "STATE.md"
            j.write_text(build_journal(list(range(1, 16))), encoding="utf-8")
            rc, out, _ = run_main(
                ["verify", str(j), "--max-completed", "10", "--json"])
            data = json.loads(out)
            bloat = [f for f in data["findings"] if f["kind"] == "bloat"]
            self.assertEqual(rc, 1)
            self.assertEqual(bloat[0]["severity"], "error")
            self.assertIn("15 inline entries", bloat[0]["claim"])
            self.assertIn("prune", bloat[0]["detail"])
            self.assertIn(str(j), bloat[0]["detail"])

    def test_under_budget_is_ok_and_exit_zero(self):
        with tempfile.TemporaryDirectory() as td:
            j = Path(td) / "STATE.md"
            j.write_text(build_journal([1, 2]), encoding="utf-8")
            rc, out, _ = run_main(
                ["verify", str(j), "--max-completed", "10", "--json"])
            data = json.loads(out)
            bloat = [f for f in data["findings"] if f["kind"] == "bloat"]
            self.assertEqual(rc, 0)
            self.assertEqual(bloat[0]["severity"], "ok")

    def test_off_by_default(self):
        with tempfile.TemporaryDirectory() as td:
            j = Path(td) / "STATE.md"
            j.write_text(build_journal(list(range(1, 40))), encoding="utf-8")
            rc, out, _ = run_main(["verify", str(j), "--json"])
            data = json.loads(out)
            self.assertFalse([f for f in data["findings"]
                              if f["kind"] == "bloat"])
            self.assertEqual(rc, 0)

    def test_directory_expansion(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a.md").write_text(build_journal(list(range(1, 13))))
            (root / "b.md").write_text(build_journal([1]))
            rc, out, _ = run_main(
                ["verify", str(root), "--max-completed", "5", "--json"])
            data = json.loads(out)
            errs = [f for f in data["findings"]
                    if f["kind"] == "bloat" and f["severity"] == "error"]
            self.assertEqual(len(errs), 1)
            self.assertIn("a.md", errs[0]["where"])

    def test_statusline_reports_bloat_error(self):
        with tempfile.TemporaryDirectory() as td:
            j = Path(td) / "STATE.md"
            j.write_text(build_journal(list(range(1, 9))))
            rc, out, _ = run_main(
                ["verify", str(j), "--max-completed", "3", "--statusline"])
            self.assertEqual(rc, 1)
            self.assertIn("daybreak FAIL", out)
            self.assertIn("[bloat]", out)


class TestPruneStatusline(unittest.TestCase):
    def _journal(self, td):
        j = Path(td) / "j.md"
        j.write_text(build_journal(list(range(1, 16))), encoding="utf-8")
        return j

    def test_dryrun_shape(self):
        with tempfile.TemporaryDirectory() as td:
            rc, out, _ = run_main(
                ["prune", str(self._journal(td)), "--keep-last", "10",
                 "--statusline"])
            self.assertEqual(rc, 0)
            self.assertIn("daybreak PRUNE: would archive 5 entries", out)

    def test_write_shape_and_idempotence(self):
        with tempfile.TemporaryDirectory() as td:
            j = self._journal(td)
            rc, out, _ = run_main(
                ["prune", str(j), "--keep-last", "10", "--write",
                 "--statusline"])
            self.assertEqual(rc, 0)
            self.assertIn("daybreak PRUNE: archived 5 entries", out)
            self.assertIn("→", out)
            rc2, out2, _ = run_main(
                ["prune", str(j), "--keep-last", "10", "--statusline"])
            self.assertEqual(rc2, 0)
            self.assertIn("nothing to archive", out2)


class TestDecayLoop(unittest.TestCase):
    """The v1.3.0 story: verify flags bloat -> prune repairs -> verify clean."""

    def test_watchdog_then_prune_then_clean(self):
        with tempfile.TemporaryDirectory() as td:
            j = Path(td) / "STATE.md"
            j.write_text(build_journal(list(range(1, 21))), encoding="utf-8")
            rc1, _, _ = run_main(
                ["verify", str(j), "--max-completed", "12", "--json"])
            self.assertEqual(rc1, 1)
            rc2, out2, _ = run_main(
                ["prune", str(j), "--keep-last", "12", "--write"])
            self.assertEqual(rc2, 0)
            self.assertTrue(Path(str(j).replace(".md", "-archive.md"))
                            .exists())
            rc3, out3, _ = run_main(
                ["verify", str(j), "--max-completed", "12", "--json"])
            self.assertEqual(rc3, 0)
            data = json.loads(out3)
            self.assertFalse([f for f in data["findings"]
                              if f["kind"] == "bloat"
                              and f["severity"] == "error"])
            # archive holds exactly the moved blocks, verbatim order
            arch = Path(str(j).replace(".md", "-archive.md")).read_text()
            self.assertIn("**s1 ", arch)
            self.assertIn("**s8 ", arch)
            self.assertNotIn("**s20 ", arch)


if __name__ == "__main__":
    unittest.main()
