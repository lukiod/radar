"""Regression tests for the booking/form checks in tools/site_audit.py.

Run: python3 tests/test_site_audit.py
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from site_audit import has_form  # noqa: E402
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


class OpaqueVendorLinkTests(unittest.TestCase):
    """A booking-worded link to a completely different domain is a
    scheduling vendor even when the URL carries no book/schedule keyword,
    e.g. a short link like dental4.me/practice/1."""

    def test_opaque_external_scheduler_link_counts(self):
        html = '<a href="https://dental4.me/oasisdental/1">Schedule Adults</a>'
        self.assertTrue(has_online_booking(html.lower(), domain="oasisdentalhealth.com"))

    def test_link_to_own_domain_does_not_count_without_a_keyword(self):
        html = '<a href="https://oasisdentalhealth.com/contact/">Book an appointment</a>'
        self.assertFalse(has_online_booking(html.lower(), domain="oasisdentalhealth.com"))

    def test_social_link_with_booking_word_does_not_count(self):
        html = '<a href="https://www.facebook.com/oasisdental/">Book now on Facebook</a>'
        self.assertFalse(has_online_booking(html.lower(), domain="oasisdentalhealth.com"))

    def test_iframe_embedded_scheduler_counts(self):
        html = '<iframe src="https://app.acuityscheduling.com/schedule.php?owner=1"></iframe>'
        self.assertTrue(has_online_booking(html.lower(), domain="example.com"))

    def test_unrelated_iframe_does_not_count(self):
        html = '<iframe src="https://www.youtube.com/embed/abc123"></iframe>'
        self.assertFalse(has_online_booking(html.lower(), domain="example.com"))


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



class EmbeddedFormWidgetTests(unittest.TestCase):
    """A widget-rendered form leaves no <form> tag, so it read as absent."""

    def test_leadconnector_form_widget_is_a_form(self):
        html = '<html><body><iframe src="https://api.leadconnectorhq.com/widget/form/2cxlcvlqidiusuaagpf8"></iframe></body></html>'
        self.assertTrue(has_form(html.lower()))

    def test_msgsndr_survey_widget_is_a_form(self):
        html = '<html><body><iframe src="https://msgsndr.com/widget/survey/bii4npzhr2y1t9e9w4fa"></iframe></body></html>'
        self.assertTrue(has_form(html.lower()))

    def test_review_widget_is_not_a_form(self):
        # Review widgets collect nothing; counting them would hide real leaks.
        for src in ("https://services.leadconnectorhq.com/reputation/widgets/review_widget/yapexotbeuz5qvlqeyi0",
                    "https://reviews.solutionreach.com/vs/reviews/north_haven_family_dentistry"):
            self.assertFalse(has_form(f'<html><body><iframe src="{src}"></iframe></body></html>'.lower()), src)

    def test_plain_form_and_js_builder_still_count(self):
        self.assertTrue(has_form('<html><form action="/x">'.lower()))
        self.assertTrue(has_form('<html><div class="wpforms-form">'.lower()))
        self.assertFalse(has_form('<html><body><p>call us</p></body></html>'.lower()))

    def test_squarespace_form_block_is_a_form(self):
        # rooterproplumbingga.com ships 12 sqs-form-block classes, no <form>.
        html = '<html><body><div class="sqs-block form-block sqs-block-form"><div class="sqs-form-block-context"></div></div></body></html>'
        self.assertTrue(has_form(html.lower()))

    def test_generic_form_wrapper_alone_is_not_a_form(self):
        # "form-wrapper" appears on pages with no form at all.
        self.assertFalse(has_form('<html><body><div class="form-wrapper"></div></body></html>'.lower()))


if __name__ == "__main__":
    unittest.main()
