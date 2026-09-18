"""Take the next day's sends out of the gated backlog.

    python3 tools/queue_from_backlog.py --backlog <backlog.jsonl> --out <queue.jsonl> --limit 40

Rows are drawn round robin across metros so a day's mail is not a hundred
letters to one city, and only rows carrying verify_queue's `verified` verdict
are eligible, so a claim that stopped being true cannot re-enter a queue.
Selection is stable: the same backlog and limit give the same queue, which
is what makes a bounced day reproducible.

A row is also dropped if its address is suppressed, its mailbox is gone, or
the firm has already been written to. The backlog holds rows drafted before
the probe existed, so this is the last point that can catch a dead address
before it becomes a bounce. Within a metro the rows addressed to a person
are drawn before the shared inboxes.
"""

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from draft_batch import GENERIC_LOCAL, QUEUES, address_live, load_jsonl  # noqa: E402
from gmail_send import SUPPRESSION, load_suppression, suppressed  # noqa: E402


def load(path):
    return [json.loads(l) for l in Path(path).read_text().splitlines() if l.strip()]


def claimed_identifiers(except_path=None):
    """Every address and domain that is spoken for: mailed already, or sitting
    unsent in another day's queue.

    Both halves are needed. A mailed row carries a message_id, an unsent one
    does not, and a backlog row is a copy taken before the send, so the
    backlog cannot answer this on its own and run_queue does not consult
    history either. Checking only the mailed rows is not enough: a firm
    pending in one day's queue and drawn again into the next gets two cold
    emails, which is the failure this exists to stop, and it happened, five
    firms shared between the 09 18 and 09 19 queues.

    Backlogs are skipped because they are the pool being drawn from, and the
    file being built is skipped so a rebuild returns the same rows twice.
    """
    out = set()
    except_path = Path(except_path).resolve() if except_path else None
    for folder in QUEUES:
        for f in folder.glob("*.jsonl"):
            if f.name.endswith("-backlog.jsonl"):
                continue
            if except_path and f.resolve() == except_path:
                continue
            for r in load_jsonl(f):
                # Only a row that names an address is a send record. The pool
                # and audit files under state/ carry a domain and no address,
                # and claiming their domains marked every sourced firm as
                # already written to, which starved the queue to nothing.
                if not r.get("to"):
                    continue
                out.add(r["to"].lower())
                if r.get("domain"):
                    out.add(r["domain"].lower())
    return out


def is_named(row):
    """Whether the address is something other than a known role mailbox.

    This is a heuristic, not a promise: a city or department name passes it
    and is still a shared box. It only has to be good enough to sort by. A
    mailbox like info@ is often nobody's job to read, three quarters of the
    backlog is role addressed, and the only reply so far came from an
    address that is not, so the others are drawn first and kept for later
    rather than dropped.
    """
    local = (row.get("to") or "").split("@")[0].lower()
    return bool(local) and local not in GENERIC_LOCAL and not any(h.isdigit() for h in local)


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
    # Stable, so rows of the same kind keep their order and the same backlog
    # and limit still give the same queue.
    groups = [sorted(by_metro[k], key=lambda r: not is_named(r)) for k in sorted(by_metro)]
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

    # A spoken for firm joins the table the suppression check reads, so a
    # second cold email needs the address and the domain to have both missed.
    table = load_suppression(os.path.abspath(SUPPRESSION)) | claimed_identifiers(args.out)
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
