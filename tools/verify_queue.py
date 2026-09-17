"""Re-measure every claim in a queue against the live site before it is sent.

    python3 tools/verify_queue.py <queue.jsonl> [--limit N]

Each row carries the leaks its copy names in evidence.leaks. A leak means
"this is missing", so the row only stands if the check still says missing
on the live page right now. Rows that no longer hold are printed and the
exit code is 1, so a send can be gated on this.

The audit is a snapshot and the copy is a claim about the present tense.
The two drift apart whenever a check improves or a site changes, and the
drift has put a wrong sentence in a real business inbox six times, so the
check runs again here rather than trusting the stored record.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from site_audit import fetch, has_form, has_online_booking, contact_links  # noqa: E402


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


def main(argv):
    queue = Path(argv[0])
    limit = None
    if "--limit" in argv:
        limit = int(argv[argv.index("--limit") + 1])
    rows = [json.loads(line) for line in queue.read_text().splitlines() if line.strip()]
    if limit:
        rows = rows[:limit]
    bad, unverifiable, ok = [], [], 0
    for row in rows:
        claimed = sorted((row.get("evidence") or {}).get("leaks") or [])
        if not claimed:
            continue
        observed = live_leaks(row["domain"])
        if observed is None:
            unverifiable.append(row["domain"])
            continue
        # Anything claimed missing has to still be missing.
        present = [k for k in claimed if k not in observed]
        if present:
            bad.append((row["domain"], claimed, observed, present))
        else:
            ok += 1
    for domain, claimed, observed, present in bad:
        print(f"WRONG {domain}: copy claims {present} missing, the site has it (live missing: {observed})")
    for domain in unverifiable:
        print(f"UNVERIFIABLE {domain}: site could not be read, so the claim cannot stand")
    print(f"\n{len(rows)} rows: {ok} hold, {len(bad)} wrong, {len(unverifiable)} unverifiable")
    return 1 if bad or unverifiable else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
