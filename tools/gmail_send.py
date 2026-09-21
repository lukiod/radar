"""Send a queue of plain text emails through the Gmail API, stdlib only.

    python3 tools/gmail_send.py --queue <queue.jsonl> [--pace 120] [--limit 20] [--dry-run]
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

--daily-cap bounds the whole mailbox for the day, across every queue, because
the per queue limit bounds one queue and the senders run side by side.
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
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from state_paths import MX_CACHE as MX_CACHE_PATH  # noqa: E402
from state_paths import SEND_BUDGET  # noqa: E402

TOKEN_DIR = os.path.expanduser("~/.gmail-mcp")
SUPPRESSION = os.path.join(os.path.dirname(__file__), "..", "..", "internal-docs", "comms", "suppression.txt")
QUEUES = os.path.join(os.path.dirname(__file__), "..", "..", "internal-docs", "comms", "queues")
FROM_NAME = "Mohak Gupta"
DAILY_CAP = int(os.environ.get("RADAR_DAILY_CAP") or 100)


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
    creds_path = os.path.join(TOKEN_DIR, "credentials.json")
    with open(creds_path, encoding="utf-8") as fh:
        creds = json.load(fh)
    if creds.get("expiry_date", 0) > time.time() * 1000 + 60_000:
        return creds["access_token"]
    with open(os.path.join(TOKEN_DIR, "gcp-oauth.keys.json"), encoding="utf-8") as fh:
        keys = json.load(fh)
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
    # Through a private temp file, moved into place. Opening the real path with
    # "w" truncates it before the new bytes exist, so a crash in between leaves
    # a file json cannot parse and every later send fails on it. The refresh
    # runs per row, the day's send is a timer nobody watches, and two senders
    # can hold the file at once, which the per process name also rules out.
    tmp = f"{creds_path}.{os.getpid()}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(creds, fh)
        os.replace(tmp, creds_path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
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
    if row.get("in_reply_to"):
        # A follow up lands under the message it answers, in the reader's own
        # client, instead of arriving as a second stranger.
        msg["In-Reply-To"] = row["in_reply_to"]
        msg["References"] = row.get("references") or row["in_reply_to"]
    msg.set_content(row["body"])
    for path in row.get("attachments") or []:
        ctype, _ = mimetypes.guess_type(path)
        maintype, subtype = (ctype or "application/octet-stream").split("/", 1)
        with open(path, "rb") as fh:
            msg.add_attachment(fh.read(), maintype=maintype, subtype=subtype, filename=os.path.basename(path))
    return base64.urlsafe_b64encode(msg.as_bytes()).decode()


# The rest of this file opens the cache by name, so it stays a plain string here.
MX_CACHE = str(MX_CACHE_PATH)


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
    body = {"raw": build(sender, row)}
    if row.get("thread_id"):
        body["threadId"] = row["thread_id"]
    payload = json.dumps(body).encode()
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


def today_utc():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def count_sent_today(queues_dir):
    """Rows in any queue that actually went out today.

    Read from the queues rather than trusted to a counter, because the queues
    are the record of what happened. A counter that is the only witness drifts
    the first time a send happens outside this process.
    """
    n = 0
    for p in Path(queues_dir).glob("*.jsonl"):
        if p.name.endswith("-backlog.jsonl"):
            continue
        try:
            lines = p.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if (row.get("sent_at") or "").startswith(today_utc()):
                n += 1
    return n


def reserve_send(cap, queues_dir):
    """Take one slot of today's budget, or None when the day is spent.

    The per queue limit bounds one queue, not the mailbox. Three senders on
    three queues each honour their own limit and the mailbox still sends three
    times as much, which is the total the sending domain is judged on. The
    count is reconciled against the queues on every reservation so it cannot
    fall below what actually went out, and the reservation is taken under a
    lock every sender shares, so two of them cannot spend the last slot.
    """
    path = str(SEND_BUDGET)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path + ".lock", "a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            with open(path, encoding="utf-8") as fh:
                state = json.load(fh)
        except Exception:
            state = {}
        if state.get("date") != today_utc():
            state = {"date": today_utc(), "sent": 0}
        state["sent"] = max(state.get("sent") or 0, count_sent_today(queues_dir))
        if state["sent"] >= cap:
            return None
        state["sent"] += 1
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh)
        os.replace(tmp, path)
        return state["sent"]


def run_queue(path, pace, limit, dry_run, daily_cap=None):
    lock = None if dry_run else take_queue_lock(path)
    rows = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    table = load_suppression(os.path.abspath(SUPPRESSION))
    sender = "dry-run@example.com" if dry_run else profile_address(access_token())
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
        cap = DAILY_CAP if daily_cap is None else daily_cap
        if reserve_send(cap, os.path.dirname(os.path.abspath(path))) is None:
            print(f"today's cap of {cap} sends is spent; stopping with {len(rows) - i} row(s) left in {os.path.basename(path)}")
            return sent
        try:
            # Read the token per row: a paced run outlives the hour a token
            # lives, and reading it once up front killed the rest of the queue.
            res = send(access_token(), sender, row)
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
    ap.add_argument("--daily-cap", type=int, default=DAILY_CAP,
                    help="total sends allowed across every queue today")
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
        if reserve_send(args.daily_cap, QUEUES) is None:
            sys.exit(f"today's cap of {args.daily_cap} sends is spent; raise --daily-cap to override")
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
