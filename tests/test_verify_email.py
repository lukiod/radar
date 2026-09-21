"""Regression tests for tools/verify_email.py.

Run: python3 tests/test_verify_email.py
"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import verify_email  # noqa: E402
from verify_email import catch_all, catch_all_cached  # noqa: E402


class CatchAllTests(unittest.TestCase):
    """An accepted verdict only means something where the server says no to
    an address that cannot exist. 40 of the 90 domains queued for 09 22 said
    yes to a random local part, and 5 of the 13 addresses that had already
    hard bounced still say yes to both, so the verdict on those carries no
    evidence at all."""

    def setUp(self):
        self.real = verify_email.rcpt_check
        self.cache = verify_email.CATCH_ALL_CACHE
        verify_email.CATCH_ALL_CACHE = "/tmp/catch-all-test.json"
        Path(verify_email.CATCH_ALL_CACHE).unlink(missing_ok=True)

    def tearDown(self):
        verify_email.rcpt_check = self.real
        Path(verify_email.CATCH_ALL_CACHE).unlink(missing_ok=True)
        verify_email.CATCH_ALL_CACHE = self.cache

    def test_a_server_that_accepts_junk_is_weak(self):
        verify_email.rcpt_check = lambda a, timeout=15: ("accepted", "ok")
        self.assertEqual(catch_all("example.com"), "weak")

    def test_a_server_that_refuses_junk_is_strong(self):
        verify_email.rcpt_check = lambda a, timeout=15: ("rejected", "550")
        self.assertEqual(catch_all("example.com"), "strong")

    def test_a_refused_probe_is_not_a_verdict_about_the_domain(self):
        # probe_blocked and unreachable are facts about our own connection.
        for status in ("probe_blocked", "unreachable", "unknown"):
            verify_email.rcpt_check = lambda a, timeout=15, s=status: (s, "detail")
            self.assertEqual(catch_all("example.com"), "unknown", status)

    def test_the_probe_asks_about_an_address_that_cannot_exist(self):
        seen = []
        verify_email.rcpt_check = lambda a, timeout=15: (seen.append(a), ("rejected", "x"))[1]
        catch_all("example.com")
        self.assertEqual(len(seen), 1)
        local, domain = seen[0].split("@")
        self.assertEqual(domain, "example.com")
        self.assertGreaterEqual(len(local), 12)

    def test_the_verdict_is_cached_per_domain(self):
        calls = []
        verify_email.rcpt_check = lambda a, timeout=15: (calls.append(a), ("rejected", "x"))[1]
        self.assertEqual(catch_all_cached("example.com"), "strong")
        self.assertEqual(catch_all_cached("example.com"), "strong")
        self.assertEqual(len(calls), 1)

    def test_a_verdict_already_on_file_is_not_asked_again(self):
        Path(verify_email.CATCH_ALL_CACHE).write_text(json.dumps({"example.com": {"verdict": "weak"}}))
        calls = []
        verify_email.rcpt_check = lambda a, timeout=15: (calls.append(a), ("rejected", "x"))[1]
        self.assertEqual(catch_all_cached("example.com"), "weak")
        self.assertEqual(calls, [])

    def test_parallel_domains_all_survive_the_write(self):
        """The probe runs in threads. An unlocked read modify write kept 10 of
        the 105 domains classified, because every thread read the file before
        any of the others had written it."""
        import concurrent.futures
        verify_email.rcpt_check = lambda a, timeout=15: ("rejected", "x")
        domains = [f"d{i}.example.com" for i in range(60)]
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
            list(pool.map(catch_all_cached, domains))
        on_disk = json.loads(Path(verify_email.CATCH_ALL_CACHE).read_text())
        self.assertEqual(sorted(on_disk), sorted(domains))


if __name__ == "__main__":
    unittest.main()
