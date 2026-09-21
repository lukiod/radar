"""Say where the outreach actually stands, in one command.

    python3 tools/funnel.py [--days 7] [--mailbox]

Every number here is read from what the run already wrote: the queues carry
each row's send time, lane and kind, so the sends are counted from them
rather than from a hand tally, and the runway is counted from the rows that
are verified and have no message id. Nothing is asked of a model and nothing
is guessed.

Bounces need the mailbox, so they are behind --mailbox and read from the
delivery status notifications the same way inbox_check does.

Why this exists: the reply rate is the only number that says whether any of
this works, and it was being recomputed by hand from four files each time it
was asked. A yardstick that has to be reassembled is one that does not get
read.
"""

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from state_paths import PROSPECTS  # noqa: E402

QUEUES = Path(__file__).resolve().parents[2] / "internal-docs" / "comms" / "queues"
SUPPRESSION = Path(__file__).resolve().parents[2] / "internal-docs" / "comms" / "suppression.txt"


def load_rows():
    """Every queue row, with the send record the sender stamped on it."""
    rows = []
    for path in sorted(QUEUES.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                row["_queue"] = path.name
                rows.append(row)
    return rows


def sendable(row):
    """Verified, not rejected, not yet sent, not held back."""
    return bool(row.get("verified")) and not row.get("rejected") and not row.get("message_id") \
        and not row.get("undeliverable") and not row.get("suppressed")


def counts(rows):
    sent = [r for r in rows if r.get("message_id") and r.get("sent_at")]
    first = [r for r in sent if not r.get("follow_up_of")]
    follow = [r for r in sent if r.get("follow_up_of")]
    runway = [r for r in rows if sendable(r)]
    return sent, first, follow, runway


def by_day(rows):
    days = defaultdict(lambda: [0, 0])
    for r in rows:
        day = r["sent_at"][:10]
        days[day][1 if r.get("follow_up_of") else 0] += 1
    return dict(sorted(days.items()))


def mailbox_bounces(token=None):
    from gmail_send import access_token
    from inbox_check import api, bounce_recipient, is_permanent, message_ids
    import base64

    token = token or access_token()
    ids = message_ids(token, "from:mailer-daemon@googlemail.com subject:(Delivery Status Notification)")
    hard, soft, seen = [], [], set()
    for mid in ids:
        raw = base64.urlsafe_b64decode(api(token, f"messages/{mid}", format="raw")["raw"] + "===")
        addr, status, action = bounce_recipient(raw)
        if not addr or addr in seen:
            continue
        seen.add(addr)
        (hard if is_permanent(status, action) else soft).append((addr, status))
    return hard, soft


def report(days, mailbox):
    rows = load_rows()
    sent, first, follow, runway = counts(rows)
    table = by_day(sent)
    recent = list(table.items())[-days:]

    print(f"{'day':12} {'first':>6} {'follow':>7}")
    for day, (f, fo) in recent:
        print(f"{day:12} {f:>6} {fo:>7}")

    print(f"\nfirst touches sent: {len(first)}")
    print(f"follow ups sent:    {len(follow)}")
    print(f"total sent:         {len(sent)}")
    # The earliest sends predate the per day queue files, so this counts what
    # a queue recorded and is a floor rather than the mailbox's own figure.
    print("                    (counted from the queues, so it excludes any send made before its row was filed)")

    if first:
        # Not by lane. lane is not the copy: 152 sent rows read agency there
        # while carrying the local body and the local closing line, so a
        # breakdown by it was read as 152 agency sends and invited a wrong
        # conclusion about which copy was failing. copy is stamped for exactly
        # this read, and the rows sent before it existed are the honest "?".
        stamped = Counter(r.get("copy") if r.get("copy") is not None else "?" for r in first)
        print("\nby copy:", dict(stamped))
        if stamped.get("?") == len(first):
            print("                    (every send predates the copy stamp, so none of them can be separated by it yet)")
        print("by kind:", dict(Counter(r.get("kind") or "?" for r in first)))

    print(f"\nsuppression list:   {len([l for l in SUPPRESSION.read_text().splitlines() if l.strip() and not l.startswith('#')]) if SUPPRESSION.exists() else 0} entries")
    print(f"runway:             {len(runway)} verified rows waiting, about {len(runway) // 100} days at 100 a day")
    held = Counter(r.get("rejected") and "rejected" or r.get("undeliverable") and "undeliverable"
                   or r.get("suppressed") and "suppressed" or None for r in rows)
    held.pop(None, None)
    if held:
        print(f"held back:          {dict(held)}")
    if PROSPECTS.exists():
        print(f"pool:               {len(PROSPECTS.read_text().splitlines())} sourced domains")

    if mailbox:
        hard, soft = mailbox_bounces()
        print(f"\nhard bounces:       {len(hard)}")
        print(f"soft bounces:       {len(soft)}")
        if sent:
            print(f"hard bounce rate:   {len(hard) / len(sent) * 100:.1f}% of {len(sent)} sends")
    else:
        print("\nbounces: not read. Pass --mailbox to count them from the mailbox.")
    return 0


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--mailbox", action="store_true")
    args = ap.parse_args(argv)
    return report(args.days, args.mailbox)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
