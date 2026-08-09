"""Free, credential-less ASIN verification by scraping the Amazon product
page directly -- the same technique amazon_video_maker.download_assets()
already relies on for the actual render, trimmed down to just the fields a
validation card needs (title, image, price).

Amazon Creators API is more accurate when configured (real price/availability
from the catalog, no risk of a layout-driven mis-scrape), so it stays the
first choice. But requiring it before a user can see ANY signal on their
ASINs was the wrong default -- most creators don't have API access yet, and
"MANUAL_REVIEW" for every single row wasn't useful. This is the fallback that
fires automatically when no Creators API credentials are configured.
"""

from __future__ import annotations

import html as html_module
import re
from concurrent.futures import ThreadPoolExecutor

import requests

FETCH_TIMEOUT = 10
MAX_WORKERS = 6

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
]

_TITLE_RE = re.compile(r'<span id="productTitle"[^>]*>\s*(.*?)\s*</span>', re.DOTALL)
_PRICE_RE = re.compile(r'class="a-offscreen">\s*([^<]+?)\s*<')
_HIRES_RE = re.compile(r'"hiRes"\s*:\s*"([^"]+)"')
_UNAVAILABLE_RE = re.compile(
    r"Currently unavailable|out of stock|no longer available", re.IGNORECASE
)


def _scrape_one(asin: str) -> dict:
    url = f"https://www.amazon.com/dp/{asin}"
    result = {
        "asin": asin,
        "name": "",
        "imageUrl": "",
        "price": "",
        "availability": "UNKNOWN",
        "affiliateUrl": url,
        "validationStatus": "NOT_FOUND",
    }
    for ua in USER_AGENTS:
        try:
            resp = requests.get(
                url,
                headers={
                    "User-Agent": ua,
                    "Accept": "text/html,application/xhtml+xml",
                    "Accept-Language": "en-US,en;q=0.9",
                },
                timeout=FETCH_TIMEOUT,
            )
        except requests.RequestException:
            continue
        if resp.status_code != 200 or len(resp.text) < 10_000:
            continue
        content = resp.text

        title_match = _TITLE_RE.search(content)
        if not title_match:
            continue  # likely a CAPTCHA/interstitial -- try the next UA
        result["name"] = html_module.unescape(title_match.group(1)).strip()[:180]

        price_match = _PRICE_RE.search(content)
        if price_match:
            result["price"] = html_module.unescape(price_match.group(1)).strip()[:40]

        image_match = _HIRES_RE.search(content)
        if image_match:
            result["imageUrl"] = image_match.group(1)

        result["availability"] = (
            "OUT_OF_STOCK" if _UNAVAILABLE_RE.search(content) else "AVAILABLE"
        )
        result["validationStatus"] = "SCRAPED"
        break
    return result


def lookup_asins(asins: list[str]) -> dict[str, dict]:
    """Fetches each ASIN's public product page in parallel. Returns
    {asin: result}; a lookup that fails entirely still returns a NOT_FOUND
    row rather than raising, so one bad ASIN never blocks the batch."""
    unique = list(dict.fromkeys(a.strip().upper() for a in asins if a and a.strip()))
    if not unique:
        return {}
    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(unique))) as pool:
        rows = list(pool.map(_scrape_one, unique))
    return {row["asin"]: row for row in rows}
