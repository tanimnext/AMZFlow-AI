"""Persistent, review-first article URL ingestion for video generation."""

from __future__ import annotations

import ipaddress
import json
import re
import socket
import sqlite3
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlencode, urljoin, urlsplit, urlunsplit

import requests


MAX_SOURCE_URLS = 20
MAX_PRODUCTS_PER_JOB = 10
MAX_ARTICLE_BYTES = 2 * 1024 * 1024
ASIN_PATTERN = re.compile(
    r"(?:/dp/|/gp/product/|/product/)([A-Z0-9]{10})(?:[/?#]|$)",
    re.IGNORECASE,
)
TRACKING_QUERY_PREFIXES = ("utm_",)
TRACKING_QUERY_KEYS = {"fbclid", "gclid", "ref", "ref_"}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _canonical_source_url(raw: str) -> str:
    value = str(raw or "").strip()
    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise ValueError("Each source must be a valid HTTPS URL") from exc
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise ValueError("Each source must be a valid HTTPS URL")
    if parsed.username or parsed.password:
        raise ValueError("Source URLs cannot include credentials")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Source URL contains an invalid port") from exc
    if port not in (None, 443):
        raise ValueError("Source URLs must use the standard HTTPS port")

    host = parsed.hostname.rstrip(".").lower()
    netloc = host if port is None else f"{host}:{port}"
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    if path != "/":
        path = path.rstrip("/")
    query = urlencode(
        [
            (key, val)
            for key, val in parse_qsl(parsed.query, keep_blank_values=True)
            if key.lower() not in TRACKING_QUERY_KEYS
            and not key.lower().startswith(TRACKING_QUERY_PREFIXES)
        ],
        doseq=True,
    )
    return urlunsplit(("https", netloc, path, query, ""))


def normalize_source_urls(values: list[str]) -> list[str]:
    if not isinstance(values, list):
        raise ValueError("urls must be an array")
    if len(values) > MAX_SOURCE_URLS:
        raise ValueError(f"A batch supports at most {MAX_SOURCE_URLS} URLs")
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in values:
        url = _canonical_source_url(raw)
        if url not in seen:
            normalized.append(url)
            seen.add(url)
    if not normalized:
        raise ValueError("Add at least one source URL")
    return normalized


def validate_public_url(raw: str, resolver=socket.getaddrinfo) -> str:
    """Reject URLs resolving to local, private, reserved, or multicast networks.

    Deliberately does NOT route through _canonical_source_url()'s path
    canonicalization (trailing-slash stripping, tracking-param removal) --
    this function also revalidates every redirect hop inside
    fetch_article_html's loop, and many sites (WordPress in particular) 301
    a no-trailing-slash path to the trailing-slash form as their canonical
    URL. Stripping the slash back off on every hop fought that redirect and
    bounced between the two forms until max_redirects gave up, even though
    the page was fetchable the whole time (root-caused against a real site:
    carmechan.com's buying-guide URLs 301 bare-path -> path/, forever).
    Safety validation (scheme/credentials/port/DNS) doesn't need
    canonicalization to do its job.
    """
    value = str(raw or "").strip()
    parsed = urlsplit(value)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise ValueError("Each source must be a valid HTTPS URL")
    if parsed.username or parsed.password:
        raise ValueError("Source URLs cannot include credentials")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Source URL contains an invalid port") from exc
    if port not in (None, 443):
        raise ValueError("Source URLs must use the standard HTTPS port")
    url = value
    host = (parsed.hostname or "").rstrip(".").lower()
    try:
        literal = ipaddress.ip_address(host)
        addresses = [literal]
    except ValueError:
        try:
            records = resolver(host, 443, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise ValueError("Source hostname could not be resolved") from exc
        addresses = []
        for record in records:
            try:
                addresses.append(ipaddress.ip_address(record[4][0]))
            except (IndexError, ValueError):
                continue
    if not addresses or any(not address.is_global for address in addresses):
        raise ValueError("Source URL must resolve only to public internet addresses")
    return url


def fetch_article_html(
    source_url: str,
    *,
    session=None,
    validator=validate_public_url,
    max_redirects: int = 3,
) -> str:
    """Fetch a bounded HTML document while validating every redirect hop."""
    client = session or requests.Session()
    current = source_url
    for _ in range(max_redirects + 1):
        current = validator(current)
        response = client.get(
            current,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 Safari/537.36 EzAmazTube/1.0"
                ),
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "en-US,en;q=0.8",
            },
            timeout=(5, 20),
            allow_redirects=False,
            stream=True,
        )
        try:
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("Location", "")
                if not location:
                    raise ValueError("Source returned an invalid redirect")
                current = urljoin(current, location)
                continue
            if response.status_code != 200:
                raise ValueError(f"Source returned HTTP {response.status_code}")
            content_type = response.headers.get("Content-Type", "").lower()
            if "text/html" not in content_type and "application/xhtml+xml" not in content_type:
                raise ValueError("Source must return an HTML document")
            try:
                declared = int(response.headers.get("Content-Length", "0") or 0)
            except ValueError:
                declared = 0
            if declared > MAX_ARTICLE_BYTES:
                raise ValueError("Source article is larger than 2 MB")
            chunks = []
            total = 0
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                total += len(chunk)
                if total > MAX_ARTICLE_BYTES:
                    raise ValueError("Source article is larger than 2 MB")
                chunks.append(chunk)
            encoding = response.encoding or "utf-8"
            return b"".join(chunks).decode(encoding, errors="replace")
        finally:
            response.close()
    raise ValueError("Source redirected too many times")


def extract_asin(value: str) -> str | None:
    match = ASIN_PATTERN.search(str(value or ""))
    return match.group(1).upper() if match else None


def _clean_text(value: str, limit: int = 300) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def _humanize_slug(slug: str) -> str:
    """Turns a URL path segment ("best-nexar-pro-dash-cam-review-2026") into
    a readable title, for pages with no <title>/og:title we can fall back
    to. Left as raw hyphenated text, this ends up literally on screen as the
    video title -- with any exotic dash/hyphen variant in the URL (en-dash,
    non-breaking hyphen) rendering as a tofu box, since the video font only
    ships plain ASCII glyphs.
    """
    text = unquote(str(slug or ""))
    # Normalize every dash-like codepoint (en/em dash, non-breaking hyphen,
    # etc.) to a plain ASCII hyphen before splitting on it.
    text = re.sub(r"[‐-―−]", "-", text)
    text = re.sub(r"[-_]+", " ", text)
    text = re.sub(r"\.(html?|php|aspx?)$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"[^\x20-\x7e]", "", text)
    return _clean_text(text).title()


def _product_name_from_heading(heading: str, fallback: str) -> str:
    name = _clean_text(heading)
    name = re.sub(r"^\s*(?:#?\d+[.)-]?\s*)", "", name)
    name = re.sub(r"^\s*best\s+[^:]{0,50}:\s*", "", name, flags=re.IGNORECASE)
    if not name or name.lower() in {
        "check price",
        "view on amazon",
        "buy on amazon",
        "amazon",
    }:
        name = _clean_text(fallback)
    return name or "Amazon Product"


class _ArticleParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.meta_title = ""
        self.site_name = ""
        self.current_heading = ""
        self._tag = ""
        self._buffer: list[str] = []
        self._anchor_href = ""
        self._anchor_heading = ""
        self.links: list[dict[str, str]] = []
        self.json_ld: list[str] = []

    def handle_starttag(self, tag, attrs):
        self._tag = tag.lower()
        attributes = {str(key).lower(): str(value or "") for key, value in attrs}
        if self._tag == "meta":
            prop = (attributes.get("property") or attributes.get("name", "")).lower()
            if prop in {"og:title", "twitter:title"} and not self.meta_title:
                self.meta_title = _clean_text(attributes.get("content", ""))
            if prop == "og:site_name" and not self.site_name:
                self.site_name = _clean_text(attributes.get("content", ""))
        if self._tag in {"h1", "h2", "h3", "h4", "title", "a", "script"}:
            self._buffer = []
        if self._tag == "a":
            self._anchor_href = attributes.get("href", "")
            self._anchor_heading = self.current_heading
        if self._tag == "script":
            self._script_type = attributes.get("type", "").lower()

    def handle_data(self, data):
        if self._tag in {"h1", "h2", "h3", "h4", "title", "a", "script"}:
            self._buffer.append(data)

    def handle_endtag(self, tag):
        tag = tag.lower()
        text = _clean_text(" ".join(self._buffer), 2_000)
        if tag == "title" and text:
            self.title_parts.append(text)
        elif tag in {"h1", "h2", "h3", "h4"} and text:
            self.current_heading = text
        elif tag == "a" and self._anchor_href:
            self.links.append(
                {
                    "href": self._anchor_href,
                    "text": text,
                    "heading": self._anchor_heading,
                }
            )
            self._anchor_href = ""
        elif tag == "script" and getattr(self, "_script_type", "") == "application/ld+json":
            if text:
                self.json_ld.append(text)
        self._tag = ""
        self._buffer = []


def _strip_site_suffix(title: str, site_name: str, hostname: str) -> str:
    """Removes a trailing " - Site Name" / " | Site Name" / " – Site Name"
    from a raw <title> tag. v6 used the full page title verbatim as the video
    keyword, so a title like "Best Dash Cam Review: Top Rated - Car Mechan"
    kept the site's own branding in the generated video's title.

    Prefers the page's own declared og:site_name (most reliable -- it's the
    site naming itself) and falls back to the bare hostname/registrable
    domain label so this still helps on pages that don't set og:site_name.
    """
    text = str(title or "")
    candidates = [c for c in (site_name, hostname.split(".")[0] if hostname else "") if c]
    for candidate in candidates:
        pattern = re.compile(
            r"\s*[-|–:]\s*" + re.escape(candidate) + r"\s*$", re.IGNORECASE
        )
        stripped = pattern.sub("", text)
        if stripped and stripped != text:
            return stripped.strip()
    return text


def _title_to_keyword(title: str) -> str:
    keyword = re.sub(r"\(\s*20\d{2}\s*\)", "", title)
    keyword = re.sub(r"\b20\d{2}\b", "", keyword)
    keyword = re.sub(r"^\s*\d+\s+", "", keyword)
    keyword = re.sub(r"\s*[-|:]\s*(review|reviews|buying guide).*$", "", keyword, flags=re.I)
    return _clean_text(keyword, 120) or "Amazon Product Review"


_AMAZON_HOSTNAME_SUFFIXES = (".amazon.com",)
_AMAZON_HOSTNAMES = frozenset({"amazon.com", "amzn.to"})
_PRODUCT_CTA_RE = re.compile(
    r"\b(check price|buy now|view on amazon|buy on amazon|shop now|see price|"
    r"see (?:the )?price|get it (?:now|here)|shop (?:this|the) deal|check (?:it out|deal)|"
    r"view (?:deal|price))\b",
    re.IGNORECASE,
)


def _is_amazon_hostname(hostname: str) -> bool:
    hostname = (hostname or "").lower()
    return hostname in _AMAZON_HOSTNAMES or hostname.endswith(_AMAZON_HOSTNAME_SUFFIXES)


def _resolve_redirect_target(url: str, *, session=None, timeout=5, max_hops=4) -> str | None:
    """Bounded redirect chase for outbound links that AREN'T a direct Amazon
    URL but look like a product CTA. Most content sites route affiliate
    clicks through a cloaking/tracking redirector (their own domain, or a
    network like Skimlinks/VigLink/Partnerize/Impact) rather than linking
    amazon.com directly -- without this, "any website" only ever worked for
    the handful of sites that happen to link Amazon directly. This resolves
    where the link actually ends up so those get picked up too; the caller
    still only accepts the result if it lands on an Amazon hostname.

    HEAD-only, no body ever downloaded, hop-capped, short timeout -- a slow
    or dead redirector costs at most `timeout` seconds once, not the whole
    batch job.
    """
    client = session or requests.Session()
    current = url
    for _ in range(max_hops):
        try:
            resp = client.head(current, allow_redirects=False, timeout=timeout)
        except requests.RequestException:
            return None
        if resp.status_code in {301, 302, 303, 307, 308}:
            location = resp.headers.get("Location", "")
            if not location:
                return None
            current = urljoin(current, location)
            continue
        return current
    return None


def extract_article(source_url: str, html_text: str, *, resolve_redirects: bool = True) -> dict:
    parser = _ArticleParser()
    parser.feed(str(html_text or "")[:3_000_000])
    # The URL slug is the keyword source (de-hyphenated, title-cased by
    # _humanize_slug) -- it's what the source site optimized for SEO, and
    # skips whatever a <title> tag adds on top (site branding, "| Reviews
    # 2026", etc). The page's own title is only a fallback for URLs with no
    # usable slug (bare domain, numeric/ID-only path, and similar).
    slug_title = _humanize_slug(urlsplit(source_url).path.rsplit("/", 1)[-1])
    meta_title = _clean_text(parser.meta_title or (parser.title_parts[0] if parser.title_parts else ""), 200)
    raw_title = slug_title or meta_title
    title = _strip_site_suffix(raw_title, parser.site_name, urlsplit(source_url).hostname or "")

    products: list[dict] = []
    seen_asins: set[str] = set()

    def _add_product(asin, link, absolute):
        if not asin or asin in seen_asins:
            return False
        seen_asins.add(asin)
        products.append(
            {
                "asin": asin,
                "name": _product_name_from_heading(link["heading"], link["text"]),
                "sourceUrl": absolute[:2_000],
                "rank": len(products) + 1,
                "availability": "UNVERIFIED",
                "affiliateUrl": "",
                "features": [],
                "isIncluded": True,
            }
        )
        return True

    # Pass 1: links that already point straight at Amazon -- the common
    # case, and always given priority (no network round-trip needed).
    deferred: list[dict] = []
    for link in parser.links:
        absolute = urljoin(source_url, link["href"])
        hostname = (urlsplit(absolute).hostname or "").lower()
        if _is_amazon_hostname(hostname):
            _add_product(extract_asin(absolute), link, absolute)
        elif resolve_redirects and (link["heading"] or _PRODUCT_CTA_RE.search(link["text"] or "")):
            deferred.append({"link": link, "absolute": absolute})
        if len(products) >= MAX_PRODUCTS_PER_JOB:
            break

    # Pass 2: only for links that look like a product CTA (has a heading, or
    # anchor text like "Check Price") and weren't already a direct Amazon
    # link -- resolve where the cloaking redirector actually sends the
    # visitor, and keep it only if that's Amazon. Lower priority than pass 1
    # by construction: it only fills remaining slots up to the cap.
    if resolve_redirects and len(products) < MAX_PRODUCTS_PER_JOB and deferred:
        session = requests.Session()
        for entry in deferred[:15]:
            if len(products) >= MAX_PRODUCTS_PER_JOB:
                break
            resolved = _resolve_redirect_target(entry["absolute"], session=session)
            if not resolved:
                continue
            hostname = (urlsplit(resolved).hostname or "").lower()
            if not _is_amazon_hostname(hostname):
                continue
            _add_product(extract_asin(resolved), entry["link"], resolved)

    content_type = "SINGLE" if len(products) == 1 else "ROUNDUP"
    confidence = min(95, (50 if title else 30) + min(len(products), 3) * 15)
    if not products:
        confidence = min(confidence, 45)
    intent_words = ("best", "review", "top", "buying guide", "versus", " vs ")
    has_buyer_intent = any(word in title.lower() for word in intent_words)
    if len(products) >= 5 and has_buyer_intent:
        revenue_potential = "HIGH"
    elif products and has_buyer_intent:
        revenue_potential = "MEDIUM"
    else:
        revenue_potential = "LOW"
    return {
        "articleTitle": title,
        "keyword": _title_to_keyword(title),
        "contentType": content_type,
        "confidence": confidence,
        "revenuePotential": revenue_potential,
        "products": products,
    }


class CreatorsApiClient:
    """Small Amazon US Creators API adapter with in-memory token caching."""

    TOKEN_ENDPOINTS = {
        "2.1": "https://creatorsapi.auth.us-east-1.amazoncognito.com/oauth2/token",
        "3.1": "https://api.amazon.com/auth/o2/token",
    }
    ITEMS_ENDPOINT = "https://creatorsapi.amazon/catalog/v1/getItems"
    RESOURCES = [
        "itemInfo.title",
        "itemInfo.features",
        "images.primary.large",
        "offersV2.listings.availability",
        "offersV2.listings.price",
    ]

    def __init__(self, settings: dict, requester=requests):
        self.client_id = str(settings.get("creators_api_client_id", "")).strip()
        self.client_secret = str(
            settings.get("creators_api_client_secret", "")
        ).strip()
        self.credential_version = str(
            settings.get("creators_api_credential_version", "3.1")
        ).strip()
        self.partner_tag = str(settings.get("partner_tag", "")).strip()
        self.requester = requester
        self._token = ""
        self._token_expires_at = 0.0
        self._token_lock = threading.Lock()

    @property
    def is_configured(self) -> bool:
        return bool(
            self.client_id
            and self.client_secret
            and self.partner_tag
            and self.credential_version in self.TOKEN_ENDPOINTS
        )

    def _get_token(self) -> str:
        if not self.is_configured:
            raise ValueError("Amazon Creators API credentials are not configured")
        with self._token_lock:
            if self._token and time.monotonic() < self._token_expires_at:
                return self._token
            endpoint = self.TOKEN_ENDPOINTS[self.credential_version]
            if self.credential_version.startswith("3."):
                response = self.requester.post(
                    endpoint,
                    json={
                        "grant_type": "client_credentials",
                        "client_id": self.client_id,
                        "client_secret": self.client_secret,
                        "scope": "creatorsapi::default",
                    },
                    timeout=20,
                )
            else:
                response = self.requester.post(
                    endpoint,
                    data={
                        "grant_type": "client_credentials",
                        "client_id": self.client_id,
                        "client_secret": self.client_secret,
                        "scope": "creatorsapi/default",
                    },
                    timeout=20,
                )
            try:
                response.raise_for_status()
                payload = response.json()
                token = str(payload.get("access_token", "")).strip()
                if not token:
                    raise ValueError("Amazon Creators API returned no access token")
                expires_in = max(60, int(payload.get("expires_in", 3600)))
            except (ValueError, requests.RequestException) as exc:
                raise ValueError("Amazon Creators API authentication failed") from exc
            self._token = token
            self._token_expires_at = time.monotonic() + expires_in - 30
            return token

    def _headers(self) -> dict[str, str]:
        token = self._get_token()
        authorization = f"Bearer {token}"
        if self.credential_version.startswith("2."):
            authorization += f", Version {self.credential_version}"
        return {
            "Authorization": authorization,
            "Content-Type": "application/json",
            "x-marketplace": "www.amazon.com",
        }

    @staticmethod
    def _parse_item(item: dict) -> dict:
        item_info = item.get("itemInfo") if isinstance(item.get("itemInfo"), dict) else {}
        title_data = item_info.get("title") if isinstance(item_info.get("title"), dict) else {}
        features_data = (
            item_info.get("features")
            if isinstance(item_info.get("features"), dict)
            else {}
        )
        raw_features = features_data.get("displayValues", [])
        if not isinstance(raw_features, list):
            raw_features = []
        offers = item.get("offersV2") if isinstance(item.get("offersV2"), dict) else {}
        listings = offers.get("listings", [])
        listing = listings[0] if isinstance(listings, list) and listings else {}
        availability_data = (
            listing.get("availability")
            if isinstance(listing, dict)
            and isinstance(listing.get("availability"), dict)
            else {}
        )
        price_data = (
            listing.get("price")
            if isinstance(listing, dict) and isinstance(listing.get("price"), dict)
            else {}
        )
        money = price_data.get("money") if isinstance(price_data.get("money"), dict) else {}
        images = item.get("images") if isinstance(item.get("images"), dict) else {}
        primary = images.get("primary") if isinstance(images.get("primary"), dict) else {}
        large = primary.get("large") if isinstance(primary.get("large"), dict) else {}
        return {
            "asin": str(item.get("asin", "")).upper(),
            "name": _clean_text(title_data.get("displayValue"), 180),
            "affiliateUrl": str(item.get("detailPageURL", ""))[:2_000],
            "availability": _clean_text(
                availability_data.get("type")
                or availability_data.get("message")
                or "UNKNOWN",
                40,
            ),
            "price": _clean_text(money.get("displayAmount"), 40),
            "imageUrl": str(large.get("url", ""))[:2_000],
            "features": [_clean_text(value, 300) for value in raw_features[:8]],
        }

    def enrich_products(self, products: list[dict]) -> list[dict]:
        if not self.is_configured:
            return [
                {**product, "validationStatus": "MANUAL_REVIEW"}
                for product in products
            ]
        verified: dict[str, dict] = {}
        asins = list(
            dict.fromkeys(
                str(product.get("asin", "")).upper()
                for product in products
                if re.fullmatch(r"[A-Z0-9]{10}", str(product.get("asin", "")).upper())
            )
        )
        for start in range(0, len(asins), 10):
            response = self.requester.post(
                self.ITEMS_ENDPOINT,
                headers=self._headers(),
                json={
                    "itemIds": asins[start : start + 10],
                    "itemIdType": "ASIN",
                    "marketplace": "www.amazon.com",
                    "partnerTag": self.partner_tag,
                    "resources": self.RESOURCES,
                },
                timeout=30,
            )
            try:
                response.raise_for_status()
                payload = response.json()
            except (ValueError, requests.RequestException) as exc:
                raise ValueError("Amazon product validation failed") from exc
            items_result = payload.get("itemsResult", {})
            items = items_result.get("items", []) if isinstance(items_result, dict) else []
            if not isinstance(items, list):
                items = []
            for raw_item in items:
                if not isinstance(raw_item, dict):
                    continue
                parsed = self._parse_item(raw_item)
                if parsed["asin"]:
                    verified[parsed["asin"]] = parsed

        enriched = []
        for product in products:
            asin = str(product.get("asin", "")).upper()
            catalog = verified.get(asin)
            if not catalog:
                enriched.append(
                    {
                        **product,
                        "validationStatus": "NOT_FOUND",
                        "availability": "UNKNOWN",
                    }
                )
                continue
            enriched.append(
                {
                    **product,
                    **{key: value for key, value in catalog.items() if value},
                    "validationStatus": "VERIFIED",
                }
            )
        return enriched


class BatchStore:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self):
        connection = sqlite3.connect(self.db_path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self):
        with self._connect() as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS content_batches (
                    batch_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS content_jobs (
                    job_id TEXT PRIMARY KEY,
                    batch_id TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    source_url TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error TEXT NOT NULL DEFAULT '',
                    article_title TEXT NOT NULL DEFAULT '',
                    keyword TEXT NOT NULL DEFAULT '',
                    content_type TEXT NOT NULL DEFAULT 'ROUNDUP',
                    confidence INTEGER NOT NULL DEFAULT 0,
                    revenue_potential TEXT NOT NULL DEFAULT 'LOW',
                    products_json TEXT NOT NULL DEFAULT '[]',
                    is_approved INTEGER NOT NULL DEFAULT 0,
                    generated_at TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(batch_id) REFERENCES content_batches(batch_id)
                )
                """
            )
            # `generated_at` is new -- existing databases created before this
            # column existed need it added explicitly; CREATE TABLE IF NOT
            # EXISTS only applies to brand-new files.
            try:
                db.execute("ALTER TABLE content_jobs ADD COLUMN generated_at TEXT NOT NULL DEFAULT ''")
            except sqlite3.OperationalError:
                pass  # already has the column
            db.execute(
                "UPDATE content_jobs SET status = 'QUEUED' "
                "WHERE status IN ('FETCHING', 'EXTRACTING', 'VALIDATING')"
            )

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> dict:
        return {
            "jobId": row["job_id"],
            "batchId": row["batch_id"],
            "position": row["position"],
            "sourceUrl": row["source_url"],
            "status": row["status"],
            "error": row["error"],
            "articleTitle": row["article_title"],
            "keyword": row["keyword"],
            "contentType": row["content_type"],
            "confidence": row["confidence"],
            "revenuePotential": row["revenue_potential"],
            "products": json.loads(row["products_json"]),
            "isApproved": bool(row["is_approved"]),
            "generatedAt": row["generated_at"] or None,
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }

    def create_batch(self, urls: list[str]) -> dict:
        urls = normalize_source_urls(urls)
        batch_id = uuid.uuid4().hex
        created_at = _now()
        with self._connect() as db:
            # A URL that already has a job elsewhere which is either done
            # (generated_at set) or still in flight (any non-FAILED status)
            # is a duplicate -- skip re-queuing it so the same article never
            # gets analyzed/rendered twice. A URL whose only prior attempt(s)
            # FAILED is still allowed through, so retrying by resubmitting
            # the URL works.
            placeholders = ",".join("?" * len(urls))
            existing = db.execute(
                f"SELECT DISTINCT source_url FROM content_jobs "
                f"WHERE source_url IN ({placeholders}) "
                f"AND (generated_at != '' OR status != 'FAILED')",
                urls,
            ).fetchall()
            duplicate_urls = {row["source_url"] for row in existing}
            new_urls = [url for url in urls if url not in duplicate_urls]
            if not new_urls:
                raise ValueError(
                    "Every one of these URLs was already analyzed or generated. "
                    "Check History, or remove them before submitting again."
                )

            db.execute(
                "INSERT INTO content_batches(batch_id, created_at, updated_at) "
                "VALUES (?, ?, ?)",
                (batch_id, created_at, created_at),
            )
            for position, url in enumerate(new_urls):
                db.execute(
                    """
                    INSERT INTO content_jobs(
                        job_id, batch_id, position, source_url, status,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 'QUEUED', ?, ?)
                    """,
                    (uuid.uuid4().hex, batch_id, position, url, created_at, created_at),
                )
        return self.get_batch(batch_id)

    def get_job(self, job_id: str) -> dict:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM content_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        if row is None:
            raise KeyError("Content job was not found")
        return self._row_to_job(row)

    def get_batch(self, batch_id: str) -> dict:
        with self._connect() as db:
            batch = db.execute(
                "SELECT * FROM content_batches WHERE batch_id = ?", (batch_id,)
            ).fetchone()
            rows = db.execute(
                "SELECT * FROM content_jobs WHERE batch_id = ? ORDER BY position",
                (batch_id,),
            ).fetchall()
        if batch is None:
            raise KeyError("Content batch was not found")
        jobs = [self._row_to_job(row) for row in rows]
        asin_counts: dict[str, int] = {}
        for job in jobs:
            for asin in {product.get("asin") for product in job["products"] if product.get("asin")}:
                asin_counts[asin] = asin_counts.get(asin, 0) + 1
        for job in jobs:
            for product in job["products"]:
                occurrence_count = asin_counts.get(product.get("asin"), 0)
                product["duplicateAcrossBatch"] = occurrence_count > 1
                product["batchOccurrenceCount"] = occurrence_count
        counts: dict[str, int] = {}
        for job in jobs:
            counts[job["status"]] = counts.get(job["status"], 0) + 1
        return {
            "batchId": batch_id,
            "createdAt": batch["created_at"],
            "updatedAt": batch["updated_at"],
            "counts": counts,
            "jobs": jobs,
        }

    def list_batches(self, limit: int = 10, only_pending: bool = False) -> list[dict]:
        """`only_pending=True` excludes batches where every job has already
        been generated -- used to find "the batch still awaiting review/
        approval" without a fully-completed batch reappearing as if it still
        needed attention (e.g. on a page reload after Generate Approved)."""
        safe_limit = max(1, min(50, int(limit)))
        with self._connect() as db:
            if only_pending:
                rows = db.execute(
                    "SELECT batch_id FROM content_batches b WHERE EXISTS ("
                    "  SELECT 1 FROM content_jobs j WHERE j.batch_id = b.batch_id AND j.generated_at = ''"
                    ") ORDER BY created_at DESC LIMIT ?",
                    (safe_limit,),
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT batch_id FROM content_batches "
                    "ORDER BY created_at DESC LIMIT ?",
                    (safe_limit,),
                ).fetchall()
        return [self.get_batch(row["batch_id"]) for row in rows]

    def mark_generated(self, batch_id: str) -> None:
        """Marks every approved job in this batch as generated, so it drops
        out of the "still needs review" queue and shows in History instead."""
        with self._connect() as db:
            db.execute(
                "UPDATE content_jobs SET generated_at = ?, updated_at = ? "
                "WHERE batch_id = ? AND is_approved = 1",
                (_now(), _now(), batch_id),
            )

    def list_generated_jobs(self, limit: int = 30) -> list[dict]:
        """Flattened, newest-first list of jobs that have actually been sent
        to the render pipeline -- the History view."""
        safe_limit = max(1, min(100, int(limit)))
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM content_jobs WHERE generated_at != '' "
                "ORDER BY generated_at DESC LIMIT ?",
                (safe_limit,),
            ).fetchall()
        return [self._row_to_job(row) for row in rows]

    def set_status(self, job_id: str, status: str, error: str = "") -> dict:
        allowed = {
            "QUEUED",
            "FETCHING",
            "EXTRACTING",
            "VALIDATING",
            "READY",
            "NEEDS_ATTENTION",
            "FAILED",
        }
        if status not in allowed:
            raise ValueError("Invalid content job status")
        with self._connect() as db:
            db.execute(
                "UPDATE content_jobs SET status = ?, error = ?, updated_at = ? "
                "WHERE job_id = ?",
                (status, _clean_text(error, 500), _now(), job_id),
            )
        return self.get_job(job_id)

    def complete_job(self, job_id: str, result: dict) -> dict:
        products = list(result.get("products") or [])[:MAX_PRODUCTS_PER_JOB]
        status = "READY" if products and int(result.get("confidence", 0)) >= 60 else "NEEDS_ATTENTION"
        with self._connect() as db:
            db.execute(
                """
                UPDATE content_jobs
                   SET status = ?, error = '', article_title = ?, keyword = ?,
                       content_type = ?, confidence = ?, revenue_potential = ?,
                       products_json = ?, is_approved = 0, updated_at = ?
                 WHERE job_id = ?
                """,
                (
                    status,
                    _clean_text(result.get("articleTitle"), 200),
                    _clean_text(result.get("keyword"), 120),
                    result.get("contentType")
                    if result.get("contentType") in {"SINGLE", "ROUNDUP"}
                    else "ROUNDUP",
                    max(0, min(100, int(result.get("confidence", 0)))),
                    result.get("revenuePotential")
                    if result.get("revenuePotential") in {"LOW", "MEDIUM", "HIGH"}
                    else "LOW",
                    json.dumps(products),
                    _now(),
                    job_id,
                ),
            )
        return self.get_job(job_id)

    def update_job(self, job_id: str, changes: dict) -> dict:
        current = self.get_job(job_id)
        is_approved = bool(changes.get("isApproved", current["isApproved"]))
        if is_approved and current["status"] != "READY":
            raise ValueError("Only READY jobs can be approved")
        keyword = _clean_text(changes.get("keyword", current["keyword"]), 120)
        if not keyword:
            raise ValueError("Video keyword is required")
        products = list(changes.get("products", current["products"]))[:MAX_PRODUCTS_PER_JOB]
        clean_products = []
        seen = set()
        for product in products:
            asin = str(product.get("asin", "")).upper().strip()
            if not re.fullmatch(r"[A-Z0-9]{10}", asin) or asin in seen:
                continue
            seen.add(asin)
            clean_products.append(
                {
                    **product,
                    "asin": asin,
                    "name": _clean_text(product.get("name"), 180) or asin,
                    "isIncluded": bool(product.get("isIncluded", True)),
                }
            )
        if is_approved and not any(p["isIncluded"] for p in clean_products):
            raise ValueError("Approve at least one valid product")
        content_type = changes.get("contentType", current["contentType"])
        if content_type not in {"SINGLE", "ROUNDUP"}:
            raise ValueError("contentType must be SINGLE or ROUNDUP")
        with self._connect() as db:
            db.execute(
                """
                UPDATE content_jobs
                   SET keyword = ?, content_type = ?, products_json = ?,
                       is_approved = ?, updated_at = ?
                 WHERE job_id = ?
                """,
                (
                    keyword,
                    content_type,
                    json.dumps(clean_products),
                    int(is_approved),
                    _now(),
                    job_id,
                ),
            )
        return self.get_job(job_id)

    def approved_generator_lines(self, batch_id: str) -> list[str]:
        batch = self.get_batch(batch_id)
        lines = []
        for job in batch["jobs"]:
            if not job["isApproved"]:
                continue
            asins = [
                product["asin"]
                for product in job["products"]
                if product.get("isIncluded", True)
            ]
            if asins:
                lines.append(", ".join([job["keyword"], *asins]))
        return lines


class ContentBatchManager:
    """Runs bounded article analysis while serializing requests per source host."""

    def __init__(
        self,
        store: BatchStore,
        settings_provider,
        *,
        fetcher=fetch_article_html,
        max_workers: int = 4,
    ):
        self.store = store
        self.settings_provider = settings_provider
        self.fetcher = fetcher
        self.executor = ThreadPoolExecutor(
            max_workers=max(1, min(4, int(max_workers))),
            thread_name_prefix="content-analysis",
        )
        self._host_locks: dict[str, threading.Lock] = {}
        self._host_locks_guard = threading.Lock()
        self._active_jobs: set[str] = set()
        self._active_jobs_guard = threading.Lock()
        self._client_lock = threading.Lock()
        self._client_fingerprint: tuple[str, ...] | None = None
        self._client: CreatorsApiClient | None = None

    def _host_lock(self, source_url: str) -> threading.Lock:
        host = (urlsplit(source_url).hostname or "").lower()
        with self._host_locks_guard:
            return self._host_locks.setdefault(host, threading.Lock())

    def _creators_client(self) -> CreatorsApiClient:
        settings = self.settings_provider()
        fingerprint = tuple(
            str(settings.get(key, "")).strip()
            for key in (
                "creators_api_client_id",
                "creators_api_client_secret",
                "creators_api_credential_version",
                "partner_tag",
            )
        )
        with self._client_lock:
            if self._client is None or fingerprint != self._client_fingerprint:
                self._client = CreatorsApiClient(settings)
                self._client_fingerprint = fingerprint
            return self._client

    def creators_client(self) -> CreatorsApiClient:
        """Public accessor so other entry points (Module 2's ASIN validator)
        share this manager's cached client/token instead of building their
        own and re-authenticating on every request."""
        return self._creators_client()

    def _submit_job(self, job_id: str):
        with self._active_jobs_guard:
            if job_id in self._active_jobs:
                return
            self._active_jobs.add(job_id)
        self.executor.submit(self._analyze_job, job_id)

    def start_batch(self, batch_id: str):
        batch = self.store.get_batch(batch_id)
        for job in batch["jobs"]:
            if job["status"] in {"QUEUED", "FAILED"}:
                self._submit_job(job["jobId"])

    def retry_job(self, job_id: str):
        self.store.set_status(job_id, "QUEUED")
        self._submit_job(job_id)

    def resume_pending(self):
        for batch in self.store.list_batches(limit=50):
            self.start_batch(batch["batchId"])

    def _analyze_job(self, job_id: str):
        try:
            job = self.store.get_job(job_id)
            self.store.set_status(job_id, "FETCHING")
            with self._host_lock(job["sourceUrl"]):
                html_text = self.fetcher(job["sourceUrl"])
            self.store.set_status(job_id, "EXTRACTING")
            result = extract_article(job["sourceUrl"], html_text)
            self.store.set_status(job_id, "VALIDATING")
            client = self._creators_client()
            try:
                result["products"] = client.enrich_products(result["products"])
            except ValueError:
                result["products"] = [
                    {
                        **product,
                        "validationStatus": "VALIDATION_FAILED",
                        "availability": product.get("availability", "UNKNOWN"),
                    }
                    for product in result["products"]
                ]
                result["confidence"] = min(int(result.get("confidence", 0)), 55)
            self.store.complete_job(job_id, result)
        except Exception as exc:
            self.store.set_status(job_id, "FAILED", str(exc))
        finally:
            with self._active_jobs_guard:
                self._active_jobs.discard(job_id)
