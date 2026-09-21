"""Pull local businesses with a website from OpenStreetMap, one metro at a time.

    python3 tools/source_osm.py denver [kansas-city ...] [--kinds dental,law,home] [--out prospects.jsonl]

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

sys.path.insert(0, str(Path(__file__).resolve().parent))
from state_paths import PROSPECTS as STATE  # noqa: E402
# Order matters: a dead first host costs two timeouts per metro before the second is tried.
ENDPOINTS = ("https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter")
UA = "radar-audit/0.1 (site audits for small business outreach)"
OVERPASS_TIMEOUT_SECONDS = 45
BACKOFF = (10, 30, 60)

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
    "memphis": "35.0,-90.25,35.3,-89.85",
    "new-orleans": "29.85,-90.25,30.05,-89.9",
    "birmingham": "33.4,-87.0,33.65,-86.6",
    "huntsville": "34.62,-86.75,34.84,-86.42",
    "mobile": "30.58,-88.2,30.8,-87.88",
    "jacksonville": "30.2,-81.85,30.45,-81.45",
    "fort-myers": "26.5,-82.0,26.75,-81.72",
    "sarasota": "27.24,-82.65,27.44,-82.4",
    "greensboro": "35.98,-79.95,36.18,-79.65",
    "charleston": "32.7,-80.08,32.88,-79.8",
    "columbia": "33.92,-81.15,34.1,-80.9",
    "virginia-beach": "36.72,-76.2,36.98,-75.85",
    "baltimore": "39.2,-76.8,39.42,-76.45",
    "philadelphia": "39.85,-75.35,40.1,-75.0",
    "allentown": "40.52,-75.62,40.68,-75.38",
    "scranton": "41.31,-75.8,41.51,-75.52",
    "harrisburg": "40.2,-76.98,40.36,-76.75",
    "erie": "42.03,-80.22,42.23,-79.95",
    "cleveland": "41.38,-81.9,41.62,-81.5",
    "akron": "40.98,-81.65,41.18,-81.4",
    "toledo": "41.56,-83.7,41.76,-83.45",
    "dayton": "39.66,-84.32,39.86,-84.05",
    "detroit": "42.22,-83.25,42.48,-82.9",
    "grand-rapids": "42.86,-85.82,43.06,-85.52",
    "lansing": "42.63,-84.7,42.83,-84.4",
    "kalamazoo": "42.2,-85.72,42.38,-85.45",
    "fort-wayne": "40.98,-85.28,41.18,-85.0",
    "evansville": "37.87,-87.72,38.07,-87.42",
    "st-louis": "38.5,-90.55,38.75,-90.05",
    "springfield-mo": "37.1,-93.45,37.32,-93.15",
    "wichita": "37.58,-97.5,37.78,-97.2",
    "topeka": "38.96,-95.8,39.14,-95.55",
    "des-moines": "41.48,-93.75,41.68,-93.5",
    "cedar-rapids": "41.9,-91.8,42.06,-91.53",
    "sioux-falls": "43.46,-96.85,43.64,-96.6",
    "madison": "42.98,-89.55,43.16,-89.25",
    "duluth": "46.7,-92.25,46.88,-91.95",
    "little-rock": "34.65,-92.45,34.85,-92.15",
    "fort-smith": "35.3,-94.53,35.48,-94.27",
    "shreveport": "32.42,-93.9,32.64,-93.6",
    "baton-rouge": "30.34,-91.35,30.56,-91.02",
    "lafayette": "30.12,-92.15,30.32,-91.88",
    "jackson": "32.2,-90.32,32.4,-90.05",
    "knoxville": "35.86,-84.08,36.06,-83.78",
    "chattanooga": "34.96,-85.45,35.14,-85.18",
    "lexington": "37.94,-84.65,38.14,-84.35",
    "lubbock": "33.47,-102.0,33.69,-101.7",
    "amarillo": "35.12,-101.98,35.32,-101.68",
    "corpus-christi": "27.68,-97.55,27.92,-97.25",
    "el-paso": "31.66,-106.65,31.86,-106.33",
    "mcallen": "26.1,-98.38,26.3,-98.08",
    "tucson": "32.08,-111.15,32.38,-110.8",
    "reno": "39.42,-119.95,39.64,-119.68",
    "fresno": "36.65,-119.92,36.85,-119.62",
    "bakersfield": "35.28,-119.15,35.48,-118.9",
    "stockton": "37.9,-121.4,38.08,-121.15",
    "spokane": "47.56,-117.58,47.76,-117.28",
    "tacoma": "47.16,-122.58,47.34,-122.3",
    "eugene": "44.0,-123.18,44.14,-123.0",
    "colorado-springs": "38.72,-104.98,38.94,-104.66",
    "fort-collins": "40.5,-105.18,40.64,-104.95",
    "buffalo": "42.8,-79.0,42.98,-78.75",
    "rochester": "43.06,-77.75,43.24,-77.48",
    "syracuse": "42.96,-76.28,43.14,-76.02",
    "albany": "42.56,-73.9,42.76,-73.62",
    "hartford": "41.68,-72.8,41.85,-72.55",
    "providence": "41.74,-71.55,41.9,-71.28",
    "boston": "42.24,-71.2,42.45,-70.95",
    "manchester-nh": "42.9,-71.6,43.08,-71.32",
    "portland-me": "43.58,-70.38,43.74,-70.14",
    "newark": "40.65,-74.28,40.85,-74.05",
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
    for index, host in enumerate(ENDPOINTS):
        # A 504 is the instance rate limiting and it clears in under a minute,
        # so the primary host is retried on a backoff and the failover gets one shot.
        for attempt, wait in enumerate(BACKOFF if index == 0 else (0,)):
            try:
                log(f"  querying {host} (attempt {attempt + 1})")
                req = urllib.request.Request(host, data=data, headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=OVERPASS_TIMEOUT_SECONDS) as resp:
                    return json.load(resp)["elements"]
            except Exception as err:  # 504 and rate limits are ordinary here
                last = err
                log(f"  {host} failed: {err}")
                time.sleep(wait)
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
    # The website tag is free text, so a business name can land in it. A host
    # with a space in it is not a host, and one such row ("town center
    # dental.com") took down a 916 domain audit run rather than being dropped.
    if not re.fullmatch(r"[a-z0-9.-]+", host or ""):
        return "", ""
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


def save(path, rows):
    """Write after every metro: a long run that is killed keeps what it sourced."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows.values()))
    tmp.replace(path)


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("metros", nargs="+")
    ap.add_argument("--kinds", default="dental,law,home")
    ap.add_argument("--out", default=str(STATE))
    # Overpass answers 429 past roughly one query a minute from one address.
    ap.add_argument("--pace", type=int, default=45)
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
        save(out, rows)
        time.sleep(args.pace)
    print(f"{added} added, {len(rows)} total in {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
