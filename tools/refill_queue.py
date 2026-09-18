"""Replace the unsent rows of a queue with rows that pass the address gate.

    python3 tools/refill_queue.py --queue <queue.jsonl> --backlog <backlog.jsonl> [--limit 40]

A queue built before the gate existed holds rows the gate would now hold
back, and queue_from_backlog cannot repair it: that writes a fresh file, and
the sent rows with their message_ids are the one thing that must survive.
Rows already mailed, suppressed or rejected are kept exactly as they are,
unsent rows that still pass are kept too, and the room that opens up is
drawn again from the backlog with the gate applied.

The 09 18 queue is the case this exists for: 20 of its 35 rows had gone out
and 8 of the 15 still waiting had no confirmed mailbox.
"""

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from draft_batch import address_live, load_jsonl  # noqa: E402
from gmail_send import SUPPRESSION, load_suppression  # noqa: E402
from queue_from_backlog import claimed_identifiers, load, select  # noqa: E402


def settled(row):
    """A row that is already decided, so refilling must not touch it."""
    return bool(row.get("message_id") or row.get("suppressed") or row.get("rejected"))


def refill(rows, backlog, limit, table, address_ok=address_live):
    """Keep what is settled or still passes, drop the rest, draw the room."""
    kept, dropped = [], []
    for r in rows:
        if settled(r):
            kept.append(r)
        elif r.get("to") and address_ok(r["to"]):
            kept.append(r)
        else:
            dropped.append(r)
    room = max(0, limit - len(kept))
    return kept, dropped, select(backlog, room, table, address_ok)


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--queue", required=True)
    ap.add_argument("--backlog", required=True)
    ap.add_argument("--limit", type=int, default=40)
    args = ap.parse_args(argv)

    out = Path(args.queue)
    rows = load(out) if out.exists() else []
    # Nothing is excluded here: every other queue's rows are spoken for, and
    # so are this one's, which is what stops a kept firm being drawn again.
    table = load_suppression(os.path.abspath(SUPPRESSION)) | claimed_identifiers()
    kept, dropped, drawn = refill(rows, load(args.backlog), args.limit, table)
    with out.open("w", encoding="utf-8") as fh:
        for r in kept + drawn:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"{out.name}: kept {len(kept)}, dropped {len(dropped)} with no confirmed mailbox, "
          f"drew {len(drawn)}, {len(kept) + len(drawn)} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
