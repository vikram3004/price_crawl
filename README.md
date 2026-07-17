# price-watcher

Scrapes product pages on a schedule and opens a **GitHub issue** when a price
hits your target. Runs free on GitHub Actions — no external accounts, no secrets.

## Setup (5 min)

### 1. Notifications: GitHub issues
No bot, no tokens. When a price hits target, the workflow opens an issue
labeled `price-alert` (watch the repo, or turn on issue notifications, to get
pinged). It closes that issue automatically when the price goes back above
target. Scraper failures land in a single rolling issue labeled `scraper-error`.

The only requirement is that the workflow can write issues:
**Settings → Actions → General → Workflow permissions → Read and write access**
(this also covers the state commit — one setting, both needs).

### 2. Find your selector
Open the product page → right-click the price → Inspect → right-click the
element → Copy → Copy selector. Trim it down to something stable
(e.g. `.price-box .final-price`, not a 12-level nth-child chain).

### 3. Edit `watchlist.json`
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

### 4. Test
Actions tab → `price-check` → **Run workflow**. Check the log output and the
Issues tab.

## Local run
```bash
pip install -r requirements.txt
# Optional: open real issues from your machine. Omit to just print to stdout.
export GITHUB_TOKEN=<a PAT with `repo`/issues scope> GITHUB_REPOSITORY=owner/repo
python checker.py
```
Without `GITHUB_TOKEN`/`GITHUB_REPOSITORY` it prints alerts to stdout instead of
opening issues — handy for testing selectors.

## How it works
- `state.json` tracks last price + notified flag → no duplicate spam.
  Re-comments only if the price drops *further* after a hit.
- Target hit → opens a `price-alert` issue (one per item, deduped by a hidden
  marker in the body). Back above target → comments and closes it.
- `history.csv` accumulates every reading, committed back to the repo. Chart it later.
- Selector miss → falls back to JSON-LD `offers.price` before giving up.
- Any parse failure opens/updates a ⚠️ `scraper-error` issue and reds the run —
  you find out when a site changes its layout, instead of silently missing a deal.

## Cadence
`cron: "0 18 * * *"` = once daily at 18:00 UTC (1:00 PM EST / 2:00 PM EDT).
GitHub cron is fixed-UTC and doesn't follow daylight saving, so the local
clock time shifts an hour between winter and summer. Don't go below hourly:
Actions scheduled runs are best-effort and get queued under load anyway, and
hammering a retailer is what gets your IP blocked.

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
