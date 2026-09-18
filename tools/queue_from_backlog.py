"""Take the next day's sends out of the gated backlog.

    python3 tools/queue_from_backlog.py --backlog <backlog.jsonl> --out <queue.jsonl> --limit 40

Rows are drawn round robin across metros so a day's mail is not a hundred
letters to one city, and only rows carrying verify_queue's `verified` verdict
are eligible, so a claim that stopped being true cannot re-enter a queue.
Selection is stable: the same backlog and limit give the same queue, which
is what makes a bounced day reproducible.

A row is also dropped if its address is suppressed or its mailbox is gone.
The backlog holds rows drafted before the probe existed, so this is the last
point that can catch a dead address before it becomes a bounce.
"""

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from draft_batch import address_live  # noqa: E402
from gmail_send import SUPPRESSION, load_suppression, suppressed  # noqa: E402


def load(path):
    return [json.loads(l) for l in Path(path).read_text().splitlines() if l.strip()]


def select(rows, limit, table=frozenset(), address_ok=None):
    by_metro = defaultdict(list)
    for r in rows:
        if not (r.get("verified") and not r.get("rejected") and not r.get("message_id")):
            continue
        to = r.get("to") or ""
        if suppressed(to, table):
            continue
        if address_ok and not address_ok(to):
            continue
        by_metro[r.get("metro") or "unknown"].append(r)
    groups = [by_metro[k] for k in sorted(by_metro)]
    picked = []
    i = 0
    while len(picked) < limit and any(groups):
        group = groups[i % len(groups)]
        if group:
            picked.append(group.pop(0))
        i += 1
    return picked


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--backlog", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--limit", type=int, default=40)
    args = ap.parse_args(argv)

    table = load_suppression(os.path.abspath(SUPPRESSION))
    picked = select(load(args.backlog), args.limit, table, address_live)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        for r in picked:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    metros = defaultdict(int)
    for r in picked:
        metros[r.get("metro") or "unknown"] += 1
    print(f"{len(picked)} rows to {out} from {dict(sorted(metros.items()))}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
