"""Build config.yaml inside GitHub Actions from repository secrets/variables."""
import os
import yaml

c = yaml.safe_load(open("config.example.yaml"))
env = os.environ.get
c["channels"]["telegram"] = {"bot_token": env("TELEGRAM_BOT_TOKEN", ""), "chat_id": env("TELEGRAM_CHAT_ID", "")}
c["channels"]["webhooks"] = [u for u in env("WEBHOOK_URLS", "").split(",") if u.strip()]
if env("SMTP_USER"):
    c["channels"]["email"].update(smtp_host=env("SMTP_HOST", "smtp.gmail.com"), user=env("SMTP_USER"),
                                  password=env("SMTP_PASSWORD", ""), to=env("EMAIL_TO", env("SMTP_USER")))
c["youtube"]["api_key"] = env("YOUTUBE_API_KEY", "")
c["anthropic_api_key"] = env("ANTHROPIC_API_KEY", "")
if env("WATCHLIST"):
    c["alerts"]["watchlist"] = [s.strip() for s in env("WATCHLIST").split(",")]
# no Playwright on the quick runs -> skip Chittorgarh there
c["sources"]["enabled"]["chittorgarh"] = env("FULL_RUN") == "true"
yaml.safe_dump(c, open("config.yaml", "w"), sort_keys=False)
print("config.yaml written; telegram:", bool(c["channels"]["telegram"]["bot_token"]))
