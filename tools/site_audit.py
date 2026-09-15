"""Score a small business website for the agency outreach.

    python3 tools/site_audit.py example.com [another.com ...]
    python3 tools/site_audit.py --json example.com

Checks that can be made from the outside in a few seconds, each of which
becomes one concrete sentence in an email: HTTPS, response time, page
weight, mobile viewport, online booking, click to call, a lead form, a
visible copyright year, and whether the site is a known builder template.
Stdlib only.
"""

import json
import re
import ssl
import sys
import time
import urllib.error
import urllib.request
from datetime import date

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"
BOOKING = ("calendly", "acuity", "book now", "book online", "schedule online", "book an appointment",
           "schedule an appointment", "request appointment", "zocdoc", "housecall", "jobber", "servicetitan",
           "mindbody", "vagaro", "square appointments", "setmore", "booksy", "free estimate", "schedule your estimate",
           "schedule an estimate", "request an estimate", "request a quote", "get a quote", "schedule service")
BUILDERS = {
    "wix.com": "Wix", "squarespace": "Squarespace", "godaddy": "GoDaddy Website Builder", "weebly": "Weebly",
    "wp-content": "WordPress", "shopify": "Shopify", "duda": "Duda", "webflow": "Webflow", "jimdo": "Jimdo",
}


def fetch(url, timeout=15):
    ctx = ssl.create_default_context()
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,*/*"})
    start = time.monotonic()
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        first = time.monotonic() - start
        body = resp.read(2_000_000)
        total = time.monotonic() - start
        return resp.geturl(), resp.status, first, total, body


def audit(domain):
    result = {"domain": domain, "checks": {}, "notes": [], "score": 0}
    checks = result["checks"]
    html = b""
    final_url = None
    for scheme in ("https://", "http://"):
        for host in (domain, "www." + domain):
            try:
                final_url, status, first, total, html = fetch(scheme + host)
                checks["reachable"] = True
                checks["https"] = final_url.startswith("https://")
                checks["ttfb_s"] = round(first, 2)
                checks["load_s"] = round(total, 2)
                checks["weight_kb"] = round(len(html) / 1024)
                break
            except urllib.error.HTTPError as err:
                # 403/503 from a bot wall is a live site that refused us, not a
                # dead one; say so instead of scoring it as a rebuild candidate.
                checks["reachable"] = True
                checks["blocked_status"] = err.code
                continue
            except (urllib.error.URLError, ssl.SSLError, TimeoutError, OSError):
                continue
        if html:
            break
    if not html:
        if checks.get("blocked_status"):
            result["notes"].append(f'site answered HTTP {checks["blocked_status"]} to the scanner (bot wall); audit it in a browser')
            result["score"] = 0
            return result
        checks["reachable"] = False
        result["notes"].append("site did not respond over https or http")
        result["score"] = 0
        return result

    text = html.decode("utf-8", errors="ignore")
    lower = text.lower()
    checks["final_url"] = final_url
    checks["viewport"] = 'name="viewport"' in lower
    checks["booking"] = any(k in lower for k in BOOKING)
    checks["click_to_call"] = 'href="tel:' in lower
    checks["form"] = "<form" in lower
    checks["title"] = re.search(r"<title[^>]*>(.*?)</title>", text, re.S | re.I)
    checks["title"] = checks["title"].group(1).strip()[:120] if checks["title"] else ""
    years = [int(y) for y in re.findall(r"(?:©|&copy;|copyright)\s*(?:\d{4}\s*[-–]\s*)?(20\d\d)", lower)]
    checks["copyright_year"] = max(years) if years else None
    checks["builder"] = next((name for key, name in BUILDERS.items() if key in lower), None)

    score = 0
    if not checks["https"]:
        score += 1; result["notes"].append("no HTTPS, browsers flag the site as not secure")
    if checks["load_s"] > 3:
        score += 1; result["notes"].append(f'homepage took {checks["load_s"]}s to load')
    if checks["weight_kb"] > 1500:
        score += 1; result["notes"].append(f'homepage HTML alone is {checks["weight_kb"]} KB')
    if not checks["viewport"]:
        score += 1; result["notes"].append("no mobile viewport, the site is not built for phones")
    if not checks["booking"]:
        score += 1; result["notes"].append("no online booking or scheduling anywhere on the homepage")
    if not checks["click_to_call"]:
        score += 1; result["notes"].append("phone number is not tappable on mobile")
    if not checks["form"]:
        score += 1; result["notes"].append("no contact or lead form on the homepage")
    if checks["copyright_year"] and checks["copyright_year"] < date.today().year - 1:
        score += 1; result["notes"].append(f'footer still says {checks["copyright_year"]}')
    result["score"] = min(score, 5)
    return result


def main(argv):
    as_json = "--json" in argv
    domains = [a for a in argv if not a.startswith("--")]
    results = [audit(d) for d in domains]
    if as_json:
        print(json.dumps(results, indent=1))
        return 0
    for r in results:
        print(f'{r["domain"]}: score {r["score"]}/5')
        for note in r["notes"]:
            print(f"  - {note}")
        c = r["checks"]
        if "load_s" in c:
            print(f'  load {c["load_s"]}s, {c["weight_kb"]} KB, builder: {c.get("builder") or "unknown"}, title: {c.get("title")!r}')
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
