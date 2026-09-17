"""Regression tests for tools/verify_queue.py.

Run: python3 tests/test_verify_queue.py
"""
import sys
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


if __name__ == "__main__":
    unittest.main()
