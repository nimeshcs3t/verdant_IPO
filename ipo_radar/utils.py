"""Polite HTTP client + parsing helpers shared by all sources."""
from __future__ import annotations

import hashlib
import logging
import re
import time
from datetime import date, datetime
from pathlib import Path

import requests

log = logging.getLogger("ipo_radar")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")


class Http:
    """requests.Session with retries, per-host rate limit and an optional disk cache.

    The cache keeps you from hammering sites while you iterate on parsers:
    `Http(cache_dir="data/cache", cache_ttl=3600)`.
    """

    def __init__(self, min_interval: float = 1.5, cache_dir: str | None = None, cache_ttl: int = 900):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA, "Accept-Language": "en-IN,en;q=0.9"})
        self.min_interval = min_interval
        self._last: dict[str, float] = {}
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.cache_ttl = cache_ttl
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _throttle(self, url: str) -> None:
        host = re.sub(r"^https?://([^/]+).*", r"\1", url)
        wait = self.min_interval - (time.time() - self._last.get(host, 0))
        if wait > 0:
            time.sleep(wait)
        self._last[host] = time.time()

    def get(self, url: str, *, params: dict | None = None, headers: dict | None = None,
            retries: int = 3, use_cache: bool = True) -> requests.Response | None:
        key = hashlib.md5((url + str(params)).encode()).hexdigest()
        cpath = self.cache_dir / key if self.cache_dir else None
        if use_cache and cpath and cpath.exists() and time.time() - cpath.stat().st_mtime < self.cache_ttl:
            r = requests.Response()
            r._content, r.status_code, r.url = cpath.read_bytes(), 200, url
            r.encoding = "utf-8"
            return r
        for attempt in range(retries):
            self._throttle(url)
            try:
                r = self.s.get(url, params=params, headers=headers, timeout=25)
                if r.status_code == 200:
                    if cpath:
                        cpath.write_bytes(r.content)
                    return r
                log.warning("GET %s -> %s", url, r.status_code)
                if r.status_code in (401, 403, 404):
                    return None
            except requests.RequestException as e:
                log.warning("GET %s failed (%s), retry %d", url, e, attempt + 1)
            time.sleep(2 ** attempt)
        return None


# ---------------------------------------------------------------- parsing
_NUM = re.compile(r"-?[\d,]*\.?\d+")


def num(text) -> float | None:
    """'₹1,000 Cr' -> 1000.0 ; '3,57,14,284' -> 35714284.0 ; '81.66%' -> 81.66"""
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return float(text)
    m = _NUM.search(str(text).replace("\u20b9", ""))
    return float(m.group().replace(",", "")) if m else None


def crores(text) -> float | None:
    """Normalise an amount to ₹ crore. Handles 'Cr', 'Lakh', 'Mn', 'Bn'."""
    if text is None:
        return None
    t = str(text).lower()
    v = num(t)
    if v is None:
        return None
    if "lakh" in t or "lac" in t:
        return v / 100
    if "mn" in t or "million" in t:
        return v / 10
    if "bn" in t or "billion" in t:
        return v * 100
    return v


_DATE_FORMATS = ["%d-%b-%Y", "%d %b %Y", "%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%b %d, %Y",
                 "%d %B %Y", "%B %d, %Y", "%a, %b %d, %Y", "%a, %d %b %Y %H:%M:%S %z",
                 "%a, %d %b %Y %H:%M:%S %Z", "%d-%B-%Y"]


def parse_date(text, default_year: int | None = None) -> date | None:
    if not text:
        return None
    if isinstance(text, date):
        return text if not isinstance(text, datetime) else text.date()
    t = re.sub(r"\s+", " ", str(text)).strip().replace("Sept", "Sep")
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(t, fmt).date()
        except ValueError:
            continue
    # '16 Sep' style (no year)
    m = re.match(r"(\d{1,2})\s*([A-Za-z]{3})", t)
    if m and default_year:
        try:
            return datetime.strptime(f"{m[1]} {m[2]} {default_year}", "%d %b %Y").date()
        except ValueError:
            return None
    return None


def slugify(name: str) -> str:
    s = name.lower()
    s = re.sub(r"\b(ltd|limited|pvt|private)\b\.?", "", s)
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s


def iso(d: date | None) -> str | None:
    return d.isoformat() if d else None
