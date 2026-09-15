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
JS_FORMS = ("nf_tp_module", "ninja-forms", "nf-form", "gform_wrapper", "wpforms-form", "wpcf7-form",
            "hbspt.forms", "hs-form", "jotform", "typeform", "formstack", "cognitoforms", "elementor-form",
            "fluentform", "forminator", "formidable", "frm_forms", "data-form_id", "wufoo", "123formbuilder")
BOOKING = ("calendly", "acuity", "zocdoc", "housecall", "jobber", "servicetitan", "mindbody", "vagaro",
           "square appointments", "setmore", "booksy", "book now", "book online", "schedule online",
           "book an appointment", "schedule an appointment", "book a consultation", "schedule a consultation online",
           "book your appointment", "schedule your appointment", "online scheduling", "online booking")
# "free estimate", "schedule service" and "request a quote" were removed: on most
# homepages they caption a phone number, and a form is detected separately.
BUILDERS = {
    "wix.com": "Wix", "squarespace": "Squarespace", "godaddy": "GoDaddy Website Builder", "weebly": "Weebly",
    "wp-content": "WordPress", "shopify": "Shopify", "duda": "Duda", "webflow": "Webflow", "jimdo": "Jimdo",
}


BOOKING_TOOLS = ("calendly", "acuity", "zocdoc", "housecall", "jobber", "servicetitan", "mindbody", "vagaro",
                 "square appointments", "setmore", "booksy", "webscheduler", "schedulicity", "simplybook", "getjobber",
                 "nexhealth", "localmed", "dentrix", "flexbook", "opendental", "weave", "solutionreach", "clio", "lawmatics")
BOOKING_WORDS = {"book", "schedule", "appointment", "booking", "scheduling"}
ANCHOR_RE = re.compile(r"<(?:a|button)\b([^>]*)>(.*?)</(?:a|button)>", re.S)


def has_online_booking(lower):
    """Online self scheduling means a known scheduler is embedded, or a link or
    button whose text is about booking points somewhere other than a phone
    number. Plain prose like "call us to schedule an appointment" does not count."""
    if any(tool in lower for tool in BOOKING_TOOLS):
        return True
    for attrs, inner in ANCHOR_RE.findall(lower):
        text = re.sub(r"<[^>]+>", " ", inner)
        words = set(re.findall(r"[a-z]+", text))
        if not words & BOOKING_WORDS:
            continue
        href = re.search(r'href\s*=\s*["\']([^"\']*)', attrs)
        target = href.group(1) if href else ""
        if target.startswith("tel:") or target.startswith("mailto:"):
            continue
        if "call" in words and "online" not in words:
            continue
        # The link has to lead to a scheduler, not to a generic contact page.
        if not any(k in target for k in ("book", "schedul", "appoint", "reserv", "widget")):
            continue
        return True
    return False


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
    if "just a moment..." in lower or "__cf_chl" in lower or "cf-browser-verification" in lower:
        # A Cloudflare challenge page is not the site; scoring it would
        # report "no form, no booking" about a page that is not theirs.
        checks["blocked_status"] = "cloudflare-challenge"
        result["notes"].append("Cloudflare challenge page returned to the scanner; audit it in a browser")
        result["score"] = 0
        return result
    checks["viewport"] = 'name="viewport"' in lower
    checks["booking"] = has_online_booking(lower)
    checks["click_to_call"] = 'href="tel:' in lower
    # JavaScript rendered form builders leave no <form> in the HTML but the
    # page has a working form (GEM Family Law's FindLaw site: Ninja Forms).
    checks["form"] = "<form" in lower or any(k in lower for k in JS_FORMS)
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
