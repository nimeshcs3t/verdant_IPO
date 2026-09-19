"""Seed the database with a snapshot taken from StockScans on 18-Sep-2026 so the
dashboard and alerts can be explored before running a live sync.
Run `python run.py sync` to replace it with live data."""
from __future__ import annotations

from datetime import date, timedelta

from .db import DB
from .pipeline import upsert_ipo

SRC = {"stockscans": "https://www.stockscans.in/ipo-scans"}


def _bdays_before(d: date, n: int) -> date:
    while n:
        d -= timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d


def seed(db: DB) -> None:
    # ---------------- a full detail page: Hero Motors (mainboard, closes 18-Sep-2026)
    upsert_ipo(db, {
        "slug": "hero-motors", "name": "Hero Motors Ltd", "segment": "MAINBOARD", "status": "OPEN",
        "sector": "Auto Ancillaries", "price_low": 79, "price_high": 84, "issue_size_cr": 1000,
        "fresh_cr": 600, "ofs_cr": 400, "lot_size": 178, "face_value": 10,
        "open_date": "2026-09-16", "close_date": "2026-09-18", "allotment_date": "2026-09-21",
        "listing_date": "2026-09-23", "promoter_pre": 84.65, "promoter_post": 61.65,
        "lead_managers": "ICICI Securities Ltd", "registrar": "KFin Technologies Ltd",
        "subscription": {"qib": 1.49, "nii": 9.86, "retail": 8.23, "total": 6.66},
        "objects": ["Repayment/prepayment of certain outstanding borrowings",
                    "Capital expenditure for equipment to expand capacity at Gautam Budh Nagar",
                    "Unidentified acquisitions, inorganic growth and general corporate purposes"],
        "risks": ["Europe contributes roughly 26-32% of revenue from operations.",
                  "Dependence on e-bike and two-wheeler industry demand.",
                  "Top 10 customers contributed 74.46%, 76.96%, 88.84% and 85.97% of revenue in the "
                  "periods disclosed."],
        "sources": {"stockscans": "https://www.stockscans.in/ipo/hero-motors"}})
    fins = [("FY24", 1064, 67, 24, 17, 354, 32, 334, 1060, 132),
            ("FY25", 1090, 92, 39, 33, 357, 68, 432, 1165, 48),
            ("FY26", 1188, 121, 61, 41, 358, 116, 471, 1372, 144)]
    for fy, rev, op, pbt, pat, eq, res, debt, ta, cfo in fins:
        db.upsert("financials", {"slug": "hero-motors", "fy": fy, "revenue": rev, "op_profit": op, "pbt": pbt,
                                 "pat": pat, "equity_capital": eq, "reserves": res, "borrowings": debt,
                                 "total_assets": ta, "cfo": cfo}, ["slug", "fy"])
    db.upsert("anchor_book", {"slug": "hero-motors", "bid_date": "2026-09-15", "bid_price": 84,
                              "shares": 35714284, "amount_cr": 300, "mf_pct": 81.66,
                              "lockin_30": "2026-10-20", "lockin_90": "2026-12-19"}, ["slug"])
    for inv, sh in [("O P Munjal Holdings", 47023809), ("Hero Cycles Limited", 595238)]:
        db.upsert("pre_ipo_investors", {"slug": "hero-motors", "investor": inv, "category": "PROMOTER",
                                        "shares": sh, "deal_date": "OFS"}, ["slug", "investor", "deal_date"])

    # ---------------- current / upcoming list rows
    for slug, name, seg, st, lo, hi, size, o, c in [
        ("national-stock-exchange-of-india", "National Stock Exchange Of India Ltd", "MAINBOARD", "OPEN",
         1700, 1785, 22569, "2026-09-17", "2026-09-21"),
        ("kheria-autocomp", "Kheria Autocomp Ltd", "SME", "OPEN", 96, 101, 46, "2026-09-17", "2026-09-21"),
        ("fx-multitech", "FX Multitech Ltd", "SME", "UPCOMING", 110, 116, 45, "2026-09-21", "2026-09-23"),
        ("varmora-granito", "Varmora Granito Ltd", "MAINBOARD", "UPCOMING", 140, 148, 708, "2026-09-22", "2026-09-24"),
        ("a-one-steels-india", "A-One Steels India Ltd", "MAINBOARD", "UPCOMING", 385, 405, 405, "2026-09-24", "2026-09-28"),
    ]:
        upsert_ipo(db, {"slug": slug, "name": name, "segment": seg, "status": st, "price_low": lo,
                        "price_high": hi, "issue_size_cr": size, "open_date": o, "close_date": c,
                        "sources": {"stockscans": f"https://www.stockscans.in/ipo/{slug}"}})

    # ---------------- DRHP pipeline
    for slug, name, d in [("lalsai-global", "Lalsai Global Ltd", "2026-09-03"),
                          ("ideas-electricals-engineers", "Ideas Electricals & Engineers Ltd", "2026-09-02"),
                          ("mahanadi-coalfields", "Mahanadi Coalfields Ltd", "2026-08-31"),
                          ("aragen-life-sciences", "Aragen Life Sciences Ltd", "2026-08-26"),
                          ("sembcorp-green-infra", "Sembcorp Green Infra Ltd", "2026-08-26"),
                          ("atomberg-technologies", "Atomberg Technologies Ltd", "2026-08-20"),
                          ("muthoot-fincorp", "Muthoot Fincorp Ltd", "2026-08-12")]:
        upsert_ipo(db, {"slug": slug, "name": name, "status": "DRHP", "drhp_date": d,
                        "sources": {"stockscans": "https://www.stockscans.in/ipo-scans/filings"}})

    # ---------------- recently listed (IPO price and price on 18-Sep-2026)
    listed = [
        ("maharaja-speedex-india", "Maharaja & Speedex India Ltd", "SME", "2026-09-18", 186, 262.50, "BSE", "SPEEDEX"),
        ("karamtara-engineering", "Karamtara Engineering Ltd", "MAINBOARD", "2026-09-17", 254, 355.55, "NSE", "KARAMTARA"),
        ("rentomojo", "Rentomojo Ltd", "MAINBOARD", "2026-09-17", 404, 512.75, "NSE", "RENTOMOJO"),
        ("glass-wall-systems-india", "Glass Wall Systems (India) Ltd", "MAINBOARD", "2026-09-16", 182, 269.51, "NSE", "GLASSWALL"),
        ("shanti-inorganics", "Shanti Inorganics Ltd", "SME", "2026-09-07", 83, 181.30, "NSE", "SHANTIINOR"),
        ("esds-software-solution", "ESDS Software Solution Ltd", "MAINBOARD", "2026-09-04", 429, 1564.55, "NSE", "ESDS"),
        ("annu-projects", "Annu Projects Ltd", "MAINBOARD", "2026-09-02", 99, 50.46, "NSE", "ANNU"),
        ("tempsens-instruments-india", "Tempsens Instruments (India) Ltd", "MAINBOARD", "2026-08-28", 300, 531.60, "NSE", "TEMPSENS"),
        ("mopshop-distribution", "Mopshop Distribution Ltd", "SME", "2026-08-26", 138, 48.90, "BSE", "MOPSHOP"),
        ("horizon-industrial-parks", "Horizon Industrial Parks Ltd", "MAINBOARD", "2026-08-24", 60, 54.23, "NSE", "HORIZONIND"),
        ("fascinate-textiles", "Fascinate Textiles Ltd", "SME", "2026-08-24", 151, 47.85, "NSE", "FASCINATE"),
    ]
    for slug, name, seg, ld, ip, ltp, ex, sym in listed:
        l = date.fromisoformat(ld)
        upsert_ipo(db, {"slug": slug, "name": name, "segment": seg, "status": "LISTED", "listing_date": ld,
                        "allotment_date": _bdays_before(l, 2).isoformat(), "issue_price": ip, "exchange": ex,
                        "nse_symbol": sym if ex == "NSE" else None, "bse_code": sym if ex == "BSE" else None,
                        "yf_ticker": f"{sym}.{'NS' if ex == 'NSE' else 'BO'}",
                        "sources": {"stockscans": f"https://www.stockscans.in/ipo/{slug}"}})
        db.upsert("returns", {"slug": slug, "issue_price": ip, "ltp": ltp, "ret_vs_issue": ltp / ip - 1,
                              "as_of": "2026-09-18"}, ["slug"])
