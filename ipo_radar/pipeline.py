"""Sync job: pull every source, merge by slug, compute scores/unlocks/returns."""
from __future__ import annotations

from datetime import date, timedelta

from . import lockin
from .db import DB
from .returns import Prices
from .scoring import AnchorTiers, anchor_quality, company_quality
from .sources.chittorgarh import Chittorgarh, Ipoji
from .sources.regulators import Nse, Sebi
from .sources.stockscans import StockScans
from .sources.youtube import YouTube
from .utils import Http, log, parse_date

STATUS_RANK = {"DRHP": 0, "RHP": 1, "UPCOMING": 2, "OPEN": 3, "CLOSED": 4, "ALLOTMENT": 4, "LISTED": 5}


def upsert_ipo(db: DB, row: dict) -> None:
    row = {k: v for k, v in row.items() if not k.startswith("_")}
    old = db.ipo(row["slug"])
    if old:
        # never move status backwards (e.g. a stale DRHP list re-reporting a listed company)
        if STATUS_RANK.get(old.get("status") or "", -1) > STATUS_RANK.get(row.get("status") or "", -1):
            row.pop("status", None)
        if old.get("sources") and row.get("sources"):
            row["sources"] = {**old["sources"], **row["sources"]}
    db.upsert("ipos", row, ["slug"])


class Pipeline:
    def __init__(self, db: DB, cfg: dict):
        self.db, self.cfg = db, cfg
        s = cfg.get("sources", {})
        self.http = Http(min_interval=s.get("min_interval_sec", 1.5), cache_dir=s.get("cache_dir"),
                         cache_ttl=s.get("cache_ttl_sec", 600))
        self.ss = StockScans(self.http)
        self.sebi = Sebi(self.http)
        self.nse = Nse(self.http)
        self.cg = Chittorgarh(self.http, s.get("chittorgarh_urls"))
        self.ipoji = Ipoji(self.http, s.get("ipoji_urls"))
        self.tiers = AnchorTiers(cfg.get("anchor_tiers_file", "data/anchor_tiers.yaml"))
        yt = cfg.get("youtube", {})
        self.yt = YouTube(self.http, yt.get("api_key"), cfg.get("anthropic_api_key"),
                          yt.get("llm_model", "claude-sonnet-5"), yt.get("max_per_query", 5))
        self.prices = Prices(cfg.get("returns", {}).get("benchmark", "^CRSLDX"))

    def _enabled(self, name):
        return self.cfg.get("sources", {}).get("enabled", {}).get(name, True)

    def _safe(self, label, fn, *a):
        try:
            return fn(*a) or []
        except Exception as e:
            log.exception("%s failed: %s", label, e)
            return []

    # ------------------------------------------------------------------ steps
    def sync_lists(self):
        rows = []
        if self._enabled("sebi"):
            rows += self._safe("sebi", self.sebi.filings)
        if self._enabled("stockscans"):
            rows += self._safe("stockscans filings", self.ss.filings)
            rows += self._safe("stockscans current", self.ss.current)
            for y in self.cfg.get("returns", {}).get("years", [date.today().year]):
                rows += self._safe(f"stockscans historical {y}", self.ss.historical, y)
        if self._enabled("nse"):
            rows += self._safe("nse", self.nse.issues)
        if self._enabled("chittorgarh"):
            for seg in ("MAINBOARD", "SME"):
                rows += self._safe(f"chittorgarh {seg}", self.cg.ipo_list, seg)
            league = self._safe("chittorgarh anchor league", self.cg.anchor_league, "SME")
            self.tiers.learn_frequent(league, self.cfg.get("scoring", {}).get("sme_frequent_min_ipos", 15))
        if self._enabled("ipoji"):
            for seg in ("MAINBOARD", "SME"):
                rows += self._safe(f"ipoji {seg}", self.ipoji.ipo_list, seg)
        for r in rows:
            upsert_ipo(self.db, r)
            # site-reported price = fallback return when yfinance has no data (common for SME)
            if r.get("_ltp") and r.get("issue_price"):
                self.db.upsert("returns", {"slug": r["slug"], "issue_price": r["issue_price"], "ltp": r["_ltp"],
                                           "ret_vs_issue": r["_ltp"] / r["issue_price"] - 1,
                                           "as_of": date.today().isoformat()}, ["slug"])
        log.info("lists: %d rows merged", len(rows))

    def sync_details(self):
        horizon = (date.today() - timedelta(days=self.cfg.get("detail_days_after_listing", 45))).isoformat()
        todo = self.db.query(
            "SELECT * FROM ipos WHERE status IN ('RHP','UPCOMING','OPEN','CLOSED','ALLOTMENT') "
            "OR (status='LISTED' AND listing_date >= ?)", [horizon])
        for i in todo:
            if "stockscans" not in (i.get("sources") or {}) or not self._enabled("stockscans"):
                continue
            d = self.ss.detail(i["slug"])
            if not d:
                continue
            upsert_ipo(self.db, d["ipo"])
            for f in d["financials"]:
                self.db.upsert("financials", f, ["slug", "fy"])
            if d["anchor"]:
                self.db.upsert("anchor_book", d["anchor"], ["slug"])
            for s in d["sellers"]:
                self.db.upsert("pre_ipo_investors", s, ["slug", "investor", "deal_date"])
            cg_url = (i.get("sources") or {}).get("chittorgarh")
            if d["anchor"] and cg_url and "/ipo/" in cg_url and self._enabled("chittorgarh"):
                for a in self._safe("anchor names", self.cg.anchor_investors, cg_url, i["slug"]):
                    self.db.upsert("anchor_investors", a, ["slug", "investor"])

    def compute(self):
        wts = self.cfg.get("scoring", {}).get("weights")
        cutoff = (date.today() - timedelta(days=365 * 3 + 30)).isoformat()
        for i in self.db.query("SELECT * FROM ipos WHERE status!='DRHP' OR status IS NULL"):
            fins = self.db.financials(i["slug"])
            pre = self.db.query("SELECT * FROM pre_ipo_investors WHERE slug=?", [i["slug"]])
            book = self.db.one("SELECT * FROM anchor_book WHERE slug=?", [i["slug"]])
            inv = self.db.anchors(i["slug"])
            q = company_quality(i, fins, wts, pre)
            a = anchor_quality(i, book, inv, self.tiers)
            if q["score"] is not None or a["score"] is not None:
                self.db.upsert("scores", {"slug": i["slug"], "quality": q["score"], "quality_grade": q["grade"],
                                          "anchor": a["score"], "anchor_grade": a["grade"],
                                          "detail": {"quality": q, "anchor": a}}, ["slug"])
            if (i.get("close_date") or i.get("allotment_date") or "") >= cutoff:
                for u in lockin.unlock_schedule(i, book, fins, pre):
                    self.db.upsert("unlocks", u, ["slug", "bucket", "unlock_date"])

    def sync_returns(self):
        years = self.cfg.get("returns", {}).get("years", [date.today().year])
        start = f"{min(years)}-01-01"
        for i in self.db.query("SELECT * FROM ipos WHERE status='LISTED' AND listing_date >= ?", [start]):
            unlocks = self.db.query("SELECT * FROM unlocks WHERE slug=?", [i["slug"]])
            r = self._safe(f"returns {i['slug']}", self.prices.returns_for, i, unlocks)
            if r:
                self.db.upsert("returns", r, ["slug"])

    def sync_videos(self):
        n = self.cfg.get("youtube", {}).get("ipos_per_run", 8)
        if not self.cfg.get("youtube", {}).get("enabled", True):
            return
        todo = self.db.query(
            "SELECT * FROM ipos WHERE status IN ('RHP','UPCOMING','OPEN') AND slug NOT IN "
            "(SELECT DISTINCT slug FROM videos) ORDER BY open_date LIMIT ?", [n])
        for i in todo:
            for v in self._safe(f"youtube {i['slug']}", self.yt.collect, i["slug"], i["name"]):
                self.db.upsert("videos", v, ["video_id"])

    def run(self, steps=("lists", "details", "compute", "returns", "videos")):
        for s in steps:
            log.info("── %s", s)
            {"lists": self.sync_lists, "details": self.sync_details, "compute": self.compute,
             "returns": self.sync_returns, "videos": self.sync_videos}[s]()
