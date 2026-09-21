"""Re-measure every claim in a queue against the live site before it is sent.

    python3 tools/verify_queue.py <queue.jsonl> [--limit N] [--write]

Each row carries the leaks its copy names in evidence.leaks. A leak means
"this is missing", so the row only stands if the check still says missing
on the live page right now. Rows that no longer hold are printed and the
exit code is 1, so a send can be gated on this.

With --write the verdict is stored on the row (`rejected` with its reason,
or `verified`), and the sender refuses a row marked rejected. The gate is
then a fact about the queue rather than a step someone has to remember.

The audit is a snapshot and the copy is a claim about the present tense.
The two drift apart whenever a check improves or a site changes, and the
drift has put a wrong sentence in a real business inbox six times, so the
check runs again here rather than trusting the stored record.
"""

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from site_audit import fetch, has_form, has_online_booking, contact_links  # noqa: E402

# Written by this file and read back by the filter below, so the two cannot
# drift apart.
UNREADABLE = "site could not be read, so the claim cannot stand"


def live_leaks(domain):
    """Which of booking, form, mobile, tel are missing right now, or None when
    the site could not be read well enough to say."""
    html = None
    final_url = None
    for scheme in ("https://", "http://"):
        for host in (domain, "www." + domain):
            try:
                final_url, _, _, _, html = fetch(scheme + host, timeout=20)
                break
            except Exception:
                continue
        if html:
            break
    if not html:
        return None
    low = html.decode("utf-8", "ignore").lower()
    if "just a moment..." in low or "__cf_chl" in low:
        return None
    found = {"booking": has_online_booking(low, domain), "form": has_form(low),
             "mobile": 'name="viewport"' in low, "tel": 'href="tel:' in low}
    for link in contact_links(low, final_url):
        try:
            _, _, _, _, sub = fetch(link, timeout=10)
        except Exception:
            continue
        sub_low = sub.decode("utf-8", "ignore").lower()
        if has_online_booking(sub_low, domain):
            found["booking"] = True
        if has_form(sub_low):
            found["form"] = True
    return sorted(k for k, present in found.items() if not present)


def write_queue(queue, rows):
    tmp = queue.with_suffix(queue.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    tmp.replace(queue)


def main(argv):
    queue = Path(argv[0])
    write = "--write" in argv
    limit = None
    if "--limit" in argv:
        limit = int(argv[argv.index("--limit") + 1])
    rows = [json.loads(line) for line in queue.read_text().splitlines() if line.strip()]
    if limit:
        rows = rows[:limit]
    # A measured rejection is final, because the copy is what was wrong and
    # re-reading the page only makes the same row flap. An unreadable site is
    # not a measurement at all: it is a timeout or a hung hostname, and both
    # come back on a later attempt. Freezing that as a verdict threw away the
    # prospect, so it is the one rejected reason a later pass may revisit.
    todo = [r for r in rows if (r.get("evidence") or {}).get("leaks")
            and not r.get("message_id")
            and (not r.get("rejected") or r.get("rejected") == UNREADABLE)]
    # Six at a time: one row is four page fetches, and a queue of 200 took
    # twenty minutes single threaded, long enough that the gate got skipped.
    with ThreadPoolExecutor(max_workers=6) as pool:
        observed_by_id = {id(r): o for r, o in zip(todo, pool.map(lambda r: live_leaks(r["domain"]), todo))}
    bad, unverifiable, ok = [], [], 0
    for row in todo:
        claimed = sorted((row.get("evidence") or {}).get("leaks") or [])
        observed = observed_by_id[id(row)]
        if observed is None:
            unverifiable.append(row["domain"])
            row["rejected"] = UNREADABLE
            # A row rejected on a second pass kept the verified it earned on
            # the first, so the file said both things at once. Every reader
            # checks rejected as well, so nothing bad was sent on it, but a
            # row that reads as verified and rejected is a row nobody can
            # trust by eye. One verdict per row.
            row.pop("verified", None)
            continue
        # Anything claimed missing has to still be missing.
        present = [k for k in claimed if k not in observed]
        if present:
            row["rejected"] = f"copy claims {present} missing, the live site has it"
            row.pop("verified", None)
            bad.append((row["domain"], claimed, observed, present))
        else:
            row.pop("rejected", None)
            row["verified"] = True
            ok += 1
    if write:
        write_queue(queue, rows)
    for domain, claimed, observed, present in bad:
        print(f"WRONG {domain}: copy claims {present} missing, the site has it (live missing: {observed})")
    for domain in unverifiable:
        print(f"UNVERIFIABLE {domain}: {UNREADABLE}")
    print(f"\n{len(rows)} rows: {ok} hold, {len(bad)} wrong, {len(unverifiable)} unverifiable")
    if write:
        print(f"verdicts written to {queue}")
    return 1 if bad or unverifiable else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
