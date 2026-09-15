# radar

Bounty and opportunity watcher. Every 30 minutes a GitHub Actions job runs
`python3 -m radar.run`, which polls the sources in `radar/sources.py`
through the `gh` CLI, diffs against `state/seen.json`, writes
`state/latest.json` and a dated digest under `digest/`, and comments on the
pinned "Radar" issue when something needs a decision.

Sources in v1: bounty labelled issues across GitHub (minus a spam owner
list), Tenstorrent bounty labels and new tt-metal issues and kernel commits,
our BasedHardware/omi pull requests and new proposals there, Tarsnap issues
and releases.

Run locally: `python3 -m radar.run --dry-run` (needs an authenticated `gh`).

`index.html` renders `state/latest.json`; the repo is served with GitHub Pages
so the page is live at https://lukiod.github.io/radar/ after every run.

## Ledger and founder inbox

`state/ledger.json` holds every opportunity with a state machine
(found, qualified, produced, submitted, accepted, claimed, paid; rejected,
expired). `radar/ledger.py` adds and moves records; each run advances
records from evidence (a merged PR moves `submitted` to `accepted`, which
creates the claim step) and rewrites `state/inbox.md`, the list of steps
only the founder can take, ranked by expected value per minute. The page
shows it first.
