"""Primary sources: SEBI (DRHP / RHP filings) and NSE (live issue calendar incl. SME).

These are the ground truth. Aggregator sites lag SEBI by hours to days, so DRHP/RHP
alerts come from here first.

Note: mainboard DRHPs are filed with SEBI. SME DRHPs are filed with the exchange
(NSE Emerge / BSE SME) instead, so SME pipeline alerts come from the aggregators.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from bs4 import BeautifulSoup

from ..utils import Http, iso, log, num, parse_date, slugify

SEBI = "https://www.sebi.gov.in"
SEBI_LISTINGS = {
    # Filings > Public Issues > ...
    "DRHP": f"{SEBI}/sebiweb/home/HomeAction.do?doListing=yes&sid=3&ssid=15&smid=10",
    "RHP": f"{SEBI}/sebiweb/home/HomeAction.do?doListing=yes&sid=3&ssid=15&smid=11",
}
SEBI_RSS = f"{SEBI}/sebirss.xml"

_DOC_RE = re.compile(r"\s*[-–]\s*(DRHP|RHP|Addendum.*|Prospectus|Draft.*)$", re.I)


def _doc_kind(title: str) -> str | None:
    t = title.upper()
    if "ADDENDUM" in t:
        return "ADDENDUM"
    if "DRHP" in t or "DRAFT" in t:
        return "DRHP"
    if "RHP" in t or "RED HERRING" in t:
        return "RHP"
    return None


def parse_sebi_listing(html: str, kind: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    out = []
    for tr in soup.select("tr"):
        tds = tr.find_all("td")
        a = tr.find("a", href=True)
        if len(tds) < 2 or not a:
            continue
        d = parse_date(tds[0].get_text(strip=True))
        title = a.get("title") or a.get_text(" ", strip=True)
        if not d or not title:
            continue
        name = _DOC_RE.sub("", title).strip()
        href = a["href"] if a["href"].startswith("http") else SEBI + a["href"]
        row = {"slug": slugify(name), "name": name, "segment": "MAINBOARD",
               "sources": {"sebi": href}}
        if kind == "DRHP":
            row.update(status="DRHP", drhp_date=iso(d), drhp_url=href)
        else:
            row.update(status="RHP", rhp_date=iso(d), rhp_url=href)
        out.append(row)
    return out


def parse_sebi_rss(xml_text: str) -> list[dict]:
    out = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return out
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        kind = _doc_kind(title)
        if kind not in ("DRHP", "RHP"):
            continue
        link = item.findtext("link")
        d = parse_date(item.findtext("pubDate"))
        name = _DOC_RE.sub("", title).strip()
        row = {"slug": slugify(name), "name": name, "segment": "MAINBOARD", "sources": {"sebi": link}}
        if kind == "DRHP":
            row.update(status="DRHP", drhp_date=iso(d), drhp_url=link)
        else:
            row.update(status="RHP", rhp_date=iso(d), rhp_url=link)
        out.append(row)
    return out


class Sebi:
    def __init__(self, http: Http):
        self.http = http

    def filings(self) -> list[dict]:
        rows: dict[str, dict] = {}
        r = self.http.get(SEBI_RSS, use_cache=False)
        if r:
            for row in parse_sebi_rss(r.text):
                rows.setdefault(row["slug"] + row["status"], row)
        for kind, url in SEBI_LISTINGS.items():
            r = self.http.get(url, use_cache=False)
            if r:
                for row in parse_sebi_listing(r.text, kind):
                    rows.setdefault(row["slug"] + row["status"], row)
        log.info("SEBI: %d DRHP/RHP filings", len(rows))
        return list(rows.values())


class Nse:
    """NSE's JSON API used by its own website. Needs a cookie from the homepage first."""
    HOME = "https://www.nseindia.com"
    ENDPOINTS = {
        "current": "/api/ipo-current-issue",
        "upcoming": "/api/all-upcoming-issues?category=ipo",
    }

    def __init__(self, http: Http):
        self.http = http
        self._primed = False

    def _prime(self):
        if not self._primed:
            self.http.get(self.HOME, use_cache=False)
            self._primed = True

    def _get(self, path: str):
        self._prime()
        r = self.http.get(self.HOME + path, use_cache=False,
                          headers={"Referer": self.HOME + "/market-data/all-upcoming-issues-ipo",
                                   "Accept": "application/json"})
        try:
            return r.json() if r else None
        except ValueError:
            return None

    def issues(self) -> list[dict]:
        out = []
        for key in ("current", "upcoming"):
            data = self._get(self.ENDPOINTS[key]) or []
            if isinstance(data, dict):
                data = data.get("data", [])
            for d in data:
                name = d.get("companyName") or d.get("company") or d.get("symbol")
                if not name:
                    continue
                series = str(d.get("series", "")).upper()
                price = str(d.get("issuePrice") or d.get("priceBand") or "")
                band = [num(x) for x in re.split(r"\s*(?:-|to)\s*", price) if num(x)]
                row = {
                    "slug": slugify(name), "name": name, "nse_symbol": d.get("symbol"),
                    "segment": "SME" if series in ("SME", "ST", "SM") or d.get("isSme") else "MAINBOARD",
                    "open_date": iso(parse_date(d.get("issueStartDate"))),
                    "close_date": iso(parse_date(d.get("issueEndDate"))),
                    "status": "OPEN" if key == "current" else "UPCOMING",
                    "sources": {"nse": self.HOME + "/market-data/all-upcoming-issues-ipo"},
                }
                if band:
                    row["price_low"], row["price_high"] = band[0], band[-1]
                if d.get("noOfTime"):
                    row["subscription"] = {"total": num(d["noOfTime"])}
                out.append(row)
        return out
