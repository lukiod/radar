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
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

# --- mail route --------------------------------------------------------------
# A domain can be perfectly reachable and still refuse all mail: it publishes a
# Null MX (".") or has no address record at all. Two of the first 30 prospects
# bounced that way, so the audit records the route and the queue builder drops
# rows without one. Resolved over DNS over HTTPS because the host has no local
# resolver and no dnspython.

MX_CACHE = Path(__file__).resolve().parents[1] / "state" / "mx-cache.json"


def _doh(name, rtype):
    url = "https://dns.google/resolve?name=" + urllib.parse.quote(name) + "&type=" + rtype
    req = urllib.request.Request(url, headers={"accept": "application/dns-json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.load(resp)


def mail_route(domain):
    """None when unknown, True when the domain can receive mail, False when it
    provably cannot (Null MX, or no MX and no address record)."""
    try:
        cache = json.loads(MX_CACHE.read_text())
    except Exception:
        cache = {}
    if domain in cache:
        return cache[domain]
    try:
        ans = _doh(domain, "MX").get("Answer", [])
        records = [a["data"].split()[-1].rstrip(".") for a in ans if a.get("type") == 15]
        if records:
            ok = any(r not in ("", ".") for r in records)
        else:
            status = _doh(domain, "A").get("Status")
            ok = None if status != 0 and status != 3 else (status == 0)
    except Exception:
        return None
    cache[domain] = ok
    try:
        MX_CACHE.parent.mkdir(parents=True, exist_ok=True)
        # Merge with what is on disk; the sender writes this file too.
        try:
            on_disk = json.loads(MX_CACHE.read_text())
        except Exception:
            on_disk = {}
        on_disk.update(cache)
        MX_CACHE.write_text(json.dumps(on_disk, sort_keys=True))
    except Exception:
        pass
    return ok


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


BOOKING_TOOLS = ("calendly", "acuity", "as.me/", "zocdoc", "housecall", "jobber", "servicetitan", "mindbody", "vagaro",
                 "square appointments", "setmore", "booksy", "webscheduler", "schedulicity", "simplybook", "getjobber",
                 "nexhealth", "localmed", "dentrix", "flexbook", "opendental", "weave", "solutionreach", "clio", "lawmatics")
BOOKING_WORDS = {"book", "schedule", "appointment", "booking", "scheduling"}
ANCHOR_RE = re.compile(r"<(?:a|button)\b([^>]*)>(.*?)</(?:a|button)>", re.S)
HREF_RE = re.compile(r'href\s*=\s*["\']([^"\']+)', re.I)
EMAIL_RE = re.compile(r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}", re.I)
CONTACT_WORDS = ("contact", "consult", "schedule", "book", "appoint", "request")
JUNK_EMAIL = ("example.com", "sentry", "wixpress", "domain.com", "email.com", ".png", ".jpg", ".gif", ".svg", "godaddy", "wordpress", "noreply", "no-reply")


# Widget paths, not hostnames: leadconnector also serves review widgets.
FORM_WIDGET_PATHS = ("leadconnectorhq.com/widget/form", "leadconnectorhq.com/widget/survey",
                     "leadconnectorhq.com/widget/booking", "msgsndr.com/widget/form",
                     "msgsndr.com/widget/survey", "msgsndr.com/widget/booking")
# Squarespace's own classes; a generic "form-wrapper" appears without a form.
JS_FORMS = JS_FORMS + ("sqs-block-form", "sqs-form-block")


# A parked domain renders its title in the browser, so the title check in
# draft_batch never sees it; dental911.com is GoDaddy parking with an empty
# title and scored 3/5 on leaks that belong to a parked page.
PARKED_SIGNALS = ("parking-lander", 'ap:"parking"', "window.lander_system", "this domain is parked",
                  "this domain is for sale", "domain parking", "sedoparking", "hugedomains", "afternic")


def is_parked(html):
    """Lowercases its own input: "buy this domain" was dropped as a signal
    because a shop can say it about a gift card."""
    lower = html.lower()
    return any(s in lower for s in PARKED_SIGNALS)


def has_form(lower):
    """A <form> tag, a known JS form builder, or an embedded form widget."""
    return ("<form" in lower or any(k in lower for k in JS_FORMS)
            or any(k in lower for k in FORM_WIDGET_PATHS))


SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.S)


def strip_script_style(lower):
    """A stray '<' inside inline JS (a comparator like x<a, a template string)
    is read by ANCHOR_RE as an unclosed tag and swallows everything up to the
    next '</a>' or '</button>' on the page, tens of KB of script counted as one
    anchor's text. Anchor based checks run on this instead of the raw HTML."""
    return SCRIPT_STYLE_RE.sub(" ", lower)


IFRAME_SRC_RE = re.compile(r"<iframe\b[^>]*\bsrc\s*=\s*[\"\']([^\"\']+)", re.I)
JS_REDIRECT_RE = re.compile(r"""location(?:\.href)?\s*=\s*["']([^"']+)["']""")
META_REFRESH_RE = re.compile(r"""<meta[^>]+http-equiv\s*=\s*["']?refresh["']?[^>]*content\s*=\s*["'][^"']*url\s*=\s*([^"';]+)""", re.I)


def redirect_target(lower, base):
    """The URL a client side redirect stub points at, or None. dental911.com
    answers with 114 bytes of window.location and scored 4/5 on leaks, which
    is a report about a stub, not about the practice. A real page of this size
    with a redirect in it does not exist; the anchor check is the guard."""
    if len(lower) > 2000 or "<a " in lower or "<form" in lower:
        return None
    m = META_REFRESH_RE.search(lower) or JS_REDIRECT_RE.search(lower)
    return urllib.parse.urljoin(base, m.group(1).strip()) if m else None
SOCIAL_HOSTS = ("facebook.com", "instagram.com", "twitter.com", "x.com", "linkedin.com", "yelp.com",
                "google.com", "goo.gl", "youtube.com", "tiktok.com", "pinterest.com", "apple.com")


def has_online_booking(lower, domain=None):
    """Online self scheduling means a known scheduler is embedded, an iframe
    loads one, or a link or button whose text is about booking leads
    somewhere real. Plain prose like "call us to schedule an appointment"
    does not count."""
    if any(tool in lower for tool in BOOKING_TOOLS):
        return True
    clean = strip_script_style(lower)
    own = (domain or "").replace("www.", "")
    for attrs, inner in ANCHOR_RE.findall(clean):
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
        host = re.sub(r"^https?://", "", target).split("/", 1)[0].lower()
        if any(s in host for s in SOCIAL_HOSTS):
            continue  # "facebook.com" contains "book"; a social link is never a scheduler
        if any(k in target for k in ("book", "schedul", "appoint", "reserv", "widget")):
            return True
        # A booking-worded link that jumps to a completely different domain
        # (not the small business's own site) is a scheduling vendor even
        # when its URL is opaque, e.g. a short link like dental4.me/practice/1
        # that carries no keyword in the path.
        if own and host and own not in host and host not in own and "." in host:
            return True
    # An iframe embedding a third party scheduler leaves no matching anchor at all.
    for src in IFRAME_SRC_RE.findall(clean):
        if any(tool in src for tool in BOOKING_TOOLS):
            return True
    return False


def contact_links(lower, base):
    """Internal links whose href or text says contact, book, schedule, consult;
    the pages a visitor would open to reach the business. At most four."""
    seen, out = set(), []
    parsed = urllib.parse.urlsplit(base)
    for attrs, inner in ANCHOR_RE.findall(lower):
        m = HREF_RE.search(attrs)
        if not m:
            continue
        href = m.group(1).strip()
        text = re.sub(r"<[^>]+>", " ", inner)
        if not any(w in href or w in text for w in CONTACT_WORDS):
            continue
        if href.startswith(("tel:", "mailto:", "#", "javascript:")):
            continue
        full = urllib.parse.urljoin(base, href)
        host = urllib.parse.urlsplit(full).netloc
        if host.replace("www.", "") != parsed.netloc.replace("www.", ""):
            continue
        full = full.split("#", 1)[0]
        if full in seen or full.rstrip("/") == base.rstrip("/"):
            continue
        seen.add(full)
        out.append(full)
        if len(out) == 4:
            break
    return out


def find_emails(lower, domain):
    found = set()
    for m in EMAIL_RE.findall(lower):
        m = m.strip(".").lower()
        if any(j in m for j in JUNK_EMAIL):
            continue
        found.add(m)
    own = {e for e in found if e.endswith("@" + domain.replace("www.", "")) or e.endswith("." + domain.replace("www.", ""))}
    return sorted(own) or sorted(found)[:3]


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
    checks["mail_route"] = mail_route(domain)
    if checks["mail_route"] is False:
        result["notes"].append("the domain accepts no mail at all (Null MX); no email can reach it")
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
    for _ in range(2):
        target = redirect_target(text.lower(), final_url)
        if not target:
            break
        try:
            final_url, status, first, total, html = fetch(target)
        except Exception:
            break
        text = html.decode("utf-8", errors="ignore")
        checks["https"] = final_url.startswith("https://")
        checks["load_s"] = round(total, 2)
        checks["weight_kb"] = round(len(html) / 1024)
    lower = text.lower()
    checks["final_url"] = final_url
    if is_parked(lower):
        checks["blocked_status"] = "parked"
        result["notes"].append("the domain is parked or for sale, no business is on it")
        result["score"] = 0
        return result
    if "just a moment..." in lower or "__cf_chl" in lower or "cf-browser-verification" in lower:
        # A Cloudflare challenge page is not the site; scoring it would
        # report "no form, no booking" about a page that is not theirs.
        checks["blocked_status"] = "cloudflare-challenge"
        result["notes"].append("Cloudflare challenge page returned to the scanner; audit it in a browser")
        result["score"] = 0
        return result
    checks["viewport"] = 'name="viewport"' in lower
    checks["booking"] = has_online_booking(lower, domain)
    checks["click_to_call"] = 'href="tel:' in lower
    # JavaScript rendered form builders leave no <form> in the HTML but the
    # page has a working form (GEM Family Law's FindLaw site: Ninja Forms).
    checks["form"] = has_form(lower)
    checks["title"] = re.search(r"<title[^>]*>(.*?)</title>", text, re.S | re.I)
    checks["title"] = checks["title"].group(1).strip()[:120] if checks["title"] else ""
    years = [int(y) for y in re.findall(r"(?:©|&copy;|copyright)\s*(?:\d{4}\s*[-–]\s*)?(20\d\d)", lower)]
    checks["copyright_year"] = max(years) if years else None
    checks["builder"] = next((name for key, name in BUILDERS.items() if key in lower), None)
    emails = set(find_emails(lower, domain))
    # A homepage without booking or a form is not a site without them: the
    # contact page is where most small sites keep both, so it is checked
    # before either leak is claimed.
    pages = []
    for link in contact_links(lower, final_url):
        try:
            _, _, _, _, sub = fetch(link, timeout=10)
        except Exception:
            continue
        sub_lower = sub.decode("utf-8", errors="ignore").lower()
        pages.append(link)
        emails.update(find_emails(sub_lower, domain))
        if has_online_booking(sub_lower, domain):
            checks["booking"] = True
            checks["booking_page"] = link
        if has_form(sub_lower):
            checks["form"] = True
            checks["form_page"] = link
    checks["pages_checked"] = [final_url] + pages
    checks["emails"] = sorted(emails)

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
        score += 1; result["notes"].append("no online booking or scheduling on the homepage or the contact page")
    if not checks["click_to_call"]:
        score += 1; result["notes"].append("phone number is not tappable on mobile")
    if not checks["form"]:
        score += 1; result["notes"].append("no contact or lead form on the homepage or the contact page")
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
