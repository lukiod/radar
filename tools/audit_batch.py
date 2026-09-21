"""Audit many domains and keep the results as one JSON line each.

Usage: python3 tools/audit_batch.py <domain> [<domain> ...]
       python3 tools/audit_batch.py --file domains.txt

Appends to the audit file under RADAR_STATE (one record per domain per day,
re running a
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

from state_paths import AUDITS as STATE  # noqa: E402


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
    done = 0
    failed = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(audit, d): d for d in domains}
        for future in concurrent.futures.as_completed(futures):
            try:
                result = future.result()
            except Exception as err:
                # A domain that is not a hostname raises inside audit, and one
                # such row used to end the whole batch with it.
                failed.append(f"{futures[future]}: {err}")
                continue
            result["date"] = today
            rows.append(result)
            done += 1
            if done % 25 == 0 or done == len(domains):
                # Partial saves so a long batch can be read while it runs
                # and nothing is lost if it is killed.
                rows.sort(key=lambda r: (r["domain"], r["date"]))
                save(rows)
                print(f"{done}/{len(domains)} audited", file=sys.stderr, flush=True)
    rows.sort(key=lambda r: (r["domain"], r["date"]))
    save(rows)
    for line in failed:
        print(f"skipped {line}", file=sys.stderr, flush=True)
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
