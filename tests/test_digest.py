import unittest
from datetime import date

from daybreak.digest import build_digest, select, digest_json
from daybreak.parser import parse_text

TODAY = date(2026, 8, 23)

JOURNAL = """\
# State

## Active projects
- alpha project does things
  - NEXT (alpha): finish M4 next session #pinned
- beta project other things

## Completed
- old completed thing from long ago 2025-01-01

## OWNER VERDICT (binding)
- do not build lookalikes

## Lab
- Session counter: 2; NEXT SESSION is lab

## Lessons learned
- unittest discover needs -t .
- another lesson entirely
- """ + ("padding lesson text repeated over and over " * 12) + """
"""


def entries():
    return parse_text(JOURNAL, today=TODAY)


class TestDigest(unittest.TestCase):
    def test_must_keeps_always_present_even_tiny_budget(self):
        out = build_digest(entries(), budget=10, today=TODAY)
        self.assertIn("do not build lookalikes", out)      # owner verdict
        self.assertIn("Session counter: 2", out)           # lab section
        self.assertIn("finish M4 next session", out)       # pinned + NEXT

    def test_budget_exhausted_by_musts_leaves_no_fills(self):
        from daybreak.score import is_must_keep
        es = entries()
        must_cost = sum(len(e.text) + 32 for e in es if is_must_keep(e))
        must, fills = select(es, must_cost, TODAY)
        self.assertTrue(must)
        for e, _ in fills:
            self.assertIsNotNone(_)  # at most trimmed entries fit

    def test_active_outranks_completed(self):
        from daybreak.score import score as sc
        es = entries()
        active = next(e for e in es if e.section == "Active projects"
                      and "NEXT" not in e.text)
        completed = next(e for e in es if e.section == "Completed")
        self.assertGreater(sc(active, TODAY), sc(completed, TODAY))
        _, fills = select(es, 10**6, TODAY)
        order = [e.key for e, _ in fills]
        self.assertLess(order.index(active.key), order.index(completed.key))

    def test_deterministic(self):
        a = build_digest(entries(), budget=1500, today=TODAY)
        b = build_digest(entries(), budget=1500, today=TODAY)
        self.assertEqual(a, b)

    def test_trim_marker(self):
        out = build_digest(entries(), budget=600, today=TODAY)
        self.assertIn("…", out)

    def test_json_shape(self):
        data = digest_json(entries(), 3000, TODAY)
        self.assertIn("must_keep", data)
        self.assertIn("fills", data)
        for item in data["must_keep"]:
            self.assertTrue(item["must"])
        for item in data["fills"]:
            self.assertFalse(item["must"])
            self.assertIn("trimmed_at", item)


def _mk(e):
    from daybreak.score import is_must_keep
    return is_must_keep(e)


if __name__ == "__main__":
    unittest.main()
