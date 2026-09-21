"""Regression tests for tools/gmail_send.py threading.

Run: python3 tests/test_follow_up.py
"""
import base64
import email
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import gmail_send  # noqa: E402


class ThreadingTests(unittest.TestCase):
    """A second touch has to land under the message it answers, or it arrives
    as a second stranger in the inbox and pays the cold start again."""

    def raw(self, row):
        raw = gmail_send.build("me@example.com", row)
        return email.message_from_bytes(base64.urlsafe_b64decode(raw))

    def test_a_follow_up_carries_in_reply_to_and_references(self):
        ref = "<CAJXPg5B@mail.gmail.com>"
        msg = self.raw({"to": "a@x.com", "subject": "Re: x", "body": "hi",
                        "in_reply_to": ref, "references": ref})
        self.assertEqual(msg["In-Reply-To"], ref)
        self.assertEqual(msg["References"], ref)

    def test_references_falls_back_to_the_answered_message(self):
        ref = "<CAJXPg5B@mail.gmail.com>"
        msg = self.raw({"to": "a@x.com", "subject": "Re: x", "body": "hi", "in_reply_to": ref})
        self.assertEqual(msg["References"], ref)

    def test_a_first_cold_email_carries_neither_header(self):
        msg = self.raw({"to": "a@x.com", "subject": "x", "body": "hi"})
        self.assertIsNone(msg["In-Reply-To"])
        self.assertIsNone(msg["References"])

    def test_the_send_call_files_the_message_on_its_own_thread(self):
        captured = {}
        real = gmail_send.urllib.request.urlopen
        gmail_send.urllib.request.urlopen = capture(captured)
        try:
            gmail_send.send("tok", "me@example.com",
                            {"to": "a@x.com", "subject": "Re: x", "body": "hi", "thread_id": "t1"})
        finally:
            gmail_send.urllib.request.urlopen = real
        self.assertEqual(captured["body"]["threadId"], "t1")
        self.assertIn("raw", captured["body"])

    def test_a_first_cold_email_does_not_pin_a_thread(self):
        captured = {}
        real = gmail_send.urllib.request.urlopen
        gmail_send.urllib.request.urlopen = capture(captured)
        try:
            gmail_send.send("tok", "me@example.com", {"to": "a@x.com", "subject": "x", "body": "hi"})
        finally:
            gmail_send.urllib.request.urlopen = real
        self.assertNotIn("threadId", captured["body"])


def capture(store):
    class Reply:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"id": "1", "threadId": "t1"}'

    def fake_urlopen(req, timeout=None):
        store["body"] = json.loads(req.data.decode())
        return Reply()

    return fake_urlopen


if __name__ == "__main__":
    unittest.main()
