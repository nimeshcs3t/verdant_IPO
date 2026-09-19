#!/usr/bin/env python
"""IPO Radar — Indian mainboard + SME IPO tracker and alerting.

  python run.py demo                 seed a snapshot and print today's alerts
  python run.py sync [--steps ...]   pull all sources and recompute
  python run.py alerts [--dry-run]   send new alerts
  python run.py watch --every 15     sync + alerts in a loop (minutes)
  python run.py ipo hero-motors      print everything known about one IPO
  python run.py test-alert           send one test message to every channel
  streamlit run dashboard.py         open the dashboard
"""
from __future__ import annotations

import argparse
import json
import logging
import time
from datetime import date
from pathlib import Path

import yaml

from ipo_radar import alerts
from ipo_radar.db import DB
from ipo_radar.pipeline import Pipeline


def load_cfg(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        p = Path("config.example.yaml")
    return yaml.safe_load(p.read_text()) or {}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["demo", "sync", "alerts", "watch", "ipo", "test-alert"])
    ap.add_argument("slug", nargs="?")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--steps", nargs="*", default=["lists", "details", "compute", "returns", "videos"])
    ap.add_argument("--every", type=int, default=15, help="minutes between runs for `watch`")
    ap.add_argument("--dry-run", action="store_true", help="print alerts without marking them sent")
    ap.add_argument("--today", help="pretend today is YYYY-MM-DD (testing)")
    ap.add_argument("-v", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if a.v else logging.INFO, format="%(asctime)s %(message)s",
                        datefmt="%H:%M:%S")
    cfg = load_cfg(a.config)
    db = DB(cfg.get("db_path", "data/ipo_radar.db"))
    today = date.fromisoformat(a.today) if a.today else None

    if a.cmd == "demo":
        from ipo_radar.demo import seed
        seed(db)
        Pipeline(db, cfg).compute()
        alerts.run(db, cfg, today, dry_run=True)
    elif a.cmd == "sync":
        Pipeline(db, cfg).run(a.steps)
    elif a.cmd == "alerts":
        alerts.run(db, cfg, today, dry_run=a.dry_run)
    elif a.cmd == "watch":
        p = Pipeline(db, cfg)
        n = 0
        while True:
            # heavy steps (returns, videos) hourly; lists/details/compute every tick
            steps = ["lists", "details", "compute"] + (["returns", "videos"] if n % 4 == 0 else [])
            try:
                p.run(steps)
                alerts.run(db, cfg)
            except Exception:
                logging.exception("watch tick failed")
            n += 1
            time.sleep(a.every * 60)
    elif a.cmd == "test-alert":
        alerts.Dispatcher(cfg).send(alerts.Alert("test", "-", "TEST", "IPO Radar is connected",
                                                 "If you can read this, alerts will reach you here.", 2))
    elif a.cmd == "ipo":
        out = {"ipo": db.ipo(a.slug), "financials": db.financials(a.slug),
               "anchor_book": db.one("SELECT * FROM anchor_book WHERE slug=?", [a.slug]),
               "anchors": db.anchors(a.slug),
               "pre_ipo": db.query("SELECT * FROM pre_ipo_investors WHERE slug=?", [a.slug]),
               "scores": db.one("SELECT * FROM scores WHERE slug=?", [a.slug]),
               "unlocks": db.query("SELECT * FROM unlocks WHERE slug=? ORDER BY unlock_date", [a.slug]),
               "returns": db.one("SELECT * FROM returns WHERE slug=?", [a.slug]),
               "videos": db.query("SELECT * FROM videos WHERE slug=?", [a.slug])}
        print(json.dumps(out, indent=2, default=str, ensure_ascii=False))


if __name__ == "__main__":
    main()
