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
import smtplib
import socket
import sys
import urllib.request


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


if __name__ == "__main__":
    for address in sys.argv[1:]:
        status, detail = rcpt_check(address)
        print(f"{status:11} {address}  ({detail})")
