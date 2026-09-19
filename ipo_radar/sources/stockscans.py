"""StockScans (stockscans.in) — current / historical / SEBI-filed IPO lists and rich detail pages.

Pages used
  /ipo-scans                    current + upcoming (mainboard and SME)
  /ipo-scans/historical?year=Y  listed IPOs: IPO price, listing gain, current price
  /ipo-scans/filings            DRHPs filed with SEBI
  /ipo/<slug>                   timeline, subscription, anchor book, OFS, shareholding,
                                financials, objects, risks, DRHP link

The site is server-rendered, so parsing works on page *text* with tolerant regexes
(`\\s*` between label and value), which survives most CSS/markup changes.
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from io import StringIO

import pandas as pd
from bs4 import BeautifulSoup

from ..utils import Http, crores, iso, log, num, parse_date, slugify

BASE = "https://www.stockscans.in"
_STATUS = ["Closes today", "Upcoming", "Open", "Closed", "Listed", "Allotment"]


def _text(node) -> str:
    soup = BeautifulSoup(node, "lxml") if isinstance(node, str) else node
    return re.sub(r"\s+", " ", soup.get_text(" ")).strip()


def _infer_year(day_month: str, anchor: date | None = None) -> date | None:
    """'16 Sept' -> nearest sensible date to `anchor` (today by default)."""
    anchor = anchor or date.today()
    d = parse_date(day_month, default_year=anchor.year)
    if d and (d - anchor).days < -200:
        d = d.replace(year=anchor.year + 1)
    elif d and (d - anchor).days > 200:
        d = d.replace(year=anchor.year - 1)
    return d


# ------------------------------------------------------------------ list pages
def parse_current(html: str, today: date | None = None) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    out = []
    for a in soup.select('a[href^="/ipo/"], a[href^="https://www.stockscans.in/ipo/"]'):
        slug = a["href"].rstrip("/").split("/ipo/")[-1]
        t = _text(a)
        if "Price Band" not in t:
            continue
        m_name = re.match(r"(.+?(?:Ltd|Limited))", t)
        name = m_name.group(1).strip() if m_name else slug.replace("-", " ").title()
        rest = t[len(name):] if m_name else t
        status = next((s for s in _STATUS if s in rest[:40]), None)
        row = {
            "slug": slug, "name": name,
            "segment": "SME" if re.match(r"\s*SME\b", rest) else "MAINBOARD",
            "status": {"Closes today": "OPEN"}.get(status, (status or "").upper()) or None,
        }
        m = re.search(r"Price Band\s*₹?\s*([\d,.]+)\s*-\s*₹?\s*([\d,.]+)", t)
        if m:
            row["price_low"], row["price_high"] = num(m[1]), num(m[2])
        m = re.search(r"Issue Size\s*(₹\s*[\d,.]+\s*Cr)", t)
        if m:
            row["issue_size_cr"] = crores(m[1])
        m = re.search(r"IPO Dates\s*(\d{1,2})\s*([A-Za-z]{3,4})?\s*-\s*(\d{1,2})\s*([A-Za-z]{3,4})", t)
        if m:
            close = _infer_year(f"{m[3]} {m[4]}", today)
            open_ = _infer_year(f"{m[1]} {m[2] or m[4]}", today)
            if open_ and close and open_ > close:
                open_ = open_.replace(year=open_.year - 1)
            row["open_date"], row["close_date"] = iso(open_), iso(close)
        m = re.search(r"Subscription\s*([\d.,]+)\s*x", t)
        if m:
            row["subscription"] = {"total": num(m[1])}
        row["sources"] = {"stockscans": f"{BASE}/ipo/{slug}"}
        out.append(row)
    return out


def parse_historical(html: str) -> list[dict]:
    """Listed IPOs. Exchange + symbol come from the logo URL (…/logos/NSE%3ASYMBOL)."""
    soup = BeautifulSoup(html, "lxml")
    out = []
    for tr in soup.select("tr"):
        a = tr.select_one('a[href*="/ipo/"]')
        tds = tr.find_all("td")
        if not a or len(tds) < 6:
            continue
        slug = a["href"].rstrip("/").split("/ipo/")[-1]
        img = tr.select_one("img")
        exch = sym = None
        if img and img.get("src"):
            m = re.search(r"logos/(NSE|BSE)(?:%3A|:)([A-Z0-9&\-]+)", img["src"])
            if m:
                exch, sym = m[1], m[2]
        cells = [_text(td) for td in tds]
        first = cells[0]
        row = {
            "slug": slug, "name": a.get_text(strip=True), "status": "LISTED",
            "segment": "SME" if first.endswith("SME") else "MAINBOARD",
            "listing_date": iso(parse_date(cells[1])),
            "issue_price": num(cells[3]),
            "sources": {"stockscans": f"{BASE}/ipo/{slug}"},
        }
        if exch == "NSE":
            row["nse_symbol"], row["yf_ticker"], row["exchange"] = sym, f"{sym}.NS", "NSE"
        elif exch == "BSE":
            row["bse_code"], row["yf_ticker"], row["exchange"] = sym, f"{sym}.BO", "BSE"
        row["_ltp"] = num(cells[5])          # site's current price (fallback if yfinance fails)
        out.append(row)
    return out


def parse_filings(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    out, seen = [], set()
    for tr in soup.select("tr"):
        tds = tr.find_all("td")
        if len(tds) < 2:
            continue
        name = tds[0].get_text(" ", strip=True)
        d = parse_date(tds[1].get_text(strip=True))
        if not name or not d or name in seen:
            continue
        seen.add(name)
        link = tr.select_one('a[href$=".pdf"], a[href*="drhp"], a[href*="sebi.gov.in"]')
        out.append({"slug": slugify(name), "name": name, "status": "DRHP", "drhp_date": iso(d),
                    "drhp_url": link["href"] if link else None,
                    "sources": {"stockscans": f"{BASE}/ipo-scans/filings"}})
    return out


# ------------------------------------------------------------------ detail page
_FIN_ROWS = {
    "Revenue": "revenue", "Operating Profit": "op_profit", "PBT": "pbt", "PAT": "pat", "EPS": "eps",
    "Equity Capital": "equity_capital", "Reserves": "reserves", "Borrowings": "borrowings",
    "Total Assets": "total_assets", "Operating Cash Flow": "cfo", "Investing Cash Flow": "cfi",
    "Financing Cash Flow": "cff",
}


def _grab(pattern: str, t: str, fn=lambda x: x):
    m = re.search(pattern, t, flags=re.I)
    return fn(m.group(1)) if m else None


def parse_detail_text(t: str, slug: str, today: date | None = None) -> dict:
    """Everything that can be pulled from flattened page text. Kept separate from HTML
    so it can be unit-tested and reused on other sites' text."""
    today = today or date.today()
    t = re.sub(r"\s+", " ", t)
    ipo: dict = {"slug": slug}
    ipo["lot_size"] = _grab(r"Lot\s*(?:Size)?\s*([\d,]+)\s*(?:shares|Min)", t, lambda x: int(num(x)))
    ipo["face_value"] = _grab(r"Face Value\s*₹\s*([\d.]+)", t, num)
    ipo["issue_size_cr"] = _grab(r"Issue Size\s*(₹\s*[\d,.]+\s*Cr)", t, crores)
    ipo["fresh_cr"] = _grab(r"Fresh Issue\s*(₹\s*[\d,.]+\s*Cr)", t, crores)
    ipo["ofs_cr"] = _grab(r"Offer for Sale\s*(₹\s*[\d,.]+\s*Cr)", t, crores)
    m = re.search(r"₹\s*([\d,.]+)\s*-\s*([\d,.]+)", t)
    if m:
        ipo["price_low"], ipo["price_high"] = num(m[1]), num(m[2])
    ipo["lead_managers"] = _grab(r"Lead Manager\s*(.+?)\s*(?:Co-Manager|Registrar)", t)
    ipo["registrar"] = _grab(r"Registrar\s*(.+?)\s*(?:##|Ownership|Offer for Sale|Selling|$)", t)
    m = re.search(r"Promoter(?: Group)?\s*([\d.]+)\s*%\s*([\d.]+)\s*%", t)
    if m:
        ipo["promoter_pre"], ipo["promoter_post"] = float(m[1]), float(m[2])

    # anchor book first: its full date gives us the year for the timeline
    anchor = {"slug": slug}
    bid = _grab(r"Bid Date\s*(\d{1,2}-[A-Za-z]{3}-\d{4})", t, parse_date)
    anchor["bid_date"] = iso(bid)
    anchor["bid_price"] = _grab(r"Bid Price\s*₹\s*([\d,.]+)", t, num)
    anchor["shares"] = _grab(r"Bid Price\s*₹\s*[\d,.]+\s*Shares\s*([\d,]+)", t, lambda x: int(num(x)))
    anchor["amount_cr"] = _grab(r"Shares\s*[\d,]+\s*Amount\s*(₹\s*[\d,.]+\s*Cr)", t, crores)
    anchor["mf_pct"] = _grab(r"Mutual Funds\s*([\d.]+)\s*%", t, num)
    anchor["lockin_30"] = _grab(r"30-day lock-in\s*(\d{1,2}-[A-Za-z]{3}-\d{4})", t, lambda x: iso(parse_date(x)))
    anchor["lockin_90"] = _grab(r"90-day lock-in\s*(\d{1,2}-[A-Za-z]{3}-\d{4})", t, lambda x: iso(parse_date(x)))

    ref = bid or today
    for label, col in [("Bidding opens", "open_date"), ("Bidding closes", "close_date"),
                       ("Allotment", "allotment_date"), ("Listing", "listing_date")]:
        m = re.search(r"(\d{1,2}\s*[A-Za-z]{3,4})\s*" + label, t)
        if m:
            ipo[col] = iso(_infer_year(m[1], ref))

    sub = {}
    for cat in ["QIB", "NII", "Retail", "Total"]:
        v = _grab(cat + r"\s*([\d.]+)\s*x\s*[\d.,]+\s*Cr", t, num)
        if v is not None:
            sub[cat.lower()] = v
    if sub:
        ipo["subscription"] = sub

    # Selling shareholders -> pre-IPO investor map. Tag after the name e.g. PRMTR / INVSTR / OTHR
    sellers = []
    block = _grab(r"Selling Shareholder.*?Amt\s*₹\s*Cr(.*?)Total", t)
    if block:
        for m in re.finditer(r"([A-Z][\w .&,'()/-]+?)\s*(PRMTR|PROMOTER|INVSTR|INVESTOR|OTHR|OTHERS?|SHLDR)"
                             r"\s*([\d,]+)\s*([\d,.]+)", block):
            cat = "PROMOTER" if m[2].startswith("PR") else "PRE_IPO"
            sellers.append({"slug": slug, "investor": m[1].strip(), "category": cat,
                            "shares": int(num(m[3])), "price": None, "deal_date": "OFS"})
    return {"ipo": {k: v for k, v in ipo.items() if v is not None},
            "anchor": anchor if anchor.get("bid_date") else None,
            "sellers": sellers}


def parse_detail(html: str, slug: str, today: date | None = None) -> dict:
    soup = BeautifulSoup(html, "lxml")
    t = _text(soup)
    res = parse_detail_text(t, slug, today)
    h1 = soup.find("h1")
    if h1:
        res["ipo"]["name"] = h1.get_text(strip=True)
    res["ipo"]["segment"] = "SME" if re.search(r"\bSME\b", t[:600]) else "MAINBOARD"

    def _list_after(heading_re):
        h = soup.find(lambda tag: tag.name in ("h2", "h3", "h4") and re.search(heading_re, tag.get_text()))
        lst = h.find_next(["ol", "ul"]) if h else None
        return [li.get_text(" ", strip=True) for li in lst.find_all("li")] if lst else []

    res["ipo"]["objects"] = _list_after(r"Objects of the Issue")
    res["ipo"]["risks"] = _list_after(r"^Risks")
    drhp = soup.find("a", string=re.compile(r"DRHP|Draft prospectus", re.I)) or \
        soup.find("a", href=re.compile(r"drhp|\.pdf", re.I))
    if drhp and drhp.get("href"):
        res["ipo"]["drhp_url"] = drhp["href"]

    # Financial tables: first column is 'RevenueCr', 'PATCr' etc.
    fins: dict[str, dict] = {}
    try:
        for df in pd.read_html(StringIO(html)):
            if df.shape[1] < 2 or "Financial Year" not in str(df.columns[0]):
                continue
            for _, r in df.iterrows():
                label = str(r.iloc[0])
                col = next((v for k, v in _FIN_ROWS.items() if label.startswith(k)), None)
                if not col:
                    continue
                for fy in df.columns[1:]:
                    fins.setdefault(str(fy), {"slug": slug, "fy": str(fy)})[col] = num(r[fy])
    except ValueError:
        pass
    res["financials"] = list(fins.values())
    return res


# ------------------------------------------------------------------ fetchers
class StockScans:
    def __init__(self, http: Http):
        self.http = http

    def current(self) -> list[dict]:
        r = self.http.get(f"{BASE}/ipo-scans")
        return parse_current(r.text) if r else []

    def historical(self, year: int | None = None) -> list[dict]:
        r = self.http.get(f"{BASE}/ipo-scans/historical", params={"year": year} if year else None)
        return parse_historical(r.text) if r else []

    def filings(self) -> list[dict]:
        r = self.http.get(f"{BASE}/ipo-scans/filings")
        return parse_filings(r.text) if r else []

    def detail(self, slug: str) -> dict | None:
        r = self.http.get(f"{BASE}/ipo/{slug}")
        if not r:
            return None
        try:
            return parse_detail(r.text, slug)
        except Exception as e:  # never let one bad page kill the sync
            log.exception("stockscans detail %s: %s", slug, e)
            return None
