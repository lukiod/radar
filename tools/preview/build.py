"""Generate a one page preview site for a small business from a JSON brief.

    python3 tools/preview/build.py brief.json out_dir

The brief holds what the prospect's current site already says (name, phone,
services, service area, hours, a few review quotes) plus the form endpoint.
Output is a static folder deployable to any host: index.html with a
request-service form, click to call on every screen, no JavaScript needed
for the page to work. Stdlib only.
"""

import html
import json
import sys
from pathlib import Path

TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{name} | {tagline}</title>
<meta name="description" content="{name}: {tagline}. Serving {area}. Call {phone} or request service online.">
<style>
  :root {{ --brand:{brand}; --ink:#111827; --muted:#6b7280; --bg:#ffffff; --soft:#f3f4f6; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; font:16px/1.6 system-ui,-apple-system,Segoe UI,Roboto,sans-serif; color:var(--ink); background:var(--bg); }}
  a {{ color:inherit; }}
  header {{ display:flex; align-items:center; justify-content:space-between; padding:14px 20px; border-bottom:1px solid #e5e7eb; position:sticky; top:0; background:var(--bg); z-index:2; }}
  .brand {{ font-weight:700; font-size:18px; }}
  .call {{ background:var(--brand); color:#fff; text-decoration:none; padding:10px 16px; border-radius:8px; font-weight:600; white-space:nowrap; }}
  main {{ max-width:960px; margin:0 auto; padding:0 20px 60px; }}
  .hero {{ padding:48px 0 24px; }}
  h1 {{ font-size:clamp(28px,5vw,44px); line-height:1.15; margin:0 0 12px; }}
  .lead {{ color:var(--muted); font-size:18px; margin:0 0 20px; }}
  .cta {{ display:flex; gap:12px; flex-wrap:wrap; }}
  .btn {{ display:inline-block; padding:14px 20px; border-radius:8px; text-decoration:none; font-weight:600; }}
  .primary {{ background:var(--brand); color:#fff; }}
  .secondary {{ background:var(--soft); color:var(--ink); }}
  section {{ padding:28px 0; border-top:1px solid #e5e7eb; }}
  h2 {{ font-size:22px; margin:0 0 14px; }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:14px; }}
  .card {{ background:var(--soft); border-radius:10px; padding:16px; }}
  .card h3 {{ margin:0 0 6px; font-size:17px; }}
  .card p {{ margin:0; color:var(--muted); font-size:15px; }}
  blockquote {{ margin:0; padding:16px; background:var(--soft); border-radius:10px; font-style:italic; }}
  blockquote footer {{ font-style:normal; color:var(--muted); margin-top:8px; font-size:14px; }}
  form {{ display:grid; gap:12px; max-width:520px; }}
  label {{ display:grid; gap:6px; font-size:14px; font-weight:600; }}
  input, textarea, select {{ font:inherit; padding:12px; border:1px solid #d1d5db; border-radius:8px; width:100%; }}
  textarea {{ min-height:110px; }}
  .fine {{ color:var(--muted); font-size:13px; }}
  footer.site {{ padding:24px 20px; border-top:1px solid #e5e7eb; color:var(--muted); font-size:14px; text-align:center; }}
  .sticky-call {{ display:none; }}
  @media (max-width:640px) {{
    .sticky-call {{ display:block; position:fixed; bottom:0; left:0; right:0; background:var(--brand); color:#fff; text-align:center; padding:14px; font-weight:700; text-decoration:none; }}
    main {{ padding-bottom:90px; }}
  }}
</style>
</head>
<body>
<header>
  <div class="brand">{name}</div>
  <a class="call" href="tel:{phone_digits}">Call {phone}</a>
</header>
<main>
  <div class="hero">
    <h1>{headline}</h1>
    <p class="lead">{tagline}. Serving {area}.{hours_line}</p>
    <div class="cta">
      <a class="btn primary" href="#request">Request service or schedule service</a>
      <a class="btn secondary" href="tel:{phone_digits}">Call {phone}</a>
    </div>
  </div>

  <section id="services">
    <h2>What we do</h2>
    <div class="grid">
{services}
    </div>
  </section>

{reviews_section}

  <section id="request">
    <h2>Request service</h2>
    <p class="fine">Tell us what is going on and where. We reply during business hours; for emergencies call {phone}.</p>
    <form action="{form_action}" method="POST">
      <input type="hidden" name="_subject" value="Service request from {name} website">
      <label>Name <input name="name" required autocomplete="name"></label>
      <label>Phone <input name="phone" type="tel" required autocomplete="tel"></label>
      <label>Service address <input name="address" required autocomplete="street-address"></label>
      <label>What do you need? <textarea name="message" required></textarea></label>
      <label>When? <select name="urgency"><option>As soon as possible</option><option>This week</option><option>Getting quotes</option></select></label>
      <button class="btn primary" type="submit">Send request</button>
      <p class="fine">Your details go straight to {name}. No spam, no lists.</p>
    </form>
  </section>
</main>
<footer class="site">{name} · {area} · <a href="tel:{phone_digits}">{phone}</a> · {year}</footer>
<a class="sticky-call" href="tel:{phone_digits}">Call now: {phone}</a>
</body>
</html>
"""


def build(brief, out_dir):
    e = html.escape
    services = "\n".join(
        f'      <div class="card"><h3>{e(s["name"])}</h3><p>{e(s.get("blurb", ""))}</p></div>'
        for s in brief["services"]
    )
    reviews = brief.get("reviews") or []
    reviews_section = ""
    if reviews:
        quotes = "\n".join(
            f'      <blockquote>{e(r["text"])}<footer>{e(r.get("who", "Customer"))}</footer></blockquote>' for r in reviews
        )
        reviews_section = f'  <section id="reviews">\n    <h2>What customers say</h2>\n    <div class="grid">\n{quotes}\n    </div>\n  </section>\n'
    hours = brief.get("hours")
    page = TEMPLATE.format(
        name=e(brief["name"]),
        tagline=e(brief["tagline"]),
        headline=e(brief.get("headline") or brief["tagline"]),
        area=e(brief["area"]),
        phone=e(brief["phone"]),
        phone_digits="".join(ch for ch in brief["phone"] if ch.isdigit() or ch == "+"),
        hours_line=f" {e(hours)}." if hours else "",
        brand=e(brief.get("brand_color", "#1d4ed8")),
        services=services,
        reviews_section=reviews_section,
        form_action=e(brief.get("form_action", "#")),
        year=brief.get("year", "2026"),
    )
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text(page)
    return out / "index.html"


if __name__ == "__main__":
    brief = json.loads(Path(sys.argv[1]).read_text())
    print(build(brief, sys.argv[2]))
