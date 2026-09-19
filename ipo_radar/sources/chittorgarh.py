"""Chittorgarh.com — the deepest free source for Indian IPO data, especially SME.

Used for:
  * mainboard + SME IPO lists (cross-check/fill gaps from StockScans)
  * anchor investor names per IPO (StockScans shows only the anchor totals)
  * the year-wise "anchor investor-wise" league table, which we use to learn which
    names show up in dozens of SME books (a low-signal anchor)

Report pages are JS-rendered, so these go through `rendered.render` (Playwright).
URLs live in config.yaml so you can fix them without touching code if the site moves.
"""
from __future__ import annotations

from ..utils import Http, crores, iso, log, num, parse_date, slugify
from .rendered import links, pick, render, tables

DEFAULT_URLS = {
    "mainboard_list": "https://www.chittorgarh.com/report/mainboard-ipo-list-in-india-bse-nse/83/",
    "sme_list": "https://www.chittorgarh.com/report/sme-ipo-list-in-india-bse-sme-nse-emerge/84/",
    "anchor_league_mainboard": "https://www.chittorgarh.com/report/anchor-investors-list/133/mainboard/",
    "anchor_league_sme": "https://www.chittorgarh.com/report/anchor-investors-list/133/sme/",
}


class Chittorgarh:
    def __init__(self, http: Http, urls: dict | None = None):
        self.http = http
        self.urls = {**DEFAULT_URLS, **(urls or {})}

    def ipo_list(self, segment: str = "MAINBOARD") -> list[dict]:
        url = self.urls["sme_list" if segment == "SME" else "mainboard_list"]
        html = render(url, self.http)
        if not html:
            return []
        detail_links = links(html, r"/ipo/")
        out = []
        for df in tables(html):
            c_name = pick(df, r"^(company|issuer|ipo)")
            if not c_name:
                continue
            c_open, c_close = pick(df, r"open"), pick(df, r"clos")
            c_list = pick(df, r"listing date|list")
            c_price = pick(df, r"issue price|price")
            c_size = pick(df, r"size")
            c_exch = pick(df, r"exchange")
            for _, r in df.iterrows():
                name = str(r[c_name]).replace(" IPO", "").strip()
                if not name or name == "nan":
                    continue
                row = {"slug": slugify(name), "name": name, "segment": segment}
                if c_open:
                    row["open_date"] = iso(parse_date(r[c_open]))
                if c_close:
                    row["close_date"] = iso(parse_date(r[c_close]))
                if c_list:
                    row["listing_date"] = iso(parse_date(r[c_list]))
                if c_price:
                    p = str(r[c_price])
                    if "-" in p or " to " in p:
                        lo, hi = [num(x) for x in p.replace(" to ", "-").split("-")[:2]]
                        row["price_low"], row["price_high"] = lo, hi
                    else:
                        row["issue_price"] = num(p)
                if c_size:
                    row["issue_size_cr"] = crores(r[c_size])
                if c_exch:
                    row["exchange"] = str(r[c_exch])
                href = next((h for t, h in detail_links.items() if name.lower() in t.lower()), None)
                row["sources"] = {"chittorgarh": href or url}
                out.append(row)
        return out

    def anchor_investors(self, detail_url: str, slug: str) -> list[dict]:
        """Anchor allocation table from an IPO page: investor | shares | amount."""
        html = render(detail_url, self.http)
        if not html:
            return []
        out = []
        for df in tables(html):
            c_inv = pick(df, r"anchor investor|investor name|name of")
            c_amt = pick(df, r"amount|value|allocat.*rs")
            if not c_inv or not c_amt:
                continue
            c_sh = pick(df, r"shares")
            c_pct = pick(df, r"%|percent")
            for _, r in df.iterrows():
                inv = str(r[c_inv]).strip()
                if not inv or inv.lower() in ("total", "nan"):
                    continue
                out.append({"slug": slug, "investor": inv,
                            "shares": int(num(r[c_sh]) or 0) if c_sh else None,
                            "amount_cr": crores(r[c_amt]),
                            "pct": num(r[c_pct]) if c_pct else None})
        return out

    def anchor_league(self, segment: str = "SME") -> list[dict]:
        """[{investor, n_ipos, amount_cr}] — how often each anchor appears this year."""
        key = "anchor_league_sme" if segment == "SME" else "anchor_league_mainboard"
        html = render(self.urls[key], self.http)
        out = []
        for df in tables(html or ""):
            c_inv, c_n = pick(df, r"investor"), pick(df, r"no\.? of|ipos")
            if not c_inv or not c_n:
                continue
            c_amt = pick(df, r"amount|invest|total")
            for _, r in df.iterrows():
                out.append({"investor": str(r[c_inv]).strip(), "n_ipos": int(num(r[c_n]) or 0),
                            "amount_cr": crores(r[c_amt]) if c_amt else None})
        if not out:
            log.info("chittorgarh anchor league empty (JS render needed? install playwright)")
        return out


class Ipoji:
    """IPOJI (ipoji.com). Layout changes often; we use generic table sniffing on the
    configured pages and treat it as a fill-in source for SME dates/GMP."""

    DEFAULT_URLS = {
        "mainboard": "https://www.ipoji.com/ipo/mainboard",
        "sme": "https://www.ipoji.com/ipo/sme",
    }

    def __init__(self, http: Http, urls: dict | None = None):
        self.http = http
        self.urls = {**self.DEFAULT_URLS, **(urls or {})}

    def ipo_list(self, segment: str = "MAINBOARD") -> list[dict]:
        url = self.urls["sme" if segment == "SME" else "mainboard"]
        html = render(url, self.http, wait_selector="table, a[href*='/ipo/']")
        out = []
        for df in tables(html or ""):
            c_name = pick(df, r"company|ipo|name")
            if not c_name:
                continue
            c_date = pick(df, r"date|open")
            c_gmp = pick(df, r"gmp")
            for _, r in df.iterrows():
                name = str(r[c_name]).replace(" IPO", "").strip()
                if not name or name == "nan":
                    continue
                row = {"slug": slugify(name), "name": name, "segment": segment,
                       "sources": {"ipoji": url}}
                if c_date:
                    row["open_date"] = iso(parse_date(str(r[c_date]).split("-")[0].strip()))
                if c_gmp:
                    row["_gmp"] = num(r[c_gmp])
                out.append(row)
        return out
