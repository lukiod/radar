"""Signal sources. Each function returns a list of event dicts with the
keys: id, source, kind, title, url, at (ISO timestamp), and extra.

All GitHub access goes through the gh CLI so the runner needs no token
handling of its own; GITHUB_TOKEN from Actions is enough.
"""

import json
import subprocess
from datetime import datetime, timedelta, timezone

# Organisations whose "bounties" are token rewards, self referential boards
# or unverifiable payers. Kept short and explicit so a removal is a review.
SPAM_OWNERS = {
    "scottcjn", "rustchain", "misakanet", "ikalus1988", "auscaster", "relayhop",
    "unsafelabs", "securebananalabs", "kodaksax", "vansh-09", "freedom-winds",
    "2510034127qq-wq", "dev-kp-eloper", "fufufu1116", "nspg13", "ldavis2700",
    "alstonburbach", "xsovad06", "ahavahdev1",
}

TT_REPOS = [
    "tenstorrent/tt-metal", "tenstorrent/tt-mlir", "tenstorrent/tt-forge",
    "tenstorrent/tt-forge-fe", "tenstorrent/tt-xla", "tenstorrent/tt-lang",
    "tenstorrent/pytorch2.0_ttnn", "tenstorrent/tt-llk",
]

# Paths in tt-metal where a new or changed file is an unaudited numerical kernel.
TT_KERNEL_PREFIXES = (
    "tt_metal/hw/ckernels/",
    "tt_metal/tt-llk/",
    "tt_metal/hw/inc/api/compute/",
    "ttnn/cpp/ttnn/operations/eltwise/",
    "ttnn/cpp/ttnn/operations/reduction/",
    "ttnn/cpp/ttnn/operations/normalization/",
)

TARSNAP_REPOS = ["Tarsnap/tarsnap", "Tarsnap/kivaloo", "Tarsnap/spiped", "Tarsnap/scrypt"]


def gh(*args):
    out = subprocess.run(["gh", *args], capture_output=True, text=True, check=False)
    if out.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args[:3])}: {out.stderr.strip()[:300]}")
    return out.stdout


def gh_json(*args):
    text = gh(*args)
    return json.loads(text) if text.strip() else []


def iso(dt):
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def since_iso(hours):
    return iso(datetime.now(timezone.utc) - timedelta(hours=hours))


def new_bounty_issues(hours=24):
    """Issues labelled bounty or titled bounty, created recently, minus spam owners."""
    events = []
    seen = set()
    queries = [
        ["--label", "bounty"],
        ["bounty in:title"],
        ["--label", "💎 Bounty"],
        ["--label", "Paid Bounty 💰"],
    ]
    for query in queries:
        rows = gh_json(
            "search", "issues", *query, "--state", "open", "--created", f">={since_iso(hours)[:10]}",
            "--sort", "created", "--limit", "100",
            "--json", "repository,title,url,createdAt,commentsCount,author,labels",
        )
        for row in rows:
            owner = row["repository"]["nameWithOwner"].split("/")[0].lower()
            if owner in SPAM_OWNERS or row["url"] in seen:
                continue
            seen.add(row["url"])
            events.append({
                "id": row["url"],
                "source": "github_search",
                "kind": "bounty_issue",
                "title": f'{row["repository"]["nameWithOwner"]}: {row["title"]}',
                "url": row["url"],
                "at": row["createdAt"],
                "extra": {
                    "comments": row["commentsCount"],
                    "author": row["author"]["login"],
                    "labels": [label["name"] for label in row.get("labels", [])],
                },
            })
    return events


def tenstorrent_bounty_labels(hours=48):
    """Bounty labelled issues across the Tenstorrent program repos, recently updated."""
    events = []
    for repo in TT_REPOS:
        rows = gh_json(
            "issue", "list", "--repo", repo, "--label", "bounty", "--state", "all", "--limit", "50",
            "--json", "number,title,url,createdAt,updatedAt,state,assignees,author",
        )
        cutoff = since_iso(hours)
        for row in rows:
            if row["updatedAt"] < cutoff:
                continue
            events.append({
                "id": row["url"],
                "source": "tenstorrent",
                "kind": "tt_bounty",
                "title": f'{repo}#{row["number"]} {row["title"]}',
                "url": row["url"],
                "at": row["updatedAt"],
                "extra": {
                    "state": row["state"],
                    "author": row["author"]["login"],
                    "assignees": [a["login"] for a in row["assignees"]],
                    "created": row["createdAt"],
                },
            })
    return events


def tenstorrent_new_issues(hours=24):
    """Every new tt-metal issue, so a hit is checked against claims before work starts."""
    rows = gh_json(
        "issue", "list", "--repo", "tenstorrent/tt-metal", "--state", "open", "--limit", "100",
        "--search", f"created:>={since_iso(hours)[:10]}",
        "--json", "number,title,url,createdAt,author,labels",
    )
    cutoff = since_iso(hours)
    return [
        {
            "id": row["url"],
            "source": "tenstorrent",
            "kind": "tt_issue",
            "title": f'tt-metal#{row["number"]} {row["title"]}',
            "url": row["url"],
            "at": row["createdAt"],
            "extra": {"author": row["author"]["login"], "labels": [l["name"] for l in row["labels"]]},
        }
        for row in rows
        if row["createdAt"] >= cutoff
    ]


def tenstorrent_kernel_commits(hours=24):
    """Commits on tt-metal main touching kernel paths in the window."""
    rows = gh_json(
        "api", f"repos/tenstorrent/tt-metal/commits?sha=main&since={since_iso(hours)}&per_page=100",
    )
    events = []
    for row in rows:
        sha = row["sha"]
        detail = gh_json("api", f"repos/tenstorrent/tt-metal/commits/{sha}")
        files = [f["filename"] for f in detail.get("files", [])]
        hits = [f for f in files if f.startswith(TT_KERNEL_PREFIXES)]
        if not hits:
            continue
        added = [f["filename"] for f in detail.get("files", []) if f.get("status") == "added" and f["filename"] in hits]
        events.append({
            "id": row["html_url"],
            "source": "tenstorrent",
            "kind": "tt_kernel_commit",
            "title": row["commit"]["message"].splitlines()[0][:120],
            "url": row["html_url"],
            "at": row["commit"]["committer"]["date"],
            "extra": {"files": hits[:40], "added": added, "author": (row.get("author") or {}).get("login")},
        })
    return events


def omi_activity(hours=24, our_login="lukiod"):
    """Our omi PRs plus new bounty proposals and merged proposals by others."""
    events = []
    ours = gh_json(
        "pr", "list", "--repo", "BasedHardware/omi", "--author", our_login, "--state", "all", "--limit", "50",
        "--json", "number,title,url,state,mergedAt,updatedAt,reviewDecision",
    )
    for row in ours:
        events.append({
            "id": row["url"],
            "source": "omi",
            "kind": "our_pr",
            "title": f'omi PR #{row["number"]} {row["title"]}',
            "url": row["url"],
            "at": row["updatedAt"],
            "extra": {"state": row["state"], "merged_at": row["mergedAt"], "review": row["reviewDecision"]},
        })
    proposals = gh_json(
        "search", "issues", "--repo", "BasedHardware/omi", "bounty proposal in:title",
        "--created", f">={since_iso(hours)[:10]}", "--limit", "50",
        "--json", "number,title,url,createdAt,state,author",
    )
    for row in proposals:
        events.append({
            "id": row["url"],
            "source": "omi",
            "kind": "omi_proposal",
            "title": f'omi#{row["number"]} {row["title"]}',
            "url": row["url"],
            "at": row["createdAt"],
            "extra": {"state": row["state"], "author": row["author"]["login"]},
        })
    return events


def tarsnap_activity(hours=72):
    events = []
    for repo in TARSNAP_REPOS:
        rows = gh_json(
            "issue", "list", "--repo", repo, "--state", "all", "--limit", "20",
            "--json", "number,title,url,createdAt,updatedAt,state,author",
        )
        cutoff = since_iso(hours)
        for row in rows:
            if row["updatedAt"] < cutoff:
                continue
            events.append({
                "id": row["url"],
                "source": "tarsnap",
                "kind": "tarsnap_issue",
                "title": f'{repo}#{row["number"]} {row["title"]}',
                "url": row["url"],
                "at": row["updatedAt"],
                "extra": {"state": row["state"], "author": row["author"]["login"]},
            })
        releases = gh_json("api", f"repos/{repo}/releases?per_page=3")
        for rel in releases:
            if rel.get("published_at", "") >= since_iso(hours):
                events.append({
                    "id": rel["html_url"],
                    "source": "tarsnap",
                    "kind": "tarsnap_release",
                    "title": f'{repo} release {rel.get("tag_name")}',
                    "url": rel["html_url"],
                    "at": rel["published_at"],
                    "extra": {"prerelease": rel.get("prerelease")},
                })
    return events


ALL_SOURCES = [
    new_bounty_issues,
    tenstorrent_bounty_labels,
    tenstorrent_new_issues,
    tenstorrent_kernel_commits,
    omi_activity,
    tarsnap_activity,
]
