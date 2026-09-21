"""Build a second touch for cold emails that got no answer.

    python3 tools/follow_up.py --out <queue.jsonl> [--after-days 4] [--limit 40]

Every send so far went out once. A prospect who never replied got one
letter and nothing after it, which is the cheapest reply rate left on the
table: the first email has to earn attention from nothing, and a short
second note in the same thread arrives under a subject the reader already
saw and chose not to answer.

The second touch is a reply, not a new cold email. It carries In Reply To
and References pointing at the Message Id of the first, and the sender
files it on the same thread, so the reader sees it directly beneath the
message they ignored instead of as a fresh stranger in the inbox.

Nothing new is claimed about the firm. The note restates the same verified
leak from the row's own evidence, so a follow up can never introduce a
claim the audit did not make. A row whose facts no longer render is
dropped rather than sent with an empty opening.

One follow up per firm, ever. A firm with a follow up already sent or
already queued is refused, a suppressed address is refused, and the row
carrying "reply" or "no" is a human who answered and must not be written
to again.

The Message Id is read from the Gmail API once per send and cached, since
the queue row only stores the API id, which is not what the References
header needs.
"""

import argparse
import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from draft_batch import QUEUES, SIGNATURE, greeting, load_jsonl  # noqa: E402
from gmail_send import SUPPRESSION, access_token, load_suppression, suppressed  # noqa: E402
from state_paths import MESSAGE_IDS  # noqa: E402

ANSWERED = ("reply", "replied", "reply_at", "answered")


def sent_rows():
    """Every row that actually went out, newest last."""
    out = []
    for folder in QUEUES:
        for f in sorted(Path(folder).glob("*.jsonl")):
            if f.name.endswith("-backlog.jsonl"):
                continue
            for r in load_jsonl(f):
                if r.get("message_id") and r.get("sent_at"):
                    out.append(r)
    return out


def spoken_for():
    """Addresses and domains that must not get a second touch.

    Two things claim a firm: a follow up already sent, and a follow up
    already sitting in a queue built earlier. The second half is what
    stops a rebuild from mailing the same note twice, and it is read from
    the queues rather than from the file being written.
    """
    addrs, domains = set(), set()
    for folder in QUEUES:
        for f in Path(folder).glob("*.jsonl"):
            if f.name.endswith("-backlog.jsonl"):
                continue
            for r in load_jsonl(f):
                if not r.get("follow_up_of"):
                    continue
                if r.get("to"):
                    addrs.add(r["to"].lower())
                if r.get("domain"):
                    domains.add(r["domain"].lower())
    return addrs, domains


def answered(row):
    return any(row.get(k) for k in ANSWERED)


def message_id_of(token, api_id, cache):
    """The RFC Message Id header, which is what References must carry."""
    if api_id in cache:
        return cache[api_id]
    url = ("https://gmail.googleapis.com/gmail/v1/users/me/messages/" + api_id
           + "?format=metadata&metadataHeaders=Message-Id")
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + token})
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.load(resp)["payload"]["headers"]
    for h in payload:
        if h["name"].lower() == "message-id":
            cache[api_id] = h["value"]
            return h["value"]
    cache[api_id] = None
    return None


def note(kind, domain, lk):
    """One short restatement of the leak the first email already named.

    Rebuilt from the row's own leaks rather than reusing the opening
    sentence, so the second touch reads shorter than the first instead of
    repeating it. A leak the audit did not find is never mentioned.
    """
    if "booking" in lk:
        if kind == "law":
            return f"clients reading at 10pm still cannot book a consult on {domain}"
        if kind == "dental":
            return f"patients reading at night still cannot book on {domain}"
        return f"the after hours jobs on {domain} still cannot be booked"
    if "form" in lk:
        return f"there is still nowhere on {domain} to ask a question before committing"
    return None


def compose(row, when):
    """The second note: same verified leak, smaller ask, one line of exit."""
    domain = row.get("domain")
    lk = (row.get("evidence") or {}).get("leaks") or []
    gap = note(row.get("kind"), domain, lk)
    if not gap:
        return None
    body = (greeting(row.get("to") or "", row.get("company") or "")
            + f"\n\nI wrote on {when} about {domain} and did not hear back, which is fair."
            + f"\n\nOne thing I would still leave with you: {gap}."
            + f"\n\nReply with the word design and I will build a homepage for {domain}"
              f" and send it over, yours to keep either way."
            + SIGNATURE)
    return f"Re: {row['subject']}", body


def build(rows, token, cache, after_days, limit, table):
    addrs, domains = spoken_for()
    cutoff = datetime.now(timezone.utc) - timedelta(days=after_days)
    out = []
    for r in rows:
        if len(out) >= limit:
            break
        to = (r.get("to") or "").lower()
        domain = (r.get("domain") or "").lower()
        if not to or to in addrs or domain in domains:
            continue
        if suppressed(to, table):
            continue
        if r.get("rejected") or r.get("undeliverable") or r.get("error") or answered(r):
            continue
        try:
            when = datetime.strptime(r["sent_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if when > cutoff:
            continue
        made = compose(r, when.strftime("%d %B"))
        if not made:
            continue
        ref = message_id_of(token, r["message_id"], cache)
        if not ref:
            continue
        subject, body = made
        out.append({
            "slug": r.get("slug"),
            "lane": r.get("lane"),
            "kind": r.get("kind"),
            "metro": r.get("metro"),
            "domain": r.get("domain"),
            "company": r.get("company"),
            "to": r.get("to"),
            "subject": subject,
            "body": body,
            "attachments": [],
            "amount": r.get("amount"),
            "evidence": r.get("evidence"),
            "follow_up_of": r["message_id"],
            "in_reply_to": ref,
            "references": ref,
            "thread_id": r.get("thread_id"),
            "verified": r.get("verified"),
        })
        addrs.add(to)
        domains.add(domain)
    return out


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--after-days", type=int, default=4)
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    cache = {}
    if MESSAGE_IDS.exists():
        cache = json.loads(MESSAGE_IDS.read_text())
    table = load_suppression(os.path.abspath(SUPPRESSION))
    # The token is read even in a dry run: the Message Id comes from the API,
    # so a dry run that skipped it would report zero rows and prove nothing.
    token = access_token()
    rows = build(sent_rows(), token, cache, args.after_days, args.limit, table)
    if not args.dry_run:
        MESSAGE_IDS.parent.mkdir(parents=True, exist_ok=True)
        MESSAGE_IDS.write_text(json.dumps(cache))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"{len(rows)} follow ups to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
