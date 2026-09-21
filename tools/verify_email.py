"""Check that an address is deliverable before sending to it.

    python3 tools/verify_email.py a@example.com b@example.org

Two checks, stdlib only: the domain has MX records (DNS over HTTPS so it
works without dig), and the primary MX accepts RCPT TO for the address in an
SMTP conversation that is closed before DATA, so nothing is sent. Servers
that accept every address ("catch all") show as accepted; a 550 on the
address is a hard no. From a residential IP many hosts (Outlook, Rackspace)
refuse the probe itself on a Spamhaus PBL listing; that is reported as
probe_blocked and says nothing about the address. Google hosted domains
answer properly.
"""

import json
import os
import random
import smtplib
import socket
import string
import sys
import urllib.request

CATCH_ALL_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..",
                               "internal-docs", "comms", "outreach-state", "catch-all.json")


def mx_hosts(domain):
    with urllib.request.urlopen(f"https://dns.google/resolve?name={domain}&type=MX", timeout=10) as r:
        data = json.load(r)
    answers = [a["data"] for a in data.get("Answer", []) if a.get("type") == 15]
    hosts = sorted((int(a.split()[0]), a.split()[1].rstrip(".")) for a in answers)
    return [h for _, h in hosts]


def rcpt_check(address, timeout=15):
    domain = address.rsplit("@", 1)[-1]
    hosts = mx_hosts(domain)
    if not hosts:
        return "no_mx", "domain has no MX records"
    for host in hosts[:2]:
        try:
            with smtplib.SMTP(host, 25, timeout=timeout) as s:
                s.ehlo("mail.codeconclave.com")
                s.mail("mohaktheprodev@gmail.com")
                code, msg = s.rcpt(address)
                s.quit()
            text = msg.decode(errors="ignore") if isinstance(msg, bytes) else str(msg)
            if 200 <= code < 300:
                return "accepted", f"{host} {code} {text[:80]}"
            if "spamhaus" in text.lower() or "blocked" in text.lower() or "rbl" in text.lower():
                return "probe_blocked", f"{host} {code} {text[:80]}"
            if code in (550, 551, 553):
                return "rejected", f"{host} {code} {text[:80]}"
            return "unknown", f"{host} {code} {text[:80]}"
        except smtplib.SMTPResponseException as exc:
            text = exc.smtp_error.decode(errors="ignore") if isinstance(exc.smtp_error, bytes) else str(exc.smtp_error)
            if "spamhaus" in text.lower() or "blocked" in text.lower() or "rbl" in text.lower():
                return "probe_blocked", f"{host} {exc.smtp_code} {text[:80]}"
            last = f"{host}: {exc.smtp_code} {text[:80]}"
            continue
        except (socket.timeout, OSError, smtplib.SMTPException) as exc:
            last = f"{host}: {exc}"
            continue
    return "unreachable", last


def catch_all(domain, timeout=15):
    """Whether this domain accepts an address that cannot exist.

    A server that takes a random local part is answering the same yes to
    everything, so an accepted verdict on a real address there carries no
    evidence. Measured on the 09 22 queue, 40 of 90 domains behaved this way,
    and of 13 addresses that had already hard bounced, 5 still answer accepted
    for both the real address and for junk. That is why this is recorded and
    not acted on: the probe cannot tell a delivering catch all from a server
    that takes every recipient and rejects at DATA, so the bounce rate of each
    class has to decide, not this function.
    """
    junk = "".join(random.choices(string.ascii_lowercase + string.digits, k=16)) + "@" + domain
    status, _ = rcpt_check(junk, timeout=timeout)
    if status == "accepted":
        return "weak"
    if status in ("rejected", "no_mx"):
        return "strong"
    return "unknown"


def catch_all_cached(domain):
    """catch_all with a per domain file cache, because the answer is a fact
    about the domain and not about any one address."""
    cache = {}
    try:
        with open(CATCH_ALL_CACHE, encoding="utf-8") as fh:
            cache = json.load(fh)
    except Exception:
        cache = {}
    entry = cache.get(domain)
    if isinstance(entry, dict) and entry.get("verdict"):
        return entry["verdict"]
    verdict = catch_all(domain)
    cache[domain] = {"verdict": verdict}
    try:
        os.makedirs(os.path.dirname(CATCH_ALL_CACHE), exist_ok=True)
        tmp = CATCH_ALL_CACHE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(cache, fh, sort_keys=True)
        os.replace(tmp, CATCH_ALL_CACHE)
    except Exception:
        pass
    return verdict


if __name__ == "__main__":
    for address in sys.argv[1:]:
        status, detail = rcpt_check(address)
        print(f"{status:11} {address}  ({detail})")