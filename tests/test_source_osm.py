"""Regression tests for tools/source_osm.py.

Run: python3 tests/test_source_osm.py
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from source_osm import domain_of  # noqa: E402


class DomainOfTests(unittest.TestCase):
    """The OSM website tag is free text, so it can hold a business name
    instead of a url. One such row, "town center dental.com", reached the
    audit as a hostname with a space in it and ended a 916 domain run."""

    def test_a_name_in_the_website_field_is_not_a_domain(self):
        self.assertEqual(domain_of("town center dental.com"), ("", ""))

    def test_a_real_url_still_parses(self):
        self.assertEqual(domain_of("https://www.PophamLaw.com/contact"), ("pophamlaw.com", "contact"))
        self.assertEqual(domain_of("acmedental.com"), ("acmedental.com", ""))
        self.assertEqual(domain_of("http://rizeexterior.com"), ("rizeexterior.com", ""))

    def test_a_port_and_a_trailing_slash_are_dropped(self):
        self.assertEqual(domain_of("https://example.com:8443/"), ("example.com", ""))

    def test_nothing_in_gives_nothing_back(self):
        self.assertEqual(domain_of(""), ("", ""))
        self.assertEqual(domain_of("   "), ("", ""))


if __name__ == "__main__":
    unittest.main()
