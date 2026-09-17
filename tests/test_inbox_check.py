"""Regression tests for tools/inbox_check.py.

Run: PYTHONPATH=. python3 tests/test_inbox_check.py
"""
import sys
import unittest
from email.message import EmailMessage
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from inbox_check import (address_of, bounce_recipient, is_bounce,  # noqa: E402
                         is_ours, is_system)


def dsn(final_recipient, status="5.1.1"):
    """A delivery status notification as Gmail actually puts it on the wire:
    the machine readable part is a message/delivery-status, which the email
    package hands back as a list of sub Messages rather than as bytes."""
    return (
        "From: Mail Delivery Subsystem <mailer-daemon@googlemail.com>\r\n"
        "Subject: Delivery Status Notification (Failure)\r\n"
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
        "Action: failed\r\n"
        f"Status: {status}\r\n"
        "--B--\r\n"
    ).encode()


class BounceRecipientTests(unittest.TestCase):
    def test_the_failed_address_and_status_are_read(self):
        addr, status = bounce_recipient(dsn("marcus@evansroofingokc.com"))
        self.assertEqual(addr, "marcus@evansroofingokc.com")
        self.assertEqual(status, "5.1.1")

    def test_the_address_is_lowercased_for_suppression_matching(self):
        addr, _ = bounce_recipient(dsn("Info@BuiltRightDigital.com", "5.1.3"))
        self.assertEqual(addr, "info@builtrightdigital.com")

    def test_a_message_with_no_report_part_returns_nothing(self):
        msg = EmailMessage()
        msg["From"] = "someone@example.com"
        msg.set_content("hi")
        self.assertEqual(bounce_recipient(msg.as_bytes()), (None, ""))


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


if __name__ == "__main__":
    unittest.main()
