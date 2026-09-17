"""Regression tests for tools/queue_from_backlog.py.

Run: PYTHONPATH=. python3 tests/test_queue_from_backlog.py
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from queue_from_backlog import select  # noqa: E402


def row(domain, metro, **kw):
    r = {"domain": domain, "metro": metro, "to": "info@" + domain}
    r.update(kw)
    return r


class SelectTests(unittest.TestCase):
    def test_only_gated_rows_are_eligible(self):
        rows = [row("a.com", "denver", verified=True),
                row("b.com", "denver"),                       # never gated
                row("c.com", "denver", verified=True, rejected="stale"),
                row("d.com", "denver", verified=True, message_id="sent")]
        self.assertEqual([r["domain"] for r in select(rows, 10)], ["a.com"])

    def test_metros_are_drawn_round_robin(self):
        rows = [row(f"d{i}.com", "denver", verified=True) for i in range(5)]
        rows += [row(f"p{i}.com", "phoenix", verified=True) for i in range(5)]
        picked = [r["domain"] for r in select(rows, 4)]
        self.assertEqual(picked, ["d0.com", "p0.com", "d1.com", "p1.com"])

    def test_a_thin_metro_does_not_stall_the_rest(self):
        rows = [row("d0.com", "denver", verified=True), row("d1.com", "denver", verified=True)]
        rows += [row(f"p{i}.com", "phoenix", verified=True) for i in range(4)]
        picked = [r["domain"] for r in select(rows, 6)]
        self.assertEqual(picked, ["d0.com", "p0.com", "d1.com", "p1.com", "p2.com", "p3.com"])

    def test_the_limit_is_respected_when_rows_run_out(self):
        rows = [row(f"d{i}.com", "denver", verified=True) for i in range(3)]
        self.assertEqual(len(select(rows, 40)), 3)

    def test_selection_is_stable(self):
        rows = [row(f"d{i}.com", "denver", verified=True) for i in range(5)]
        rows += [row(f"p{i}.com", "phoenix", verified=True) for i in range(5)]
        self.assertEqual([r["domain"] for r in select(rows, 6)],
                         [r["domain"] for r in select(rows, 6)])


if __name__ == "__main__":
    unittest.main()
