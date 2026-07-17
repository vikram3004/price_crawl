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

TG_TOKEN = os.environ.get("TG_TOKEN")
TG_CHAT = os.environ.get("TG_CHAT")

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


# ---------- alerting ----------

def send(text: str):
    if not (TG_TOKEN and TG_CHAT):
        print(f"[no telegram creds] {text}")
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT, "text": text, "disable_web_page_preview": False},
            timeout=15,
        ).raise_for_status()
    except Exception as e:
        print(f"alert failed: {e}", file=sys.stderr)


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
            cur = item.get("currency", "USD")
            send(
                f"🎯 PRICE HIT\n{item['name']}\n"
                f"Now: {cur} {price:.2f} (target {cur} {item['target']:.2f})\n"
                f"{item['url']}"
            )
        elif was_notified and not hit:
            send(f"↩️ Back above target: {item['name']} is now {price:.2f}")

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
        send("⚠️ Scraper problems:\n" + "\n".join(failures))
        print("\n".join(failures), file=sys.stderr)
        sys.exit(1)  # surface as a red run in Actions


if __name__ == "__main__":
    main()
