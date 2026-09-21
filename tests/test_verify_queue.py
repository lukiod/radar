"""Regression tests for tools/verify_queue.py.

Run: python3 tests/test_verify_queue.py
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import verify_queue  # noqa: E402


class LiveLeaksTests(unittest.TestCase):
    """A claim of "missing" only stands if the check still says missing."""

    def test_unreadable_site_is_not_a_pass(self):
        # None, not an empty list: "could not read it" must never look like
        # "read it and found nothing", which is what a false claim came from.
        original = verify_queue.fetch
        verify_queue.fetch = lambda *a, **k: (_ for _ in ()).throw(OSError("down"))
        try:
            self.assertIsNone(verify_queue.live_leaks("example.com"))
        finally:
            verify_queue.fetch = original

    def test_challenge_page_is_not_a_pass(self):
        original = verify_queue.fetch
        body = b"<html><title>Just a moment...</title></html>"
        verify_queue.fetch = lambda *a, **k: ("https://example.com/", 200, 0.1, 0.2, body)
        try:
            self.assertIsNone(verify_queue.live_leaks("example.com"))
        finally:
            verify_queue.fetch = original

    def test_missing_checks_come_back_as_leaks(self):
        original = verify_queue.fetch
        verify_queue.fetch = lambda *a, **k: ("https://example.com/", 200, 0.1, 0.2, b"<html>hi</html>")
        try:
            self.assertEqual(verify_queue.live_leaks("example.com"), ["booking", "form", "mobile", "tel"])
        finally:
            verify_queue.fetch = original


class VerdictTests(unittest.TestCase):
    """A row carries one verdict, and a rejected row carries none of the other.

    Rejection left an earlier pass's verified in place, so a queue could hold a
    row marked both verified and rejected. Nothing was sent on it, because every
    reader checks rejected too, but the row was unreadable by eye, which is what
    the field is for.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.queue = Path(self.tmp.name) / "q.jsonl"
        self.real = verify_queue.live_leaks

    def tearDown(self):
        verify_queue.live_leaks = self.real
        self.tmp.cleanup()

    def _row(self, domain, leaks, **extra):
        row = {"to": f"a@{domain}", "domain": domain, "verified": True,
               "evidence": {"leaks": leaks}}
        row.update(extra)
        return row

    def _write(self, rows):
        self.queue.write_text("".join(json.dumps(r) + "\n" for r in rows))

    def _run(self):
        verify_queue.main([str(self.queue), "--write"])
        return [json.loads(l) for l in self.queue.read_text().splitlines() if l.strip()]

    def test_a_row_that_still_holds_is_verified_and_not_rejected(self):
        self._write([self._row("a.com", ["booking"])])
        verify_queue.live_leaks = lambda d: ["booking"]
        got = self._run()
        self.assertTrue(got[0].get("verified"))
        self.assertNotIn("rejected", got[0])

    def test_a_claim_that_no_longer_holds_drops_its_verified(self):
        self._write([self._row("a.com", ["form"])])
        verify_queue.live_leaks = lambda d: []
        got = self._run()
        self.assertNotIn("verified", got[0])
        self.assertIn("the live site has it", got[0]["rejected"])

    def test_a_site_that_cannot_be_read_drops_its_verified(self):
        self._write([self._row("a.com", ["booking"])])
        verify_queue.live_leaks = lambda d: None
        got = self._run()
        self.assertNotIn("verified", got[0])
        self.assertIn("could not be read", got[0]["rejected"])

    def test_a_rejected_row_is_not_checked_again(self):
        self._write([self._row("a.com", ["booking"], rejected="stale")])
        seen = []
        verify_queue.live_leaks = lambda d: (seen.append(d) or ["booking"])
        self._run()
        self.assertEqual(seen, [])


if __name__ == "__main__":
    unittest.main()
