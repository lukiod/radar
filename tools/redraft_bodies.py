"""Rewrite the subject and body of an existing queue or backlog in place.

    python3 tools/redraft_bodies.py --in <backlog.jsonl> [--out <path>]

A queue row carries its own rendered body, so changing the copy in
draft_batch leaves every already drafted row speaking the old version. The
fix is not to re run draft_batch: the pool shrinks as addresses are
contacted, so a rebuild returns a subset of what is already there and
silently drops the rest. It is to re render each row from the audit facts
the row already carries.

Nothing but subject and body is touched, so sent rows, suppression state
and evidence survive a copy change untouched.
"""

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from draft_batch import (SIGNATURE, facts_sentence, greeting, offer,  # noqa: E402
                         self_check, subject_for)


def render(row):
    """The same assembly draft_batch uses, from the row's stored leaks."""
    kind = row.get("kind")
    domain = row.get("domain")
    lk = (row.get("evidence") or {}).get("leaks") or []
    facts = facts_sentence(kind, domain, lk)
    if not facts:
        return None
    body = (greeting(row.get("to") or "", row.get("company") or "")
            + "\n\n" + facts[0] + ("; " + "; ".join(facts[1:]) if len(facts) > 1 else "") + ".\n\n"
            + offer(kind, lk) + "\n\n"
            + self_check(kind, domain, lk) + "\n\n"
            + "If it is worth closing, reply and I will send the design for your own homepage first, "
              "before you decide anything." + SIGNATURE)
    return subject_for(kind, domain, lk), body


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="src", required=True)
    ap.add_argument("--out", dest="dst")
    args = ap.parse_args(argv)

    rows, changed, kept = [], 0, 0
    for line in Path(args.src).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        made = render(row)
        if made and (row.get("subject"), row.get("body")) != made:
            row["subject"], row["body"] = made
            changed += 1
        elif made:
            kept += 1
        rows.append(row)

    dst = Path(args.dst or args.src)
    with dst.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"{changed} re rendered, {kept} already current, {len(rows)} rows to {dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
