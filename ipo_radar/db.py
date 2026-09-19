"""SQLite storage. One file, no server. Every table is keyed by a stable `slug`."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS ipos (
    slug TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    segment TEXT,               -- MAINBOARD | SME
    exchange TEXT,              -- NSE | BSE | NSE,BSE | NSE EMERGE | BSE SME
    status TEXT,                -- DRHP | RHP | UPCOMING | OPEN | CLOSED | LISTED | WITHDRAWN
    sector TEXT,
    price_low REAL, price_high REAL, issue_price REAL,
    issue_size_cr REAL, fresh_cr REAL, ofs_cr REAL,
    lot_size INTEGER, face_value REAL,
    open_date TEXT, close_date TEXT, allotment_date TEXT, listing_date TEXT,
    drhp_date TEXT, rhp_date TEXT, drhp_url TEXT, rhp_url TEXT,
    nse_symbol TEXT, bse_code TEXT, yf_ticker TEXT,
    promoter_pre REAL, promoter_post REAL,
    lead_managers TEXT, registrar TEXT,
    objects TEXT,               -- JSON list
    risks TEXT,                 -- JSON list
    capex_heavy INTEGER DEFAULT 0,  -- >50% of fresh issue funds capex -> longer promoter lock-in
    subscription TEXT,          -- JSON {qib, nii, retail, total}
    sources TEXT,               -- JSON {source: url}
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS financials (
    slug TEXT, fy TEXT,
    revenue REAL, op_profit REAL, pbt REAL, pat REAL, eps REAL,
    equity_capital REAL, reserves REAL, borrowings REAL, total_assets REAL,
    cfo REAL, cfi REAL, cff REAL,
    PRIMARY KEY (slug, fy)
);

CREATE TABLE IF NOT EXISTS anchor_book (
    slug TEXT PRIMARY KEY,
    bid_date TEXT, bid_price REAL, shares INTEGER, amount_cr REAL,
    mf_pct REAL, lockin_30 TEXT, lockin_90 TEXT
);

CREATE TABLE IF NOT EXISTS anchor_investors (
    slug TEXT, investor TEXT, shares INTEGER, amount_cr REAL, pct REAL,
    PRIMARY KEY (slug, investor)
);

CREATE TABLE IF NOT EXISTS pre_ipo_investors (
    slug TEXT, investor TEXT, category TEXT,   -- PROMOTER | PRE_IPO | ESOP | PE_VC
    shares INTEGER, price REAL, deal_date TEXT, pct_post REAL,
    PRIMARY KEY (slug, investor, deal_date)
);

CREATE TABLE IF NOT EXISTS unlocks (
    slug TEXT, bucket TEXT, unlock_date TEXT, pct_equity REAL, shares INTEGER, note TEXT,
    PRIMARY KEY (slug, bucket, unlock_date)
);

CREATE TABLE IF NOT EXISTS scores (
    slug TEXT PRIMARY KEY, quality REAL, quality_grade TEXT,
    anchor REAL, anchor_grade TEXT, detail TEXT, updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS returns (
    slug TEXT PRIMARY KEY, issue_price REAL, listing_open REAL, listing_close REAL,
    ltp REAL, ret_vs_issue REAL, ret_vs_listing_open REAL, ret_vs_listing_close REAL,
    listing_gain REAL, cagr_since_listing REAL, benchmark_ret REAL, alpha REAL,
    max_drawdown REAL, unlock_impact TEXT, as_of TEXT
);

CREATE TABLE IF NOT EXISTS videos (
    video_id TEXT PRIMARY KEY, slug TEXT, title TEXT, channel TEXT, published TEXT,
    kind TEXT, url TEXT, transcript_flags TEXT, summary TEXT
);

CREATE TABLE IF NOT EXISTS alerts_sent (
    key TEXT PRIMARY KEY, slug TEXT, kind TEXT, message TEXT,
    sent_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""

JSON_COLS = {"objects", "risks", "subscription", "sources", "detail", "transcript_flags", "unlock_impact"}


class DB:
    def __init__(self, path: str | Path = "data/ipo_radar.db"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def conn(self):
        c = sqlite3.connect(self.path)
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        finally:
            c.close()

    # ---- generic helpers -------------------------------------------------
    def upsert(self, table: str, row: dict[str, Any], keys: Iterable[str]) -> None:
        """Insert or merge. None values never overwrite existing data, so partial
        scrapes from different sources can be layered safely."""
        row = {k: (json.dumps(v) if k in JSON_COLS and not isinstance(v, (str, type(None))) else v)
               for k, v in row.items()}
        keys = list(keys)
        cols = list(row)
        placeholders = ",".join("?" for _ in cols)
        updates = ",".join(f"{c}=COALESCE(excluded.{c}, {table}.{c})" for c in cols if c not in keys)
        sql = f"INSERT INTO {table} ({','.join(cols)}) VALUES ({placeholders})"
        sql += f" ON CONFLICT({','.join(keys)}) DO " + (f"UPDATE SET {updates}" if updates else "NOTHING")
        with self.conn() as c:
            c.execute(sql, [row[k] for k in cols])

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[dict]:
        with self.conn() as c:
            rows = [dict(r) for r in c.execute(sql, list(params))]
        for r in rows:
            for k in list(r):
                if k in JSON_COLS and isinstance(r[k], str):
                    try:
                        r[k] = json.loads(r[k])
                    except json.JSONDecodeError:
                        pass
        return rows

    def one(self, sql: str, params: Iterable[Any] = ()) -> dict | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def execute(self, sql: str, params: Iterable[Any] = ()) -> None:
        with self.conn() as c:
            c.execute(sql, list(params))

    # ---- convenience -----------------------------------------------------
    def ipo(self, slug: str) -> dict | None:
        return self.one("SELECT * FROM ipos WHERE slug=?", [slug])

    def financials(self, slug: str) -> list[dict]:
        return self.query("SELECT * FROM financials WHERE slug=? ORDER BY fy", [slug])

    def anchors(self, slug: str) -> list[dict]:
        return self.query("SELECT * FROM anchor_investors WHERE slug=? ORDER BY amount_cr DESC", [slug])

    def alert_seen(self, key: str) -> bool:
        return self.one("SELECT 1 AS x FROM alerts_sent WHERE key=?", [key]) is not None

    def mark_alert(self, key: str, slug: str, kind: str, message: str) -> None:
        self.upsert("alerts_sent", {"key": key, "slug": slug, "kind": kind, "message": message}, ["key"])
