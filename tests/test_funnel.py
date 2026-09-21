"""Regression tests for tools/funnel.py.

Run: python3 tests/test_funnel.py
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import funnel  # noqa: E402


class CountingTests(unittest.TestCase):
    """The reply rate is the only number that says whether the outreach
    works, so the counts it is built from have to be read off the rows
    rather than tallied by hand."""

    def test_a_sent_row_is_counted_and_an_unsent_one_is_runway(self):
        rows = [
            {"to": "a@x.com", "message_id": "1", "sent_at": "2026-09-16T07:00:00Z", "verified": True},
            {"to": "b@x.com", "verified": True},
            {"to": "c@x.com"},
        ]
        sent, first, follow, runway = funnel.counts(rows)
        self.assertEqual([r["to"] for r in sent], ["a@x.com"])
        self.assertEqual(len(first), 1)
        self.assertEqual(len(follow), 0)
        self.assertEqual([r["to"] for r in runway], ["b@x.com"])

    def test_a_follow_up_is_not_counted_as_a_first_touch(self):
        rows = [
            {"to": "a@x.com", "message_id": "1", "sent_at": "2026-09-16T07:00:00Z"},
            {"to": "b@x.com", "message_id": "2", "sent_at": "2026-09-21T05:00:00Z", "follow_up_of": "1"},
        ]
        _, first, follow, _ = funnel.counts(rows)
        self.assertEqual(len(first), 1)
        self.assertEqual(len(follow), 1)

    def test_a_row_that_cannot_be_sent_is_not_runway(self):
        rows = [
            {"to": "a@x.com", "verified": True, "rejected": "claim no longer holds"},
            {"to": "b@x.com", "verified": True, "suppressed": True},
            {"to": "c@x.com", "verified": True, "undeliverable": True},
            {"to": "d@x.com", "verified": True, "message_id": "9", "sent_at": "2026-09-16T07:00:00Z"},
            {"to": "e@x.com"},
        ]
        _, _, _, runway = funnel.counts(rows)
        self.assertEqual(runway, [])

    def test_the_day_table_splits_first_touches_from_follow_ups(self):
        rows = [
            {"to": "a@x.com", "message_id": "1", "sent_at": "2026-09-16T07:00:00Z"},
            {"to": "b@x.com", "message_id": "2", "sent_at": "2026-09-16T08:00:00Z"},
            {"to": "c@x.com", "message_id": "3", "sent_at": "2026-09-21T05:00:00Z", "follow_up_of": "1"},
        ]
        self.assertEqual(funnel.by_day(rows), {"2026-09-16": [2, 0], "2026-09-21": [0, 1]})


if __name__ == "__main__":
    unittest.main()
