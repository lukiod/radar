"""Read the outreach mailbox: human replies, and bounces that must be suppressed.

    python3 tools/inbox_check.py                  # new mail since the last run
    python3 tools/inbox_check.py --days 30
    python3 tools/inbox_check.py --apply-bounces  # write new bounces to suppression.txt
    python3 tools/inbox_check.py --apply-replies  # and stop automated mail to anyone who answered

Replies are the only thing in this pipeline that can turn into money, and
nothing was reading them. Bounces were arriving the same way and being
recorded by hand, which is how the ledger came to hold 8 rejects where the
suppression list holds 9.

A reply from a prospect is now recorded as a reason to stop writing to them.
Someone who answered is a person on the other end of the thread, whatever
they said, and one that is worth money must not be queued again as cold.
Stdlib only.
"""

import argparse
import base64
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from email import message_from_bytes
from email.utils import parsedate_to_datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gmail_send import access_token, profile_address  # noqa: E402
from state_paths import INBOX_SEEN  # noqa: E402

STATE = str(INBOX_SEEN)
SUPPRESSION = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..",
                           "internal-docs", "comms", "suppression.txt")
QUEUES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..",
                      "internal-docs", "comms", "queues")
BOUNCE_FROM = ("mailer-daemon", "postmaster")
FINAL_RE = re.compile(r"^final-recipient:\s*(?:rfc822;)?\s*(\S+)", re.I | re.M)
ACTION_RE = re.compile(r"^action:\s*(\S+)", re.I | re.M)
STATUS_RE = re.compile(r"^status:\s*(\S+)", re.I | re.M)


def api(token, path, **params):
    url = "https://gmail.googleapis.com/gmail/v1/users/me/" + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + token})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)


def headers(payload):
    return {h["name"].lower(): h["value"] for h in payload.get("headers", [])}


def body_text(payload):
    """Concatenated text/plain parts, or the decoded body of a single part."""
    out = []
    stack = [payload]
    while stack:
        part = stack.pop()
        if part.get("parts"):
            stack.extend(part["parts"])
            continue
        data = part.get("body", {}).get("data")
        if data:
            try:
                out.append(base64.urlsafe_b64decode(data + "===").decode("utf-8", errors="ignore"))
            except Exception:
                continue
    return "\n".join(out)


def bounce_recipient(raw):
    """The address a delivery status notification failed on, or None.

    Gmail reports a rejected recipient in the message/delivery-status part;
    the human readable part repeats it but only the machine part is stable.
    """
    msg = message_from_bytes(raw)
    for part in msg.walk():
        if part.get_content_type() != "message/delivery-status":
            continue
        payload = part.get_payload(decode=True)
        # A message/* part comes back as a list of sub Messages, not bytes.
        text = payload.decode("utf-8", errors="ignore") if payload is not None else part.get_payload()
        text = text if isinstance(text, str) else "\n".join(m.as_string() for m in text)
        to = FINAL_RE.search(text)
        if to:
            status = STATUS_RE.search(text)
            action = ACTION_RE.search(text)
            return (to.group(1).strip("<>").lower(),
                    status.group(1) if status else "",
                    action.group(1).lower() if action else "")
    return None, "", ""


def is_permanent(status, action):
    """Whether a delivery report means the mailbox is gone for good.

    Gmail sends a delivery status notification both when a message fails and
    when it is merely delayed, and both carry 'Delivery Status Notification'
    in the subject. A delay is action=delayed with a 4.x.x status and Gmail
    is still retrying, so treating it as a dead address suppresses a working
    mailbox and reports a bounce rate that never happened, which then halves
    the next day's volume for no reason.
    """
    if action and action != "failed":
        return False
    return (status or "").startswith("5")


def is_bounce(from_addr, subject):
    return any(b in from_addr.lower() for b in BOUNCE_FROM) or "delivery status notification" in subject.lower()


def is_failure_subject(subject):
    """Whether the notification itself says the delivery failed for good.

    Gmail writes "(Failure)" in the subject only for a permanent rejection and
    "(Delay)" while it is still retrying, so the subject is a second, coarser
    signal that survives a delivery status part this code cannot parse. It
    matters because a notification read as a delay is marked seen and never
    looked at again, so a dead address goes on being mailed, which is the one
    mistake that burns the sending domain.
    """
    return "(failure)" in subject.lower()


def address_of(from_addr):
    m = re.search(r"<([^>]+)>", from_addr or "")
    return (m.group(1) if m else (from_addr or "")).strip().lower()


def is_ours(from_addr, me):
    return address_of(from_addr) == me


# Product notices, security alerts, confirmations. Not replies, and listing
# them under REPLIES makes a mailbox look answered when nobody wrote back.
SYSTEM_LOCAL = ("no-reply", "noreply", "do-not-reply", "donotreply", "notifications", "forwarding-noreply")


def is_system(from_addr):
    addr = address_of(from_addr)
    local = addr.split("@", 1)[0]
    return local in SYSTEM_LOCAL or local.startswith(("no-reply", "noreply"))


def load_seen():
    try:
        return set(json.load(open(STATE)))
    except Exception:
        return set()


def save_seen(seen):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    json.dump(sorted(seen), open(STATE, "w"))


def load_suppression():
    out = set()
    if not os.path.exists(SUPPRESSION):
        return out
    with open(SUPPRESSION, encoding="utf-8") as fh:
        for line in fh:
            line = line.split("#", 1)[0].strip().lower()
            if line:
                out.add(line)
    return out


def written_to():
    """Every address and domain this pipeline has actually emailed.

    A reply is only a reply if it comes from someone we wrote to. Anything in
    the mailbox that is not ours and not a system sender is classified as a
    reply, so without this a supplier newsletter becomes an answer, and an
    answer is about to be recorded as a reason never to write again.
    """
    addrs, domains = set(), set()
    if not os.path.isdir(QUEUES):
        return addrs, domains
    for name in os.listdir(QUEUES):
        if not name.endswith(".jsonl"):
            continue
        with open(os.path.join(QUEUES, name), encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                row = json.loads(line)
                if row.get("to"):
                    addrs.add(row["to"].strip().lower())
                if row.get("domain"):
                    domains.add(row["domain"].strip().lower())
    return addrs, domains


def message_ids(token, query):
    out, page = [], None
    while True:
        res = api(token, "messages", q=query, maxResults=100, **({"pageToken": page} if page else {}))
        out.extend(m["id"] for m in res.get("messages", []))
        page = res.get("nextPageToken")
        if not page or len(out) >= 500:
            break
    return out


def scan(days, apply_bounces, apply_replies=False):
    token = access_token()
    me = profile_address(token).lower()
    seen = load_seen()
    # Two things this query must not do, both learned the hard way. It must
    # not say in:inbox: nine bounty claim replies from omi's support desk sit
    # outside the inbox, and an inbox only search read the whole desk as
    # silence. And it must not say -from:me: Gmail's from:me matches the omi
    # desk's own address, so that filter deleted exactly the replies being
    # looked for. Ours are dropped in code below, where the comparison is ours.
    ids = message_ids(token, f"in:anywhere newer_than:{days}d -in:spam -in:trash")
    replies, bounces, delays, other = [], [], [], []
    fresh = set()
    for mid in ids:
        if mid in seen:
            continue
        full = api(token, f"messages/{mid}", format="full")
        hdr = headers(full["payload"])
        from_addr = hdr.get("from", "")
        subject = hdr.get("subject", "")
        if is_bounce(from_addr, subject):
            raw = api(token, f"messages/{mid}", format="raw")["raw"]
            addr, status, action = bounce_recipient(base64.urlsafe_b64decode(raw + "==="))
            if is_permanent(status, action) or (not status and is_failure_subject(subject)):
                bounces.append((addr, status, subject, mid))
            else:
                delays.append((addr, status, action))
        elif is_ours(from_addr, me) or is_system(from_addr):
            other.append((from_addr, subject))
        else:
            snippet = full.get("snippet", "")
            replies.append((from_addr, subject, snippet, hdr.get("date", "")))
        fresh.add(mid)

    print(f"{len(fresh)} new message(s) in the last {days} days\n")
    answered = set()
    if replies:
        addrs, domains = written_to()
        for from_addr, *_ in replies:
            who = address_of(from_addr)
            if who and (who in addrs or who.split("@")[-1] in domains):
                answered.add(who)
    if replies:
        print(f"REPLIES ({len(replies)})")
        for from_addr, subject, snippet, date in replies:
            when = ""
            try:
                when = parsedate_to_datetime(date).astimezone(timezone.utc).strftime("%m %d %H:%M")
            except Exception:
                pass
            mark = "   ANSWERED, no further automated mail" if address_of(from_addr) in answered else ""
            print(f"  {when}  {from_addr}{mark}\n    {subject}\n    {snippet[:220]}")
        print()
    # A prospect who answered has a person on the other end, whatever they
    # said, and the sequence has to stop for them. Until this was written a
    # reply changed nothing: the next queue was drawn from the pool and the
    # address could be mailed again, which is the one lead in the pipeline
    # that is worth money being treated as though it did not exist.
    if answered:
        if apply_replies:
            table = load_suppression()
            lines = [f"{who}  # replied {datetime.now(timezone.utc):%Y-%m-%d}, handled by hand"
                     for who in sorted(answered)
                     if who not in table and who.split("@")[-1] not in table]
            if lines:
                with open(SUPPRESSION, "a", encoding="utf-8") as fh:
                    for line in lines:
                        fh.write(line + "\n")
                print(f"  {len(lines)} address(es) written to suppression.txt, automated mail stops")
            print()
        else:
            print("  rerun with --apply-replies to stop automated mail to these addresses\n")
    # A bounce that is not yet in suppression.txt has not been dealt with, so
    # it is held out of `seen` and reported again next run. Otherwise a look at
    # the mailbox without --apply-bounces marks it read and the address is
    # mailed again forever, which is the one mistake that burns the domain.
    held = set()
    if bounces:
        table = load_suppression()
        print(f"BOUNCES ({len(bounces)})")
        lines = []
        stuck = set()
        for addr, status, subject, mid in bounces:
            if not addr:
                # The notification says the delivery failed but the status part
                # did not yield an address. Nothing can be suppressed from it,
                # so it is held rather than marked seen: a bounce that scrolls
                # past unread leaves a dead address in the queue.
                print(f"  unparsed bounce, message {mid}: {subject}")
                stuck.add(mid)
                continue
            already = addr in table or addr.split("@")[-1] in table
            print(f"  {addr}  status {status or '?'}{'  (already suppressed)' if already else ''}")
            if not already:
                lines.append(f"{addr}  # bounced {datetime.now(timezone.utc):%Y-%m-%d} {status or 'unknown'}")
                held.add(mid)
        if lines and apply_bounces:
            with open(SUPPRESSION, "a", encoding="utf-8") as fh:
                for line in lines:
                    fh.write(line + "\n")
            print(f"  wrote {len(lines)} line(s) to suppression.txt")
            held.clear()
        elif lines:
            print("  rerun with --apply-bounces to write these to suppression.txt")
        # An unparsed bounce survives the clear: applying the ones that parsed
        # is not a reason to forget the ones that did not.
        held |= stuck
        print()
    # Delays are reported and then marked seen. They are not an action: the
    # address is not dead until Gmail gives up and sends a failure, which
    # arrives as its own message and is caught on the next run.
    if delays:
        print(f"DELAYED ({len(delays)}), still retrying, not suppressed")
        for addr, status, action in delays:
            print(f"  {addr}  status {status or '?'} action {action or '?'}")
        print()
    if other:
        print(f"OTHER ({len(other)}), not replies")
        for from_addr, subject in other[:10]:
            print(f"  {from_addr}: {subject[:80]}")
    save_seen((seen | fresh) - held)
    return len(replies), len(bounces)


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--apply-bounces", action="store_true")
    ap.add_argument("--apply-replies", action="store_true",
                    help="stop automated mail to any address that answered")
    args = ap.parse_args(argv)
    replies, bounces = scan(args.days, args.apply_bounces, args.apply_replies)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
