"""Pull local businesses with a website from OpenStreetMap, one metro at a time.

    python3 tools/source_osm.py denver [kansas-city ...] [--kinds dental,law,home] [--out state/prospects.jsonl]

Overpass is free and needs no key, so this is the bulk source for the
agency outreach: name, website, phone, city and (rarely) an email per
business. Chains are dropped (a website whose path points at one branch
of a national brand is not an owner to write to). Results are appended
to the prospects file keyed by domain; a domain already present is left
alone so the file accumulates across metros and days.
"""

import argparse
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

STATE = Path(__file__).resolve().parents[1] / "state" / "prospects.jsonl"
ENDPOINTS = ("https://overpass.kumi.systems/api/interpreter", "https://overpass-api.de/api/interpreter")
UA = "radar-audit/0.1 (site audits for small business outreach)"
OVERPASS_TIMEOUT_SECONDS = 45

# south, west, north, east
METROS = {
    "denver": "39.5,-105.3,40.1,-104.6",
    "kansas-city": "38.8,-94.9,39.4,-94.3",
    "phoenix": "33.2,-112.4,33.8,-111.6",
    "dallas": "32.6,-97.1,33.1,-96.5",
    "houston": "29.5,-95.7,30.1,-95.0",
    "austin": "30.1,-97.95,30.55,-97.5",
    "san-antonio": "29.25,-98.75,29.7,-98.25",
    "atlanta": "33.55,-84.6,34.05,-84.1",
    "charlotte": "35.0,-81.1,35.45,-80.6",
    "nashville": "35.95,-87.0,36.35,-86.5",
    "tampa": "27.75,-82.75,28.15,-82.25",
    "orlando": "28.35,-81.6,28.75,-81.15",
    "salt-lake": "40.45,-112.1,40.85,-111.7",
    "boise": "43.45,-116.5,43.75,-116.1",
    "oklahoma-city": "35.3,-97.75,35.7,-97.3",
    "tulsa": "35.95,-96.1,36.3,-95.75",
    "indianapolis": "39.6,-86.35,39.95,-85.95",
    "columbus": "39.85,-83.2,40.15,-82.8",
    "raleigh": "35.65,-78.9,36.0,-78.5",
    "minneapolis": "44.8,-93.5,45.15,-93.0",
    "portland": "45.35,-122.85,45.65,-122.45",
    "sacramento": "38.4,-121.6,38.75,-121.2",
    "las-vegas": "35.95,-115.4,36.35,-115.0",
    "albuquerque": "34.95,-106.8,35.25,-106.45",
    "omaha": "41.15,-96.25,41.35,-95.9",
    "louisville": "38.1,-85.9,38.35,-85.5",
    "richmond": "37.4,-77.65,37.7,-77.3",
    "milwaukee": "42.9,-88.15,43.2,-87.85",
    "pittsburgh": "40.3,-80.15,40.6,-79.8",
    "cincinnati": "39.0,-84.7,39.3,-84.35",
}

KINDS = {
    "dental": ['nwr["amenity"="dentist"]["website"];'],
    "law": ['nwr["office"="lawyer"]["website"];'],
    "home": ['nwr["craft"~"plumber|hvac|roofer|electrician"]["website"];', 'nwr["shop"="hvac"]["website"];',
             'nwr["office"~"plumber|hvac|roofer|electrician"]["website"];'],
    "medspa": ['nwr["shop"="beauty"]["beauty"~"spa|aesthetic|laser"]["website"];', 'nwr["amenity"="clinic"]["healthcare:speciality"~"dermatology|aesthetic"]["website"];'],
    "agency": ['nwr["office"~"advertising_agency|marketing|web_design"]["website"];'],
    "physio": ['nwr["healthcare"="physiotherapist"]["website"];', 'nwr["amenity"="clinic"]["healthcare:speciality"="physiotherapy"]["website"];'],
}

CHAINS = ("aspendental", "brightnow", "smilebrands", "pacificdentalservices", "heartland", "westerndental", "affordabledentures",
          "monarchdental", "castledental", "gentledental", "midwestdental", "familia", "greatexpressions", "dentalone", "kooldsmiles",
          "mydentist", "comfortdental", "perfectteeth", "smiledirect", "rotorooter", "mrrooter", "benjaminfranklinplumbing",
          "onehourair", "aireserv", "mrelectric", "arsrescue", "servicechampions", "legalzoom", "forthepeople", "morganandmorgan", "lawyers.findlaw", "avvo", "yelp",
          "facebook.com", "google.com", "linkedin.com", "instagram.com", "yellowpages", "healthgrades", "zocdoc", "wixsite", "business.site")


def query(bbox, kinds):
    body = "\n".join(line for k in kinds for line in KINDS[k])
    return f"[out:json][timeout:120][bbox:{bbox}];\n(\n{body}\n);\nout tags center;"


def overpass(q, log=lambda msg: None):
    data = urllib.parse.urlencode({"data": q}).encode()
    last = None
    for host in ENDPOINTS:
        for attempt in range(2):
            try:
                log(f"  querying {host} (attempt {attempt + 1})")
                req = urllib.request.Request(host, data=data, headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=OVERPASS_TIMEOUT_SECONDS) as resp:
                    return json.load(resp)["elements"]
            except Exception as err:  # 504 and rate limits are ordinary here
                last = err
                log(f"  {host} failed: {err}")
                time.sleep(5)
    raise RuntimeError(f"overpass failed for both endpoints: {last}")


def kind_of(tags):
    if tags.get("amenity") == "dentist":
        return "dental"
    if tags.get("office") == "lawyer":
        return "law"
    if tags.get("office") in ("advertising_agency", "marketing", "web_design"):
        return "agency"
    if tags.get("healthcare") == "physiotherapist" or tags.get("healthcare:speciality") == "physiotherapy":
        return "physio"
    if tags.get("shop") == "beauty" or tags.get("healthcare:speciality") in ("dermatology", "aesthetic"):
        return "medspa"
    return "home"


def domain_of(website):
    website = website.strip()
    if not re.match(r"^https?://", website, re.I):
        website = "http://" + website
    parts = urllib.parse.urlsplit(website)
    host = parts.netloc.lower().split(":")[0]
    host = host[4:] if host.startswith("www.") else host
    path = parts.path.strip("/")
    return host, path


def load(path):
    if not path.exists():
        return {}
    rows = {}
    for line in path.read_text().splitlines():
        if line.strip():
            r = json.loads(line)
            rows[r["domain"]] = r
    return rows


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("metros", nargs="+")
    ap.add_argument("--kinds", default="dental,law,home")
    ap.add_argument("--out", default=str(STATE))
    args = ap.parse_args(argv)
    kinds = args.kinds.split(",")
    out = Path(args.out)
    rows = load(out)
    added = 0
    for metro in args.metros:
        if metro not in METROS:
            print(f"unknown metro {metro}; known: {', '.join(sorted(METROS))}")
            continue
        print(f"{metro}: querying overpass...", flush=True)
        try:
            elements = overpass(query(METROS[metro], kinds), log=lambda m: print(m, flush=True))
        except RuntimeError as err:
            print(f"{metro}: SKIPPED, {err}", flush=True)
            continue
        kept = 0
        for el in elements:
            tags = el.get("tags", {})
            site = tags.get("website") or tags.get("contact:website")
            if not site or not tags.get("name"):
                continue
            domain, path = domain_of(site)
            if not domain or "." not in domain or any(c in domain for c in CHAINS):
                continue
            if path.count("/") >= 1 or (path and re.search(r"\d{4,}", path)):
                continue  # a branch page of a chain, not the business's own site
            if domain in rows:
                continue
            rows[domain] = {
                "domain": domain, "name": tags["name"], "kind": kind_of(tags), "metro": metro,
                "city": tags.get("addr:city"), "state": tags.get("addr:state"), "phone": tags.get("phone") or tags.get("contact:phone"),
                "osm_email": tags.get("email") or tags.get("contact:email"), "source": "osm",
            }
            kept += 1
        added += kept
        print(f"{metro}: {len(elements)} elements, {kept} new domains")
        time.sleep(2)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows.values()))
    print(f"{added} added, {len(rows)} total in {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
