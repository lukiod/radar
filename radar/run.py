"""Collect events from every source, diff against the last run, write state
and a digest, and print what is new.

    python3 -m radar.run            # full run, writes state/ and digest/
    python3 -m radar.run --dry-run  # print only, write nothing
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import ledger, sources

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "state" / "latest.json"
SEEN = ROOT / "state" / "seen.json"
DIGESTS = ROOT / "digest"

# Kinds that deserve a loud line at the top of the digest.
LOUD = {"tt_bounty", "tt_kernel_commit", "bounty_issue", "tarsnap_release"}


def load(path, default):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def collect():
    events, errors = [], []
    for source in sources.ALL_SOURCES:
        try:
            events.extend(source())
        except Exception as exc:  # one broken source must not blank the run
            errors.append(f"{source.__name__}: {exc}")
    events.sort(key=lambda e: e["at"], reverse=True)
    return events, errors


def our_pr_changes(previous, current):
    """PR state transitions for our own PRs, so a merge is never missed."""
    before = {e["id"]: e["extra"] for e in previous if e.get("kind") == "our_pr"}
    changes = []
    for event in current:
        if event["kind"] != "our_pr":
            continue
        old = before.get(event["id"])
        if old and (old.get("state") != event["extra"]["state"] or old.get("review") != event["extra"]["review"]):
            changes.append(f'{event["title"]}: {old.get("state")}/{old.get("review")} -> {event["extra"]["state"]}/{event["extra"]["review"]}')
    return changes


def render(now, new_events, pr_changes, errors, totals):
    lines = [f"# Radar digest {now[:16].replace('T', ' ')} UTC", ""]
    loud = [e for e in new_events if e["kind"] in LOUD]
    quiet = [e for e in new_events if e["kind"] not in LOUD]
    if pr_changes:
        lines += ["## Our PRs changed", ""] + [f"- {c}" for c in pr_changes] + [""]
    if loud:
        lines += ["## Act on these", ""]
        for e in loud:
            extra = e["extra"]
            note = ""
            if e["kind"] == "tt_bounty":
                note = f' ({extra.get("state")}, assigned: {", ".join(extra.get("assignees") or ["nobody"])})'
            elif e["kind"] == "tt_kernel_commit":
                note = f' (files: {len(extra.get("files", []))}, added: {len(extra.get("added", []))})'
            elif e["kind"] == "bounty_issue":
                amount = extra.get("amount_usd")
                note = f' (${amount}, comments: {extra.get("comments")})' if amount else f' (comments: {extra.get("comments")})'
            lines.append(f'- [{e["kind"]}] {e["title"]}{note}\n  {e["url"]}')
        lines.append("")
    if quiet:
        lines += ["## Context", ""] + [f'- [{e["kind"]}] {e["title"]}\n  {e["url"]}' for e in quiet] + [""]
    if not new_events and not pr_changes:
        lines += ["Nothing new since the last run.", ""]
    lines += ["## Totals this run", ""] + [f"- {k}: {v}" for k, v in sorted(totals.items())] + [""]
    if errors:
        lines += ["## Source errors", ""] + [f"- {e}" for e in errors] + [""]
    return "\n".join(lines)


def main(argv):
    dry = "--dry-run" in argv
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    previous = load(STATE, {}).get("events", [])
    seen = set(load(SEEN, []))
    events, errors = collect()
    new_events = [e for e in events if e["id"] not in seen]
    pr_changes = our_pr_changes(previous, events)
    totals = {}
    for e in events:
        totals[e["kind"]] = totals.get(e["kind"], 0) + 1
    digest = render(now, new_events, pr_changes, errors, totals)
    print(digest)
    if dry:
        return 0
    STATE.parent.mkdir(exist_ok=True)
    DIGESTS.mkdir(exist_ok=True)
    STATE.write_text(json.dumps({"generated_at": now, "events": events, "errors": errors}, indent=1))
    book = ledger.load()
    advanced = ledger.sync_from_events(book, events)
    ledger.save(book)
    (ROOT / "state" / "inbox.md").write_text(ledger.render_inbox(book))
    if advanced:
        digest += "\n## Ledger advanced\n\n" + "\n".join(f"- {rid}" for rid in advanced) + "\n"
        print(digest.split("## Ledger advanced")[1])
    SEEN.write_text(json.dumps(sorted(seen | {e["id"] for e in events})[-5000:]))
    if new_events or pr_changes:
        path = DIGESTS / f"{now[:10]}.md"
        existing = path.read_text() if path.exists() else ""
        path.write_text(existing + ("\n" if existing else "") + digest)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
