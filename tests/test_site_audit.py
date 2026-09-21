"""Regression tests for the booking/form checks in tools/site_audit.py.

Run: python3 tests/test_site_audit.py
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from site_audit import JUNK_EMAIL, PLACEHOLDER_LOCAL, find_emails, has_form, is_parked, redirect_target  # noqa: E402
from site_audit import has_online_booking  # noqa: E402


class JunkAddressTests(unittest.TestCase):
    """srcset filenames parse as addresses. Shipping .webp means sending to a
    filename, and the placeholder check has to be exact or it eats real ones."""

    def test_image_filenames_are_not_addresses(self):
        html = 'srcset="az-specialist@2x.webp 2x, header-logo@2x.webp 2x"'
        self.assertEqual([e for e in find_emails(html.lower(), "1800theeagle.com") if "webp" in e], [])

    def test_a_placeholder_is_not_an_address(self):
        html = "write to example@gmail.com or test@acmedental.com"
        self.assertEqual(find_emails(html.lower(), "acmedental.com"), [])

    def test_a_real_address_ending_in_example_is_kept(self):
        html = "careexample@gmail.com"
        self.assertEqual(find_emails(html.lower(), "careexample.com"), ["careexample@gmail.com"])

    def test_a_domain_broker_is_not_an_address(self):
        html = "interested@domainmarket.com"
        self.assertEqual(find_emails(html.lower(), "forsale.com"), [])


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


class SocialHostSuffixTests(unittest.TestCase):
    """SOCIAL_HOSTS was matched with a bare substring, so any host merely
    ending in those letters read as social and every booking link on it was
    dropped: nationsdentalstudio.com shows a real Book Online link to
    mychart.myoryx.com and the check called it absent."""

    def test_a_host_merely_ending_in_x_com_is_not_social(self):
        html = '<a href="https://mychart.myoryx.com/online-schedule/index.html?realm=1">Book Online</a>'
        self.assertTrue(has_online_booking(html.lower(), domain="nationsdentalstudio.com"))

    def test_a_tx_domain_can_still_show_its_own_booking_link(self):
        html = '<a href="https://apolloairtx.com/book-online">Book Online</a>'
        self.assertTrue(has_online_booking(html.lower(), domain="apolloairtx.com"))

    def test_a_lookalike_domain_is_not_social(self):
        html = '<a href="https://myfacebook.com/book">Book now</a>'
        self.assertTrue(has_online_booking(html.lower(), domain="oasisdentalhealth.com"))

    def test_a_real_subdomain_of_a_social_host_is_still_dropped(self):
        html = '<a href="https://m.facebook.com/oasisdental/">Book now</a>'
        self.assertFalse(has_online_booking(html.lower(), domain="oasisdentalhealth.com"))


class WhiteLabelFormEmbedTests(unittest.TestCase):
    """The builder's script is served under the agency's own hostname and the
    form's identity sits on the iframe, so the page carries neither a <form>
    tag nor a known widget path. parkhilldental.com and gemstateroofing.com
    ship exactly this and were reported as having no form at all."""

    def test_a_form_embed_script_and_iframe_is_a_form(self):
        html = ('<iframe data-form-name="a - contact us" data-form-id="to99owspigbe2jhrwa4d"'
                ' title="a - contact us"></iframe>'
                '<script src="https://api.wonderistcrm.com/js/form_embed.js"></script>')
        self.assertTrue(has_form(html.lower()))

    def test_an_iframe_carrying_a_form_id_is_a_form(self):
        html = '<iframe data-form-id="swb5iuswatyvjuff63hc" title="roofing services estimate submission-"></iframe>'
        self.assertTrue(has_form(html.lower()))

    def test_an_unrelated_embed_script_is_not_a_form(self):
        self.assertFalse(has_form('<script src="https://www.youtube.com/iframe_api"></script>'.lower()))


class ChatWidgetIsNotABookingTests(unittest.TestCase):
    """A chat widget is not a scheduler. Reading one as evidence of booking
    would suppress a real leak and drop the lead, which is the opposite error
    from the false claim and just as wrong."""

    def test_a_leadconnector_chat_widget_is_not_booking(self):
        html = ('<script src="https://widgets.leadconnectorhq.com/loader.js"'
                ' data-resources-url="https://widgets.leadconnectorhq.com/chat-widget/loader.js"'
                ' data-widget-id="69665387406a7c000a41568d"></script>')
        self.assertFalse(has_online_booking(html.lower(), domain="nashvilleroofingco.com"))

    def test_a_podium_webchat_widget_is_not_booking(self):
        html = ('<script defer src="https://connect.podium.com/widget.js#org_token=abc"'
                ' id=podium-widget></script>')
        self.assertFalse(has_online_booking(html.lower(), domain="anywhererooter.com"))


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


class RedirectStubTests(unittest.TestCase):
    """dental911.com answers with 114 bytes that set window.location and
    scored 4/5 on leaks, a report about a stub rather than a practice."""

    def test_js_location_stub_is_followed(self):
        self.assertEqual(redirect_target('<!doctype html><html><head><script>window.onload=function(){window.location.href="/lander"}</script></head></html>',
                                         "https://dental911.com"), "https://dental911.com/lander")

    def test_meta_refresh_stub_is_followed(self):
        self.assertEqual(redirect_target('<html><head><meta http-equiv="refresh" content="0; url=/home"></head></html>',
                                         "https://example.com"), "https://example.com/home")

    def test_a_real_page_with_a_redirect_in_it_is_not_a_stub(self):
        real = '<html><body><a href="/contact">Contact</a>' + "<p>filler</p>" * 300 + '<script>location="x"</script></body></html>'
        self.assertIsNone(redirect_target(real, "https://example.com"))

    def test_relative_and_absolute_targets_both_resolve(self):
        self.assertEqual(redirect_target('<script>location.href="/a"</script>', "https://e.com/x/"), "https://e.com/a")
        self.assertEqual(redirect_target('<script>location.href="https://o.com/b"</script>', "https://e.com/"), "https://o.com/b")



class ParkedDomainTests(unittest.TestCase):
    """A parked page renders its title in the browser, so the title based
    check never sees it and the leaks scored belong to a parking page."""

    def test_godaddy_parking_lander_is_flagged(self):
        page = '<script>window.LANDER_SYSTEM="PW"</script><script>window._trfd.push({ap:"parking"})</script><div id="root"></div>'
        self.assertTrue(is_parked(page.lower()))

    def test_for_sale_marketplaces_are_flagged(self):
        self.assertTrue(is_parked("<h1>This domain is for sale</h1>"))
        self.assertTrue(is_parked('<a href="https://www.hugedomains.com/">Buy this domain</a>'))

    def test_a_real_business_page_is_not_flagged(self):
        for page in ("<h1>Denver Family Dental</h1><p>Book an appointment</p>",
                     "<p>We offer free parking for patients</p>",
                     '<a href="/buy">Buy this domain name gift card</a>'):
            self.assertFalse(is_parked(page.lower()), page)



if __name__ == "__main__":
    unittest.main()
