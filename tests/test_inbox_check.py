"""Regression tests for tools/inbox_check.py.

Run: PYTHONPATH=. python3 tests/test_inbox_check.py
"""
import base64
import contextlib
import io
import sys
import tempfile
import unittest
from email.message import EmailMessage
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import inbox_check  # noqa: E402
from inbox_check import (address_of, bounce_recipient, is_bounce,  # noqa: E402
                         is_ours, is_permanent, is_system)


def dsn(final_recipient, status="5.1.1", action="failed",
        subject="Delivery Status Notification (Failure)"):
    """A delivery status notification as Gmail actually puts it on the wire:
    the machine readable part is a message/delivery-status, which the email
    package hands back as a list of sub Messages rather than as bytes."""
    return (
        "From: Mail Delivery Subsystem <mailer-daemon@googlemail.com>\r\n"
        f"Subject: {subject}\r\n"
        "MIME-Version: 1.0\r\n"
        'Content-Type: multipart/report; report-type=delivery-status; boundary="B"\r\n'
        "\r\n"
        "--B\r\n"
        "Content-Type: text/plain; charset=UTF-8\r\n"
        "\r\n"
        "Address not found.\r\n"
        "--B\r\n"
        "Content-Type: message/delivery-status\r\n"
        "\r\n"
        "Reporting-MTA: dns; googlemail.com\r\n"
        "\r\n"
        f"Final-Recipient: rfc822; {final_recipient}\r\n"
        f"Action: {action}\r\n"
        f"Status: {status}\r\n"
        "--B--\r\n"
    ).encode()


class BounceRecipientTests(unittest.TestCase):
    def test_the_failed_address_and_status_are_read(self):
        addr, status, action = bounce_recipient(dsn("marcus@evansroofingokc.com"))
        self.assertEqual(addr, "marcus@evansroofingokc.com")
        self.assertEqual(status, "5.1.1")
        self.assertEqual(action, "failed")

    def test_the_address_is_lowercased_for_suppression_matching(self):
        addr, _, _ = bounce_recipient(dsn("Info@BuiltRightDigital.com", "5.1.3"))
        self.assertEqual(addr, "info@builtrightdigital.com")

    def test_a_message_with_no_report_part_returns_nothing(self):
        msg = EmailMessage()
        msg["From"] = "someone@example.com"
        msg.set_content("hi")
        self.assertEqual(bounce_recipient(msg.as_bytes()), (None, "", ""))


class PermanentFailureTests(unittest.TestCase):
    """Gmail sends a delivery status notification for a delay as well as for
    a failure, and both say so in the subject. Suppressing on a delay kills a
    working address and reports a bounce rate that never happened."""

    def test_a_five_hundred_failure_is_permanent(self):
        self.assertTrue(is_permanent("5.1.1", "failed"))
        self.assertTrue(is_permanent("5.7.1", "failed"))

    def test_a_delay_is_not_permanent(self):
        self.assertFalse(is_permanent("4.4.1", "delayed"))
        self.assertFalse(is_permanent("4.2.2", "delayed"))

    def test_a_four_hundred_with_a_failed_action_is_not_permanent(self):
        self.assertFalse(is_permanent("4.4.1", "failed"))


class IsBounceTests(unittest.TestCase):
    def test_mailer_daemon_and_subject_both_count(self):
        self.assertTrue(is_bounce("Mail Delivery Subsystem <mailer-daemon@googlemail.com>", "whatever"))
        self.assertTrue(is_bounce("someone@example.com", "Delivery Status Notification (Failure)"))
        self.assertTrue(is_bounce("postmaster@relay.example.com", "Undelivered Mail Returned to Sender"))

    def test_a_human_reply_is_not_a_bounce(self):
        self.assertFalse(is_bounce("Overlandpark@sweettoothpdo.com", "Re: correction to my email"))


class AddressOfTests(unittest.TestCase):
    def test_the_bracketed_address_wins_over_the_display_name(self):
        self.assertEqual(address_of("omi from Omi <email@omi.me>"), "email@omi.me")
        self.assertEqual(address_of("Mohak Gupta <mohaktheprodev@gmail.com>"), "mohaktheprodev@gmail.com")

    def test_a_bare_address_is_itself(self):
        self.assertEqual(address_of("Overlandpark@sweettoothpdo.com"), "overlandpark@sweettoothpdo.com")
        self.assertEqual(address_of(""), "")


class OursTests(unittest.TestCase):
    """Gmail's from:me matched the omi support desk, so a -from:me filter in
    the query deleted the ten bounty replies it was meant to keep. The
    comparison has to be ours."""

    def test_our_own_mail_is_ours(self):
        self.assertTrue(is_ours("Mohak <mohaktheprodev@gmail.com>", "mohaktheprodev@gmail.com"))

    def test_the_omi_desk_is_not_ours(self):
        self.assertFalse(is_ours("omi from Omi <email@omi.me>", "mohaktheprodev@gmail.com"))


class SystemSenderTests(unittest.TestCase):
    def test_alerts_and_confirmations_are_not_replies(self):
        for from_addr in ("Google <no-reply@google.com>",
                          "Gmail Team <forwarding-noreply@google.com>",
                          "notifications@github.com"):
            self.assertTrue(is_system(from_addr), from_addr)

    def test_a_person_is_not_a_system_sender(self):
        self.assertFalse(is_system("Overlandpark@sweettoothpdo.com"))
        self.assertFalse(is_system("omi from Omi <email@omi.me>"))


class MailboxHarness(unittest.TestCase):
    """One delivery report in the mailbox, everything around it patched."""

    def setUp(self):
        self.mod = inbox_check
        self.tmp = Path(tempfile.mkdtemp())
        self.supp = self.tmp / "suppression.txt"
        self.supp.write_text("# nothing suppressed yet\n")
        self.saved = []
        self.mod.SUPPRESSION = str(self.supp)
        self.mod.access_token = lambda: "t"
        self.mod.profile_address = lambda t: "mohaktheprodev@gmail.com"
        self.mod.load_seen = lambda: set()
        self.mod.save_seen = lambda s: self.saved.append(set(s))
        self.mod.message_ids = lambda t, q: ["m1"]
        raw = base64.urlsafe_b64encode(self.report()).decode()
        full = {"payload": {"headers": [
            {"name": "From", "value": "Mail Delivery Subsystem <mailer-daemon@googlemail.com>"},
            {"name": "Subject", "value": "Delivery Status Notification (Failure)"}]},
            "snippet": ""}
        self.mod.api = lambda t, path, **kw: {"raw": raw} if kw.get("format") == "raw" else full

    def report(self):
        return dsn("info@dead.com")

    def scan(self, apply_bounces):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.mod.scan(14, apply_bounces)
        return buf.getvalue()


class BounceStaysUnseenUntilSuppressedTests(MailboxHarness):
    """A bounce is only dealt with once it is in suppression.txt. Marking it
    seen on a plain run lost it for good: the address was mailed again on every
    later day and nothing said so, on the one list that protects the domain."""

    def test_a_plain_run_does_not_mark_an_unwritten_bounce_seen(self):
        out = self.scan(apply_bounces=False)
        self.assertIn("rerun with --apply-bounces", out)
        self.assertEqual(self.saved[-1], set())
        self.assertNotIn("info@dead.com", self.supp.read_text())

    def test_applying_writes_the_line_and_only_then_marks_it_seen(self):
        out = self.scan(apply_bounces=True)
        self.assertIn("wrote 1 line(s)", out)
        self.assertIn("info@dead.com", self.supp.read_text())
        self.assertEqual(self.saved[-1], {"m1"})

    def test_a_bounce_already_suppressed_is_marked_seen(self):
        self.supp.write_text("info@dead.com  # bounced earlier\n")
        self.scan(apply_bounces=False)
        self.assertEqual(self.saved[-1], {"m1"})


class DelayDoesNotSuppressTests(MailboxHarness):
    """Three delay reports on 09 17 arrived looking exactly like bounces and
    would have suppressed two live addresses and read as a 26% bounce rate
    against the real 10.5%, halving the next day's sends for nothing."""

    def report(self):
        return dsn("info@slowserver.com", "4.4.1", "delayed",
                   "Delivery Status Notification (Delay)")

    def test_a_delay_is_never_written_to_suppression(self):
        out = self.scan(apply_bounces=True)
        self.assertIn("DELAYED (1)", out)
        self.assertNotIn("info@slowserver.com", self.supp.read_text())

    def test_a_delay_is_marked_seen_so_it_is_not_reported_forever(self):
        self.scan(apply_bounces=True)
        self.assertEqual(self.saved[-1], {"m1"})

    def test_a_delay_is_not_counted_as_a_bounce(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            replies, bounces = self.mod.scan(14, False)
        self.assertEqual((replies, bounces), (0, 0))


if __name__ == "__main__":
    unittest.main()
