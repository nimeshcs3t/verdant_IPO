"""Post-listing performance.

  listing gain          listing-day open vs issue price (how the market priced it on day 1)
  since IPO price       what an allottee has made
  since listing open    what someone buying at the open on day 1 has made
  since listing close   same, buying at the day-1 close
  CAGR                  only shown after 1 year (annualising a few weeks is misleading)
  alpha                 since-IPO return minus benchmark over the same dates
  unlock impact         stock vs benchmark from 5 sessions before to 5 after each unlock

Prices: yfinance (NSE symbol + '.NS', BSE symbol/code + '.BO').
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from .utils import log, parse_date


def compute(ipo: dict, px: pd.DataFrame, bench: pd.DataFrame | None = None,
            unlocks: list[dict] | None = None, as_of: date | None = None) -> dict | None:
    issue = ipo.get("issue_price") or ipo.get("price_high")
    if px is None or px.empty or not issue:
        return None
    px = px.sort_index()
    lo, lc, ltp = float(px["Open"].iloc[0]), float(px["Close"].iloc[0]), float(px["Close"].iloc[-1])
    first_day = px.index[0]
    days = (px.index[-1] - first_day).days
    out = {
        "slug": ipo["slug"], "issue_price": issue, "listing_open": lo, "listing_close": lc, "ltp": ltp,
        "listing_gain": lo / issue - 1,
        "ret_vs_issue": ltp / issue - 1,
        "ret_vs_listing_open": ltp / lo - 1,
        "ret_vs_listing_close": ltp / lc - 1,
        "cagr_since_listing": (ltp / lo) ** (365 / days) - 1 if days >= 365 else None,
        "max_drawdown": float((px["Close"] / px["Close"].cummax() - 1).min()),
        "as_of": (as_of or px.index[-1].date()).isoformat(),
    }
    if bench is not None and not bench.empty:
        b = bench["Close"].sort_index()
        b = b[b.index >= first_day]
        if len(b) > 1:
            out["benchmark_ret"] = float(b.iloc[-1] / b.iloc[0] - 1)
            out["alpha"] = out["ret_vs_issue"] - out["benchmark_ret"]
    impacts = []
    for u in unlocks or []:
        d = parse_date(u["unlock_date"])
        if not d or pd.Timestamp(d) > px.index[-1]:
            continue
        i = px.index.searchsorted(pd.Timestamp(d))
        if i - 5 < 0 or i + 5 >= len(px):
            continue
        r = float(px["Close"].iloc[i + 5] / px["Close"].iloc[i - 5] - 1)
        rb = None
        if bench is not None and not bench.empty:
            bs = bench["Close"].sort_index()
            j0, j1 = bs.index.searchsorted(px.index[i - 5]), bs.index.searchsorted(px.index[i + 5])
            if j1 < len(bs):
                rb = float(bs.iloc[j1] / bs.iloc[j0] - 1)
        impacts.append({"bucket": u["bucket"], "date": u["unlock_date"], "stock": round(r, 4),
                        "vs_bench": round(r - rb, 4) if rb is not None else None})
    out["unlock_impact"] = impacts
    return out


class Prices:
    def __init__(self, benchmark: str = "^CRSLDX"):
        self.benchmark = benchmark
        self._bench: pd.DataFrame | None = None

    def history(self, ticker: str, start: str) -> pd.DataFrame | None:
        try:
            import yfinance as yf
        except ImportError:
            log.warning("pip install yfinance to compute returns")
            return None
        try:
            df = yf.download(ticker, start=start, progress=False, auto_adjust=False, multi_level_index=False)
        except TypeError:  # older yfinance without multi_level_index
            df = yf.download(ticker, start=start, progress=False, auto_adjust=False)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
        except Exception as e:
            log.warning("yfinance %s: %s", ticker, e)
            return None
        return df.dropna(subset=["Close"]) if df is not None and not df.empty else None

    def bench(self, start: str) -> pd.DataFrame | None:
        if self._bench is None or str(self._bench.index[0].date()) > start:
            self._bench = self.history(self.benchmark, start)
        return self._bench

    def tickers_for(self, ipo: dict) -> list[str]:
        t = []
        if ipo.get("yf_ticker"):
            t.append(ipo["yf_ticker"])
        if ipo.get("nse_symbol"):
            t.append(f"{ipo['nse_symbol']}.NS")
        if ipo.get("bse_code"):
            t.append(f"{ipo['bse_code']}.BO")
        return list(dict.fromkeys(t))

    def returns_for(self, ipo: dict, unlocks: list[dict]) -> dict | None:
        start = ipo.get("listing_date")
        if not start:
            return None
        for tk in self.tickers_for(ipo):
            px = self.history(tk, start)
            if px is not None:
                return compute(ipo, px, self.bench("2015-01-01"), unlocks)
        return None
