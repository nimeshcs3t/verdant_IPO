"""Turn database state into alerts, dedupe them, and send them.

Each alert has a stable key (e.g. "unlock:hero-motors:Anchor 50% (30-day):T-7") stored in
`alerts_sent`, so re-running the job every 15 minutes never double-sends.
"""
from __future__ import annotations

import smtplib
from dataclasses import dataclass
from datetime import date, timedelta
from email.message import EmailMessage

import requests

from .db import DB
from .utils import log, parse_date


@dataclass
class Alert:
    key: str
    slug: str
    kind: str
    title: str
    body: str
    priority: int = 1   # 0 low, 1 normal, 2 high


def _fmt_money(v):
    return f"₹{v:,.0f} Cr" if v else "TBA"


def _band(i):
    if i.get("issue_price"):
        return f"₹{i['issue_price']:g}"
    if i.get("price_low") and i.get("price_high"):
        return f"₹{i['price_low']:g}–{i['price_high']:g}"
    return "TBA"


def _scoreline(db: DB, slug: str) -> str:
    s = db.one("SELECT * FROM scores WHERE slug=?", [slug])
    if not s:
        return ""
    bits = []
    if s.get("quality") is not None:
        bits.append(f"Quality {s['quality']:.0f}/100 ({s['quality_grade']})")
    if s.get("anchor") is not None:
        bits.append(f"Anchor {s['anchor']:.0f}/100 ({s['anchor_grade']})")
    flags = (s.get("detail") or {}).get("quality", {}).get("flags", [])
    line = " · ".join(bits)
    if flags:
        line += "\nFlags: " + "; ".join(flags[:3])
    return line


def detect(db: DB, cfg: dict, today: date | None = None) -> list[Alert]:
    today = today or date.today()
    acfg = cfg.get("alerts", {})
    segments = {s.upper() for s in acfg.get("segments", ["MAINBOARD", "SME"])}
    watch = set(acfg.get("watchlist", []))
    lookback = timedelta(days=acfg.get("filing_lookback_days", 7))
    unlock_leads = acfg.get("unlock_lead_days", [7, 1, 0])
    min_unlock_pct = acfg.get("min_unlock_pct", 1.0)
    out: list[Alert] = []

    ipos = db.query("SELECT * FROM ipos")
    for i in ipos:
        slug, name = i["slug"], i["name"]
        seg = (i.get("segment") or "MAINBOARD").upper()
        if seg not in segments and slug not in watch:
            continue
        tag = f"[{seg}]"

        for kind, col, url_col, label in [("DRHP", "drhp_date", "drhp_url", "filed its DRHP"),
                                          ("RHP", "rhp_date", "rhp_url", "filed its RHP (IPO is near)")]:
            d = parse_date(i.get(col))
            if d and today - lookback <= d <= today:
                out.append(Alert(f"{kind.lower()}:{slug}", slug, kind, f"{tag} {name} {label}",
                                 f"Filed {d:%d %b %Y}\n{i.get(url_col) or ''}".strip(), 2 if kind == "RHP" else 1))

        o, c = parse_date(i.get("open_date")), parse_date(i.get("close_date"))
        if o and o > today:
            out.append(Alert(f"dates:{slug}:{o}", slug, "DATES", f"{tag} {name} IPO: {o:%d %b} – {c:%d %b}" if c
                             else f"{tag} {name} IPO opens {o:%d %b}",
                             f"Price band {_band(i)} · Size {_fmt_money(i.get('issue_size_cr'))}"
                             f" (Fresh {_fmt_money(i.get('fresh_cr'))}, OFS {_fmt_money(i.get('ofs_cr'))})\n"
                             + _scoreline(db, slug)))
        for col, kind, label in [("open_date", "OPEN", "opens today"), ("close_date", "CLOSE", "closes today"),
                                 ("allotment_date", "ALLOTMENT", "allotment today"),
                                 ("listing_date", "LISTING", "lists today")]:
            if parse_date(i.get(col)) == today:
                sub = i.get("subscription") or {}
                extra = ""
                if kind == "CLOSE" and sub:
                    extra = "Subscription so far: " + ", ".join(f"{k.upper()} {v:g}x" for k, v in sub.items())
                out.append(Alert(f"{kind.lower()}:{slug}:{today}", slug, kind, f"{tag} {name} {label}",
                                 (extra + "\n" + _scoreline(db, slug)).strip(), 2 if kind in ("OPEN", "LISTING") else 1))

    for a in db.query("SELECT a.*, i.name, i.segment FROM anchor_book a JOIN ipos i USING(slug)"):
        s = db.one("SELECT * FROM scores WHERE slug=?", [a["slug"]]) or {}
        det = (s.get("detail") or {}).get("anchor", {})
        body = (f"₹{a['amount_cr'] or 0:,.0f} Cr at ₹{a['bid_price'] or 0:g}"
                + (f" · MFs {a['mf_pct']:.0f}%" if a.get("mf_pct") else "")
                + (f"\nAnchor score {s['anchor']:.0f}/100 ({s['anchor_grade']})" if s.get("anchor") is not None else "")
                + ("\n" + "; ".join(det.get("reasons", [])[:3]) if det else ""))
        out.append(Alert(f"anchor:{a['slug']}", a["slug"], "ANCHOR",
                         f"[{a['segment']}] {a['name']} anchor book is out", body, 1))

    for u in db.query("SELECT u.*, i.name, i.segment FROM unlocks u JOIN ipos i USING(slug)"):
        d = parse_date(u["unlock_date"])
        if not d or (u.get("pct_equity") is not None and u["pct_equity"] < min_unlock_pct):
            continue
        lead = (d - today).days
        if lead in unlock_leads:
            when = "today" if lead == 0 else f"in {lead} day{'s' if lead > 1 else ''} ({d:%d %b})"
            pct = f"{u['pct_equity']:.1f}% of equity" if u.get("pct_equity") is not None else "size n/a"
            r = db.one("SELECT ltp, ret_vs_issue FROM returns WHERE slug=?", [u["slug"]])
            perf = f"\nStock is {r['ret_vs_issue']:+.0%} vs IPO price" if r and r.get("ret_vs_issue") is not None else ""
            out.append(Alert(f"unlock:{u['slug']}:{u['bucket']}:T-{lead}", u["slug"], "UNLOCK",
                             f"[{u['segment']}] {u['name']}: {u['bucket']} lock-in ends {when}",
                             f"{pct}. {u.get('note') or ''}{perf}", 2 if lead <= 1 else 1))

    for r in db.query("SELECT r.*, i.name, i.segment, i.listing_date FROM returns r JOIN ipos i USING(slug)"):
        ld = parse_date(r.get("listing_date"))
        if ld and (today - ld).days <= 3 and r.get("listing_gain") is not None:
            out.append(Alert(f"listed:{r['slug']}", r["slug"], "LISTED",
                             f"[{r['segment']}] {r['name']} listed at {r['listing_gain']:+.1%}",
                             f"Open ₹{r['listing_open']:g} vs issue ₹{r['issue_price']:g}; now ₹{r['ltp']:g} "
                             f"({r['ret_vs_issue']:+.1%} vs IPO)", 1))

    for v in db.query("SELECT v.*, i.name FROM videos v JOIN ipos i USING(slug) WHERE v.kind IN ('interview','roadshow')"):
        flags = (v.get("transcript_flags") or {}).get("red_flags", []) if isinstance(v.get("transcript_flags"), dict) else []
        body = f"{v['channel']} · {v.get('published') or ''}\n{v['url']}"
        if v.get("summary"):
            body += f"\n{v['summary'][:500]}"
        elif flags:
            body += "\nMentions: " + ", ".join(f["term"] for f in flags[:5])
        out.append(Alert(f"video:{v['video_id']}", v["slug"], "VIDEO", f"{v['name']}: new {v['kind']} video",
                         body + f"\n{v['title']}", 0))
    return out


# ------------------------------------------------------------------ delivery
class Dispatcher:
    def __init__(self, cfg: dict):
        self.cfg = cfg.get("channels", {})

    def send(self, a: Alert) -> None:
        text = f"{a.title}\n{a.body}".strip()
        if self.cfg.get("console", True):
            print("─" * 60 + f"\n{'🔴' if a.priority == 2 else '🔔'} {text}")
        tg = self.cfg.get("telegram") or {}
        if tg.get("bot_token") and tg.get("chat_id"):
            try:
                requests.post(f"https://api.telegram.org/bot{tg['bot_token']}/sendMessage", timeout=15,
                              json={"chat_id": tg["chat_id"], "text": text, "disable_web_page_preview": True,
                                    "disable_notification": a.priority == 0})
            except requests.RequestException as e:
                log.warning("telegram: %s", e)
        for hook in (self.cfg.get("webhooks") or []):
            try:  # Discord uses "content", Slack uses "text"; send both
                requests.post(hook, json={"content": text[:1900], "text": text}, timeout=15)
            except requests.RequestException as e:
                log.warning("webhook: %s", e)
        em = self.cfg.get("email") or {}
        if em.get("smtp_host") and em.get("to") and a.priority >= em.get("min_priority", 1):
            try:
                msg = EmailMessage()
                msg["Subject"], msg["From"], msg["To"] = a.title, em.get("from", em.get("user")), em["to"]
                msg.set_content(text)
                with smtplib.SMTP(em["smtp_host"], em.get("smtp_port", 587), timeout=20) as s:
                    s.starttls()
                    if em.get("user"):
                        s.login(em["user"], em["password"])
                    s.send_message(msg)
            except Exception as e:
                log.warning("email: %s", e)


def run(db: DB, cfg: dict, today: date | None = None, dry_run: bool = False) -> list[Alert]:
    d = Dispatcher(cfg)
    fresh = [a for a in detect(db, cfg, today) if not db.alert_seen(a.key)]
    fresh.sort(key=lambda a: -a.priority)
    for a in fresh:
        d.send(a)
        if not dry_run:
            db.mark_alert(a.key, a.slug, a.kind, f"{a.title}\n{a.body}")
    log.info("alerts: %d new", len(fresh))
    return fresh
