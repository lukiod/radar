"""Move a past queue's unsent rows into the next day's queue.

    python3 tools/carry_forward.py --out <queue.jsonl> --limit 40

A day's queue is longer than the day's send budget, so when the cap is
reached the tail is left unsent. claimed_identifiers counts an unsent row in
any queue as spoken for, which is what stops a firm being mailed twice, and
it also means that tail can never be drawn from the backlog again. On 09 21
that was 51 drafted, verified rows in the day's queue and 13 in the follow
up queue, discarded at the moment the cap was hit.

The rows move rather than copy. A copy left behind in the source is carried
again the next day from the older file as well, and the same firm is mailed
twice, which is the exact failure claimed_identifiers exists to prevent.
Source files keep every row that is already decided, so the send log is
untouched.

Rows older than the newest queue are drawn last, addresses are re-probed
before they move, and a queue that would exceed the limit stops where it is
and carries the rest tomorrow.
"""

import argparse
import datetime
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from draft_batch import QUEUES, address_live, load_jsonl  # noqa: E402
from gmail_send import SUPPRESSION, load_suppression, suppressed  # noqa: E402

DECIDED = ("message_id", "suppressed", "rejected", "undeliverable", "error")


def decided(row):
    """A row the sender has already dealt with, so it stays where it is."""
    return any(row.get(k) for k in DECIDED)


def queue_date(path):
    """The date a queue file is named for, or "" for anything else."""
    parts = Path(path).name.split("-")
    return "-".join(parts[:3]) if len(parts) >= 3 and parts[0].isdigit() else ""


def sources(out, before):
    """Past queues to draw a tail from, newest date first.

    A queue whose date is not before the day being built is skipped, so the
    file being written can never be its own source.
    """
    found = []
    for folder in QUEUES:
        for f in folder.glob("*.jsonl"):
            if f.name.endswith("-backlog.jsonl") or f.resolve() == Path(out).resolve():
                continue
            date = queue_date(f)
            if date and date < before:
                found.append((date, f))
    return [f for _, f in sorted(found, key=lambda p: p[0], reverse=True)]


def write(path, rows):
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def carry(out, limit, before, table, address_ok=address_live):
    """Pull the unsent rows forward, leaving the decided ones behind.

    Whatever the target already holds is kept and counts against the limit,
    so carrying into a file twice, or into one the builder has already
    filled, cannot exceed the day's rows.
    """
    existing = load_jsonl(Path(out))
    room = max(0, limit - len(existing))
    moved, seen = [], {(r.get("to") or "").lower() for r in existing}
    for src in sources(out, before):
        rows = load_jsonl(src)
        stay = []
        for row in rows:
            to = (row.get("to") or "").lower()
            if (decided(row) or not to or to in seen or len(moved) >= room
                    or suppressed(to, table) or not address_ok(row["to"])):
                stay.append(row)
                continue
            seen.add(to)
            moved.append(row)
        if len(stay) != len(rows):
            write(src, stay)
    return len(existing), existing + moved


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--before", default="")
    args = ap.parse_args(argv)

    out = Path(args.out)
    before = args.before or queue_date(out) or datetime.date.today().isoformat()
    table = load_suppression(os.path.abspath(SUPPRESSION))
    kept, rows = carry(out, args.limit, before, table, address_live)
    out.parent.mkdir(parents=True, exist_ok=True)
    write(out, rows)
    print(f"{len(rows) - kept} unsent row(s) carried into {out.name}, {kept} already there")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
