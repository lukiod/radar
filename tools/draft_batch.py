"""Turn audited prospects into a send queue, one verified email per business.

    python3 tools/draft_batch.py --out state/queue-2026-09-16.jsonl [--limit 30] [--kinds dental,law,home] [--metros denver]

Reads state/prospects.jsonl (from tools/source_osm.py) and the latest
audit per domain from state/audits.jsonl (tools/audit_batch.py). A row
is drafted only when the site answered the scanner, an address on the
site's own domain was found, and the audit shows a leak the segment
template can name. Every sentence about the prospect's site comes from
a check that ran on the homepage and its contact pages; nothing about
load time, nothing guessed. Addresses already queued, sent or suppressed
are skipped.
"""

import argparse
import glob
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROSPECTS = ROOT / "state" / "prospects.jsonl"
AUDITS = ROOT / "state" / "audits.jsonl"
SUPPRESSION = ROOT.parent / "internal-docs" / "comms" / "suppression.txt"
QUEUES = [ROOT / "state", ROOT.parent / "internal-docs" / "comms" / "queues"]

SIGNATURE = '\n\nMohak Gupta\nCode Conclave\n\nReply "stop" and I will not write again.\n'
GENERIC_LOCAL = ("info", "office", "contact", "hello", "admin", "frontdesk", "reception", "appointments", "billing", "mail", "team", "support")
AMOUNTS = {"dental": 3500, "law": 4000, "home": 3000, "medspa": 3500, "physio": 3000}


def leaks(checks):
    out = []
    if not checks.get("booking"):
        out.append("booking")
    if not checks.get("form"):
        out.append("form")
    if not checks.get("viewport"):
        out.append("mobile")
    if not checks.get("click_to_call"):
        out.append("tel")
    return out


def facts_sentence(kind, domain, lk):
    """The leak in the owner's terms, built only from verified checks."""
    parts = []
    if kind == "dental":
        if "booking" in lk:
            parts.append(f"patients cannot book an appointment from {domain}, so a new patient reading at night has to wait until you open and call, and the ones who wanted it done book with the practice that let them pick a slot")
        if "form" in lk:
            parts.append("there is no form on the site either, so the only way in is the phone")
    elif kind == "law":
        if "booking" in lk:
            parts.append(f"there is no way to book a consultation on {domain}, so a potential client reading at 10pm waits for a call back the next day, and the ones who are ready book with the firm that let them choose a time")
        if "form" in lk:
            parts.append("there is no intake form either, so nothing reaches you until they call")
    else:  # home services
        if "form" in lk:
            parts.append(f"{domain} has no way to request service, so a homeowner who gets voicemail after hours has nothing to fill in and the next company's request button gets the job")
        elif "booking" in lk:
            parts.append(f"{domain} takes a message but cannot book a job, so every after hours request waits for a call back and the urgent ones go to the company that could schedule them on the spot")
    if "mobile" in lk:
        parts.append("on a phone the site is the desktop page shrunk down")
    if "tel" in lk:
        parts.append("the phone number is not tappable on a phone")
    return parts


def offer(kind):
    if kind == "dental":
        return "I set up online booking that lands on your schedule, with automatic reminders and a review request after each visit, in a week, one go, and give the site a clean new design while I am at it."
    if kind == "law":
        return "I set up consult scheduling with confidential intake and conflict check questions in front of it, in a week, one go, and give the site a design that looks like the firm you are while I am at it."
    return "I set up an after hours intake that collects the address and the problem, books the request and texts your on call tech, in a week, one go, and give the site a clean new design while I am at it."


def subject_for(kind, domain, lk):
    if kind == "dental":
        return f"the appointments {domain} is not booking at night"
    if kind == "law":
        return f"the consults {domain} is not getting at 10pm"
    if "form" in lk:
        return f"{domain} after the phone goes to voicemail"
    return f"the jobs {domain} cannot book after hours"


def greeting(email, name):
    local = email.split("@")[0].lower()
    if local in GENERIC_LOCAL or re.search(r"\d", local):
        return f"Hello, for the owner of {name}:"
    first = re.split(r"[._-]", local)[0]
    if len(first) >= 3 and first.isalpha():
        return f"Hello {first.capitalize()},"
    return f"Hello, for the owner of {name}:"


def pick_email(emails, domain):
    own = [e for e in emails if e.endswith("@" + domain)]
    if not own:
        return None
    named = [e for e in own if e.split("@")[0] not in GENERIC_LOCAL and not re.search(r"\d", e.split("@")[0])]
    return (named or own)[0]


def load_jsonl(path):
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def already_contacted():
    seen = set()
    for folder in QUEUES:
        for f in glob.glob(str(folder / "*.jsonl")):
            for r in load_jsonl(Path(f)):
                if r.get("to"):
                    seen.add(r["to"].lower())
    if SUPPRESSION.exists():
        for line in SUPPRESSION.read_text().splitlines():
            line = line.split("#", 1)[0].strip().lower()
            if line:
                seen.add(line)
    return seen


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--kinds", default="dental,law,home")
    ap.add_argument("--metros", default="")
    ap.add_argument("--min-score", type=int, default=1)
    args = ap.parse_args(argv)
    kinds = set(args.kinds.split(","))
    metros = set(args.metros.split(",")) if args.metros else None
    latest = {}
    for r in load_jsonl(AUDITS):
        if r["domain"] not in latest or r["date"] >= latest[r["domain"]]["date"]:
            latest[r["domain"]] = r
    seen = already_contacted()
    seen_domains = {e.split("@")[-1] for e in seen if "@" in e}
    rows, skipped = [], {}
    for p in load_jsonl(PROSPECTS):
        if p["kind"] not in kinds or (metros and p["metro"] not in metros):
            continue
        a = latest.get(p["domain"])
        if not a or not a["checks"].get("reachable") or a["checks"].get("blocked_status"):
            skipped["no audit or blocked"] = skipped.get("no audit or blocked", 0) + 1
            continue
        c = a["checks"]
        lk = leaks(c)
        if a["score"] < args.min_score or not (set(lk) & {"booking", "form"}):
            skipped["no leak"] = skipped.get("no leak", 0) + 1
            continue
        email = pick_email(c.get("emails") or [], p["domain"]) or (p.get("osm_email") if p.get("osm_email", "").endswith("@" + p["domain"]) else None)
        if not email:
            skipped["no own domain email"] = skipped.get("no own domain email", 0) + 1
            continue
        if email in seen or p["domain"] in seen_domains:
            skipped["already contacted"] = skipped.get("already contacted", 0) + 1
            continue
        facts = facts_sentence(p["kind"], p["domain"], lk)
        body = (greeting(email, p["name"]) + "\n\n" + facts[0][0].upper() + facts[0][1:] + ("; " + "; ".join(facts[1:]) if len(facts) > 1 else "") + ".\n\n"
                + offer(p["kind"]) + " I build a working preview of your own site first, before any decision.\n\n"
                + 'Worth a look? Reply "yes" and the preview is yours within a week.' + SIGNATURE)
        rows.append({
            "slug": p["domain"].split(".")[0], "lane": "agency", "kind": p["kind"], "metro": p["metro"], "domain": p["domain"],
            "company": p["name"], "to": email, "subject": subject_for(p["kind"], p["domain"], lk), "body": body,
            "attachments": [], "amount": AMOUNTS.get(p["kind"], 3000), "evidence": {"leaks": lk, "pages_checked": c.get("pages_checked"), "audit_date": a["date"]},
        })
        seen.add(email)
        if len(rows) >= args.limit:
            break
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("a", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"{len(rows)} drafted to {out}; skipped {skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
