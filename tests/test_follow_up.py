"""Regression tests for tools/follow_up.py.

Run: python3 tests/test_follow_up.py
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import follow_up  # noqa: E402
from follow_up import answered, compose, note, spoken_for  # noqa: E402


class NoteTests(unittest.TestCase):
    """The second touch restates the leak the first email named. A leak the
    audit did not find must never be mentioned, because the claim class this
    lane already paid for once was telling a firm something untrue about its
    own site."""

    def test_a_leak_that_was_not_found_is_never_claimed(self):
        for lk in ([], ["tel"], ["mobile"], ["tel", "mobile"]):
            for kind in ("dental", "law", "home"):
                self.assertIsNone(note(kind, "x.com", lk), (kind, lk))

    def test_each_kind_states_its_own_leak(self):
        self.assertIn("cannot book a consult", note("law", "x.com", ["booking"]))
        self.assertIn("reading at night", note("dental", "x.com", ["booking"]))
        self.assertIn("after hours jobs", note("home", "x.com", ["booking"]))

    def test_the_form_leak_is_used_when_booking_was_not_found(self):
        self.assertIn("ask a question", note("law", "x.com", ["form"]))

    def test_booking_outranks_form_when_both_were_found(self):
        # The same ordering facts_sentence uses, so the two can never disagree.
        self.assertIn("cannot book a consult", note("law", "x.com", ["form", "booking"]))

    def test_the_domain_is_named_so_the_note_is_about_them(self):
        self.assertIn("x.com", note("home", "x.com", ["booking"]))


class ComposeTests(unittest.TestCase):
    def row(self, **over):
        base = {
            "to": "colleen@wolflawcolorado.com",
            "company": "Wolf Law",
            "kind": "law",
            "domain": "wolflawcolorado.com",
            "subject": "the consults wolflawcolorado.com is not getting at 10pm",
            "evidence": {"leaks": ["booking"]},
        }
        base.update(over)
        return base

    def test_a_row_with_nothing_to_say_is_dropped_not_sent(self):
        self.assertIsNone(compose(self.row(evidence={"leaks": ["tel"]}), "16 September"))

    def test_the_subject_is_the_original_so_it_reads_as_a_continuation(self):
        subject, _ = compose(self.row(), "16 September")
        self.assertEqual(subject, "Re: " + self.row()["subject"])

    def test_the_note_is_shorter_than_a_first_cold_email(self):
        _, body = compose(self.row(), "16 September")
        self.assertLess(len(body), 700)

    def test_the_exit_line_appears_once(self):
        # SIGNATURE already carries the stop promise; a second one in the body
        # read as a template and repeated the same offer.
        _, body = compose(self.row(), "16 September")
        self.assertEqual(body.lower().count("i will not write again"), 1)

    def test_no_dash_is_used_as_prose(self):
        for kind in ("dental", "law", "home"):
            _, body = compose(self.row(kind=kind), "16 September")
            self.assertNotIn(" - ", body)
            self.assertNotIn(" — ", body)


class SpokenForTests(unittest.TestCase):
    """One follow up per firm, ever. A firm already followed up is refused by
    both its address and its domain, and the check reads the queues rather
    than the file being written, so a rebuild cannot mail the note twice."""

    def setUp(self):
        self.real = follow_up.QUEUES
        self.tmp = tempfile.TemporaryDirectory()
        follow_up.QUEUES = [Path(self.tmp.name)]

    def tearDown(self):
        follow_up.QUEUES = self.real
        self.tmp.cleanup()

    def write(self, name, rows):
        with open(Path(self.tmp.name) / name, "w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")

    def test_a_firm_already_followed_up_is_refused(self):
        self.write("q.jsonl", [{"to": "a@x.com", "domain": "x.com", "follow_up_of": "abc"}])
        addrs, domains = spoken_for()
        self.assertIn("a@x.com", addrs)
        self.assertIn("x.com", domains)

    def test_a_first_cold_email_does_not_claim_the_firm(self):
        self.write("q.jsonl", [{"to": "a@x.com", "domain": "x.com", "message_id": "abc"}])
        addrs, domains = spoken_for()
        self.assertEqual(addrs, set())
        self.assertEqual(domains, set())

    def test_a_backlog_is_not_a_send_record(self):
        self.write("q-backlog.jsonl", [{"to": "a@x.com", "domain": "x.com", "follow_up_of": "abc"}])
        addrs, domains = spoken_for()
        self.assertEqual(addrs, set())
        self.assertEqual(domains, set())


class AnsweredTests(unittest.TestCase):
    """A human who answered is not a prospect to chase."""

    def test_a_reply_marker_claims_the_row(self):
        for key in ("reply", "replied", "reply_at", "answered"):
            self.assertTrue(answered({key: "no thanks"}), key)

    def test_an_unanswered_row_is_still_open(self):
        self.assertFalse(answered({"message_id": "abc", "sent_at": "2026-09-16T00:00:00Z"}))


if __name__ == "__main__":
    unittest.main()
