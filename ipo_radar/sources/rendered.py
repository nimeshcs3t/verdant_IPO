"""Helpers for sites whose tables are filled by JavaScript (Chittorgarh reports, IPOJI).

Plain `requests` returns an empty shell for these ("Loading... Total Records: 0"), so we
render with Playwright when it's installed:  pip install playwright && playwright install chromium
Without Playwright we still try a plain GET (works for pages that are server-rendered).
"""
from __future__ import annotations

import re
from io import StringIO

import pandas as pd

from ..utils import Http, log

_pw = None


def render(url: str, http: Http | None = None, wait_selector: str = "table tbody tr",
           timeout_ms: int = 25000) -> str | None:
    global _pw
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        r = (http or Http()).get(url)
        return r.text if r else None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(user_agent=(http.s.headers["User-Agent"] if http else None))
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            try:
                page.wait_for_selector(wait_selector, timeout=timeout_ms)
            except Exception:
                log.warning("render: selector %s not found on %s", wait_selector, url)
            html = page.content()
            browser.close()
            return html
    except Exception as e:
        log.warning("render failed for %s: %s", url, e)
        return None


def tables(html: str) -> list[pd.DataFrame]:
    try:
        return pd.read_html(StringIO(html))
    except ValueError:
        return []


def pick(df: pd.DataFrame, *patterns: str) -> str | None:
    """Find the first column whose header matches any regex (case-insensitive)."""
    for pat in patterns:
        for c in df.columns:
            if re.search(pat, str(c), re.I):
                return c
    return None


def links(html: str, href_pattern: str) -> dict[str, str]:
    """Map visible link text -> href for links matching a pattern (to get detail URLs)."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "lxml")
    return {a.get_text(" ", strip=True): a["href"] for a in soup.find_all("a", href=re.compile(href_pattern))}
