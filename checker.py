#!/usr/bin/env python3
"""
Price watcher — fetches product pages, parses prices, alerts on target hit.
Designed to run headless on GitHub Actions cron.
"""
import json
import os
import random
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).parent
WATCHLIST = ROOT / "watchlist.json"
STATE = ROOT / "state.json"
HISTORY = ROOT / "history.csv"

UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]


def headers():
    return {
        "User-Agent": random.choice(UA_POOL),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control": "no-cache",
    }


# ---------- parsing ----------

PRICE_RE = re.compile(r"(\d{1,3}(?:[,\s]\d{3})*(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)")


def parse_price(text: str) -> float:
    """Extract a float from messy price text like '$1,299.00' or '₹ 49,999'."""
    cleaned = text.replace("\xa0", " ").strip()
    m = PRICE_RE.search(cleaned)
    if not m:
        raise ValueError(f"no numeric price found in {text!r}")
    return float(m.group(1).replace(",", "").replace(" ", ""))


def fetch_static(url: str) -> str:
    r = requests.get(url, headers=headers(), timeout=25)
    r.raise_for_status()
    return r.text


def fetch_rendered(url: str) -> str:
    """Lazy-import Playwright so static-only runs stay fast."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(user_agent=random.choice(UA_POOL))
        page.goto(url, wait_until="networkidle", timeout=45000)
        html = page.content()
        browser.close()
    return html


def extract_price(html: str, selector: str) -> float:
    soup = BeautifulSoup(html, "html.parser")

    # 1. Try the CSS selector
    el = soup.select_one(selector)
    if el:
        return parse_price(el.get_text())

    # 2. Fallback: JSON-LD structured data (works on a surprising number of sites)
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "{}")
        except json.JSONDecodeError:
            continue
        for node in data if isinstance(data, list) else [data]:
            offers = node.get("offers") if isinstance(node, dict) else None
            if isinstance(offers, dict) and offers.get("price"):
                return float(offers["price"])
            if isinstance(offers, list) and offers and offers[0].get("price"):
                return float(offers[0]["price"])

    raise LookupError(f"selector {selector!r} matched nothing and no JSON-LD offer found")


# ---------- alerting (via GitHub Issues) ----------

GH_TOKEN = os.environ.get("GITHUB_TOKEN")
GH_REPO = os.environ.get("GITHUB_REPOSITORY")            # "owner/repo", set by Actions
GH_API = os.environ.get("GITHUB_API_URL", "https://api.github.com")

ALERT_LABEL = "price-alert"
ERROR_LABEL = "scraper-error"


def _gh(method: str, path: str, **kwargs):
    """Call the GitHub REST API for the current repo. Returns parsed JSON (or {})."""
    resp = requests.request(
        method,
        f"{GH_API}/repos/{GH_REPO}{path}",
        headers={
            "Authorization": f"Bearer {GH_TOKEN}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        timeout=20,
        **kwargs,
    )
    resp.raise_for_status()
    return resp.json() if resp.content else {}


def _configured() -> bool:
    return bool(GH_TOKEN and GH_REPO)


def _find_open_issue(marker: str, label: str):
    """Return the open issue whose body carries `marker`, or None."""
    issues = _gh("GET", f"/issues?state=open&labels={label}&per_page=100")
    for issue in issues:
        if "pull_request" in issue:      # the issues endpoint also lists PRs
            continue
        if marker in (issue.get("body") or ""):
            return issue
    return None


def open_or_update_alert(item: dict, price: float):
    """First target hit → open an issue; still/further below → comment on it."""
    marker = f"<!-- pw-item:{item['id']} -->"
    cur = item.get("currency", "USD")
    now = datetime.now(timezone.utc).isoformat()
    line = f"{cur} {price:.2f} (target {cur} {item['target']:.2f})"

    if not _configured():
        print(f"[no github token] PRICE HIT {item['name']}: {line}")
        return

    existing = _find_open_issue(marker, ALERT_LABEL)
    if existing:
        _gh("POST", f"/issues/{existing['number']}/comments",
            json={"body": f"🎯 Still at/below target: **{line}** — {now}"})
    else:
        body = (
            f"**{item['name']}** hit your target price.\n\n"
            f"- **Now:** {cur} {price:.2f}\n"
            f"- **Target:** {cur} {item['target']:.2f}\n"
            f"- **URL:** {item['url']}\n"
            f"- **Checked:** {now}\n\n"
            f"_Opened automatically by price-watcher. "
            f"It closes itself when the price goes back above target._\n\n"
            f"{marker}"
        )
        _gh("POST", "/issues",
            json={"title": f"🎯 Price hit: {item['name']} — {cur} {price:.2f}",
                  "body": body, "labels": [ALERT_LABEL]})


def resolve_alert(item: dict, price: float):
    """Price back above target → comment on and close the open alert issue, if any."""
    marker = f"<!-- pw-item:{item['id']} -->"
    cur = item.get("currency", "USD")
    now = datetime.now(timezone.utc).isoformat()

    if not _configured():
        print(f"[no github token] BACK ABOVE TARGET {item['name']}: {cur} {price:.2f}")
        return

    existing = _find_open_issue(marker, ALERT_LABEL)
    if existing:
        _gh("POST", f"/issues/{existing['number']}/comments",
            json={"body": f"↩️ Back above target: now {cur} {price:.2f} — {now}. Closing."})
        _gh("PATCH", f"/issues/{existing['number']}", json={"state": "closed"})


def report_failures(failures: list):
    """Scraper problems → open, or append to, a single rolling error issue."""
    marker = "<!-- pw-errors -->"
    now = datetime.now(timezone.utc).isoformat()
    listing = "\n".join(f"- {f}" for f in failures)

    if not _configured():
        print(f"[no github token] SCRAPER PROBLEMS:\n{listing}")
        return

    existing = _find_open_issue(marker, ERROR_LABEL)
    if existing:
        _gh("POST", f"/issues/{existing['number']}/comments",
            json={"body": f"⚠️ {now}\n{listing}"})
    else:
        body = (
            "price-watcher hit scraping problems — usually a site changed its "
            "layout and a selector needs updating.\n\n"
            f"**{now}**\n{listing}\n\n"
            "_Close this issue once the selectors are fixed; a new one opens "
            f"if problems recur._\n\n{marker}"
        )
        _gh("POST", "/issues",
            json={"title": "⚠️ price-watcher: scraper problems",
                  "body": body, "labels": [ERROR_LABEL]})


# ---------- state ----------

def load(path, default):
    if path.exists():
        return json.loads(path.read_text())
    return default


def log_history(item_id: str, price: float):
    new = not HISTORY.exists()
    with HISTORY.open("a") as f:
        if new:
            f.write("timestamp,item_id,price\n")
        f.write(f"{datetime.now(timezone.utc).isoformat()},{item_id},{price}\n")


# ---------- main ----------

def main():
    items = load(WATCHLIST, [])
    state = load(STATE, {})
    failures = []

    for item in items:
        iid = item["id"]
        prev = state.get(iid, {})
        try:
            html = fetch_rendered(item["url"]) if item.get("render_js") else fetch_static(item["url"])
            price = extract_price(html, item["selector"])
        except Exception as e:
            failures.append(f"{item['name']}: {type(e).__name__}: {e}")
            state[iid] = {**prev, "last_error": str(e), "checked_at": datetime.now(timezone.utc).isoformat()}
            continue

        log_history(iid, price)
        hit = price <= item["target"]
        was_notified = prev.get("notified", False)
        last_price = prev.get("price")

        # Alert on first hit, or if price dropped further after a previous alert
        should_alert = hit and (not was_notified or (last_price and price < last_price))

        if should_alert:
            open_or_update_alert(item, price)
        elif was_notified and not hit:
            resolve_alert(item, price)

        state[iid] = {
            "price": price,
            "notified": hit,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "last_error": None,
        }
        print(f"{item['name']}: {price} (target {item['target']}) hit={hit}")
        time.sleep(random.uniform(2, 5))  # be polite

    STATE.write_text(json.dumps(state, indent=2))

    if failures:
        report_failures(failures)
        print("\n".join(failures), file=sys.stderr)
        sys.exit(1)  # surface as a red run in Actions


if __name__ == "__main__":
    main()
