"""Regression tests for tools/queue_from_backlog.py.

Run: PYTHONPATH=. python3 tests/test_queue_from_backlog.py
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import queue_from_backlog  # noqa: E402
import refill_queue  # noqa: E402
from queue_from_backlog import claimed_identifiers, is_named, select  # noqa: E402
from refill_queue import refill, settled  # noqa: E402


def row(domain, metro, **kw):
    r = {"domain": domain, "metro": metro, "to": "info@" + domain}
    r.update(kw)
    return r


class SelectTests(unittest.TestCase):
    def test_only_gated_rows_are_eligible(self):
        rows = [row("a.com", "denver", verified=True),
                row("b.com", "denver"),                       # never gated
                row("c.com", "denver", verified=True, rejected="stale"),
                row("d.com", "denver", verified=True, message_id="sent")]
        self.assertEqual([r["domain"] for r in select(rows, 10)], ["a.com"])

    def test_metros_are_drawn_round_robin(self):
        rows = [row(f"d{i}.com", "denver", verified=True) for i in range(5)]
        rows += [row(f"p{i}.com", "phoenix", verified=True) for i in range(5)]
        picked = [r["domain"] for r in select(rows, 4)]
        self.assertEqual(picked, ["d0.com", "p0.com", "d1.com", "p1.com"])

    def test_a_thin_metro_does_not_stall_the_rest(self):
        rows = [row("d0.com", "denver", verified=True), row("d1.com", "denver", verified=True)]
        rows += [row(f"p{i}.com", "phoenix", verified=True) for i in range(4)]
        picked = [r["domain"] for r in select(rows, 6)]
        self.assertEqual(picked, ["d0.com", "p0.com", "d1.com", "p1.com", "p2.com", "p3.com"])

    def test_the_limit_is_respected_when_rows_run_out(self):
        rows = [row(f"d{i}.com", "denver", verified=True) for i in range(3)]
        self.assertEqual(len(select(rows, 40)), 3)

    def test_a_suppressed_address_is_never_selected(self):
        rows = [row("a.com", "denver", verified=True), row("b.com", "denver", verified=True)]
        picked = select(rows, 10, frozenset({"info@a.com"}))
        self.assertEqual([r["domain"] for r in picked], ["b.com"])

    def test_a_suppressed_domain_covers_its_addresses(self):
        rows = [row("a.com", "denver", verified=True)]
        self.assertEqual(select(rows, 10, frozenset({"a.com"})), [])

    def test_a_mailbox_that_is_gone_is_never_selected(self):
        rows = [row("a.com", "denver", verified=True), row("b.com", "denver", verified=True)]
        picked = select(rows, 10, frozenset(), lambda a: a != "info@a.com")
        self.assertEqual([r["domain"] for r in picked], ["b.com"])

    def test_without_a_checker_nothing_is_probed(self):
        rows = [row("a.com", "denver", verified=True)]
        self.assertEqual(len(select(rows, 10)), 1)

    def test_a_row_addressed_to_a_person_is_named(self):
        self.assertTrue(is_named(row("a.com", "denver", to="joel@a.com")))
        self.assertTrue(is_named(row("a.com", "denver", to="anita.hayat@a.com")))

    def test_a_shared_inbox_is_not_named(self):
        for to in ("info@a.com", "office@a.com", "smiles@a.com", "frontdesk@a.com",
                   "admin2@a.com", "a1@a.com"):
            self.assertFalse(is_named(row("a.com", "denver", to=to)), to)

    def test_a_person_is_drawn_before_a_shared_inbox(self):
        rows = [row("role.com", "denver", verified=True, to="info@role.com"),
                row("person.com", "denver", verified=True, to="joel@person.com")]
        self.assertEqual([r["domain"] for r in select(rows, 10)],
                         ["person.com", "role.com"])

    def test_the_shared_inboxes_are_still_drawn_when_the_people_run_out(self):
        rows = [row("person.com", "denver", verified=True, to="joel@person.com"),
                row("r1.com", "denver", verified=True),
                row("r2.com", "denver", verified=True)]
        self.assertEqual([r["domain"] for r in select(rows, 10)],
                         ["person.com", "r1.com", "r2.com"])

    def test_ordering_within_a_kind_is_unchanged(self):
        rows = [row(f"d{i}.com", "denver", verified=True) for i in range(4)]
        self.assertEqual([r["domain"] for r in select(rows, 4)],
                         ["d0.com", "d1.com", "d2.com", "d3.com"])

    def test_a_firm_already_written_to_is_not_drawn_again(self):
        """The guard is the table, so it covers the address and the domain."""
        rows = [row("a.com", "denver", verified=True), row("b.com", "denver", verified=True)]
        self.assertEqual([r["domain"] for r in select(rows, 10, frozenset({"a.com"}))], ["b.com"])
        self.assertEqual([r["domain"] for r in select(rows, 10, frozenset({"info@a.com"}))], ["b.com"])

    def test_selection_is_stable(self):
        rows = [row(f"d{i}.com", "denver", verified=True) for i in range(5)]
        rows += [row(f"p{i}.com", "phoenix", verified=True) for i in range(5)]
        self.assertEqual([r["domain"] for r in select(rows, 6)],
                         [r["domain"] for r in select(rows, 6)])


class ClaimedIdentifiersTests(unittest.TestCase):
    """A firm is spoken for once it is mailed, and also once it is sitting
    unsent in another day's queue. Five firms were shared between the 09 18
    and 09 19 queues, and a firm drawn into both gets two cold emails. The
    backlog must not count, or the first draw empties itself."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        queue_from_backlog.QUEUES = [self.tmp]

    def write(self, name, rows):
        (self.tmp / name).write_text("".join(json.dumps(r) + "\n" for r in rows))

    def test_a_sent_row_gives_up_its_address_and_domain(self):
        self.write("a.jsonl", [{"to": "Joel@Firm.com", "domain": "firm.com", "message_id": "m1"}])
        self.assertEqual(claimed_identifiers(), {"joel@firm.com", "firm.com"})

    def test_a_row_pending_in_another_queue_is_spoken_for(self):
        self.write("a.jsonl", [{"to": "joel@firm.com", "domain": "firm.com"}])
        self.assertEqual(claimed_identifiers(), {"joel@firm.com", "firm.com"})

    def test_a_pool_row_with_no_address_does_not_speak_for_the_firm(self):
        """The prospect pool carries domains and no addresses. Reading it
        as a send record claimed every sourced firm and left nothing to draw."""
        self.write("prospects.jsonl", [{"domain": "firm.com", "metro": "denver"}])
        self.write("audits.jsonl", [{"domain": "other.com", "score": 8}])
        self.assertEqual(claimed_identifiers(), set())

    def test_a_backlog_is_the_pool_and_is_not_spoken_for(self):
        self.write("2026-09-19-backlog.jsonl", [{"to": "joel@firm.com", "domain": "firm.com"}])
        self.assertEqual(claimed_identifiers(), set())

    def test_a_row_already_sent_out_of_a_backlog_does_speak_for_the_firm(self):
        """The hole the backlog skip left. A firm mailed out of a backlog was
        not spoken for, so a second row for it in another backlog was drawn and
        got a second cold email. Only the sent row counts, so an unsent one in
        the same file still leaves the pool drawable."""
        self.write("2026-09-19-backlog.jsonl", [
            {"to": "joel@firm.com", "domain": "firm.com", "message_id": "m1"},
            {"to": "anna@other.com", "domain": "other.com"},
        ])
        self.assertEqual(claimed_identifiers(), {"joel@firm.com", "firm.com"})

    def test_the_file_being_built_does_not_claim_its_own_rows(self):
        self.write("2026-09-19.jsonl", [{"to": "joel@firm.com", "domain": "firm.com"}])
        self.assertEqual(claimed_identifiers(self.tmp / "2026-09-19.jsonl"), set())

    def test_a_rebuild_returns_the_same_rows_twice(self):
        self.write("2026-09-19.jsonl", [{"to": "joel@firm.com", "domain": "firm.com"}])
        self.assertEqual(claimed_identifiers(self.tmp / "2026-09-19.jsonl"),
                         claimed_identifiers(self.tmp / "2026-09-19.jsonl"))


class RefillTests(unittest.TestCase):
    """A queue built before the address gate existed holds rows the gate would
    now hold back. Refilling keeps every row that is already decided, drops
    the unsent ones with no confirmed mailbox, and fills the room from the
    backlog without ever touching a row that has a message id."""

    def test_a_mailed_row_is_kept_and_nothing_is_drawn_in_its_place(self):
        rows = [{"to": "a@x.com", "domain": "x.com", "message_id": "m1"}]
        kept, dropped, drawn = refill(rows, [], 40, frozenset(), lambda a: True)
        self.assertEqual([r["to"] for r in kept], ["a@x.com"])
        self.assertEqual((dropped, drawn), ([], []))

    def test_an_unsent_row_with_no_confirmed_mailbox_is_dropped(self):
        rows = [{"to": "a@x.com", "domain": "x.com"}]
        kept, dropped, _ = refill(rows, [], 40, frozenset(), lambda a: False)
        self.assertEqual(kept, [])
        self.assertEqual([r["to"] for r in dropped], ["a@x.com"])

    def test_an_unsent_row_that_still_passes_is_kept(self):
        rows = [{"to": "a@x.com", "domain": "x.com"}]
        kept, dropped, _ = refill(rows, [], 40, frozenset(), lambda a: True)
        self.assertEqual([r["to"] for r in kept], ["a@x.com"])
        self.assertEqual(dropped, [])

    def test_a_suppressed_or_rejected_row_is_settled(self):
        rows = [{"to": "a@x.com", "suppressed": "stop"}, {"to": "b@x.com", "rejected": "unverified"}]
        self.assertTrue(all(settled(r) for r in rows))
        kept, dropped, _ = refill(rows, [], 40, frozenset(), lambda a: False)
        self.assertEqual(len(kept), 2)
        self.assertEqual(dropped, [])

    def test_the_room_left_is_drawn_from_the_backlog(self):
        rows = [{"to": "a@x.com", "domain": "x.com", "message_id": "m1"}]
        backlog = [row("b.com", "denver", verified=True)]
        _, _, drawn = refill(rows, backlog, 40, frozenset(), lambda a: True)
        self.assertEqual([r["domain"] for r in drawn], ["b.com"])

    def test_the_limit_is_not_exceeded(self):
        rows = [{"to": f"a{i}@x.com", "domain": f"x{i}.com", "message_id": "m"} for i in range(3)]
        backlog = [row(f"b{i}.com", "denver", verified=True) for i in range(5)]
        kept, _, drawn = refill(rows, backlog, 4, frozenset(), lambda a: True)
        self.assertEqual(len(kept) + len(drawn), 4)

    def test_a_drawn_row_never_repeats_a_row_already_in_the_queue(self):
        rows = [{"to": "a@x.com", "domain": "x.com", "message_id": "m1"}]
        backlog = [row("x.com", "denver", verified=True), row("b.com", "denver", verified=True)]
        _, _, drawn = refill(rows, backlog, 40, frozenset({"x.com"}), lambda a: True)
        self.assertEqual([r["domain"] for r in drawn], ["b.com"])


if __name__ == "__main__":
    unittest.main()
