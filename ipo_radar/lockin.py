"""Unlock calendar from SEBI ICDR lock-in rules.

All lock-ins run from the IPO *allotment* date. A 30-day lock-in ends on allotment + 29
days (the stock is free the next day) — this matches the dates exchanges and StockScans
publish, e.g. allotment 21-Sep-2026 -> 30-day end 20-Oct-2026, 90-day end 19-Dec-2026.

MAINBOARD (ICDR Reg. 16/17, post Aug-2021 amendment)
  anchor               50% after 30 days, 50% after 90 days
  pre-IPO non-promoter 6 months
  promoter excess >20% 6 months   (1 year if >50% of fresh issue funds capex)
  promoter MPC (20%)   18 months  (3 years if capex-heavy)
SME (ICDR Chapter IX)
  anchor               50% after 30 days, 50% after 90 days
  pre-IPO non-promoter 1 year
  promoter excess      50% after 1 year, 50% after 2 years (issues allotted from Mar-2025);
                       1 year for older issues
  promoter MPC (20%)   3 years

The offer document is the final word (exemptions exist: VC/AIF holders, ESOP trusts, etc.).
Always check the "Capital Structure" chapter of the RHP for the exact lock-in table.
"""
from __future__ import annotations

from datetime import date, timedelta

from dateutil.relativedelta import relativedelta

from .utils import iso, parse_date

SME_STAGGER_FROM = date(2025, 3, 1)


def _end(allot: date, **kw) -> date:
    return allot + relativedelta(**kw) - timedelta(days=1)


def _next_business_day(d: date, n: int = 1) -> date:
    while n:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d


def estimate_allotment(ipo: dict) -> tuple[date | None, bool]:
    """Real allotment date if known, else close + 1 business day (T+3 listing regime)."""
    d = parse_date(ipo.get("allotment_date"))
    if d:
        return d, False
    close = parse_date(ipo.get("close_date"))
    return (_next_business_day(close), True) if close else (None, True)


def capital(ipo: dict, fins: list[dict]) -> dict:
    """Pre/post issue share counts and market cap from the latest balance sheet."""
    price = ipo.get("issue_price") or ipo.get("price_high")
    fv = ipo.get("face_value") or 10
    latest = next((f for f in reversed(fins) if f.get("equity_capital")), None)
    out = {"price": price}
    if not (price and latest):
        return out
    pre = latest["equity_capital"] * 1e7 / fv
    fresh = (ipo.get("fresh_cr") or 0) * 1e7 / price
    post = pre + fresh
    out.update(pre_shares=pre, post_shares=post, mcap_cr=post * price / 1e7)
    if ipo.get("issue_size_cr"):
        out["issue_pct"] = 100 * ipo["issue_size_cr"] / out["mcap_cr"]
    return out


def unlock_schedule(ipo: dict, anchor: dict | None = None, fins: list[dict] | None = None,
                    pre_ipo: list[dict] | None = None) -> list[dict]:
    allot, estimated = estimate_allotment(ipo)
    if not allot:
        return []
    sme = (ipo.get("segment") or "").upper() == "SME"
    capex = bool(ipo.get("capex_heavy"))
    cap = capital(ipo, fins or [])
    post_sh = cap.get("post_shares")
    note_est = " (allotment date estimated)" if estimated else ""
    slug = ipo["slug"]
    rows: list[dict] = []

    def add(bucket, when, pct=None, shares=None, note=""):
        if pct is None and shares and post_sh:
            pct = 100 * shares / post_sh
        if shares is None and pct is not None and post_sh:
            shares = int(post_sh * pct / 100)
        rows.append({"slug": slug, "bucket": bucket, "unlock_date": iso(when),
                     "pct_equity": round(pct, 2) if pct is not None else None,
                     "shares": int(shares) if shares else None, "note": note + note_est})

    # --- anchors: prefer the dates the exchange/aggregator published
    a_sh = (anchor or {}).get("shares")
    half = a_sh / 2 if a_sh else None
    d30 = parse_date((anchor or {}).get("lockin_30")) or _end(allot, days=30)
    d90 = parse_date((anchor or {}).get("lockin_90")) or _end(allot, days=90)
    add("Anchor 50% (30-day)", d30, shares=half, note="Half of anchor book")
    add("Anchor 50% (90-day)", d90, shares=half, note="Remaining anchor book")

    # --- pre-IPO non-promoter shareholders
    other_pct = None
    if pre_ipo:
        other_pct = sum(p.get("pct_post") or 0 for p in pre_ipo if p.get("category") != "PROMOTER") or None
    if other_pct is None and ipo.get("promoter_pre") is not None and cap.get("pre_shares"):
        nonprom_pre = (100 - ipo["promoter_pre"]) / 100 * cap["pre_shares"]
        other_pct = max(0.0, 100 * nonprom_pre / cap["post_shares"])
    if other_pct is None and ipo.get("promoter_post") is not None and cap.get("issue_pct"):
        other_pct = max(0.0, 100 - ipo["promoter_post"] - cap["issue_pct"])
    add("Pre-IPO investors (non-promoter)", _end(allot, years=1) if sme else _end(allot, months=6),
        pct=other_pct, note="Shareholders before the IPO who aren't promoters (PE/VC, HNIs, ESOP)")

    # --- promoters
    pp = ipo.get("promoter_post")
    mpc = min(20.0, pp) if pp is not None else None
    excess = max(0.0, pp - 20) if pp is not None else None
    if sme:
        if allot >= SME_STAGGER_FROM:
            add("Promoter excess 50% (1-year)", _end(allot, years=1),
                pct=excess / 2 if excess is not None else None, note="Promoter holding above 20%")
            add("Promoter excess 50% (2-year)", _end(allot, years=2),
                pct=excess / 2 if excess is not None else None, note="Promoter holding above 20%")
        else:
            add("Promoter excess (1-year)", _end(allot, years=1), pct=excess, note="Promoter holding above 20%")
        add("Promoter minimum contribution (3-year)", _end(allot, years=3), pct=mpc, note="Locked 20% MPC")
    else:
        add("Promoter excess", _end(allot, years=1) if capex else _end(allot, months=6), pct=excess,
            note="Promoter holding above 20%" + ("; capex-heavy issue" if capex else ""))
        add("Promoter minimum contribution", _end(allot, years=3) if capex else _end(allot, months=18),
            pct=mpc, note="Locked 20% MPC")
    return rows
