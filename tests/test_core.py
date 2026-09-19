from datetime import date

import pandas as pd
import pytest

from ipo_radar import alerts, lockin
from ipo_radar.db import DB
from ipo_radar.returns import compute
from ipo_radar.scoring import AnchorTiers, anchor_quality, company_quality, pre_ipo_markups
from ipo_radar.sources.regulators import parse_sebi_rss
from ipo_radar.sources.stockscans import parse_current, parse_detail_text, parse_historical

# Flattened text of stockscans.in/ipo/hero-motors (18-Sep-2026), as get_text() produces it
DETAIL = ("Hero Motors Ltd Auto AncillariesIPO ₹79 - 84 Lot178Min Invest₹14,952Face Value₹10"
          "Issue Size₹1,000 CrFresh Issue₹600 CrOffer for Sale₹400 Cr Timeline 16 Sept Bidding opens "
          "18 Sept Bidding closes 21 Sept Allotment 22 Sept Refund & demat credit 23 Sept Listing "
          "Shares & amount CategorySubscriptionShares OfferedShares BidAmt ₹ CrQIB1.49x5.60 Cr8.35 Cr701.27"
          "NII9.86x1.68 Cr16.55 Cr1,390.40Retail8.23x3.92 Cr32.27 Cr2,710.59Total6.66x11.90 Cr79.29 Cr6,660.00 "
          "Lead ManagerICICI Securities LtdCo-ManagerDAM Capital Advisors LtdRegistrarKFin Technologies Ltd "
          "## Ownership & Proceeds Offer for Sale Selling ShareholderShares OfferedAmt ₹ CrO P Munjal Holdings"
          "PRMTR4,70,23,809395.00Hero Cycles LimitedPRMTR5,95,2385.00Total4,76,19,047400.00 "
          "Shareholding CategoryPre IPOPost IPOPromoter Group84.65%61.65%Public15.35%38.35% "
          "Anchor Book Bid Date15-Sep-2026Bid Price₹84Shares3,57,14,284Amount₹300 CrMutual Funds81.66% of "
          "the book30-day lock-in20-Oct-202690-day lock-in19-Dec-2026")


@pytest.mark.parametrize("text", [DETAIL, DETAIL.replace("₹", " ₹ ").replace("%", " % ")])
def test_detail_parser(text):
    r = parse_detail_text(text, "hero-motors", today=date(2026, 9, 18))
    i, a = r["ipo"], r["anchor"]
    assert i["issue_size_cr"] == 1000 and i["fresh_cr"] == 600 and i["ofs_cr"] == 400
    assert i["lot_size"] == 178 and i["face_value"] == 10
    assert (i["price_low"], i["price_high"]) == (79, 84)
    assert i["open_date"] == "2026-09-16" and i["allotment_date"] == "2026-09-21"
    assert i["listing_date"] == "2026-09-23"
    assert (i["promoter_pre"], i["promoter_post"]) == (84.65, 61.65)
    assert i["subscription"] == {"qib": 1.49, "nii": 9.86, "retail": 8.23, "total": 6.66}
    assert a["shares"] == 35714284 and a["amount_cr"] == 300 and a["mf_pct"] == 81.66
    assert a["lockin_30"] == "2026-10-20" and a["lockin_90"] == "2026-12-19"
    assert [s["investor"] for s in r["sellers"]] == ["O P Munjal Holdings", "Hero Cycles Limited"]


def test_list_parser():
    html = """<a href="/ipo/kheria-autocomp"><div><span>Kheria Autocomp Ltd</span><span>SME</span>
      <span>Open</span></div><div>Price Band<b>₹96 - 101</b></div><div>Issue Size<b>₹46 Cr</b></div>
      <div>IPO Dates<b>17 - 21 Sept</b></div><div>Subscription<b>0.19x</b></div></a>
      <a href="/ipo/a-one-steels-india">A-One Steels India Ltd Upcoming Price Band ₹385 - 405 Issue Size ₹405 Cr
      IPO Dates 28 Sept - 2 Oct</a>"""
    rows = parse_current(html, today=date(2026, 9, 18))
    k, s = rows
    assert k["segment"] == "SME" and k["status"] == "OPEN" and k["price_high"] == 101
    assert k["open_date"] == "2026-09-17" and k["close_date"] == "2026-09-21"
    assert k["subscription"]["total"] == 0.19
    assert s["segment"] == "MAINBOARD" and s["open_date"] == "2026-09-28" and s["close_date"] == "2026-10-02"


def test_historical_parser():
    html = """<table><tr><th>Company</th></tr><tr>
      <td><img src="https://www.stockscans.in/api/company/logos/NSE%3AESDS"><a href="/ipo/esds-software-solution">
      ESDS Software Solution Ltd</a></td><td>4 Sep 2026</td><td>₹5,028 Cr</td><td>₹429</td><td>111.75%</td>
      <td>₹1,564.55</td><td>264.70%</td><td></td></tr></table>"""
    (r,) = parse_historical(html)
    assert r["yf_ticker"] == "ESDS.NS" and r["issue_price"] == 429 and r["listing_date"] == "2026-09-04"
    assert r["_ltp"] == 1564.55


def test_lockin_mainboard_matches_published_dates():
    ipo = {"slug": "x", "segment": "MAINBOARD", "allotment_date": "2026-09-21", "promoter_post": 61.65}
    rows = {r["bucket"]: r["unlock_date"] for r in lockin.unlock_schedule(ipo)}
    assert rows["Anchor 50% (30-day)"] == "2026-10-20"
    assert rows["Anchor 50% (90-day)"] == "2026-12-19"
    assert rows["Pre-IPO investors (non-promoter)"] == "2027-03-20"
    assert rows["Promoter minimum contribution"] == "2028-03-20"


def test_lockin_sme_staggered_and_capex():
    ipo = {"slug": "s", "segment": "SME", "allotment_date": "2026-01-05", "promoter_post": 60}
    rows = {r["bucket"]: r for r in lockin.unlock_schedule(ipo)}
    assert rows["Pre-IPO investors (non-promoter)"]["unlock_date"] == "2027-01-04"
    assert rows["Promoter excess 50% (1-year)"]["pct_equity"] == 20
    assert rows["Promoter excess 50% (2-year)"]["unlock_date"] == "2028-01-04"
    assert rows["Promoter minimum contribution (3-year)"]["unlock_date"] == "2029-01-04"
    mb = {"slug": "m", "segment": "MAINBOARD", "allotment_date": "2026-01-05", "promoter_post": 60, "capex_heavy": 1}
    r2 = {r["bucket"]: r["unlock_date"] for r in lockin.unlock_schedule(mb)}
    assert r2["Promoter minimum contribution"] == "2029-01-04" and r2["Promoter excess"] == "2027-01-04"


def test_lockin_estimates_allotment_from_close():
    rows = lockin.unlock_schedule({"slug": "e", "segment": "MAINBOARD", "close_date": "2026-09-18"})
    assert rows[0]["unlock_date"] == "2026-10-20" and "estimated" in rows[0]["note"]  # Fri close -> Mon allot


def test_anchor_quality_tiers():
    t = AnchorTiers("data/anchor_tiers.yaml")
    good = [{"investor": n, "amount_cr": 20} for n in [
        "SBI Mutual Fund - Small Cap", "HDFC Mutual Fund", "Government of Singapore", "Nomura India Fund",
        "ICICI Prudential Life Insurance", "Kotak Mahindra Mutual Fund"]]
    bad = [{"investor": n, "amount_cr": 2} for n in [
        "Rajasthan Global Securities Pvt Ltd", "Saint Capital Fund", "NAV Capital VCC", "Finavenue Growth Fund"]]
    g = anchor_quality({"segment": "MAINBOARD"}, None, good, t)
    b = anchor_quality({"segment": "SME"}, None, bad, t)
    assert g["grade"] == "A" and b["score"] <= 40 and b["mix"]["sme_frequent"] == 1


def test_quality_and_premarkups():
    ipo = {"segment": "MAINBOARD", "issue_size_cr": 500, "fresh_cr": 500, "ofs_cr": 0, "price_high": 100,
           "face_value": 10, "promoter_post": 60, "open_date": "2026-09-01",
           "objects": ["Repayment of borrowings"]}
    fins = [{"fy": "FY24", "revenue": 500, "pat": 50, "equity_capital": 50, "reserves": 150, "borrowings": 20, "cfo": 45},
            {"fy": "FY25", "revenue": 650, "pat": 70, "equity_capital": 50, "reserves": 220, "borrowings": 20, "cfo": 60},
            {"fy": "FY26", "revenue": 850, "pat": 95, "equity_capital": 50, "reserves": 315, "borrowings": 10, "cfo": 80}]
    pre = [{"investor": "XYZ Fund", "category": "PRE_IPO", "price": 40, "deal_date": "2026-01-15"}]
    q = company_quality(ipo, fins, pre_ipo=pre)
    assert q["grade"] in ("A", "B") and any("XYZ Fund" in f for f in q["flags"])
    m = pre_ipo_markups(ipo, pre)[0]
    assert m["multiple"] == 2.5 and 7 < m["months_before"] < 8


def test_returns_and_unlock_impact():
    idx = pd.bdate_range("2026-01-01", periods=60)
    px = pd.DataFrame({"Open": 120.0, "Close": [120 + i for i in range(60)]}, index=idx)
    bench = pd.DataFrame({"Close": [1000 + i for i in range(60)]}, index=idx)
    r = compute({"slug": "t", "issue_price": 100}, px, bench,
                [{"bucket": "Anchor 50% (30-day)", "unlock_date": idx[30].date().isoformat()}])
    assert r["listing_gain"] == pytest.approx(0.2) and r["ret_vs_issue"] == pytest.approx(0.79)
    assert r["cagr_since_listing"] is None and r["alpha"] < r["ret_vs_issue"]
    assert r["unlock_impact"][0]["stock"] > 0


def test_sebi_rss():
    xml = """<rss><channel><item><title>Acme Widgets Limited - DRHP</title><link>https://sebi.gov.in/a.html</link>
    <pubDate>Tue, 15 Sep 2026 10:00:00 +0530</pubDate></item><item><title>Circular on X</title></item>
    <item><title>Beta Ltd - RHP</title><link>https://sebi.gov.in/b.html</link><pubDate>Wed, 16 Sep 2026 10:00:00 +0530</pubDate></item>
    </channel></rss>"""
    rows = parse_sebi_rss(xml)
    assert [(r["slug"], r["status"]) for r in rows] == [("acme-widgets", "DRHP"), ("beta", "RHP")]
    assert rows[0]["drhp_date"] == "2026-09-15"


def test_alert_dedupe(tmp_path):
    db = DB(tmp_path / "t.db")
    db.upsert("ipos", {"slug": "a", "name": "A Ltd", "segment": "SME", "status": "DRHP",
                       "drhp_date": "2026-09-17"}, ["slug"])
    cfg = {"channels": {"console": False}}
    assert len(alerts.run(db, cfg, date(2026, 9, 18))) == 1
    assert len(alerts.run(db, cfg, date(2026, 9, 18))) == 0
