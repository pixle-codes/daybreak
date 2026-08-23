import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from daybreak.cli import main
from daybreak.prune import (apply_prune, completed_bounds, prune_lines,
                            split_blocks)


def build_journal(sessions=(39, 37, 31), marker=True):
    parts = [
        "# Builder State\n",
        "\n",
        "## Active projects\n",
        "- active entry s39\n",
        "  wrapped active line\n",
        "\n",
        "## Completed\n",
    ]
    for s in sessions:
        parts.append(f"- **s{s} product session — thing v1.0** (tagged): notes\n")
        parts.append("  continuation detail one\n")
        parts.append("  continuation detail two\n")
        parts.append("\n")
    if marker:
        parts.append("- markerless entry that must stay forever\n")
        parts.append("\n")
    parts.append("## Lab\n")
    parts.append("- lab log s40 line\n")
    parts.append("\n")
    parts.append("## Parked / blacklist (research-killed)\n")
    parts.append("- parked lead stays\n")
    return "".join(parts)


class TestPruneUnit(unittest.TestCase):
    def test_completed_bounds(self):
        lines = build_journal().splitlines(keepends=True)
        lo, hi = completed_bounds(lines)
        self.assertIn("## Completed", lines[lo - 1])
        self.assertEqual(lines[hi].rstrip(), "## Lab")

    def test_roundtrip_when_nothing_stale(self):
        text = build_journal()
        new_text, moves = prune_lines(text, keep_last=99)
        self.assertEqual(moves, [])
        self.assertIs(new_text, text)

    def test_moves_only_older_than_window(self):
        text = build_journal(sessions=(39, 37, 31))
        new_text, moves = prune_lines(text, keep_last=1)
        self.assertEqual([m["session"] for m in moves], [37, 31])
        self.assertIn("s39 product session", new_text)
        self.assertNotIn("s37 product session", new_text)
        self.assertNotIn("s31 product session", new_text)

    def test_continuation_lines_move_with_block(self):
        text = build_journal()
        new_text, moves = prune_lines(text, keep_last=1)
        moved = [m for m in moves if m["session"] == 37]
        self.assertEqual(len(moved), 1)
        lines = text.splitlines(keepends=True)
        block = "".join(lines[moved[0]["start"]:moved[0]["end"]])
        self.assertIn("continuation detail one", block)
        # trailing blank belongs to the block; next bullet untouched
        self.assertTrue(block.endswith("\n\n"))
        # next bullet starts exactly where the block ended
        self.assertTrue(lines[moved[0]["end"]].startswith("- **"))

    def test_markerless_block_never_moves(self):
        text = build_journal()
        new_text, _ = prune_lines(text, keep_last=1, before=None)
        self.assertIn("markerless entry", new_text)

    def test_before_selector_is_strictly_older(self):
        text = build_journal(sessions=(35, 31))
        _, moves = prune_lines(text, before=32)
        self.assertEqual([m["session"] for m in moves], [31])
        _, moves = prune_lines(text, before=31)
        self.assertEqual([m["session"] for m in moves], [])

    def test_other_sections_byte_identical(self):
        text = build_journal()
        new_text, moves = prune_lines(text, before=100)
        lines_old = text.splitlines(keepends=True)
        lines_new = new_text.splitlines(keepends=True)
        removed = set()
        for m in moves:
            removed.update(range(m["start"], m["end"]))
        kept_old = [ln for i, ln in enumerate(lines_old) if i not in removed]
        self.assertEqual(kept_old, lines_new)

    def test_no_completed_section_is_noop(self):
        text = "# Journal\n## Lab\n- note\n"
        new_text, moves = prune_lines(text)
        self.assertEqual(new_text, text)
        self.assertEqual(moves, [])

    def test_second_run_is_idempotent(self):
        text = build_journal()
        once, moves1 = prune_lines(text, keep_last=1)
        twice, moves2 = prune_lines(once, keep_last=1)
        self.assertEqual(twice, once)
        self.assertEqual(moves2, [])


class TestPruneApply(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.dir = Path(d.name)
        self.journal = self.dir / "STATE.md"
        self.journal.write_text(build_journal(), encoding="utf-8")

    def test_write_backup_archive_and_shrink(self):
        old = self.journal.read_text(encoding="utf-8")
        new_text, moves = prune_lines(old, keep_last=1)
        info = apply_prune(self.journal, new_text, moves,
                           self.dir / "STATE-archive.md", old)
        self.assertEqual(info["moved"], 2)
        self.assertEqual(
            (self.dir / "STATE.md.prune-bak").read_text(encoding="utf-8"), old)
        written = self.journal.read_text(encoding="utf-8")
        self.assertEqual(written, new_text)
        archive = (self.dir / "STATE-archive.md").read_text(encoding="utf-8")
        self.assertIn("Archived journal entries", archive)
        self.assertIn("s37 product session", archive)
        self.assertIn("s31 product session", archive)
        self.assertIn("continuation detail two", archive)
        self.assertNotIn("s39 product session", archive)
        # no temp litter left behind
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()),
                         ["STATE-archive.md", "STATE.md", "STATE.md.prune-bak"])

    def test_append_preserves_existing_archive(self):
        arch = self.dir / "STATE-archive.md"
        arch.write_text("# Archived journal entries\n\n- prior block s10\n",
                        encoding="utf-8")
        old = self.journal.read_text(encoding="utf-8")
        new_text, moves = prune_lines(old, keep_last=1)
        apply_prune(self.journal, new_text, moves, arch, old)
        text = arch.read_text(encoding="utf-8")
        self.assertIn("prior block s10", text)
        self.assertIn("s37 product session", text)
        self.assertEqual(text.count("Archived journal entries"), 1)

    def test_split_blocks_never_crosses_heading(self):
        lines = build_journal().splitlines(keepends=True)
        lo, hi = completed_bounds(lines)
        blocks = split_blocks(lines, lo, hi)
        last_start, last_end = blocks[-1]
        self.assertLessEqual(last_end, hi)


class TestPruneCli(unittest.TestCase):
    def run_cli(self, args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(args)
        return code, out.getvalue(), err.getvalue()

    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.dir = Path(d.name)
        self.journal = self.dir / "STATE.md"
        self.journal.write_text(build_journal(), encoding="utf-8")

    def test_dry_run_default_leaves_file_alone(self):
        before = self.journal.read_text(encoding="utf-8")
        code, out, _ = self.run_cli(["prune", str(self.journal),
                                     "--keep-last", "1"])
        self.assertEqual(code, 0)
        self.assertIn("would archive 2 block(s)", out)
        self.assertEqual(self.journal.read_text(encoding="utf-8"), before)
        self.assertEqual(list(self.dir.iterdir()), [self.journal])

    def test_write_flag_applies_with_default_archive_name(self):
        code, out, _ = self.run_cli(["prune", str(self.journal),
                                     "--keep-last", "1", "--write"])
        self.assertEqual(code, 0)
        self.assertIn("archived 2 block(s)", out)
        self.assertIn("STATE-archive.md", out)
        self.assertFalse((self.dir / "STATE.md").read_text(
            encoding="utf-8").startswith("# Builder State\n\n## Active"
                                         " projects\n- active entry s39\n"
                                         "  wrapped active line\n- **s37"))

    def test_mutually_exclusive_selectors_exit_2(self):
        code, _, err = self.run_cli(["prune", str(self.journal),
                                     "--keep-last", "2", "--before", "30"])
        self.assertEqual(code, 2)
        self.assertIn("mutually exclusive", err)

    def test_keep_last_below_one_exit_2(self):
        code, _, err = self.run_cli(["prune", str(self.journal),
                                     "--keep-last", "0"])
        self.assertEqual(code, 2)
        self.assertIn(">= 1", err)

    def test_missing_file_exit_2(self):
        code, _, err = self.run_cli(["prune", str(self.dir / "nope.md")])
        self.assertEqual(code, 2)
        self.assertIn("daybreak:", err)

    def test_json_mode_shape(self):
        code, out, _ = self.run_cli(["prune", str(self.journal),
                                     "--before", "38", "--json"])
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["mode"], "dry-run")
        self.assertEqual(data["moved"], 2)
        self.assertEqual([b["session"] for b in data["blocks"]], [37, 31])
        self.assertGreater(data["bytes_before"], data["bytes_after"])

    def test_write_then_digest_still_parses(self):
        from daybreak.parser import parse_file
        self.run_cli(["prune", str(self.journal), "--keep-last", "1",
                      "--write"])
        entries = parse_file(self.journal)
        sections = {e.section for e in entries}
        self.assertIn("Completed", sections)
        self.assertIn("Lab", sections)
        self.assertTrue(any("markerless" in e.text for e in entries))


if __name__ == "__main__":
    unittest.main()
