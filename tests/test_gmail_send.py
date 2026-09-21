"""Regression tests for the daily send budget in tools/gmail_send.py.

Run: python3 tests/test_gmail_send.py
"""
import io
import json
import sys
import tempfile
import time
import unittest
import urllib.error
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


class TokenRefreshTests(unittest.TestCase):
    """The credential file every send depends on.

    It was written by opening the real path with "w" and dumping into it, so
    the file was truncated before the replacement bytes existed. A crash in
    between left a file json cannot parse, and every send fails on an
    unparseable credential file until somebody reads it. The refresh runs once
    per row and the day's send is now a timer nobody is watching, which is what
    makes the window worth closing.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.real_dir = gmail_send.TOKEN_DIR
        gmail_send.TOKEN_DIR = self.tmp.name
        self.dir = Path(self.tmp.name)
        self.creds = self.dir / "credentials.json"
        (self.dir / "gcp-oauth.keys.json").write_text(
            json.dumps({"installed": {"client_id": "i", "client_secret": "s"}}), encoding="utf-8")
        self.creds.write_text(json.dumps(
            {"access_token": "old", "refresh_token": "r", "expiry_date": 0}), encoding="utf-8")
        original = gmail_send.urllib.request.urlopen
        gmail_send.urllib.request.urlopen = lambda *a, **k: io.BytesIO(
            json.dumps({"access_token": "new", "expires_in": 3600}).encode())
        self.addCleanup(setattr, gmail_send.urllib.request, "urlopen", original)
        self.addCleanup(setattr, gmail_send, "TOKEN_DIR", self.real_dir)
        self.addCleanup(self.tmp.cleanup)

    def test_a_refresh_replaces_the_file_and_leaves_it_parseable(self):
        self.assertEqual(gmail_send.access_token(), "new")
        self.assertEqual(json.loads(self.creds.read_text(encoding="utf-8"))["access_token"], "new")

    def test_no_temp_file_is_left_behind(self):
        gmail_send.access_token()
        self.assertEqual(list(self.dir.glob("*.tmp")), [])

    def test_a_write_that_fails_leaves_the_old_credentials_intact(self):
        """The point of the temp file. With the old code the real path was
        already truncated at this moment, so the file read back empty and the
        next send could not authenticate at all."""
        def boom(*a, **k):
            raise OSError("disk full")
        original = gmail_send.json.dump
        gmail_send.json.dump = boom
        self.addCleanup(setattr, gmail_send.json, "dump", original)
        with self.assertRaises(OSError):
            gmail_send.access_token()
        self.assertEqual(json.loads(self.creds.read_text(encoding="utf-8"))["access_token"], "old")
        self.assertEqual(list(self.dir.glob("*.tmp")), [])

    def test_a_live_token_is_used_without_a_refresh(self):
        self.creds.write_text(json.dumps(
            {"access_token": "live", "refresh_token": "r",
             "expiry_date": int((time.time() + 3600) * 1000)}), encoding="utf-8")
        self.assertEqual(gmail_send.access_token(), "live")

    def test_a_forced_refresh_replaces_a_token_that_looks_live(self):
        """What the 401 retry needs. A token the file calls live is exactly the
        case a plain read will not refresh, so the retry has to ask."""
        self.creds.write_text(json.dumps(
            {"access_token": "live", "refresh_token": "r",
             "expiry_date": int((time.time() + 3600) * 1000)}), encoding="utf-8")
        self.assertEqual(gmail_send.access_token(force=True), "new")


class SendRetryTests(unittest.TestCase):
    """A 401 on a live looking token abandons the whole queue.

    Three queues did that today, each stopping where it stood. The token is
    rejected while the file still calls it live because the credential file has
    a second holder that refreshes it mid run. One forced refresh and one retry
    turns that into a recovered row, and a real auth failure into the same stop
    as before.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.queue = self.dir / "q.jsonl"
        self.queue.write_text(
            json.dumps({"to": "a@x.com", "subject": "s", "body": "b"}) + "\n", encoding="utf-8")

        self.real_dir = gmail_send.TOKEN_DIR
        gmail_send.TOKEN_DIR = self.tmp.name
        self.addCleanup(setattr, gmail_send, "TOKEN_DIR", self.real_dir)
        (self.dir / "gcp-oauth.keys.json").write_text(
            json.dumps({"installed": {"client_id": "i", "client_secret": "s"}}), encoding="utf-8")
        (self.dir / "credentials.json").write_text(json.dumps(
            {"access_token": "live", "refresh_token": "r",
             "expiry_date": int((time.time() + 3600) * 1000)}), encoding="utf-8")
        original = gmail_send.urllib.request.urlopen
        gmail_send.urllib.request.urlopen = lambda *a, **k: io.BytesIO(
            json.dumps({"access_token": "new", "expires_in": 3600}).encode())
        self.addCleanup(setattr, gmail_send.urllib.request, "urlopen", original)

        self.slots = []

        def reserve(cap, d):
            self.slots.append(1)
            return len(self.slots)

        for name, value in (("profile_address", lambda t: "me@x.com"),
                            ("domain_accepts_mail", lambda d: True),
                            ("reserve_send", reserve)):
            self.addCleanup(setattr, gmail_send, name, getattr(gmail_send, name))
            setattr(gmail_send, name, value)
        self.tokens = []

    def patch_send(self, outcomes):
        """Hand back the queue's send calls: an exception instance to raise, or
        a result dict to return."""
        def fake(token, sender, row):
            self.tokens.append(token)
            out = outcomes[len(self.tokens) - 1]
            if isinstance(out, Exception):
                raise out
            return out
        self.addCleanup(setattr, gmail_send, "send", gmail_send.send)
        gmail_send.send = fake

    def unauthorised(self):
        return urllib.error.HTTPError(
            "https://gmail.googleapis.com/x", 401, "Unauthorized", {}, io.BytesIO(b"{}"))

    def row(self):
        return json.loads(self.queue.read_text(encoding="utf-8").strip())

    def test_a_401_is_retried_once_with_a_freshly_refreshed_token(self):
        self.patch_send([self.unauthorised(), {"id": "m1", "threadId": "t1"}])
        self.assertEqual(gmail_send.run_queue(str(self.queue), 0, 0, False), 1)
        self.assertEqual(self.tokens, ["live", "new"])
        self.assertEqual(self.row()["message_id"], "m1")
        self.assertNotIn("error", self.row())

    def test_the_retry_spends_one_slot_of_todays_budget_not_two(self):
        """The slot is taken before the send, so a retry must not take another
        one. Two would count one letter twice against the mailbox cap."""
        self.patch_send([self.unauthorised(), {"id": "m2", "threadId": "t2"}])
        gmail_send.run_queue(str(self.queue), 0, 0, False)
        self.assertEqual(len(self.slots), 1)

    def test_a_second_401_still_gives_up_the_queue(self):
        """The retry recovers a raced token, it does not paper over dead auth.
        The row is left pending so a later run picks it up."""
        self.patch_send([self.unauthorised(), self.unauthorised()])
        self.assertEqual(gmail_send.run_queue(str(self.queue), 0, 0, False), 0)
        self.assertEqual(self.tokens, ["live", "new"])
        self.assertNotIn("message_id", self.row())
        self.assertEqual(self.row()["error"], "HTTP 401")

    def test_another_error_is_not_retried(self):
        self.patch_send([urllib.error.HTTPError(
            "https://gmail.googleapis.com/x", 400, "Bad Request", {}, io.BytesIO(b"{}"))])
        self.assertEqual(gmail_send.run_queue(str(self.queue), 0, 0, False), 0)
        self.assertEqual(self.tokens, ["live"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
