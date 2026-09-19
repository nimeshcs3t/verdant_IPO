# Deploying IPO Radar for free

```
GitHub Actions (free)  ──every 30 min──▶  scrape + score + send alerts ──▶ Telegram / email
        │ saves the database to the `data` branch
        ▼
Streamlit Community Cloud (free) ──reads `data` branch every 10 min──▶ dashboard URL
```

Alerts never depend on the dashboard: if the dashboard is asleep, alerts still arrive.

## 1. Telegram bot (5 min)
1. In Telegram, message **@BotFather** → `/newbot` → pick a name. Copy the token.
2. Send any message to your new bot.
3. Open `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser; copy `"chat":{"id": …}`.
   For a channel instead: add the bot as a channel admin and use `@yourchannel` as the chat id.

## 2. Test on your computer (10 min)
```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
cp config.example.yaml config.yaml                     # paste bot_token and chat_id
python run.py test-alert                               # should arrive on Telegram
python run.py sync -v                                  # first live pull; watch for errors
python run.py alerts --dry-run
streamlit run dashboard.py
```
If a source logs errors, fix its parser now. It's much easier to debug locally than inside CI.

## 3. Push to GitHub
Create a **private** repository (e.g. `ipo-radar`) and push this folder.
`config.yaml` is git-ignored, so your keys never get committed.

## 4. Add secrets
Repo → Settings → Secrets and variables → Actions → **New repository secret**:

| Secret | Needed? |
|---|---|
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | yes |
| `YOUTUBE_API_KEY` | recommended (free, see step 7) |
| `WEBHOOK_URLS` | optional, comma-separated Discord/Slack webhooks |
| `SMTP_USER`, `SMTP_PASSWORD`, `EMAIL_TO` | optional, Gmail + app password |
| `ANTHROPIC_API_KEY` | optional (paid), interview summaries |

Optional variable (Variables tab): `WATCHLIST` = `hero-motors,varmora-granito`.

## 5. Start the scheduler
Actions tab → enable workflows → **ipo-radar** → **Run workflow**. The first run is a full run
(~5 min). Check the log, then check that a `data` branch now exists.

Schedule: a quick run every 30 minutes, about 08:30–18:00 IST on weekdays, plus a full run
every evening around 20:50 IST. Full runs cover Chittorgarh, returns and YouTube.
GitHub may start scheduled runs 5–20 minutes late at busy times; this doesn't matter here.

Free-tier maths for a private repo: about 20 quick runs × ~2 min × 22 days, plus 30 full runs
× ~6 min, comes to roughly 1,100 of the 2,000 free minutes a month. Public repos have
unlimited minutes. The `keepalive` job stops GitHub pausing the schedule after 60 days.

## 6. Dashboard on Streamlit Community Cloud
1. Go to share.streamlit.io and sign in with GitHub.
2. Create app → pick the repo, branch `main`, main file `dashboard.py`.
3. Advanced settings → Secrets:
   ```toml
   GITHUB_REPO = "your-username/ipo-radar"
   GITHUB_TOKEN = "github_pat_..."   # only for a private repo
   ```
   Create the token at GitHub → Settings → Developer settings → Fine-grained tokens. Give it
   access to this repo only, with Contents: Read-only.
4. Deploy. You get a URL like `ipo-radar-you.streamlit.app`; bookmark it on your phone.

The app sleeps after 12 hours with no visitors. Opening the link wakes it in about 30 seconds.
Your data and alerts are unaffected while it sleeps.

## 7. YouTube API key (free, 10,000 units/day)
console.cloud.google.com → new project → APIs & Services → enable "YouTube Data API v3" →
Credentials → Create API key. Each search costs 100 units. The default settings use well
under the daily quota. Without a key, yt-dlp search is used, but YouTube often blocks it from
cloud servers.

## 8. Keep it healthy
- **Failure emails:** GitHub emails you when a run fails. Open the log; it's usually one
  site that changed its layout. Every source is isolated, so the other sources keep working.
- **A source changes its layout:** fix the parser, run `pytest -q`, then push.
- **Blocked sources:** NSE and YouTube sometimes block GitHub's servers. StockScans and SEBI
  cover the calendar and filings, so alerts continue.
- **Backups:** every run overwrites the `data` branch. To keep history, download
  `ipo_radar.db` from that branch now and then.

## Always-on alternative (still free): Oracle Cloud "Always Free" VM
Use this if you want 24/7 runs, 5-minute checks, or a dashboard that never sleeps.
1. Sign up at oracle.com/cloud/free. A card is needed for verification; Always Free
   resources aren't charged.
2. Create an Ampere A1 VM (Ubuntu), then SSH in and set it up:
   ```bash
   sudo apt update && sudo apt install -y python3-venv git
   git clone https://github.com/you/ipo-radar && cd ipo-radar
   python3 -m venv .venv && . .venv/bin/activate
   pip install -r requirements.txt && playwright install --with-deps chromium
   cp config.example.yaml config.yaml && nano config.yaml
   ```
3. Create two systemd services so everything restarts on reboot:
   `python run.py watch --every 10` and
   `streamlit run dashboard.py --server.port 8501 --server.address 0.0.0.0`.
4. Open port 8501 in the VCN security list. Better still, use a free Cloudflare Tunnel so
   you don't expose the port.

If you use the VM, disable the GitHub workflow so the two don't both send alerts.
A Raspberry Pi or an always-on home PC works the same way. A home connection has the
advantage of an Indian residential IP, which NSE doesn't block.
