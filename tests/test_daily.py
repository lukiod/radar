"""Regression tests for tools/daily.py.

Run: python3 tests/test_daily.py
"""
import datetime
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
        daily.QUEUES = self.queues
        daily.carry_tail = lambda argv: None
        daily.run_queue = lambda *a, **k: 0
        self.asked = []

    def tearDown(self):
        daily.QUEUES = self.real_queues
        daily.carry_tail, daily.build_queue, daily.run_queue = self.real_carry, self.real_build, self.real_run
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


if __name__ == "__main__":
    unittest.main()
