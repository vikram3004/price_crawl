# price-watcher

Scrapes product pages on a schedule, alerts you on Telegram when a price hits your target. Runs free on GitHub Actions.

## Setup (10 min)

### 1. Telegram bot
- DM [@BotFather](https://t.me/botfather) → `/newbot` → copy the token
- DM your new bot anything, then open:
  `https://api.telegram.org/bot<TOKEN>/getUpdates` → copy `chat.id`

### 2. Repo secrets
Settings → Secrets and variables → Actions → New repository secret:
- `TG_TOKEN` — bot token
- `TG_CHAT` — your chat id

### 3. Find your selector
Open the product page → right-click the price → Inspect → right-click the
element → Copy → Copy selector. Trim it down to something stable
(e.g. `.price-box .final-price`, not a 12-level nth-child chain).

### 4. Edit `watchlist.json`
```json
{
  "id": "unique-slug",
  "name": "Human readable name",
  "url": "https://...",
  "selector": ".price",
  "target": 299.99,
  "currency": "USD",
  "render_js": false
}
```
Set `render_js: true` only if the price is empty in "View Source" — it costs
~40s of Playwright startup per run.

### 5. Test
Actions tab → `price-check` → **Run workflow**. Check the log output.

## Local run
```bash
pip install -r requirements.txt
export TG_TOKEN=... TG_CHAT=...
python checker.py
```
Without the env vars it prints alerts to stdout instead of sending them.

## How it works
- `state.json` tracks last price + notified flag → no duplicate spam.
  Re-alerts only if the price drops *further* after a hit.
- `history.csv` accumulates every reading, committed back to the repo. Chart it later.
- Selector miss → falls back to JSON-LD `offers.price` before giving up.
- Any parse failure sends you a ⚠️ alert and reds the run — you find out when
  a site changes its layout, instead of silently missing a deal.

## Cadence
`cron: "0 */6 * * *"` = every 6 hours. Don't go below hourly: Actions
scheduled runs are best-effort and get queued under load anyway, and hammering
a retailer is what gets your IP blocked.

## Caveats
- **Amazon / Flipkart / Walmart** actively block datacenter IPs (which is what
  Actions runners are). Expect CAPTCHAs. Use their affiliate APIs, or run this
  on a residential IP (Raspberry Pi + cron, same script).
- Scheduled workflows are disabled after 60 days of repo inactivity. The state
  commits each run count as activity, so this self-heals.
- Public repo = free unlimited Actions minutes. Private = 2000 min/month.
- Check the site's ToS/robots.txt. This is for personal use at low frequency.

## Alternative hosts
| Host | Notes |
|---|---|
| Oracle Cloud Free Tier | Always-free ARM VM, real cron, residential-ish IP, Playwright fine |
| Raspberry Pi at home | Best for blocked retailers — your home IP |
| Cloudflare Workers | Cron triggers + 100k req/day free, but rewrite in JS, no browser |
| Fly.io / Render | Free tier cron, cold starts |
