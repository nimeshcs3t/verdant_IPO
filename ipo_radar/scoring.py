"""Transparent, rule-based scores. Every point comes with a reason string so you can
see *why* an IPO scored what it did — and disagree with it. Tune weights in config.yaml.

Company quality (0-100)   growth, profitability, balance sheet, cash conversion,
                          issue structure, valuation, minus red flags
Anchor book quality (0-100)  who is in the book, how concentrated it is
Pre-IPO investors         price paid vs IPO price (how much they're marking up)
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

from .lockin import capital
from .utils import parse_date

DEFAULT_WEIGHTS = {"growth": 20, "profitability": 20, "balance_sheet": 15,
                   "cash_conversion": 15, "issue_structure": 15, "valuation": 15}


def grade(score: float | None) -> str:
    if score is None:
        return "NA"
    return "A" if score >= 75 else "B" if score >= 60 else "C" if score >= 45 else "D"


def _band(x, cuts, scores):
    """Piecewise: first cut that x is below gives that score; else last score."""
    for c, s in zip(cuts, scores):
        if x < c:
            return s
    return scores[-1]


def _cagr(first, last, years):
    if not first or not last or first <= 0 or last <= 0 or years <= 0:
        return None
    return (last / first) ** (1 / years) - 1


# ------------------------------------------------------------------ company quality
def company_quality(ipo: dict, fins: list[dict], weights: dict | None = None,
                    pre_ipo: list[dict] | None = None) -> dict:
    w = {**DEFAULT_WEIGHTS, **(weights or {})}
    parts, reasons, flags = {}, [], []
    fins = [f for f in fins if f.get("revenue")]
    sme = (ipo.get("segment") or "").upper() == "SME"

    if len(fins) >= 2:
        f0, fl = fins[0], fins[-1]
        yrs = len(fins) - 1
        rev_g = _cagr(f0["revenue"], fl["revenue"], yrs)
        pat_g = _cagr(f0.get("pat"), fl.get("pat"), yrs)
        if rev_g is not None:
            s = _band(rev_g * 100, [5, 10, 15, 25], [2, 4, 6, 8, 10])
            if pat_g is not None and pat_g > rev_g:
                s = min(10, s + 1)
            parts["growth"] = s
            reasons.append(f"Revenue CAGR {rev_g:.0%} over {yrs}y" +
                           (f", PAT CAGR {pat_g:.0%}" if pat_g is not None else ""))
        # last-year spike right before listing
        if len(fins) >= 3 and fins[-2].get("pat") and fl.get("pat"):
            prev_g = (fins[-2]["pat"] / fins[-3]["pat"] - 1) if fins[-3].get("pat") else None
            last_g = fl["pat"] / fins[-2]["pat"] - 1 if fins[-2]["pat"] > 0 else None
            if last_g and last_g > 1.0 and (prev_g is None or prev_g < 0.3):
                flags.append(f"PAT jumped {last_g:.0%} in the year before IPO after flat history — check for one-offs")
    if fins:
        fl = fins[-1]
        nw = (fl.get("equity_capital") or 0) + (fl.get("reserves") or 0)
        margin = (fl.get("pat") or 0) / fl["revenue"] if fl["revenue"] else None
        roe = fl["pat"] / nw if nw > 0 and fl.get("pat") is not None else None
        if roe is not None:
            s = _band(roe * 100, [5, 10, 15, 20], [2, 4, 6, 8, 10])
            if margin is not None and margin < 0.03:
                s = max(1, s - 2)
            parts["profitability"] = s
            reasons.append(f"ROE {roe:.0%}, PAT margin {margin:.1%}")
        if nw <= 0:
            flags.append("Negative or zero net worth")
        if fl.get("borrowings") is not None and nw > 0:
            de = fl["borrowings"] / nw
            parts["balance_sheet"] = _band(de, [0.3, 0.7, 1.0, 2.0], [10, 8, 6, 3, 1])
            reasons.append(f"Debt/equity {de:.2f}")
        cfo = sum(f.get("cfo") or 0 for f in fins if f.get("cfo") is not None)
        pat = sum(f.get("pat") or 0 for f in fins if f.get("cfo") is not None)
        if pat > 0 and any(f.get("cfo") is not None for f in fins):
            conv = cfo / pat
            parts["cash_conversion"] = _band(conv, [0.2, 0.5, 0.8], [1, 4, 7, 10])
            reasons.append(f"Cumulative CFO/PAT {conv:.2f}")
            if fl.get("cfo") is not None and fl["cfo"] < 0:
                flags.append("Negative operating cash flow in latest year")

    # issue structure
    size = ipo.get("issue_size_cr")
    ofs = ipo.get("ofs_cr")
    if ofs is None and ipo.get("fresh_cr") is not None and size:
        ofs = max(0.0, size - ipo["fresh_cr"])
    if size and ofs is not None:
        ofs_share = ofs / size
        s = _band(ofs_share * 100, [1, 30, 60, 90], [10, 8, 5, 3, 1])
        objects = " ".join(ipo.get("objects") or []).lower()
        if re.search(r"unidentified acquisition|inorganic", objects):
            s -= 1
            reasons.append("Part of proceeds for unidentified acquisitions")
        if re.search(r"repay|prepay|capital exp|capex|purchase of (plant|machinery|equipment)", objects):
            s += 1
        pp = ipo.get("promoter_post")
        if pp is not None:
            if pp < 35:
                s -= 2
                flags.append(f"Low promoter holding post-IPO ({pp:.1f}%)")
            elif pp >= 50:
                s += 1
        parts["issue_structure"] = max(0, min(10, s))
        reasons.append(f"OFS is {ofs_share:.0%} of the issue")
        if sme and size < 20:
            flags.append(f"Very small SME issue (₹{size:.0f} Cr) — thin post-listing liquidity")

    # valuation at upper band
    cap = capital(ipo, fins)
    if cap.get("mcap_cr") and fins:
        pat_last = fins[-1].get("pat")
        if pat_last and pat_last > 0:
            pe = cap["mcap_cr"] / pat_last
            parts["valuation"] = _band(pe, [15, 25, 40, 60], [10, 8, 6, 4, 2])
            reasons.append(f"P/E {pe:.1f}x at upper band, M-cap ₹{cap['mcap_cr']:,.0f} Cr")
        else:
            parts["valuation"] = 1
            flags.append("Loss-making at IPO")

    # risk-text heuristics
    risks = " ".join(ipo.get("risks") or [])
    m = re.search(r"top\s*(?:ten|10|five|5)\s*customers.{0,300}?(\d{2}\.\d+)\s*%", risks, re.I | re.S)
    if m and float(m.group(1)) >= 70:
        flags.append(f"Customer concentration: top customers ≈{m.group(1)}% of revenue")
    if re.search(r"\b(sebi|enforcement directorate|income tax|gst)\b.{0,60}\b(search|survey|show cause|notice)",
                 risks, re.I):
        flags.append("Regulatory/tax proceedings disclosed in risk factors")

    # pre-IPO markups
    for p in pre_ipo_markups(ipo, pre_ipo or []):
        if p["multiple"] and p["multiple"] >= 2 and p["months_before"] is not None and p["months_before"] <= 18:
            flags.append(f"{p['investor']} bought at ₹{p['price']:.0f} {p['months_before']:.0f}m before IPO "
                         f"— IPO price is {p['multiple']:.1f}x")

    if not fins or len(parts) < 3:
        return {"score": None, "grade": "NA", "parts": parts,
                "reasons": reasons or ["Financials not loaded yet"], "flags": flags}
    total_w = sum(w[k] for k in parts)
    score = sum(parts[k] * w[k] for k in parts) / total_w * 10
    score -= 4 * len(flags)
    score = max(0, min(100, score))
    return {"score": round(score, 1), "grade": grade(score), "parts": parts,
            "reasons": reasons, "flags": flags, "coverage": f"{len(parts)}/{len(w)} factors"}


# ------------------------------------------------------------------ anchor quality
class AnchorTiers:
    def __init__(self, path: str | Path = "data/anchor_tiers.yaml"):
        p = Path(path)
        self.t = yaml.safe_load(p.read_text()) if p.exists() else {}
        self.extra_frequent: set[str] = set()

    def learn_frequent(self, league: list[dict], min_ipos: int = 15) -> None:
        """Add names from Chittorgarh's SME anchor league table that invest in many books."""
        self.extra_frequent |= {r["investor"].lower() for r in league if r.get("n_ipos", 0) >= min_ipos}

    def classify(self, name: str) -> str:
        n = name.lower()
        if any(k in n for k in self.t.get("sme_frequent", [])) or n in self.extra_frequent:
            return "sme_frequent"
        for tier in ("tier1_domestic", "tier1_global", "tier2"):
            if any(k in n for k in self.t.get(tier, [])):
                return tier
        if re.search(r"mutual fund|life insurance|assurance|pension", n):
            return "tier2"
        return "unknown"


TIER_WEIGHT = {"tier1_domestic": 1.0, "tier1_global": 1.0, "tier2": 0.55, "unknown": 0.3, "sme_frequent": 0.0}


def anchor_quality(ipo: dict, book: dict | None, investors: list[dict], tiers: AnchorTiers) -> dict:
    if not book and not investors:
        return {"score": None, "grade": "NA", "reasons": ["Anchor book not out yet"], "mix": {}}
    reasons, mix = [], {}
    sme = (ipo.get("segment") or "").upper() == "SME"
    if investors:
        total = sum(i.get("amount_cr") or 0 for i in investors) or len(investors)
        for i in investors:
            t = tiers.classify(i["investor"])
            i["tier"] = t
            mix[t] = mix.get(t, 0) + (i.get("amount_cr") or 1) / total
        score = 100 * sum(TIER_WEIGHT[t] * share for t, share in mix.items())
        t1_names = {i["investor"].split("-")[0].strip() for i in investors if i["tier"].startswith("tier1")}
        if len(t1_names) >= 5:
            score += 5
            reasons.append(f"{len(t1_names)} distinct tier-1 institutions")
        top3 = sorted((i.get("amount_cr") or 0 for i in investors), reverse=True)[:3]
        if total and sum(top3) / total > 0.6 and len(investors) > 3:
            score -= 10
            reasons.append("Top-3 anchors hold >60% of the book")
        t1 = mix.get("tier1_domestic", 0) + mix.get("tier1_global", 0)
        reasons.append(f"Tier-1 share {t1:.0%} of book value across {len(investors)} anchors")
        if mix.get("sme_frequent", 0) > 0.3:
            reasons.append(f"{mix['sme_frequent']:.0%} from frequent SME anchors (low signal)")
    else:
        mf = book.get("mf_pct")
        if mf is None:
            return {"score": None, "grade": "NA", "reasons": ["Anchor investor list not available"], "mix": {}}
        score = min(95, 25 + 0.7 * mf)
        reasons.append(f"Mutual funds took {mf:.0f}% of the book (investor list not loaded; MF share only)")
        mix = {"mutual_funds": mf / 100}
    if book and ipo.get("issue_size_cr") and book.get("amount_cr"):
        share = book["amount_cr"] / ipo["issue_size_cr"]
        reasons.append(f"Anchor book ₹{book['amount_cr']:,.0f} Cr = {share:.0%} of issue")
    score = max(0, min(100, score))
    if sme and mix.get("sme_frequent", 0) > 0.5:
        score = min(score, 40)
    return {"score": round(score, 1), "grade": grade(score), "reasons": reasons,
            "mix": {k: round(v, 3) for k, v in mix.items()}}


# ------------------------------------------------------------------ pre-IPO investors
def pre_ipo_markups(ipo: dict, pre_ipo: list[dict]) -> list[dict]:
    """For each pre-IPO deal with a price: IPO price multiple and how recent it was."""
    price = ipo.get("issue_price") or ipo.get("price_high")
    ref = parse_date(ipo.get("open_date")) or parse_date(ipo.get("rhp_date"))
    out = []
    for p in pre_ipo:
        if not p.get("price"):
            continue
        d = parse_date(p.get("deal_date"))
        months = (ref - d).days / 30.4 if (ref and d) else None
        out.append({"investor": p["investor"], "category": p.get("category"), "price": p["price"],
                    "deal_date": p.get("deal_date"),
                    "multiple": round(price / p["price"], 2) if price else None,
                    "months_before": round(months, 1) if months is not None else None})
    return out
