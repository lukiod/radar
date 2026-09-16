"""Regression tests for tools/draft_batch.py.

Run: python3 tests/test_draft_batch.py
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from draft_batch import site_looks_unrelated  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
