"""The opportunity ledger: one record per thing that could turn into money,
with a state machine the watcher advances from evidence.

States, in order:
    found -> qualified -> produced -> submitted -> accepted -> claimed -> paid
Side exits: rejected, expired.

Each record carries the three numbers the founder decides on: amount (USD),
probability (0 to 1), and founder_minutes for the next step. The inbox is
the list of records whose next step is the founder's.

    python3 -m radar.ledger add --lane omi --title "..." --url ... --amount 50 --prob 0.5
    python3 -m radar.ledger set <id> submitted
    python3 -m radar.ledger inbox
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / "state" / "ledger.json"

STATES = ["found", "qualified", "produced", "submitted", "accepted", "claimed", "paid", "rejected", "expired"]

# Who acts next, per state. "founder" states produce inbox items.
NEXT_ACTOR = {
    "found": "agent", "qualified": "agent", "produced": "founder_or_agent",
    "submitted": "wait", "accepted": "founder", "claimed": "wait",
    "paid": "done", "rejected": "done", "expired": "done",
}

# What the founder does at each founder state, per lane.
FOUNDER_STEP = {
    ("omi", "accepted"): "Claim email is drafted in prodev with the PayPal line blank; add your PayPal address and send.",
    ("agency", "produced"): "Send the draft from your mailbox; add physical address and the stop line.",
    ("agency", "accepted"): "Reply to the prospect and book the call; the preview site is ready.",
    ("review_first_repo", "produced"): "Read the staged patch, submit the PR under your name with the AI disclosure.",
    ("review_first_repo", "accepted"): "Reply to the reviewer yourself (repo policy); drafts on request.",
    ("grant", "produced"): "Submit the application from the draft.",
    ("gig", "produced"): "Post the proposal from your freelance account.",
    ("gig", "accepted"): "Accept the contract; the deliverable is ready.",
    ("tenstorrent", "accepted"): "Accept the bounty terms on the issue; approve card time if validation needs it.",
    ("product", "produced"): "Approve the listing and connect PayPal on the storefront.",
    ("hackathon", "produced"): "Register on lablab.ai with the prodev address, get an AssemblyAI key, record the 3 minute demo from the README script, submit.",
}


def now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load():
    try:
        return json.loads(LEDGER.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {"records": []}


def save(data):
    LEDGER.parent.mkdir(exist_ok=True)
    LEDGER.write_text(json.dumps(data, indent=1, sort_keys=True))


def add(data, lane, title, url, amount=0.0, prob=0.0, minutes=0, state="found", note=""):
    rid = f"{lane}-{len(data['records']) + 1:04d}"
    rec = {
        "id": rid, "lane": lane, "title": title, "url": url,
        "amount_usd": float(amount), "probability": float(prob), "founder_minutes": int(minutes),
        "state": state, "note": note,
        "history": [{"at": now(), "state": state}],
    }
    data["records"].append(rec)
    return rec


def set_state(data, rid, state, note=None):
    assert state in STATES, state
    for rec in data["records"]:
        if rec["id"] == rid:
            rec["state"] = state
            rec["history"].append({"at": now(), "state": state})
            if note is not None:
                rec["note"] = note
            return rec
    raise KeyError(rid)


def find_by_url(data, url):
    for rec in data["records"]:
        if rec["url"] == url:
            return rec
    return None


def expected_value(rec):
    return rec["amount_usd"] * rec["probability"]


def inbox(data):
    """Founder items, best three numbers first."""
    items = []
    for rec in data["records"]:
        actor = NEXT_ACTOR.get(rec["state"], "agent")
        if actor not in ("founder", "founder_or_agent"):
            continue
        step = FOUNDER_STEP.get((rec["lane"], rec["state"]))
        if not step:
            continue
        items.append((rec, step))
    items.sort(key=lambda pair: -(expected_value(pair[0]) / max(pair[0]["founder_minutes"], 1)))
    return items


def render_inbox(data):
    items = inbox(data)
    lines = [f"# Founder inbox {now()[:10]}", ""]
    if not items:
        lines.append("Nothing needs you today.")
    else:
        lines.append("Each line: expected value ÷ your minutes, highest first.")
        lines.append("")
        for rec, step in items:
            ev = expected_value(rec)
            lines.append(f'- **{rec["title"]}** ({rec["lane"]}, {rec["state"]})')
            lines.append(f'  ${rec["amount_usd"]:,.0f} × {rec["probability"]:.0%} = ${ev:,.0f} expected, {rec["founder_minutes"]} min. {step}')
            lines.append(f'  {rec["url"]}')
    totals = {}
    for rec in data["records"]:
        totals[rec["state"]] = totals.get(rec["state"], 0) + 1
    paid = sum(r["amount_usd"] for r in data["records"] if r["state"] == "paid")
    pipeline = sum(expected_value(r) for r in data["records"] if r["state"] in ("submitted", "accepted", "claimed"))
    lines += ["", f"Paid to date: ${paid:,.0f}. Expected value in flight: ${pipeline:,.0f}.",
              "States: " + ", ".join(f"{k} {v}" for k, v in sorted(totals.items()))]
    return "\n".join(lines) + "\n"


def sync_from_events(data, events):
    """Advance omi and review-first records from watcher evidence."""
    changed = []
    for e in events:
        if e.get("kind") != "our_pr":
            continue
        rec = find_by_url(data, e["url"])
        if not rec:
            continue
        merged = e["extra"].get("merged_at")
        if merged and rec["state"] == "submitted":
            set_state(data, rec["id"], "accepted", f"merged {merged[:10]}")
            changed.append(rec["id"])
        elif e["extra"].get("state") == "CLOSED" and not merged and rec["state"] == "submitted":
            set_state(data, rec["id"], "rejected", "closed without merge")
            changed.append(rec["id"])
    return changed


def main(argv):
    data = load()
    if not argv or argv[0] == "inbox":
        print(render_inbox(data))
        return 0
    if argv[0] == "add":
        kw = {}
        it = iter(argv[1:])
        for flag in it:
            kw[flag.lstrip("-")] = next(it)
        rec = add(data, kw["lane"], kw["title"], kw["url"], kw.get("amount", 0), kw.get("prob", 0), kw.get("minutes", 0), kw.get("state", "found"), kw.get("note", ""))
        save(data)
        print(rec["id"])
        return 0
    if argv[0] == "set":
        rec = set_state(data, argv[1], argv[2], argv[3] if len(argv) > 3 else None)
        save(data)
        print(rec["id"], rec["state"])
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
