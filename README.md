# IPO Radar — Indian IPO alerts (Mainboard + SME)

Tracks every Indian IPO from DRHP filing to three years after listing, scores it, and
alerts you on Telegram / email / Discord / Slack.

| What | Where it comes from |
|---|---|
| DRHP and RHP filings | SEBI filings pages + RSS (mainboard), StockScans "Filed with SEBI" |
| Issue calendar, price band, size, subscription | StockScans, NSE API (incl. NSE Emerge), Chittorgarh, IPOJI |
| Company quality score | Financials, OFS share, objects, promoter holding, valuation, risk-factor text |
| Anchor book quality | Anchor totals + MF share (StockScans), investor names (Chittorgarh), tier list in `data/anchor_tiers.yaml` |
| Pre-IPO investors | Selling shareholders (OFS), plus any deals you add from the RHP; IPO-price markup vs price they paid |
| Unlock timeline | SEBI ICDR lock-in rules, separate for mainboard and SME (`ipo_radar/lockin.py`) |
| Returns | Since IPO price, since listing open/close, listing gain, CAGR, alpha vs Nifty 500, drawdown, price move around each unlock (yfinance) |
| Promoter interviews / roadshows | YouTube search + transcripts, keyword scan for guidance and red-flag topics, optional LLM summary |

## Quick start

```bash
pip install -r requirements.txt
playwright install chromium          # needed for Chittorgarh/IPOJI (JS-rendered tables)
cp config.example.yaml config.yaml   # add Telegram token etc.

python run.py demo                   # load an 18-Sep-2026 snapshot, print alerts
streamlit run dashboard.py           # open the dashboard

python run.py sync                   # live pull from all sources
python run.py alerts                 # send anything new
python run.py watch --every 15       # loop: sync + alerts every 15 minutes
python run.py ipo hero-motors        # dump everything known about one IPO as JSON
pytest -q                            # parsers, lock-in maths, scoring, returns
```

To run it 24/7 for free (GitHub Actions + Streamlit Community Cloud), follow **DEPLOY.md**.

## Alerts you'll get

| Kind | Trigger |
|---|---|
| DRHP / RHP | New filing in the last 7 days (RHP = IPO is weeks away) |
| DATES | Issue dates announced, with band, size, fresh/OFS split and scores |
| OPEN / CLOSE / ALLOTMENT / LISTING | On the day; close-day alert carries QIB/NII/retail subscription |
| ANCHOR | Anchor book out, with anchor score and why |
| UNLOCK | 7 days before, the day before, and on the day each lock-in ends, with % of equity and where the stock sits vs IPO price |
| LISTED | Listing gain and current return |
| VIDEO | New promoter interview or roadshow, with transcript flags or summary |

Every alert has a stable key, so running the job every few minutes never sends duplicates.

## Lock-in rules used

| Holder | Mainboard | SME |
|---|---|---|
| Anchor investors | 50% at 30 days, 50% at 90 days | same |
| Pre-IPO non-promoter shareholders | 6 months | 1 year |
| Promoter holding above 20% | 6 months (1 year if capex-heavy) | 50% at 1 year, 50% at 2 years (issues from Mar-2025) |
| Promoter minimum contribution (20%) | 18 months (3 years if capex-heavy) | 3 years |

All periods run from the allotment date; a 30-day lock-in ends on allotment + 29 days, which
matches exchange-published dates. The RHP's "Capital Structure" chapter is authoritative —
exemptions exist (e.g. some AIF/VC holders, ESOP trusts). Set `capex_heavy = 1` on an IPO when
more than half the fresh issue funds capital expenditure.

## How the scores work

Both are rule-based and every point has a reason shown in the dashboard. Tune weights in `config.yaml`.

**Company quality (0–100):** growth (revenue/PAT CAGR), profitability (ROE, PAT margin),
balance sheet (debt/equity), cash conversion (cumulative CFO ÷ PAT), issue structure
(OFS share, what the money is for, promoter stake after IPO), valuation (P/E at the upper
band). Red flags subtract points: negative operating cash flow, a PAT spike just before the IPO,
heavy customer concentration, tax/regulatory proceedings in the risk factors, pre-IPO buyers
who paid less than half the IPO price within 18 months, very small SME issues.

**Anchor book (0–100):** weighted share of the book by investor tier (large domestic MFs and
insurers, sovereign and global long-only funds = full weight; smaller or trading-oriented
names = half; unknown = 0.3; names that appear in dozens of SME anchor books every year = 0).
Bonus for breadth, penalty if three names hold most of the book. If only the MF share is known,
the score is based on that and says so.

Grades: A ≥ 75, B ≥ 60, C ≥ 45, D below. The "Returns" tab checks whether grades actually
predicted performance — use it to recalibrate.

## Project layout

```
run.py                     CLI
dashboard.py               Streamlit UI
ipo_radar/
  sources/stockscans.py    lists, filings, historical returns, detail pages
  sources/chittorgarh.py   Chittorgarh + IPOJI (anchor names, SME anchor league)
  sources/regulators.py    SEBI DRHP/RHP, NSE issue calendar
  sources/youtube.py       interviews, roadshows, transcripts
  sources/rendered.py      Playwright helper for JS-rendered tables
  lockin.py                unlock calendar
  scoring.py               quality, anchor and pre-IPO scoring
  returns.py               post-listing performance
  alerts.py                detection, dedupe, Telegram/email/webhooks
  pipeline.py              orchestration and cross-source merge
  db.py                    SQLite schema
data/anchor_tiers.yaml     edit to change who counts as a quality anchor
```

## Things to know

- **Scrapers break when sites change.** Parsers match on visible labels ("Bid Date",
  "Fresh Issue") rather than CSS classes, and every source is isolated so one failure doesn't
  stop the run. URLs for Chittorgarh/IPOJI are overridable in `config.yaml`. Check each site's
  terms of use; the default 1.5-second per-host delay and cache keep load light.
- **NSE's API** needs a browser-like session and sometimes blocks cloud IPs; StockScans covers
  the same calendar if it fails.
- **SME DRHPs** are filed with NSE Emerge / BSE SME, not SEBI, so the SME pipeline comes from
  aggregators.
- **Pre-IPO deal prices** (the most useful signal on pre-IPO investors) live in the RHP's
  capital-build-up table. Only OFS sellers are scraped automatically; add deals with prices to
  the `pre_ipo_investors` table and the markup check and red flag switch on.
- **SME tickers on Yahoo** are patchy. If yfinance has no data, the since-IPO return from
  StockScans' historical page is used instead.
- This is a research tool, not investment advice.
