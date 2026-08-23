import unittest
from datetime import date

from daybreak.parser import parse_text, extract_dates


class TestParser(unittest.TestCase):
    def test_sections_and_entries(self):
        md = "# T\n## Active\n- alpha one\n- alpha two\n## Parked\n- beta\n"
        es = parse_text(md)
        self.assertEqual([e.section for e in es], ["Active", "Active", "Parked"])
        self.assertEqual([e.line for e in es], [3, 4, 6])

    def test_nested_bullets_fold_into_parent(self):
        md = "## S\n- parent\n  - child a\n  - child b\n- next top\n"
        es = parse_text(md)
        self.assertEqual(len(es), 2)
        self.assertIn("child a", es[0].text)
        self.assertIn("child b", es[0].text)
        self.assertTrue(es[1].text.startswith("- next top"))

    def test_blank_line_ends_entry(self):
        md = "## S\n- first line\ncontinuation\n\n- second\n"
        es = parse_text(md)
        self.assertEqual(len(es), 2)
        self.assertIn("continuation", es[0].text)

    def test_paragraph_entry(self):
        md = "# H\nplain prose here\nmore prose\n\n## Next\n- x\n"
        es = parse_text(md)
        self.assertEqual(es[0].section, "H")
        self.assertIn("more prose", es[0].text)

    def test_bullet_after_paragraph_opens_new_entry(self):
        md = "prose line\n- bullet after prose\n"
        es = parse_text(md)
        self.assertEqual(len(es), 2)
        self.assertEqual(es[0].text, "prose line")
        self.assertTrue(es[1].text.startswith("- bullet"))

    def test_headings_never_in_entry_text(self):
        md = "## Sec Name\n- body\n"
        es = parse_text(md)
        self.assertNotIn("Sec Name", "\n".join(e.text for e in es))

    def test_tags_extracted(self):
        md = "- keep this #pinned and #lab-only\n"
        es = parse_text(md)
        self.assertEqual(es[0].tags, frozenset({"pinned", "lab-only"}))

    def test_iso_date(self):
        md = "- happened 2026-08-23 ok\n"
        es = parse_text(md, today=date(2026, 8, 23))
        self.assertEqual(es[0].dates, (date(2026, 8, 23),))

    def test_yearless_future_resolves_last_year(self):
        today = date(2026, 8, 23)
        es = parse_text("- due Aug 26 thing\n", today=today)
        self.assertEqual(es[0].dates, (date(2025, 8, 26),))

    def test_dated_with_explicit_year(self):
        es = parse_text("- dies Nov 30 2027\n", today=date(2026, 8, 23))
        self.assertEqual(es[0].dates, (date(2027, 11, 30),))

    def test_day_month_order(self):
        es = parse_text("- 30 Nov 2026 deadline\n", today=date(2026, 8, 23))
        self.assertEqual(es[0].dates, (date(2026, 11, 30),))

    def test_invalid_dates_ignored(self):
        es = parse_text("- 2026-13-45 and Feb 30\n", today=date(2026, 8, 23))
        self.assertEqual(es[0].dates, ())

    def test_extract_dates_standalone(self):
        d = extract_dates("a 2024-01-02 b Sep 9", date(2026, 8, 23))
        self.assertEqual(d, (date(2024, 1, 2), date(2025, 9, 9)))

    def test_empty_and_heading_only(self):
        self.assertEqual(parse_text(""), [])
        self.assertEqual(parse_text("## just a heading\n"), [])


if __name__ == "__main__":
    unittest.main()
