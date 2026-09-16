"""Regression tests for the booking/form checks in tools/site_audit.py.

Run: python3 tests/test_site_audit.py
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from site_audit import has_online_booking  # noqa: E402


class AcuityShortLinkTests(unittest.TestCase):
    def test_as_me_link_counts_as_booking(self):
        html = '<a href="https://cotruststaxes.as.me/">Schedule Now</a>'
        self.assertTrue(has_online_booking(html.lower()))

    def test_bare_as_me_mention_without_link_still_counts(self):
        # BOOKING_TOOLS matches on page text, so a scheduler embedded via a
        # script tag rather than a plain anchor is still caught.
        html = '<script>var w = "https://cotruststaxes.as.me/schedule.js";</script>'
        self.assertTrue(has_online_booking(html.lower()))


class ScriptSwallowedAnchorTests(unittest.TestCase):
    """A JS comparator like `x<a` inside an inline <script> reads to the
    regex as an unclosed <a> tag; without stripping script/style content
    first, everything up to the next </a> or </button> on the page becomes
    that anchor's "text", which can hide or fabricate a booking match."""

    def test_js_comparator_does_not_swallow_the_real_booking_link(self):
        html = (
            '<script>function f(a,n){if(n<a){return 1}}</script>'
            '<a href="/contact">Contact us</a>'
            '<a href="https://acme.as.me/">Schedule Now</a>'
        )
        self.assertTrue(has_online_booking(html.lower()))

    def test_js_comparator_alone_is_not_booking(self):
        html = '<script>function f(a,n){if(n<a){return "book now"}}</script><a href="/about">About</a>'
        self.assertFalse(has_online_booking(html.lower()))


if __name__ == "__main__":
    unittest.main()
