"""Regression tests for the daily send budget in tools/gmail_send.py.

Run: python3 tests/test_gmail_send.py
"""
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import gmail_send  # noqa: E402
from gmail_send import count_sent_today, reserve_send, today_utc  # noqa: E402


def queue(dirpath, name, rows):
    p = Path(dirpath) / name
    p.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return p


def sent_today(n):
    return [{"to": f"a{i}@x.com", "sent_at": today_utc() + "T00:00:00Z"} for i in range(n)]


class BudgetTests(unittest.TestCase):
    """The cap that protects the sending domain is the mailbox total, and the
    per queue limit does not bound it. Three senders on three queues each
    honour their own limit and the mailbox still sends three times as much."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.queues = Path(self.tmp.name)
        self.budget = Path(self.tmp.name) / "send-budget.json"
        self._real = gmail_send.SEND_BUDGET
        gmail_send.SEND_BUDGET = self.budget
        self.addCleanup(lambda: setattr(gmail_send, "SEND_BUDGET", self._real))

    def test_every_sender_counts_toward_one_mailbox_total(self):
        # Two other queues already sent 8 today. A third sender starts its own
        # queue at zero and must still see 8 spent, not 0.
        queue(self.queues, "one.jsonl", sent_today(5))
        queue(self.queues, "two.jsonl", sent_today(3))
        queue(self.queues, "three.jsonl", [{"to": "new@x.com"}])

        self.assertEqual(count_sent_today(self.queues), 8)
        for expected in range(9, 11):
            self.assertEqual(reserve_send(10, self.queues), expected)
        self.assertIsNone(reserve_send(10, self.queues))

    def test_a_backlog_does_not_spend_budget(self):
        # Backlog rows are the pool the queue is drawn from, and a copy of a
        # sent row there would otherwise be counted a second time.
        queue(self.queues, "2026-09-21.jsonl", sent_today(2))
        queue(self.queues, "2026-09-19-backlog.jsonl", sent_today(50))
        self.assertEqual(count_sent_today(self.queues), 2)

    def test_yesterday_does_not_spend_today(self):
        queue(self.queues, "old.jsonl", [{"to": "a@x.com", "sent_at": "2026-01-01T00:00:00Z"}])
        self.assertEqual(count_sent_today(self.queues), 0)
        self.assertEqual(reserve_send(1, self.queues), 1)

    def test_the_remaining_budget_survives_a_recount(self):
        # The counter is only a witness; the queues are the record. A counter
        # that has fallen behind must not hand back slots already spent.
        queue(self.queues, "one.jsonl", sent_today(4))
        self.assertEqual(reserve_send(10, self.queues), 5)
        queue(self.queues, "two.jsonl", sent_today(3))
        # 4 + 3 already went out, so the next reservation is the eighth.
        self.assertEqual(reserve_send(10, self.queues), 8)

    def test_a_row_with_no_sent_at_is_not_a_send(self):
        queue(self.queues, "q.jsonl", [{"to": "a@x.com"}, {"to": "b@x.com", "suppressed": True}])
        self.assertEqual(count_sent_today(self.queues), 0)

    def test_the_cap_is_read_as_utc_days(self):
        stamp = json.loads(self.budget.read_text())["date"] if self.budget.exists() else None
        reserve_send(5, self.queues)
        self.assertEqual(json.loads(self.budget.read_text())["date"], today_utc())
        self.assertEqual(today_utc(), datetime.now(timezone.utc).strftime("%Y-%m-%d"))
        self.assertNotEqual(stamp, "1970-01-01")

    def test_an_unreadable_counter_is_not_fatal(self):
        self.budget.write_text("{not json", encoding="utf-8")
        self.assertEqual(reserve_send(5, self.queues), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
