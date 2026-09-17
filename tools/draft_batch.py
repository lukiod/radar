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
import datetime
import glob
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from site_audit import audit, mail_route  # noqa: E402
from verify_email import rcpt_check  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PROSPECTS = ROOT / "state" / "prospects.jsonl"
AUDITS = ROOT / "state" / "audits.jsonl"
EMAIL_CACHE = ROOT / "state" / "email-check.json"
SUPPRESSION = ROOT.parent / "internal-docs" / "comms" / "suppression.txt"
QUEUES = [ROOT / "state", ROOT.parent / "internal-docs" / "comms" / "queues"]

SIGNATURE = '\n\nMohak Gupta\nCode Conclave\n\nReply "stop" and I will not write again.\n'
GENERIC_LOCAL = ("info", "office", "contact", "hello", "admin", "frontdesk", "reception", "appointments", "billing", "mail", "team", "support",
                 "smile", "smiles", "dispatch", "service", "sales", "scheduling", "schedule", "help", "legalhelp", "lawyers", "law", "dental",
                 "staff", "manager", "management", "customerservice", "estimates", "quotes", "welcome", "inquiries", "questions")
# Mailboxes that are not the business's front door; a draft is never addressed to them.
SKIP_LOCAL = ("careers", "jobs", "hr", "press", "media", "privacy", "legal", "webmaster", "noreply", "no-reply", "marketing", "newsletter", "accounting", "payables", "records")
FIRST_NAMES = set("""james john robert michael william david richard joseph thomas charles christopher daniel matthew anthony mark donald
steven paul andrew joshua kenneth kevin brian george timothy ronald edward jason jeffrey ryan jacob gary nicholas eric jonathan stephen larry
justin scott brandon benjamin samuel gregory alexander frank patrick raymond jack dennis jerry tyler aaron jose adam nathan henry douglas zachary
peter kyle noah ethan jeremy walter christian keith roger terry austin sean gerald carl harold dylan arthur lawrence jordan jesse bryan billy
bruce gabriel joe logan alan juan albert willie elijah wayne randy vincent mason roy ralph bobby russell bradley philip eugene
mary patricia jennifer linda elizabeth barbara susan jessica sarah karen lisa nancy betty sandra margaret ashley kimberly emily donna michelle
carol amanda melissa deborah stephanie dorothy rebecca sharon laura cynthia amy kathleen angela shirley brenda emma anna pamela nicole samantha
katherine christine helen debra rachel carolyn janet maria catherine heather diane olivia julie joyce victoria ruth virginia lauren kelly
christina joan evelyn judith andrea hannah megan cheryl jacqueline martha madison teresa gloria sara janice ann kathryn abigail sophia frances
jean alice judy isabella julia grace amber denise danielle marilyn beverly charlotte natalie theresa diana brittany doris kayla alexis lori
tim tom mike dave dan chris matt jim bill bob steve jeff greg jon ben sam alex nick rick ron ken rob joe jay ted andy pat ed fred phil
kate liz beth sue meg jen jess kim deb kathy cathy patti becky vicki terri traci jodi jill molly erika erica erin colleen kristen kristin
kristopher anthony spencer trevor derek travis shane cody dustin brett blake wade chad todd troy tony rich ray hank pete jake josh luke
"""
.split())
AMOUNTS = {"dental": 3500, "law": 4000, "home": 3000, "medspa": 3500, "physio": 3000, "agency": 800}
SAMPLE_CSV = ROOT.parent / "internal-docs" / "earn" / "data" / "lead-sample-free.csv"


AGENCY_BODY = (
    "Attached are dental, law and home service sites I audited this week, with what is actually broken on each: "
    "no way to book or request service, no contact form, not built for phones, a phone number that is not tappable. "
    "Every row is a measured fact from the homepage and the contact page, not a scrape, so the first email your team "
    "sends is about the owner's business, not about you.\n\n"
    "They are yours, no strings. If the quality is right, 500 rows to your target list (trade, metro, size) is $400 "
    "once, or I keep it current: 100 fresh rows delivered on the 1st of every month for $150/month, no re-ask."
)


# OSM website tags go stale: a domain a small business used to own can
# expire and get resold or hijacked. Requiring the OSM name's words to
# appear in the page's own title was tried and rejected: 163 of ~180
# candidates failed it purely because their SEO title is generic ("Dentist",
# "Best Roofing Company in Denver") and never repeats the brand name, which
# would have thrown out real leads at a much higher rate than it catches
# stale domains. The two failure modes actually seen are both narrow and
# high precision to detect directly: the domain got resold to an unrelated
# business (title reads as a totally different trade), or it is parked,
# expired or hijacked into spam.
OFF_TOPIC_TITLE_WORDS = ("coffee", "cafe", "café", "restaurant", "bar & grill", "hotel", "motel",
                         "casino", "slot", "judi", "toto", "sbobet", "poker", "gacor",
                         "taruhan", "situs", "domain for sale", "buy this domain", "domain not valid",
                         "parked domain", "future home of", "this domain may be for sale")
# "real estate" and "realty" were tried and dropped: they false-flagged
# real estate law firms (honelegal.com, orangewoodlaw.com, raylawaz.com),
# a legitimate law specialty, at a much higher rate than they caught an
# actual real estate agency mistagged as a different kind.


def redirected_elsewhere(final_url, domain):
    """True when the domain now serves a different business's site."""
    host = re.sub(r"^https?://", "", (final_url or "")).split("/", 1)[0].lower()
    if not host:
        return False  # no final url recorded; other checks already gate this
    host = host.removeprefix("www.")
    dom = domain.removeprefix("www.")
    return host != dom and not host.endswith("." + dom)


def site_looks_unrelated(title):
    """True when the page's own title says this is not a small business
    homepage at all: a resold domain now selling coffee instead of dental
    work, a gambling spam hijack, or a parked/expired registrar page."""
    if not title:
        return False  # nothing to go on either way; other checks (reachable) already gate this
    low = title.lower()
    return any(w in low for w in OFF_TOPIC_TITLE_WORDS)


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


# Branching on the form alone told practices with a working scheduler that
# they cannot book. Both facts are read; see internal-docs/earn/outreach-scale.md
SUBJECT_ASK = {
    "dental": "{domain}: no way to ask before booking",
    "law": "{domain}: no way to ask before the consult",
    "home": "{domain}: no way to describe the job before booking",
}


def facts_sentence(kind, domain, lk):
    """The leak in the owner's terms from verified checks only; [] when there
    is none to name."""
    no_booking, no_form = "booking" in lk, "form" in lk
    parts = []
    if kind == "dental":
        if no_booking and no_form:
            parts.append(f"On {domain} a patient cannot book an appointment or even leave a request, so the only way in is a phone call during office hours, and the ones reading at night book with the practice that let them choose a slot")
        elif no_booking:
            parts.append(f"On {domain} a patient can leave a request but cannot pick an appointment time, so the ones reading at night wait for a call back the next morning and the ones who wanted it done book with the practice that let them choose a slot")
        elif no_form:
            parts.append(f"On {domain} a patient can pick a slot but has nowhere to ask a question first, so anyone who wants to check on insurance or a nervous child before committing has to call during office hours, and the ones who will not call never book")
        else:
            return []
    elif kind == "law":
        if no_booking and no_form:
            parts.append(f"On {domain} a potential client cannot book a consultation or leave a message, so nothing reaches you until they call, and the ones reading at 10pm book with the firm that let them choose a time")
        elif no_booking:
            parts.append(f"On {domain} a potential client can leave a message but cannot book a consultation, so they wait for a call back the next day, and the ones reading at 10pm book with the firm that let them choose a time")
        elif no_form:
            parts.append(f"On {domain} a potential client can book a consult but has nowhere to describe the matter first, so anyone who will not put a case into a booking without asking a question first never reaches the calendar")
        else:
            return []
    else:  # home services
        if no_booking and no_form:
            parts.append(f"On {domain} a homeowner cannot request service, so one who gets voicemail after hours has nothing to fill in and the next company's request button gets the job")
        elif no_booking:
            parts.append(f"On {domain} a homeowner can leave a message but cannot book a job, so every after hours request waits for a call back and the urgent ones go to the company that could schedule them on the spot")
        elif no_form:
            parts.append(f"On {domain} a homeowner can pick a slot but has nowhere to describe the job or send a photo first, so anyone with a question before committing calls during office hours instead, and the after hours ones go to the company that takes the details on the spot")
        else:
            return []
    if "mobile" in lk:
        parts.append("on a phone the site is the desktop page shrunk down")
    if "tel" in lk:
        # Scoped to the pages read: /locations often has tel: links.
        parts.append("the phone number on the homepage is plain text, so on a phone it cannot be tapped to call")
    return parts


def offer(kind, lk=None):
    lk = lk or []
    if "booking" not in lk:
        # A scheduler exists already; offer the intake in front of it.
        if kind == "dental":
            return "I put a short intake in front of the booking you already have, insurance and the reason for the visit and a place to ask a question, so a patient can go from question to a booked slot without calling, with automatic reminders and a review request after each visit, in a week, one go, and give the site a clean new design while I am at it."
        if kind == "law":
            return "I put a confidential intake in front of the consult booking you already have, the matter and a conflict check and a place to ask a question, so a client can go from question to a booked consult without calling, in a week, one go, and give the site a design that looks like the firm you are while I am at it."
        return "I put a short intake in front of the booking you already have that collects the address, the problem and a photo, so an after hours request arrives with the details instead of a call back, and texts your on call tech, in a week, one go, and give the site a clean new design while I am at it."
    if kind == "dental":
        return "I set up online booking that lands on your schedule, with automatic reminders and a review request after each visit, in a week, one go, and give the site a clean new design while I am at it."
    if kind == "law":
        return "I set up consult scheduling with confidential intake and conflict check questions in front of it, in a week, one go, and give the site a design that looks like the firm you are while I am at it."
    return "I set up an after hours intake that collects the address and the problem, books the request and texts your on call tech, in a week, one go, and give the site a clean new design while I am at it."


def subject_for(kind, domain, lk):
    """A subject line is a claim too."""
    if "booking" not in lk:
        return SUBJECT_ASK[kind].format(domain=domain)
    if kind == "dental":
        return f"the appointments {domain} is not booking at night"
    if kind == "law":
        return f"the consults {domain} is not getting at 10pm"
    if "form" in lk:
        return f"{domain} after the phone goes to voicemail"
    return f"the jobs {domain} cannot book after hours"


def greeting(email, name):
    """First name only when the mailbox looks like a person and the word is
    not part of the business name (spike@goldenspikeroofing.com is a brand)."""
    local = email.split("@")[0].lower()
    domain = email.split("@")[-1].lower()
    first = re.split(r"[._-]", local)[0]
    brand = (domain.split(".")[0] + " " + name.lower())
    if first in FIRST_NAMES and first not in brand:
        return f"Hello {first.capitalize()},"
    return f"Hello, for the owner of {name}:"


def pick_email(emails, domain):
    own = [e for e in emails if e.endswith("@" + domain) and e.split("@")[0] not in SKIP_LOCAL]
    if not own:
        return None
    named = [e for e in own if e.split("@")[0] not in GENERIC_LOCAL and not re.search(r"\d", e.split("@")[0])]
    return (named or own)[0]


def owner_email(osm_email, domain):
    """The business's own mailbox when OSM lists it off its domain, or None.

    A listing often carries the address the owner actually reads, on gmail or
    on a second domain of theirs, and pick_email discards every one of those
    because it only looks at the site's own domain. Dropping them loses real
    leads, but accepting them blind would write to whatever personal address
    a listing happens to hold, so the address has to name the business.
    """
    osm_email = (osm_email or "").strip().lower()
    if "@" not in osm_email:
        return None
    local, _, host = osm_email.partition("@")
    if host == domain:
        return osm_email
    slug = re.sub(r"[^a-z0-9]", "", domain.split(".")[0])
    if len(slug) < 6:
        return None
    bare_local = re.sub(r"[^a-z0-9]", "", local)
    bare_host = re.sub(r"[^a-z0-9]", "", host.split(".")[0])
    if bare_local and (slug in bare_local or bare_local in slug):
        return osm_email
    if len(bare_host) >= 6 and (slug in bare_host or bare_host in slug):
        return osm_email
    return None


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


def unusable(a, domain):
    """Why this audit cannot support a draft, or None. The same test runs on
    the stored record as a cheap pre-filter and again on a fresh one when
    --fresh is set, so the copy is written from a measurement taken seconds
    before the row is built rather than from whatever the last sweep left."""
    if not a or not a["checks"].get("reachable") or a["checks"].get("blocked_status"):
        return "no audit or blocked"
    c = a["checks"]
    if c.get("mail_route") is False or (c.get("mail_route") is None and mail_route(domain) is False):
        # A Null MX domain (or one that no longer resolves) bounces every
        # send and burns sender reputation.
        return "no mail route (Null MX)"
    if site_looks_unrelated(c.get("title")):
        return "domain resold, hijacked or parked"
    if redirected_elsewhere(c.get("final_url"), domain):
        return "domain now serves another business"
    return None


def address_live(address):
    """False only when the mail server says the mailbox is not there. A probe
    the host refuses on a Spamhaus listing (probe_blocked) or a timeout says
    nothing about the address, so those pass; 6 of the first 50 sends to
    scraped addresses bounced and every one of them was avoidable this way."""
    try:
        cache = json.loads(EMAIL_CACHE.read_text())
    except Exception:
        cache = {}
    if address in cache:
        return cache[address] != "dead"
    status, _ = rcpt_check(address)
    cache[address] = "dead" if status in ("rejected", "no_mx") else status
    try:
        EMAIL_CACHE.parent.mkdir(parents=True, exist_ok=True)
        EMAIL_CACHE.write_text(json.dumps(cache, sort_keys=True))
    except Exception:
        pass
    return cache[address] != "dead"


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--kinds", default="dental,law,home")
    ap.add_argument("--metros", default="")
    ap.add_argument("--min-score", type=int, default=1)
    ap.add_argument("--fresh", action="store_true")
    args = ap.parse_args(argv)
    kinds = set(args.kinds.split(","))
    metros = set(args.metros.split(",")) if args.metros else None
    latest = {}
    for r in load_jsonl(AUDITS):
        if r["domain"] not in latest or r["date"] >= latest[r["domain"]]["date"]:
            latest[r["domain"]] = r
    seen = already_contacted()
    seen_domains = {e.split("@")[-1] for e in seen if "@" in e}
    rows, skipped, fresh = [], {}, []
    for p in load_jsonl(PROSPECTS):
        if p["kind"] not in kinds or (metros and p["metro"] not in metros):
            continue
        a = latest.get(p["domain"])
        reason = unusable(a, p["domain"])
        if reason:
            skipped[reason] = skipped.get(reason, 0) + 1
            continue
        if args.fresh:
            a = audit(p["domain"])
            a["date"] = datetime.date.today().isoformat()
            reason = unusable(a, p["domain"])
            if reason:
                skipped[reason] = skipped.get(reason, 0) + 1
                continue
            fresh.append(a)
        c = a["checks"]
        lk = leaks(c)
        if p["kind"] == "agency":
            email = pick_email(c.get("emails") or [], p["domain"])
            if not email:
                skipped["no own domain email"] = skipped.get("no own domain email", 0) + 1
                continue
            if email in seen or p["domain"] in seen_domains:
                skipped["already contacted"] = skipped.get("already contacted", 0) + 1
                continue
            if not address_live(email):
                skipped["mailbox does not exist"] = skipped.get("mailbox does not exist", 0) + 1
                continue
            rows.append({
                "slug": p["domain"].split(".")[0], "lane": "agency", "kind": "agency", "metro": p["metro"], "domain": p["domain"],
                "company": p["name"], "to": email, "subject": "25 local business sites with the leak named, free",
                "body": greeting(email, p["name"]) + "\n\n" + AGENCY_BODY + SIGNATURE,
                "attachments": [str(SAMPLE_CSV)], "amount": AMOUNTS["agency"], "evidence": {"pages_checked": c.get("pages_checked"), "audit_date": a["date"]},
            })
            seen.add(email)
            if len(rows) >= args.limit:
                break
            continue
        if a["score"] < args.min_score or not (set(lk) & {"booking", "form"}):
            skipped["no leak"] = skipped.get("no leak", 0) + 1
            continue
        email = pick_email(c.get("emails") or [], p["domain"]) or owner_email(p.get("osm_email"), p["domain"])
        if not email:
            skipped["no own domain email"] = skipped.get("no own domain email", 0) + 1
            continue
        if email in seen or p["domain"] in seen_domains:
            skipped["already contacted"] = skipped.get("already contacted", 0) + 1
            continue
        if not address_live(email):
            skipped["mailbox does not exist"] = skipped.get("mailbox does not exist", 0) + 1
            continue
        facts = facts_sentence(p["kind"], p["domain"], lk)
        if not facts:
            # Only the mobile and tel leaks are left, so there is no honest opening.
            skipped["no bookable gap"] = skipped.get("no bookable gap", 0) + 1
            continue
        body = (greeting(email, p["name"]) + "\n\n" + facts[0] + ("; " + "; ".join(facts[1:]) if len(facts) > 1 else "") + ".\n\n"
                + offer(p["kind"], lk) + " I build a working preview of your own site first, before any decision.\n\n"
                + 'Worth a look? Reply "yes" and the preview is yours within a week.' + SIGNATURE)
        rows.append({
            # The lane names the channel, and this row is a local business, so
            # stamping it agency made every per channel rate impossible to read.
            "slug": p["domain"].split(".")[0], "lane": "local", "kind": p["kind"], "metro": p["metro"], "domain": p["domain"],
            "company": p["name"], "to": email, "subject": subject_for(p["kind"], p["domain"], lk), "body": body,
            "attachments": [], "amount": AMOUNTS.get(p["kind"], 3000), "evidence": {"leaks": lk, "pages_checked": c.get("pages_checked"), "audit_date": a["date"]},
        })
        seen.add(email)
        if len(rows) >= args.limit:
            break
    if fresh:
        with AUDITS.open("a", encoding="utf-8") as fh:
            for r in fresh:
                fh.write(json.dumps(r, sort_keys=True) + "\n")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("a", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"{len(rows)} drafted to {out}; skipped {skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
