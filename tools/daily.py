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
from gmail_send import run_queue  # noqa: E402
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


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--cap", type=int, default=40)
    ap.add_argument("--pace", type=int, default=90)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    today = datetime.date.today().isoformat()
    queue = QUEUES / f"{today}.jsonl"

    if pending(queue) == 0:
        # A queue that already sent some rows is not refilled: a past day's
        # queue is never re-run, and topping it up would mix two days.
        if queue.exists() and any(json.loads(l).get("message_id")
                                  for l in queue.read_text(encoding="utf-8").splitlines() if l.strip()):
            print(f"{queue.name} has already sent, leaving it alone")
            return 0
        print(f"{queue.name} has nothing pending, building {args.cap} rows")
        # Yesterday's unsent tail goes in first. It is already drafted and
        # verified, and claimed_identifiers would otherwise block it from the
        # backlog forever.
        carry_tail(["--out", str(queue), "--limit", str(args.cap), "--before", today])
        backlogs = backlogs_newest_first()
        for backlog in backlogs:
            # Each file is asked only for what the day still needs, so the
            # newest is drained first and an older one is reached rather than
            # skipped.
            left = args.cap - pending(queue)
            if left <= 0:
                break
            build_queue(["--backlog", str(backlog), "--out", str(queue), "--limit", str(left)])
        if not backlogs and pending(queue) == 0:
            sys.exit(f"no backlog in {QUEUES} and no tail to carry, nothing to build {queue.name} from")
    else:
        print(f"{queue.name}: {pending(queue)} row(s) already pending")

    n = run_queue(str(queue), args.pace, args.cap, args.dry_run)
    print(f"sent {n} from {queue.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
