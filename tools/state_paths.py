"""Where the sourcing pipeline keeps the data it gathers and measures.

The prospect pool, the audit results and the mailbox probe caches are real
addresses belonging to real firms, and the scheduled workflow in this
repository commits everything under state/ on every run. A single gitignore
mistake there publishes the lot, so these files live in the private
internal-docs repository instead, beside the queues and the suppression
list.

What stays in state/ is what the workflow itself owns: the event snapshots,
the seen set, the ledger and the inbox. Those are the radar's output and the
workflow is meant to commit them.

    RADAR_STATE   set this to keep the data somewhere else, for a machine
                  that lays the workspace out differently.
"""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

DEFAULT = ROOT.parent / "internal-docs" / "comms" / "outreach-state"

STATE_DIR = Path(os.environ.get("RADAR_STATE") or DEFAULT)

PROSPECTS = STATE_DIR / "prospects.jsonl"
AUDITS = STATE_DIR / "audits.jsonl"
MX_CACHE = STATE_DIR / "mx-cache.json"
EMAIL_CACHE = STATE_DIR / "email-check.json"
INBOX_SEEN = STATE_DIR / "inbox-seen.json"
MESSAGE_IDS = STATE_DIR / "message-ids.json"
SEND_BUDGET = STATE_DIR / "send-budget.json"
