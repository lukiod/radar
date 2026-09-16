"""Audit many domains and keep the results as one JSON line each.

Usage: python3 tools/audit_batch.py <domain> [<domain> ...]
       python3 tools/audit_batch.py --file domains.txt

Appends to state/audits.jsonl (one record per domain per day, re running a
domain the same day replaces its line). The file is the raw material for
lead lists and for the "what changed on their site" follow up.
"""

import concurrent.futures
import datetime
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from site_audit import audit  # noqa: E402

STATE = Path(__file__).resolve().parents[1] / "state" / "audits.jsonl"


def load():
    if not STATE.exists():
        return []
    return [json.loads(line) for line in STATE.read_text().splitlines() if line.strip()]


def save(rows):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))


def run(domains, workers=12):
    today = datetime.date.today().isoformat()
    rows = [r for r in load() if not (r["date"] == today and r["domain"] in domains)]
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        for result in pool.map(audit, domains):
            result["date"] = today
            rows.append(result)
    rows.sort(key=lambda r: (r["domain"], r["date"]))
    save(rows)
    return rows


def main(argv):
    if argv and argv[0] == "--file":
        domains = [d.strip() for d in Path(argv[1]).read_text().splitlines() if d.strip() and not d.startswith("#")]
    else:
        domains = argv
    if not domains:
        print(__doc__)
        return 1
    rows = run(sorted(set(domains)))
    today = datetime.date.today().isoformat()
    for r in rows:
        if r["date"] == today and r["domain"] in domains:
            print(f'{r["domain"]}: {r["score"]}/5  ' + "; ".join(r["notes"]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
