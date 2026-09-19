"""IPO Radar dashboard.   streamlit run dashboard.py"""
from __future__ import annotations

import os
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import requests
import streamlit as st
import yaml

from ipo_radar.db import DB
from ipo_radar.scoring import AnchorTiers, pre_ipo_markups

st.set_page_config(page_title="IPO Radar", page_icon="📡", layout="wide")
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&display=swap');
html, body, [class*="css"], .stMarkdown, .stDataFrame { font-family: 'IBM Plex Sans', system-ui, sans-serif; }
h1, h2, h3 { letter-spacing: -0.01em; color: #14213D; }
[data-testid="stMetricValue"] { font-variant-numeric: tabular-nums; }
.flag { border-left: 3px solid #C2412D; padding: 2px 10px; margin: 4px 0; color: #5a1d12; background: #fbeeea; }
.why  { border-left: 3px solid #2F5D8A; padding: 2px 10px; margin: 4px 0; color: #14213D; background: #eef3f8; }
</style>""", unsafe_allow_html=True)

cfg_path = Path("config.yaml") if Path("config.yaml").exists() else Path("config.example.yaml")
cfg = yaml.safe_load(cfg_path.read_text()) or {}
db_path = Path(cfg.get("db_path", "data/ipo_radar.db"))


def _secret(key):
    try:
        return st.secrets.get(key) or os.environ.get(key)
    except Exception:  # no secrets.toml (local run)
        return os.environ.get(key)


@st.cache_data(ttl=600, show_spinner="Fetching the latest data…")
def fetch_db(repo: str, token: str | None, dest: str) -> float:
    """Download the DB the GitHub Action publishes on the `data` branch (refreshes every 10 min)."""
    h = {"Accept": "application/vnd.github.raw"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    r = requests.get(f"https://api.github.com/repos/{repo}/contents/ipo_radar.db",
                     params={"ref": "data"}, headers=h, timeout=60)
    r.raise_for_status()
    Path(dest).parent.mkdir(parents=True, exist_ok=True)
    Path(dest).write_bytes(r.content)
    return time.time()


repo = _secret("GITHUB_REPO")
if repo:
    try:
        fetch_db(repo, _secret("GITHUB_TOKEN"), str(db_path))
    except Exception as e:
        st.error(f"Couldn't download data from {repo} (data branch): {e}. Showing the last copy, if any.")
db = DB(db_path)
today = date.today()


def df(sql, params=()):
    return pd.DataFrame(db.query(sql, params))


def pct(x):
    return None if x is None or pd.isna(x) else round(100 * x, 1)


with st.sidebar:
    st.header("IPO Radar")
    seg = st.multiselect("Segment", ["MAINBOARD", "SME"], default=["MAINBOARD", "SME"])
    if db_path.exists():
        st.caption(f"Data updated {datetime.fromtimestamp(db_path.stat().st_mtime):%d %b %H:%M}")
    n_ipos = db.one("SELECT COUNT(*) n FROM ipos")["n"]
    if n_ipos == 0:
        st.warning("No data yet. Run `python run.py demo` for a snapshot or `python run.py sync` for live data.")
seg_sql = "(" + ",".join(f"'{s}'" for s in seg or ["MAINBOARD", "SME"]) + ")"

tabs = st.tabs(["This fortnight", "DRHP / RHP pipeline", "IPO deep-dive", "Unlock calendar",
                "Returns", "Alerts sent"])

# ------------------------------------------------------------------ calendar
with tabs[0]:
    cal = df(f"""SELECT i.slug, i.name, i.segment, i.status, i.open_date, i.close_date, i.listing_date,
                 i.price_low, i.price_high, i.issue_size_cr, i.ofs_cr, i.subscription,
                 s.quality, s.quality_grade, s.anchor, s.anchor_grade
                 FROM ipos i LEFT JOIN scores s USING(slug)
                 WHERE i.segment IN {seg_sql} AND i.status IN ('RHP','UPCOMING','OPEN','CLOSED','ALLOTMENT')
                 ORDER BY i.open_date""")
    if cal.empty:
        st.info("No open or upcoming issues in the database.")
    else:
        cal["Band ₹"] = cal.apply(lambda r: f"{r.price_low:g}–{r.price_high:g}" if pd.notna(r.price_low) else "TBA", axis=1)
        cal["Subs (x)"] = cal["subscription"].apply(lambda s: s.get("total") if isinstance(s, dict) else None)
        cal["OFS %"] = (100 * cal["ofs_cr"] / cal["issue_size_cr"]).round(0)
        st.dataframe(cal[["name", "segment", "status", "open_date", "close_date", "listing_date", "Band ₹",
                          "issue_size_cr", "OFS %", "Subs (x)", "quality", "quality_grade", "anchor", "anchor_grade"]]
                     .rename(columns={"name": "Company", "issue_size_cr": "Size ₹Cr", "quality": "Quality",
                                      "quality_grade": "Q", "anchor": "Anchor", "anchor_grade": "A"}),
                     hide_index=True, width="stretch")

# ------------------------------------------------------------------ pipeline
with tabs[1]:
    days = st.slider("Filed in the last (days)", 7, 365, 60)
    since = (today - timedelta(days=days)).isoformat()
    pipe = df("""SELECT name, segment, status, drhp_date, drhp_url, rhp_date, rhp_url FROM ipos
                 WHERE (drhp_date >= ? OR rhp_date >= ?) ORDER BY COALESCE(rhp_date, drhp_date) DESC""",
              [since, since])
    c1, c2 = st.columns(2)
    c1.metric("DRHPs filed", int(pipe["drhp_date"].notna().sum()) if not pipe.empty else 0)
    c2.metric("RHPs filed (IPO imminent)", int(pipe["rhp_date"].notna().sum()) if not pipe.empty else 0)
    if not pipe.empty:
        st.dataframe(pipe, hide_index=True, width="stretch", column_config={
            "drhp_url": st.column_config.LinkColumn("DRHP"), "rhp_url": st.column_config.LinkColumn("RHP")})

# ------------------------------------------------------------------ deep-dive
with tabs[2]:
    names = df(f"SELECT slug, name FROM ipos WHERE segment IN {seg_sql} AND status!='DRHP' "
               "ORDER BY status='OPEN' DESC, (SELECT COUNT(*) FROM financials f WHERE f.slug=ipos.slug) DESC, open_date DESC")
    if names.empty:
        st.info("Nothing to show yet.")
    else:
        slug = st.selectbox("IPO", names["slug"], format_func=dict(zip(names.slug, names.name)).get)
        i = db.ipo(slug)
        sc = db.one("SELECT * FROM scores WHERE slug=?", [slug]) or {}
        det = sc.get("detail") or {}
        st.subheader(i["name"])
        st.caption(" · ".join(filter(None, [i.get("segment"), i.get("sector"), i.get("lead_managers"),
                                            *(f"[{k}]({v})" for k, v in (i.get("sources") or {}).items())])))
        m = st.columns(5)
        m[0].metric("Issue", f"₹{i['issue_size_cr']:,.0f} Cr" if i.get("issue_size_cr") else "TBA")
        m[1].metric("OFS share", f"{100 * i['ofs_cr'] / i['issue_size_cr']:.0f}%"
                    if i.get("issue_size_cr") and i.get("ofs_cr") is not None else "–")
        m[2].metric("Promoter post-IPO", f"{i['promoter_post']:.1f}%" if i.get("promoter_post") else "–")
        m[3].metric("Company quality", f"{sc['quality']:.0f} ({sc['quality_grade']})" if sc.get("quality") is not None else "–")
        m[4].metric("Anchor book", f"{sc['anchor']:.0f} ({sc['anchor_grade']})" if sc.get("anchor") is not None else "–")

        left, right = st.columns([3, 2])
        with left:
            fins = df("SELECT * FROM financials WHERE slug=? ORDER BY fy", [slug])
            if not fins.empty:
                st.markdown("**Financials (₹ Cr)**")
                st.bar_chart(fins.set_index("fy")[["revenue", "pat", "cfo"]])
                st.dataframe(fins.drop(columns=["slug"]).set_index("fy").T, width="stretch")
            if i.get("objects"):
                st.markdown("**Objects of the issue**")
                for o in i["objects"]:
                    st.markdown(f"- {o}")
        with right:
            q = det.get("quality", {})
            st.markdown("**Why this quality score**")
            for r in q.get("reasons", []):
                st.markdown(f"<div class='why'>{r}</div>", unsafe_allow_html=True)
            for f in q.get("flags", []):
                st.markdown(f"<div class='flag'>{f}</div>", unsafe_allow_html=True)
            if q.get("parts"):
                st.bar_chart(pd.Series(q["parts"], name="score /10"))

        st.markdown("### Anchor book")
        book = db.one("SELECT * FROM anchor_book WHERE slug=?", [slug])
        if book:
            b = st.columns(4)
            b[0].metric("Amount", f"₹{book['amount_cr']:,.0f} Cr" if book.get("amount_cr") else "–")
            b[1].metric("Price", f"₹{book['bid_price']:g}" if book.get("bid_price") else "–")
            b[2].metric("Mutual funds", f"{book['mf_pct']:.0f}%" if book.get("mf_pct") else "–")
            b[3].metric("Lock-ins end", f"{book.get('lockin_30') or '–'} / {book.get('lockin_90') or '–'}")
            for r in det.get("anchor", {}).get("reasons", []):
                st.markdown(f"<div class='why'>{r}</div>", unsafe_allow_html=True)
            inv = df("SELECT investor, shares, amount_cr, pct FROM anchor_investors WHERE slug=? ORDER BY amount_cr DESC", [slug])
            if not inv.empty:
                tiers = AnchorTiers(cfg.get("anchor_tiers_file", "data/anchor_tiers.yaml"))
                inv["tier"] = inv["investor"].map(tiers.classify)
                st.dataframe(inv, hide_index=True, width="stretch")
        else:
            st.caption("Anchor book not out yet (usually one working day before the issue opens).")

        st.markdown("### Pre-IPO investors and selling shareholders")
        pre = db.query("SELECT * FROM pre_ipo_investors WHERE slug=?", [slug])
        if pre:
            st.dataframe(pd.DataFrame(pre).drop(columns=["slug"]), hide_index=True, width="stretch")
            mk = pre_ipo_markups(i, pre)
            if mk:
                st.markdown("**Price paid vs IPO price**")
                st.dataframe(pd.DataFrame(mk), hide_index=True, width="stretch")
        else:
            st.caption("None loaded. Add rows to `pre_ipo_investors` from the RHP's 'Capital Structure' chapter.")

        st.markdown("### Unlock timeline")
        un = df("SELECT bucket, unlock_date, pct_equity, shares, note FROM unlocks WHERE slug=? ORDER BY unlock_date", [slug])
        if not un.empty:
            un["status"] = un["unlock_date"].apply(lambda d: "done" if d < today.isoformat() else f"in {(date.fromisoformat(d) - today).days}d")
            st.dataframe(un, hide_index=True, width="stretch")

        rt = db.one("SELECT * FROM returns WHERE slug=?", [slug])
        if rt:
            st.markdown("### Returns")
            r = st.columns(5)
            r[0].metric("Since IPO price", f"{pct(rt.get('ret_vs_issue'))}%")
            r[1].metric("Listing gain", f"{pct(rt.get('listing_gain'))}%" if rt.get("listing_gain") is not None else "–")
            r[2].metric("Since listing open", f"{pct(rt.get('ret_vs_listing_open'))}%" if rt.get("ret_vs_listing_open") is not None else "–")
            r[3].metric("vs Nifty 500", f"{pct(rt.get('alpha'))}%" if rt.get("alpha") is not None else "–")
            r[4].metric("Max drawdown", f"{pct(rt.get('max_drawdown'))}%" if rt.get("max_drawdown") is not None else "–")
            if rt.get("unlock_impact"):
                st.caption("Stock move from 5 sessions before to 5 after each unlock")
                st.dataframe(pd.DataFrame(rt["unlock_impact"]), hide_index=True)

        vids = db.query("SELECT * FROM videos WHERE slug=? ORDER BY kind, published DESC", [slug])
        st.markdown("### Interviews and roadshows")
        if not vids:
            st.caption("No videos found yet. They're searched once an RHP is filed.")
        for v in vids:
            with st.expander(f"{v['kind'].title()} · {v['channel']} · {v['title']}"):
                st.video(v["url"])
                if v.get("summary"):
                    st.markdown(v["summary"])
                fl = v.get("transcript_flags") or {}
                if isinstance(fl, dict):
                    for g in fl.get("guidance", [])[:6]:
                        st.markdown(f"<div class='why'>{g}</div>", unsafe_allow_html=True)
                    for f in fl.get("red_flags", [])[:6]:
                        st.markdown(f"<div class='flag'><b>{f['term']}</b> ×{f['count']}: …{f['snippet']}…</div>",
                                    unsafe_allow_html=True)

# ------------------------------------------------------------------ unlocks
with tabs[3]:
    horizon = st.slider("Next (days)", 7, 365, 90)
    un = df(f"""SELECT u.unlock_date, i.name, i.segment, u.bucket, u.pct_equity, u.note, r.ret_vs_issue
                FROM unlocks u JOIN ipos i USING(slug) LEFT JOIN returns r USING(slug)
                WHERE i.segment IN {seg_sql} AND u.unlock_date BETWEEN ? AND ? ORDER BY u.unlock_date""",
            [today.isoformat(), (today + timedelta(days=horizon)).isoformat()])
    if un.empty:
        st.info("No unlocks in this window.")
    else:
        un["vs IPO %"] = un["ret_vs_issue"].map(pct)
        st.caption("Stocks trading well above IPO price going into a large unlock are the ones where supply "
                   "tends to show up. Lock-in ends on the date shown; shares can trade from the next session.")
        st.dataframe(un.drop(columns=["ret_vs_issue"]), hide_index=True, width="stretch")

# ------------------------------------------------------------------ returns
with tabs[4]:
    rt = df(f"""SELECT i.name, i.segment, i.listing_date, r.issue_price, r.listing_open, r.ltp,
                r.listing_gain, r.ret_vs_issue, r.ret_vs_listing_open, r.cagr_since_listing, r.alpha,
                r.max_drawdown, s.quality_grade, s.anchor_grade
                FROM returns r JOIN ipos i USING(slug) LEFT JOIN scores s USING(slug)
                WHERE i.segment IN {seg_sql} ORDER BY i.listing_date DESC""")
    if rt.empty:
        st.info("No listed IPOs with prices yet. Run `python run.py sync --steps lists returns`.")
    else:
        for c in ["listing_gain", "ret_vs_issue", "ret_vs_listing_open", "cagr_since_listing", "alpha", "max_drawdown"]:
            rt[c] = rt[c].map(pct)
        g = rt.groupby("segment")["ret_vs_issue"].agg(["count", "median", "mean"]).round(1)
        g["% above IPO price"] = rt.groupby("segment")["ret_vs_issue"].apply(lambda s: round(100 * (s > 0).mean(), 0))
        st.dataframe(g, width="stretch")
        st.dataframe(rt.rename(columns={"listing_gain": "Listing gain %", "ret_vs_issue": "Since IPO %",
                                        "ret_vs_listing_open": "Since listing %", "cagr_since_listing": "CAGR %",
                                        "alpha": "vs Nifty 500 %", "max_drawdown": "Max DD %"}),
                     hide_index=True, width="stretch")
        if rt["quality_grade"].notna().any():
            st.markdown("**Does the quality grade predict returns?**")
            st.dataframe(rt.groupby("quality_grade")["ret_vs_issue"].agg(["count", "median"]).round(1))

# ------------------------------------------------------------------ alerts log
with tabs[5]:
    al = df("SELECT sent_at, kind, slug, message FROM alerts_sent ORDER BY sent_at DESC LIMIT 300")
    st.dataframe(al, hide_index=True, width="stretch") if not al.empty else st.info("No alerts sent yet.")
