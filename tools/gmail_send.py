"""Send a queue of plain text emails through the Gmail API, stdlib only.

    python3 tools/gmail_send.py --queue state/queue-2026-09-16.jsonl [--pace 120] [--limit 20] [--dry-run]
    python3 tools/gmail_send.py --one to@example.com "Subject" body.txt [--attach file.png]

The queue is JSON lines; each row has "to", "subject", "body" and
optionally "attachments" (absolute paths), "cc", "slug" and "lane". Rows
that already carry "message_id" are skipped, so the file doubles as the
send log: after each send the row is rewritten with "message_id",
"thread_id" and "sent_at". A row whose address matches the suppression
file is skipped and marked "suppressed".

Credentials come from the token file the Gmail MCP server wrote
(~/.gmail-mcp/credentials.json, scope gmail.modify) and the OAuth client in
gcp-oauth.keys.json; nothing is stored here. Pacing is a plain sleep between
sends so a batch looks like a person at a keyboard, not a blast.
"""

import argparse
import base64
import fcntl
import json
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formataddr

TOKEN_DIR = os.path.expanduser("~/.gmail-mcp")
SUPPRESSION = os.path.join(os.path.dirname(__file__), "..", "..", "internal-docs", "comms", "suppression.txt")
FROM_NAME = "Mohak Gupta"


def load_suppression(path):
    out = set()
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.split("#", 1)[0].strip().lower()
            if line:
                out.add(line)
    return out


def suppressed(addr, table):
    addr = addr.lower()
    domain = addr.split("@", 1)[-1]
    return addr in table or domain in table


def access_token():
    creds = json.load(open(os.path.join(TOKEN_DIR, "credentials.json")))
    if creds.get("expiry_date", 0) > time.time() * 1000 + 60_000:
        return creds["access_token"]
    keys = json.load(open(os.path.join(TOKEN_DIR, "gcp-oauth.keys.json")))
    client = keys.get("installed") or keys.get("web")
    form = urllib.parse.urlencode({
        "client_id": client["client_id"],
        "client_secret": client["client_secret"],
        "refresh_token": creds["refresh_token"],
        "grant_type": "refresh_token",
    }).encode()
    req = urllib.request.Request("https://oauth2.googleapis.com/token", data=form,
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        tok = json.load(resp)
    creds["access_token"] = tok["access_token"]
    creds["expiry_date"] = int((time.time() + tok.get("expires_in", 3600)) * 1000)
    json.dump(creds, open(os.path.join(TOKEN_DIR, "credentials.json"), "w"))
    return creds["access_token"]


def profile_address(token):
    req = urllib.request.Request("https://gmail.googleapis.com/gmail/v1/users/me/profile",
                                 headers={"Authorization": "Bearer " + token})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)["emailAddress"]


def build(sender, row):
    msg = EmailMessage()
    msg["From"] = formataddr((FROM_NAME, sender))
    msg["To"] = row["to"]
    if row.get("cc"):
        msg["Cc"] = ", ".join(row["cc"]) if isinstance(row["cc"], list) else row["cc"]
    msg["Subject"] = row["subject"]
    msg.set_content(row["body"])
    for path in row.get("attachments") or []:
        ctype, _ = mimetypes.guess_type(path)
        maintype, subtype = (ctype or "application/octet-stream").split("/", 1)
        with open(path, "rb") as fh:
            msg.add_attachment(fh.read(), maintype=maintype, subtype=subtype, filename=os.path.basename(path))
    return base64.urlsafe_b64encode(msg.as_bytes()).decode()


MX_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "state", "mx-cache.json")


def _doh(name, rtype):
    """Resolve through DNS over HTTPS so no local resolver or dnspython is needed."""
    url = "https://dns.google/resolve?name=" + urllib.parse.quote(name) + "&type=" + rtype
    req = urllib.request.Request(url, headers={"accept": "application/dns-json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.load(resp)


def domain_accepts_mail(domain):
    """False only when the domain provably cannot receive mail.

    A published Null MX (".") means the domain refuses all mail (the
    myflowermounddentist.com bounce). No MX at all is fine when an A record
    exists: RFC 5321 falls back to the address record. A resolution failure
    is treated as unknown and sent; an outage must not silently drop a
    prospect.
    """
    try:
        cache = json.load(open(MX_CACHE))
    except Exception:
        cache = {}
    if domain in cache:
        return cache[domain]
    try:
        ans = _doh(domain, "MX").get("Answer", [])
        records = [a["data"].split()[-1].rstrip(".") for a in ans if a.get("type") == 15]
        if records:
            ok = any(r not in ("", ".") for r in records)
        else:
            ok = _doh(domain, "A").get("Status") == 0
    except Exception:
        return True
    cache[domain] = ok
    try:
        os.makedirs(os.path.dirname(MX_CACHE), exist_ok=True)
        # Merge before writing: another tool (the auditor, the queue builder)
        # may have resolved domains since this process loaded the file, and a
        # blind dump would throw those answers away.
        on_disk = {}
        try:
            with open(MX_CACHE) as fh:
                on_disk = json.load(fh)
        except Exception:
            on_disk = {}
        on_disk.update(cache)
        json.dump(on_disk, open(MX_CACHE, "w"))
    except Exception:
        pass
    return ok


def send(token, sender, row):
    payload = json.dumps({"raw": build(sender, row)}).encode()
    req = urllib.request.Request("https://gmail.googleapis.com/gmail/v1/users/me/messages/send", data=payload,
                                 headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)


def take_queue_lock(path):
    """One sender per queue, enforced.

    Two processes on the same queue each hold their own copy of the rows, so
    they both send the same prospects and each rewrite clobbers the other's
    "message_id": on 09 17 that put the same cold email in ten inboxes twice
    and left nine sends unrecorded. The lock file makes that impossible.
    """
    lock_path = os.path.join(os.path.dirname(os.path.abspath(path)) or ".", "." + os.path.basename(path) + ".lock")
    fh = open(lock_path, "a+")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.seek(0)
        holder = fh.read().strip() or "another process"
        sys.exit(f"queue {path} is already being sent by {holder}; refusing to run a second sender")
    fh.seek(0)
    fh.truncate()
    fh.write(f"pid {os.getpid()} since {datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    fh.flush()
    return fh


def run_queue(path, pace, limit, dry_run):
    lock = None if dry_run else take_queue_lock(path)
    rows = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    table = load_suppression(os.path.abspath(SUPPRESSION))
    token = None if dry_run else access_token()
    sender = "dry-run@example.com" if dry_run else profile_address(token)
    sent = 0
    for i, row in enumerate(rows):
        if row.get("message_id") or row.get("suppressed"):
            continue
        if row.get("rejected"):
            # verify_queue measured the claim against the live site and it did
            # not hold; sending it would repeat the false claim class.
            print(f'skip {row["to"]}: {row["rejected"]}')
            continue
        if suppressed(row["to"], table):
            row["suppressed"] = True
            print(f'skip {row["to"]}: suppressed')
            continue
        if not domain_accepts_mail(row["to"].split("@")[-1].lower()):
            row["undeliverable"] = True
            print(f'skip {row["to"]}: domain publishes no mail route (Null MX)')
            rewrite(path, rows)
            continue
        for att in row.get("attachments") or []:
            if not os.path.exists(att):
                sys.exit(f'{row["to"]}: attachment missing: {att}')
        if limit and sent >= limit:
            break
        if dry_run:
            print(f'would send to {row["to"]}: {row["subject"]!r} ({len(row["body"])} chars, {len(row.get("attachments") or [])} attachments)')
            sent += 1
            continue
        if sent and pace:
            time.sleep(pace)
        try:
            res = send(token, sender, row)
        except urllib.error.HTTPError as err:
            body = err.read().decode(errors="ignore")[:300]
            print(f'FAILED {row["to"]}: HTTP {err.code} {body}')
            row["error"] = f"HTTP {err.code}"
            rewrite(path, rows)
            if err.code in (401, 403, 429):
                return sent
            continue
        row["message_id"] = res["id"]
        row["thread_id"] = res.get("threadId")
        row["sent_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        sent += 1
        print(f'sent {row["to"]} {res["id"]}')
        rewrite(path, rows)
    return sent


def rewrite(path, rows):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def main(argv):
    sys.stdout.reconfigure(line_buffering=True) if hasattr(sys.stdout, "reconfigure") else None
    ap = argparse.ArgumentParser()
    ap.add_argument("--queue")
    ap.add_argument("--one", nargs=3, metavar=("TO", "SUBJECT", "BODYFILE"))
    ap.add_argument("--attach", action="append", default=[])
    ap.add_argument("--pace", type=int, default=90, help="seconds between sends")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    if args.one:
        to, subject, bodyfile = args.one
        row = {"to": to, "subject": subject, "body": open(bodyfile, encoding="utf-8").read(), "attachments": args.attach}
        if args.dry_run:
            print(build("dry-run@example.com", row)[:80])
            return 0
        token = access_token()
        res = send(token, profile_address(token), row)
        print(res["id"])
        return 0
    if not args.queue:
        ap.error("--queue or --one required")
    n = run_queue(args.queue, args.pace, args.limit, args.dry_run)
    print(f"{n} sent" if not args.dry_run else f"{n} would send")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
