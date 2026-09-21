"""Regression tests for tools/draft_batch.py.

Run: python3 tests/test_draft_batch.py
"""
import datetime
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import draft_batch  # noqa: E402
from draft_batch import (SIGNATURE, address_live, facts_sentence, offer, owner_email,  # noqa: E402
                         redirected_elsewhere, self_check, site_email,
                         site_looks_unrelated, subject_for)


class OwnerEmailTests(unittest.TestCase):
    """OSM often lists the owner's mailbox off the site's domain. Those are
    real leads, but only when the address names the business."""

    def test_a_mailbox_naming_the_business_is_taken(self):
        self.assertEqual(owner_email("pinnacleroofingassociates@gmail.com", "pinnacleroofingassociates.com"),
                         "pinnacleroofingassociates@gmail.com")
        self.assertEqual(owner_email("firesidehvac@gmail.com", "firesidehvac.com"),
                         "firesidehvac@gmail.com")

    def test_a_second_domain_of_the_same_business_is_taken(self):
        self.assertEqual(owner_email("jonathan@rizeexteriorservices.com", "rizeexterior.com"),
                         "jonathan@rizeexteriorservices.com")

    def test_the_sites_own_domain_still_passes(self):
        self.assertEqual(owner_email("info@pophamlaw.com", "pophamlaw.com"), "info@pophamlaw.com")

    def test_a_strangers_personal_address_is_refused(self):
        self.assertIsNone(owner_email("tyler.borg@me.com", "coloradogumcare.com"))
        self.assertIsNone(owner_email("schloegel@gmail.com", "pophamlaw.com"))
        self.assertIsNone(owner_email("danielmurphlaw@gmail.com", "denvercocriminaldefenselawyer.com"))
        self.assertIsNone(owner_email("jlewis@matthewslaw.com", "matthewsfamilylawyers.com"))

    def test_a_short_slug_cannot_match_by_accident(self):
        self.assertIsNone(owner_email("me@example.com", "law.com"))

    def test_nothing_in_gives_nothing_back(self):
        self.assertIsNone(owner_email(None, "pophamlaw.com"))
        self.assertIsNone(owner_email("", "pophamlaw.com"))
        self.assertIsNone(owner_email("not-an-address", "pophamlaw.com"))


class SiteEmailTests(unittest.TestCase):
    """When a site publishes only an off domain address, find_emails keeps it
    and pick_email throws it away. site_email is the last resort that keeps
    the ones naming the business, which is where the small practices live."""

    def test_a_practice_gmail_is_recovered(self):
        self.assertEqual(site_email(["abjroofing@gmail.com"], "abjroofinginc.com"),
                         "abjroofing@gmail.com")
        self.assertEqual(site_email(["carrollwoodsmiles@yahoo.com"], "carrollwoodsmiles.com"),
                         "carrollwoodsmiles@yahoo.com")

    def test_a_second_domain_of_the_same_business_is_recovered(self):
        self.assertEqual(site_email(["heather@ameristarroofing.com"], "ameristarroofingkc.com"),
                         "heather@ameristarroofing.com")

    def test_a_strangers_address_on_the_page_is_refused(self):
        self.assertIsNone(site_email(["info@atlantaintercontinental.com"], "airkitchenandbath.com"))
        self.assertIsNone(site_email(["ga@webfx.com"], "ameriproroofing.com"))
        self.assertIsNone(site_email(["1539082550@qq.com"], "andersonpl.com"))

    def test_careers_and_press_boxes_are_refused(self):
        self.assertIsNone(site_email(["careers@abjroofinginc.com"], "abjroofinginc.com"))

    def test_an_own_domain_address_is_left_to_pick_email(self):
        self.assertIsNone(site_email(["hello@acmedental.com"], "acmedental.com"))

    def test_nothing_in_gives_nothing_back(self):
        self.assertIsNone(site_email([], "acmedental.com"))
        self.assertIsNone(site_email(["not-an-address"], "acmedental.com"))


class RedraftGuardTests(unittest.TestCase):
    """A carried tail mixes follow up rows into a first touch queue, and the
    re render must not rewrite them."""

    def test_a_follow_up_row_is_left_alone(self):
        import redraft_bodies
        row = {"follow_up_of": "x@y.com", "kind": "dental", "domain": "a.com",
               "to": "b@a.com", "evidence": {"leaks": ["booking"]}}
        self.assertIsNone(redraft_bodies.render(row))

    def test_a_first_touch_row_still_renders(self):
        import redraft_bodies
        row = {"kind": "dental", "domain": "a.com", "to": "b@a.com",
               "evidence": {"leaks": ["booking", "form"]}}
        made = redraft_bodies.render(row)
        self.assertIsNotNone(made)
        self.assertIn("codeconclave.com", made[1])


class SignatureTests(unittest.TestCase):
    """The offer is a design the reader has not seen, from a sender they have
    never heard of. The signature is the only place the work can be looked at,
    and for 692 of the first 696 sends it pointed nowhere."""

    def test_the_signature_names_the_site(self):
        self.assertIn("codeconclave.com", SIGNATURE)

    def test_the_stop_line_is_still_there(self):
        self.assertIn('Reply "stop"', SIGNATURE)

    def test_it_is_still_plain_text(self):
        self.assertNotIn("<", SIGNATURE)
        self.assertNotIn("http://", SIGNATURE)  # a bare domain, no scheme


class RedirectedElsewhereTests(unittest.TestCase):
    """pharrroaddentistry.com now resolves to brightworksdentistry.com."""

    def test_different_business_is_flagged(self):
        self.assertTrue(redirected_elsewhere("https://www.brightworksdentistry.com/", "pharrroaddentistry.com"))

    def test_same_domain_www_and_scheme_and_path_are_not(self):
        self.assertFalse(redirected_elsewhere("https://www.example.com/contact", "example.com"))
        self.assertFalse(redirected_elsewhere("http://example.com", "example.com"))
        self.assertFalse(redirected_elsewhere("https://clinic.example.com/", "example.com"))

    def test_missing_final_url_is_not_flagged(self):
        self.assertFalse(redirected_elsewhere(None, "example.com"))
        self.assertFalse(redirected_elsewhere("", "example.com"))


class FactsSentenceTests(unittest.TestCase):
    """Branching on the form alone told 41 rows' worth of practices with a
    working scheduler that they cannot book."""

    def test_booking_present_form_missing_never_says_cannot_book(self):
        for kind, lk in (("dental", ["form"]), ("law", ["form"]), ("home", ["form"])):
            sent = " ".join(facts_sentence(kind, "example.com", lk)).lower()
            self.assertNotIn("cannot book", sent, kind)
            self.assertNotIn("cannot pick", sent, kind)
            self.assertNotIn("cannot request service", sent, kind)
            # The true fact still has to be there: the gap is the question.
            self.assertIn("nowhere to", sent, kind)

    def test_booking_present_form_missing_subject_does_not_claim_no_booking(self):
        for kind in ("dental", "law", "home"):
            subj = subject_for(kind, "example.com", ["form"]).lower()
            self.assertNotIn("night appointments", subj, kind)
            self.assertNotIn("10pm consults", subj, kind)
            self.assertNotIn("cannot book", subj, kind)

    def test_booking_present_form_missing_offer_does_not_sell_booking(self):
        for kind in ("dental", "law", "home"):
            body = offer(kind, ["form"]).lower()
            self.assertNotIn("i set up online booking", body, kind)
            self.assertNotIn("i set up consult scheduling", body, kind)
            self.assertIn("already have", body, kind)

    def test_booking_missing_still_claims_no_booking(self):
        # The branch that was already right must not regress.
        sent = " ".join(facts_sentence("dental", "example.com", ["booking", "form"])).lower()
        self.assertIn("cannot book an appointment", sent)
        self.assertIn("not booking night appointments", subject_for("dental", "example.com", ["booking", "form"]).lower())

    def test_no_gap_returns_nothing_to_say(self):
        # Neither missing: no honest opening sentence, so drop the row.
        for kind in ("dental", "law", "home"):
            self.assertEqual(facts_sentence(kind, "example.com", ["mobile", "tel"]), [], kind)

    def test_mobile_and_tel_are_still_appended(self):
        parts = facts_sentence("dental", "example.com", ["booking", "form", "mobile", "tel"])
        self.assertEqual(len(parts), 3)
        self.assertIn("shrunk down", parts[1])
        self.assertIn("cannot be tapped", parts[2])

    def test_tel_claim_is_scoped_to_the_page_that_was_read(self):
        # sweettoothpdo.com has no tel: link on the homepage, eight on /locations.
        parts = facts_sentence("dental", "example.com", ["booking", "form", "tel"])
        self.assertIn("on the homepage", parts[1])
        self.assertNotIn("not tappable on a phone", parts[1])


class SiteLooksUnrelatedTests(unittest.TestCase):
    """A word-overlap check between the OSM listing name and the page's own
    title was tried first and rejected (163 of ~180 real leads failed it
    purely from a generic SEO title never repeating the brand name). This
    narrower detector targets only the two failure modes actually seen:
    a domain resold to an unrelated business, or parked/hijacked."""

    def test_resold_to_an_unrelated_business(self):
        self.assertTrue(site_looks_unrelated("Refuge Coffee Co. - Atlanta Coffee Truck + Atlanta Coffee Shop"))

    def test_gambling_spam_hijack(self):
        self.assertTrue(site_looks_unrelated("AGENOLX x Tcdodenver : Situs Toto Dan Toto Slot 4D Gacor Mantap"))
        self.assertTrue(site_looks_unrelated("888slot: Trải nghiệm casino trực tuyến đỉnh cao"))

    def test_parked_or_for_sale(self):
        self.assertTrue(site_looks_unrelated("Domain Not Valid"))
        self.assertTrue(site_looks_unrelated("PaulFischerLawFirm.com — Domain For Sale | Atom"))

    def test_generic_seo_title_is_not_flagged(self):
        # The exact shape that broke the earlier word-overlap check: a real
        # business whose title never repeats its own brand name.
        self.assertFalse(site_looks_unrelated("Best Roofing Company in Denver | Roofing Contractor Company"))
        self.assertFalse(site_looks_unrelated("Dentist"))
        self.assertFalse(site_looks_unrelated(""))
        self.assertFalse(site_looks_unrelated(None))

    def test_real_estate_law_is_not_flagged(self):
        # "real estate" was tried as a trigger word and dropped: it false
        # flagged real estate law firms, a legitimate law specialty.
        self.assertFalse(site_looks_unrelated("Real Estate Attorney, Business Law & Family Law Attorney"))
        self.assertFalse(site_looks_unrelated("Arizona Real Estate Lawyer You Can Rely On"))


class AddressLiveTests(unittest.TestCase):
    """Only a confirmed mailbox may send. 66 of the first 73 sends carried no
    probe verdict at all and every traceable bounce came from that group, so
    a probe the host refuses, a timeout and a never probed address are all
    held back rather than treated as good."""

    def setUp(self):
        self.real = draft_batch.rcpt_check
        self.cache = draft_batch.EMAIL_CACHE
        draft_batch.EMAIL_CACHE = Path("/tmp/email-check-test.json")
        draft_batch.EMAIL_CACHE.unlink(missing_ok=True)

    def tearDown(self):
        draft_batch.rcpt_check = self.real
        draft_batch.EMAIL_CACHE.unlink(missing_ok=True)
        draft_batch.EMAIL_CACHE = self.cache

    def test_rejection_and_no_mx_drop_the_row(self):
        for status in ("rejected", "no_mx"):
            draft_batch.EMAIL_CACHE.unlink(missing_ok=True)
            draft_batch.rcpt_check = lambda a, s=status: (s, "detail")
            self.assertFalse(address_live("info@example.com"), status)

    def test_only_a_confirmed_mailbox_passes(self):
        for status in ("accepted", "probe_blocked", "unreachable", "unknown"):
            draft_batch.EMAIL_CACHE.unlink(missing_ok=True)
            draft_batch.rcpt_check = lambda a, s=status: (s, "detail")
            self.assertEqual(address_live("info@example.com"), status == "accepted", status)

    def test_an_unprobed_address_is_probed_and_not_waved_through(self):
        """The row that is not in the cache is the one that bounced."""
        draft_batch.rcpt_check = lambda a: ("probe_blocked", "spamhaus")
        self.assertFalse(address_live("info@example.com"))

    def test_an_address_without_an_at_sign_is_never_sent(self):
        self.assertFalse(address_live("not an address"))

    def test_the_verdict_is_cached(self):
        calls = []
        draft_batch.rcpt_check = lambda a: (calls.append(a), ("accepted", "ok"))[1]
        self.assertTrue(address_live("info@example.com"))
        self.assertTrue(address_live("info@example.com"))
        self.assertEqual(len(calls), 1)

    def _cache(self, status, at):
        draft_batch.EMAIL_CACHE.write_text(json.dumps(
            {"info@example.com": {"status": status, "at": at}}))

    def test_a_refused_probe_is_asked_again_once_it_is_stale(self):
        """213 of 565 cached verdicts were the host refusing our probing IP,
        and every one was reported as a mailbox that does not exist. A
        refusal says nothing about the mailbox, so it cannot stand forever."""
        self._cache("probe_blocked",
                    (datetime.date.today() - datetime.timedelta(days=30)).isoformat())
        draft_batch.rcpt_check = lambda a: ("accepted", "ok")
        self.assertTrue(address_live("info@example.com"))

    def test_a_fresh_refusal_still_holds_the_row_back(self):
        self._cache("probe_blocked", datetime.date.today().isoformat())
        calls = []
        draft_batch.rcpt_check = lambda a: (calls.append(a), ("accepted", "ok"))[1]
        self.assertFalse(address_live("info@example.com"))
        self.assertEqual(calls, [])

    def test_a_confirmed_mailbox_is_never_asked_again(self):
        self._cache("accepted", "2020-01-01")
        calls = []
        draft_batch.rcpt_check = lambda a: (calls.append(a), ("accepted", "ok"))[1]
        self.assertTrue(address_live("info@example.com"))
        self.assertEqual(calls, [])

    def test_a_dead_mailbox_is_settled(self):
        self._cache("dead", "2020-01-01")
        calls = []
        draft_batch.rcpt_check = lambda a: (calls.append(a), ("accepted", "ok"))[1]
        self.assertFalse(address_live("info@example.com"))
        self.assertEqual(calls, [])

    def test_a_verdict_from_before_dates_were_stored_is_asked_again(self):
        draft_batch.EMAIL_CACHE.write_text(json.dumps({"info@example.com": "probe_blocked"}))
        draft_batch.rcpt_check = lambda a: ("accepted", "ok")
        self.assertTrue(address_live("info@example.com"))


class SelfCheckTests(unittest.TestCase):
    """The close asks the owner to believe a stranger. The replacement asks
    them to look at their own site for five seconds, which is the only proof
    available while an attachment costs deliverability and a hosted preview
    needs a decision nobody has made yet. It has to match the leak, because
    telling a practice that already books that it cannot book is the false
    claim class this lane already paid for once."""

    def test_a_site_with_no_booking_is_asked_to_look_for_booking(self):
        for kind in ("dental", "law", "home"):
            line = self_check(kind, "x.com", ["booking"])
            self.assertIn("look for a way to book", line, kind)
            self.assertNotIn("ask a question before committing", line, kind)

    def test_a_site_that_books_is_asked_about_the_question_instead(self):
        for kind in ("dental", "law", "home"):
            line = self_check(kind, "x.com", ["form"])
            self.assertIn("ask a question before committing", line, kind)
            self.assertNotIn("look for a way to book", line, kind)

    def test_the_leak_sentence_and_the_check_agree(self):
        """Both read the same leak list, so they can never contradict."""
        for kind in ("dental", "law", "home"):
            for lk in (["booking"], ["booking", "form"], ["form"]):
                said = " ".join(facts_sentence(kind, "x.com", lk))
                if not said:
                    continue
                line = self_check(kind, "x.com", lk)
                if "cannot book" in said or "cannot pick" in said:
                    self.assertIn("look for a way to book", line, (kind, lk))

    def test_the_domain_is_named_so_the_check_is_one_tap(self):
        self.assertIn("x.com", self_check("law", "x.com", ["booking"]))

    def test_every_offer_is_present_tense_for_work_not_yet_done(self):
        """'I set up X, in a week' claimed finished work and a duration in the
        same breath. Nothing has been set up when the email is read."""
        for kind in ("dental", "law", "home"):
            for lk in (["booking"], ["form"]):
                text = offer(kind, lk)
                self.assertNotIn("I set up", text, (kind, lk))
                self.assertNotIn(", in a week, one go", text, (kind, lk))


class OfferReadsLikeAMessageTests(unittest.TestCase):
    """245 first touches produced one reply and it was "stop". The reply that
    did come, ten minutes after a plain correction that conceded an error,
    says the voice is what gets answered, not the feature list."""

    KINDS = ("dental", "law", "home")
    LEAKS = (["booking"], ["booking", "form"], ["form"])

    def test_the_design_is_what_the_offer_opens_on(self):
        # The site was the last clause of a long sentence, joined by "and I
        # give the site a design while I am at it", so it read as a freebie
        # thrown in rather than the product.
        for kind in self.KINDS:
            for lk in self.LEAKS:
                text = offer(kind, lk)
                self.assertTrue(text.startswith("I rebuild the site so it looks like"), (kind, lk))

    def test_no_feature_triad_joined_by_ands(self):
        """One sentence carried the rebuild, four features and the deadline."""
        for kind in self.KINDS:
            for lk in self.LEAKS:
                text = offer(kind, lk)
                self.assertNotIn(" and a place to ask", text, (kind, lk))
                self.assertNotIn(" and give the site", text, (kind, lk))
                self.assertNotIn("one go", text, (kind, lk))

    def test_the_timeline_is_its_own_short_sentence(self):
        for kind in self.KINDS:
            for lk in self.LEAKS:
                text = offer(kind, lk)
                self.assertTrue(text.endswith("A week from the go ahead."), (kind, lk))
                self.assertEqual(text.count("."), 2, (kind, lk))

    def test_it_stays_short_enough_to_read_on_a_phone(self):
        for kind in self.KINDS:
            for lk in self.LEAKS:
                self.assertLess(len(offer(kind, lk)), 330, (kind, lk))


if __name__ == "__main__":
    unittest.main()
