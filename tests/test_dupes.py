import json
import unittest
from datetime import date
from pathlib import Path

from daybreak.dupes import find_dupes, normalize, stats
from daybreak.parser import parse_text

TODAY = date(2026, 8, 23)

DUP_JOURNAL = """\
## Lessons learned
- unittest discover needs -t . for relative imports in tests
- totally different topic about docker networking bridges

## Completed
- unittest discover needs -t . for relative imports in tests
"""


class TestDupes(unittest.TestCase):
    def test_identical_entries_across_sections_found(self):
        es = parse_text(DUP_JOURNAL, today=TODAY)
        pairs = find_dupes(es)
        self.assertEqual(len(pairs), 1)
        self.assertGreaterEqual(pairs[0]["ratio"], 0.99)
        self.assertNotEqual(pairs[0]["a"]["section"],
                            pairs[0]["b"]["section"])

    def test_unrelated_not_flagged(self):
        md = ("- alpha beta gamma delta epsilon\n"
              "- completely other words here now\n")
        es = parse_text(md, today=TODAY)
        self.assertEqual(find_dupes(es), [])

    def test_normalize(self):
        self.assertEqual(normalize("Hello, WORLD!!"), "hello world")


class TestStats(unittest.TestCase):
    def test_shape(self):
        es = parse_text(DUP_JOURNAL, today=TODAY)
        s = stats(es, today=TODAY)
        self.assertEqual(s["entries"], 3)
        self.assertEqual(s["next_pointers"], 0)
        self.assertIn("Lessons learned", s["sections"])
        self.assertEqual(s["sections"]["Lessons learned"], 2)
        self.assertEqual(s["est_tokens"], s["total_chars"] // 4)

    def test_next_pointer_counted(self):
        md = "## Active\n- NEXT (m4): do the thing\n"
        es = parse_text(md, today=TODAY)
        self.assertEqual(stats(es, TODAY)["next_pointers"], 1)

    def test_oldest_date(self):
        md = "## C\n- old thing 2025-01-01\n- new thing Aug 20\n"
        es = parse_text(md, today=TODAY)
        s = stats(es, TODAY)
        self.assertEqual(s["oldest_dated_entry"], "2025-01-01")
        self.assertEqual(s["dated_entries"], 2)


if __name__ == "__main__":
    unittest.main()
