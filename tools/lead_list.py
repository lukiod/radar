"""Turn state/audits.jsonl into a lead list CSV, one row per domain, latest audit.

Usage: python3 tools/lead_list.py [--min-score N] [--out state/lead-sample.csv]

Columns are the facts an outreach email can quote: the leaks found on the
homepage (no booking, no form, not built for phones, phone not tappable),
load time, page weight, site builder, footer year. Sites behind a bot wall
or unreachable are left out; nothing here is a guess.
"""

import csv
import json
import sys
from pathlib import Path

STATE = Path(__file__).resolve().parents[1] / "state" / "audits.jsonl"

LEAK_COLUMNS = [
    ("no_booking", "booking", "no online booking or scheduling on the homepage or the contact page"),
    ("no_form", "form", "no contact or lead form on the homepage or the contact page"),
    ("not_mobile", "viewport", "no mobile viewport"),
    ("phone_not_tappable", "click_to_call", "no tappable phone number on the homepage"),
]


def latest_audits():
    rows = {}
    for line in STATE.read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if r["domain"] not in rows or r["date"] >= rows[r["domain"]]["date"]:
            rows[r["domain"]] = r
    return sorted(rows.values(), key=lambda r: (-r["score"], r["domain"]))


def to_row(r):
    c = r["checks"]
    leaks = [text for col, key, text in LEAK_COLUMNS if key in c and not c[key]]
    return {
        "domain": r["domain"],
        "audited": r["date"],
        "score": r["score"],
        "leaks": "; ".join(leaks),
        "load_seconds": c.get("load_s"),
        "page_kb": c.get("weight_kb"),
        "builder": c.get("builder") or "",
        "footer_year": c.get("copyright_year") or "",
        "title": c.get("title") or "",
    }


def main(argv):
    min_score = 1
    out = STATE.parent / "lead-sample.csv"
    it = iter(argv)
    for flag in it:
        if flag == "--min-score":
            min_score = int(next(it))
        elif flag == "--out":
            out = Path(next(it))
    rows = [to_row(r) for r in latest_audits() if r["checks"].get("load_s") is not None and r["score"] >= min_score]
    with out.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else ["domain"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"{len(rows)} rows -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
