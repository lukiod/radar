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
from draft_batch import (COPY, SIGNATURE, facts_sentence, greeting, offer,  # noqa: E402
                         self_check, subject_for)


def refresh_follow_up(row):
    """Bring a follow up's salutation and sign off up to date, and nothing else.

    The body of a follow up cannot be re rendered the way a first touch can,
    because it is threaded under a message that has already gone out. The
    salutation and the signature are not part of that thread, so they are the
    two parts that can be brought forward, and both needed it: 30 follow ups
    carried a sign off with no site on it, which is the one place the reader
    could have looked at the work, and the unsent ones still opened on the
    fragment the first touches used to open on.

    Both edits are safe to make blind because follow_up.compose is the only
    thing that writes a follow up body, and it always begins on greeting()
    and ends on SIGNATURE.
    """
    body = row.get("body") or ""
    lines = body.split("\n")
    if lines:
        lines[0] = greeting(row.get("to") or "", row.get("company") or "")
    body = "\n".join(lines)
    i = body.rfind("Mohak Gupta")
    if i != -1:
        body = body[:i].rstrip("\n") + SIGNATURE
    return body


def render(row):
    """The same assembly draft_batch uses, from the row's stored leaks.

    A follow up row is not rebuilt into a first touch here, because its
    In-Reply-To points at the message it would then repeat, and a carried
    tail can put follow up rows in the same file as first touch ones. Its
    signature is refreshed and nothing else about it moves.
    """
    # A sent row is the record of a message that is already in someone's
    # inbox, so it keeps the words that were actually sent. Rewriting it
    # would make the queue claim a send that never happened, and the only
    # way left to read what went out would be the mailbox.
    if row.get("message_id"):
        return None
    if row.get("follow_up_of"):
        body = refresh_follow_up(row)
        if body == row.get("body"):
            return None
        return row.get("subject"), body
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

    rows, changed, kept, sent = [], 0, 0, 0
    for line in Path(args.src).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        made = render(row)
        if made:
            if (row.get("subject"), row.get("body")) != made:
                row["subject"], row["body"] = made
                changed += 1
            else:
                kept += 1
            # A follow up is threaded under a first touch written from some
            # earlier version, so stamping it here would answer a question
            # about the first touch with the wrong row's version.
            if not row.get("follow_up_of"):
                row["copy"] = COPY
        elif row.get("message_id"):
            sent += 1
        elif row.get("follow_up_of"):
            kept += 1
        rows.append(row)

    dst = Path(args.dst or args.src)
    # A copy change rewrites the file it reads, so an interrupted write would
    # leave a half rendered backlog where a whole one was.
    tmp = dst.with_name(dst.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    os.replace(tmp, dst)
    print(f"{changed} re rendered, {kept} already current, {sent} left as sent, {len(rows)} rows to {dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
