"""Regression tests for tools/daily.py.

Run: python3 tests/test_daily.py
"""
import contextlib
import datetime
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import daily  # noqa: E402


class BacklogDrawTests(unittest.TestCase):
    """Only the newest backlog was ever read, so the 372 row backlog drafted
    09 19 kept 365 verified rows that were never sent while 09 21's was drawn
    down beside it. The day's quota has to be able to reach a second file."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.queues = Path(self.tmp.name)
        self.real_queues = daily.QUEUES
        self.real_carry, self.real_build, self.real_run = daily.carry_tail, daily.build_queue, daily.run_queue
        self.real_scan = daily.scan_inbox
        daily.QUEUES = self.queues
        daily.carry_tail = lambda argv: None
        daily.run_queue = lambda *a, **k: 0
        daily.scan_inbox = lambda *a, **k: (0, 0)
        self.asked = []

    def tearDown(self):
        daily.QUEUES = self.real_queues
        daily.carry_tail, daily.build_queue, daily.run_queue = self.real_carry, self.real_build, self.real_run
        daily.scan_inbox = self.real_scan
        self.tmp.cleanup()

    def _backlog(self, name, rows, age):
        p = self.queues / name
        p.write_text("".join(json.dumps({"to": f"a{i}@x.com", "domain": "x.com"}) + "\n" for i in range(rows)))
        stamp = datetime.datetime.now().timestamp() - age
        os.utime(p, (stamp, stamp))
        return p

    def _build(self, unused=0):
        """Stand in for the real builder: record what it was asked for and
        draw that many, capped at what the backlog actually holds the way the
        real one is."""
        def build(argv):
            backlog = Path(argv[argv.index("--backlog") + 1])
            out = Path(argv[argv.index("--out") + 1])
            n = int(argv[argv.index("--limit") + 1])
            available = len([l for l in backlog.read_text().splitlines() if l.strip()])
            draw = min(n, available)
            self.asked.append((backlog.name, n))
            with out.open("a", encoding="utf-8") as fh:
                for i in range(draw):
                    fh.write(json.dumps({"to": f"drawn{len(self.asked)}-{i}@x.com"}) + "\n")
        return build

    def test_an_older_backlog_is_reached_when_the_newer_runs_short(self):
        self._backlog("2026-09-19-backlog.jsonl", 365, age=3600)
        self._backlog("2026-09-21-backlog.jsonl", 10, age=10)
        daily.build_queue = self._build(0)

        daily.main(["--cap", "100"])

        self.assertEqual(self.asked, [("2026-09-21-backlog.jsonl", 100), ("2026-09-19-backlog.jsonl", 90)])

    def test_the_newest_backlog_is_asked_first(self):
        self._backlog("2026-09-19-backlog.jsonl", 365, age=3600)
        self._backlog("2026-09-21-backlog.jsonl", 200, age=10)
        daily.build_queue = self._build(0)

        daily.main(["--cap", "50"])

        self.assertEqual(self.asked, [("2026-09-21-backlog.jsonl", 50)])

    def test_every_backlog_is_offered_newest_first(self):
        self._backlog("2026-09-19-backlog.jsonl", 5, age=3600)
        self._backlog("2026-09-21-backlog.jsonl", 5, age=10)
        self.assertEqual([p.name for p in daily.backlogs_newest_first()],
                         ["2026-09-21-backlog.jsonl", "2026-09-19-backlog.jsonl"])

    def test_a_queue_short_of_the_cap_is_topped_up(self):
        """A row the verifier rejected is not pending, so a queue built to the
        cap sends fewer than the cap. Seven rows went that way in the 09 22
        queue alone, and nothing drew their replacements."""
        self._backlog("2026-09-21-backlog.jsonl", 200, age=10)
        daily.build_queue = self._build(0)
        today = self.queues / (datetime.date.today().isoformat() + ".jsonl")
        rows = [{"to": f"p{i}@x.com"} for i in range(93)]
        rows += [{"to": f"r{i}@x.com", "rejected": "copy claims ['form'] missing, the live site has it"}
                 for i in range(7)]
        today.write_text("".join(json.dumps(r) + "\n" for r in rows))

        daily.main(["--cap", "100"])

        self.assertEqual(self.asked, [("2026-09-21-backlog.jsonl", 7)])

    def test_a_queue_that_has_begun_sending_is_not_topped_up(self):
        """A queue that already put rows out is not refilled, because topping
        it up would mix two days in one file."""
        self._backlog("2026-09-21-backlog.jsonl", 200, age=10)
        daily.build_queue = self._build(0)
        today = self.queues / (datetime.date.today().isoformat() + ".jsonl")
        rows = [{"to": "sent@x.com", "message_id": "m1"}]
        rows += [{"to": f"p{i}@x.com"} for i in range(50)]
        today.write_text("".join(json.dumps(r) + "\n" for r in rows))

        daily.main(["--cap", "100"])

        self.assertEqual(self.asked, [])

    def test_the_run_reads_the_mailbox_before_it_sends(self):
        """The machine exists to produce a reply. A run that sends and says
        nothing about the answer that arrived leaves the only thing worth
        money sitting in a mailbox until somebody happens to look.

        Before the send, not after: run_queue reads suppression.txt once when
        it starts, so a reply recorded afterwards arrives a day late and the
        address is mailed once more in the meantime."""
        self._backlog("2026-09-21-backlog.jsonl", 200, age=10)
        daily.build_queue = self._build(0)
        order = []
        daily.scan_inbox = lambda days, **k: (order.append("mailbox") or (0, 0))
        daily.run_queue = lambda *a, **k: (order.append("send") or 0)

        daily.main(["--cap", "5"])

        self.assertEqual(order, ["mailbox", "send"])

    def test_the_read_applies_what_it_finds(self):
        self._backlog("2026-09-21-backlog.jsonl", 200, age=10)
        daily.build_queue = self._build(0)
        asked = []
        daily.scan_inbox = lambda days, **k: (asked.append(k) or (0, 0))

        daily.main(["--cap", "5"])

        self.assertTrue(asked[0].get("apply_bounces"))
        self.assertTrue(asked[0].get("apply_replies"))

    def test_a_dry_run_looks_without_writing(self):
        """A dry run that suppressed addresses or stopped sequences would
        make the one command safe to try the one that changes things."""
        self._backlog("2026-09-21-backlog.jsonl", 200, age=10)
        daily.build_queue = self._build(0)
        asked = []
        daily.scan_inbox = lambda days, **k: (asked.append(k) or (0, 0))

        daily.main(["--cap", "5", "--dry-run"])

        self.assertFalse(asked[0].get("apply_bounces"))
        self.assertFalse(asked[0].get("apply_replies"))

    def test_a_day_that_already_sent_still_reads_the_mailbox(self):
        """A queue that has gone out returns early. The read used to sit
        after that return, so on every re run of a sent day the mailbox went
        unread, which is most of the times this runs."""
        p = self.queues / (datetime.date.today().isoformat() + ".jsonl")
        p.write_text(json.dumps({"to": "a@x.com", "message_id": "m1"}) + "\n")
        asked = []
        daily.scan_inbox = lambda days, **k: (asked.append(days) or (0, 0))

        daily.main(["--cap", "5"])

        self.assertEqual(len(asked), 1)

    def test_a_reply_is_shouted_about(self):
        self._backlog("2026-09-21-backlog.jsonl", 200, age=10)
        daily.build_queue = self._build(0)
        daily.scan_inbox = lambda days, **k: (2, 0)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            daily.main(["--cap", "5"])
        out = buf.getvalue()
        self.assertIn("2 REPLY OR REPLIES WAITING IN THE MAILBOX", out)


if __name__ == "__main__":
    unittest.main()
