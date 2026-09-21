"""Build today's queue if it is missing and send it, with no one watching.

    python3 tools/daily.py [--cap 40] [--dry-run]

Every send so far happened because a session was open and someone ran the
sender by hand. An unsent queue earns nothing, so the last mile is the one
that should not depend on a person being present.

The cap is the base rate for one mailbox. The 3% rule is deliberately not
computed here: it needs a bounce rate for the previous batch read out of the
mailbox, and a rule that silently throttles or fails to throttle on a guess
is worse than one an operator applies with the number in front of them.

Deterministic Python on purpose. No model is asked to decide whether to
send, how many, or to whom, so a daily run costs nothing but the sends.
"""

import argparse
import datetime
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from carry_forward import main as carry_tail  # noqa: E402
from follow_up import main as write_follow_ups  # noqa: E402
from gmail_send import run_queue  # noqa: E402
from inbox_check import scan as scan_inbox  # noqa: E402
from queue_from_backlog import main as build_queue  # noqa: E402

QUEUES = Path(__file__).resolve().parents[2] / "internal-docs" / "comms" / "queues"


def backlogs_newest_first():
    """Every backlog, newest first.

    Reading only the newest one stranded inventory. The 372 row backlog
    drafted 09 19 held 365 verified rows that had never been sent, and it was
    never drawn from again because 09 21's was newer, so a day's quota came
    out of one file while a fully drafted one sat beside it. Preferring fresh
    rows is the point; abandoning old ones was not.
    """
    found = sorted(QUEUES.glob("*-backlog.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    return found


def pending(path):
    """Rows in the queue that have not been sent, skipped or rejected."""
    if not path.exists():
        return 0
    n = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if not (row.get("message_id") or row.get("suppressed") or row.get("rejected")):
            n += 1
    return n


def send_follow_ups(pace, dry_run=False):
    """Send every second touch that is drafted and still unsent.

    follow_up.py builds the lane and nothing ran it, so a file it wrote was as
    far as a follow up got. Seven written notes, three of them to a named
    person, sat in 2026-09-21-followup.jsonl with no schedule that would ever
    pick them up, and a rebuild would not have recovered them either: spoken_for
    counts a row already carrying follow_up_of in a queue as claimed, so a
    second build drops them.

    They go out ahead of the cold queue because a note under a thread the
    reader already opened is worth more than one more first touch, and the
    mailbox cap covers both, so the day's total is unchanged.
    """
    n = 0
    for f in sorted(QUEUES.glob("*-followup.jsonl")):
        if pending(f) == 0:
            continue
        n += run_queue(str(f), pace, 0, dry_run)
    return n


def build_follow_ups(dry_run=False):
    """Write today's second touches from sends old enough to have gone quiet.

    Draining the lane alone empties it, since nothing rebuilt it: the second
    touch was a file somebody wrote once. The build goes to a fresh dated file
    so it cannot land on a queue that still holds unsent rows, and it refuses
    to write at all while any follow up file is still pending, because a rebuild
    claims every row already carrying follow_up_of and would drop them.

    Guarded, because it reads the Gmail API for a Message Id per first send and
    a bad minute there must not cost the day's cold queue as well.
    """
    if any(pending(f) for f in QUEUES.glob("*-followup.jsonl")):
        print("follow up lane still holds unsent rows, not rebuilding")
        return
    out = QUEUES / f"{datetime.date.today().isoformat()}-followup.jsonl"
    try:
        write_follow_ups(["--out", str(out)] + (["--dry-run"] if dry_run else []))
    except Exception as err:
        print(f"follow up build skipped: {err}")


def read_replies(days=3, apply=True):
    """Read the mailbox on the way in, rather than when someone remembers to.

    The whole machine exists to produce a reply, and nothing was watching for
    one. A run that sends a hundred emails and says nothing about the answer
    that arrived overnight leaves the only thing worth money sitting in a
    mailbox until somebody happens to look.

    On the way in and not on the way out, because run_queue reads
    suppression.txt once when it starts: a prospect who answered "stop"
    overnight is only held out of this batch if their reply is recorded
    before the batch is handed over. Reading after the send mails them one
    more time and finds out why the following morning.

    Bounces are applied on the same pass, because a dead address that is not
    suppressed is mailed again tomorrow, and a prospect who answered is
    recorded as one, because a person who replied must not be drawn again as
    cold. A dry run only looks: it writes nothing and stops nothing.
    """
    replies, bounces = scan_inbox(days, apply_bounces=apply, apply_replies=apply)
    if replies:
        print(f"\n{'=' * 66}\n  {replies} REPLY OR REPLIES WAITING IN THE MAILBOX\n{'=' * 66}")
    if bounces:
        print(f"{bounces} hard bounce(s) suppressed this run")
    return replies


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--cap", type=int, default=40)
    ap.add_argument("--pace", type=int, default=90)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    today = datetime.date.today().isoformat()
    queue = QUEUES / f"{today}.jsonl"

    # First, before anything can return early. The read sits at the top so a
    # day whose queue already went out still gets its mailbox read, and so the
    # suppression it writes is in place before the builder and the sender look
    # at it.
    read_replies(apply=not args.dry_run)

    # Before the queue check, so a day whose cold queue already went out still
    # gets its second touches away instead of returning here and stranding them.
    sent_follow = send_follow_ups(args.pace, args.dry_run)
    print(f"{'would send' if args.dry_run else 'sent'} {sent_follow} follow up(s)")
    build_follow_ups(args.dry_run)

    # A queue that already sent some rows is not refilled: a past day's queue
    # is never re-run, and topping it up would mix two days.
    started = queue.exists() and any(json.loads(l).get("message_id")
                                     for l in queue.read_text(encoding="utf-8").splitlines() if l.strip())
    if pending(queue) == 0:
        if started:
            print(f"{queue.name} has already sent, leaving it alone")
            return 0
        print(f"{queue.name} has nothing pending, building {args.cap} rows")
        # Yesterday's unsent tail goes in first. It is already drafted and
        # verified, and claimed_identifiers would otherwise block it from the
        # backlog forever.
        carry_tail(["--out", str(queue), "--limit", str(args.cap), "--before", today])
    else:
        print(f"{queue.name}: {pending(queue)} row(s) already pending")

    # Then draw up to the cap, whether the queue started the day empty or not.
    # A row the verifier rejected is not pending, so a queue built to the cap
    # sends fewer than the cap and nothing replaced the shortfall: seven rows
    # went that way in the 09 22 queue alone. A queue that has begun sending is
    # left alone, for the reason above.
    if not started:
        backlogs = backlogs_newest_first()
        try:
            for backlog in backlogs:
                # Each file is asked only for what the day still needs, so the
                # newest is drained first and an older one is reached rather
                # than skipped.
                left = args.cap - pending(queue)
                if left <= 0:
                    break
                build_queue(["--backlog", str(backlog), "--out", str(queue), "--limit", str(left)])
        except Exception as err:
            # Guarded for the same reason the follow up build is: a bad minute
            # in the builder must not cost the day's cold queue, which is
            # already drafted and sitting right here.
            print(f"top up skipped: {err}")
        if not backlogs and pending(queue) == 0:
            sys.exit(f"no backlog in {QUEUES} and no tail to carry, nothing to build {queue.name} from")

    n = run_queue(str(queue), args.pace, args.cap, args.dry_run)
    print(f"sent {n} from {queue.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
