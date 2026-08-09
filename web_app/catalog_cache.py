"""Shared on-disk cache for provider catalogs (LLM models, TTS voices/models).

Catalogs are fetched from third-party APIs that are slow, rate limited, and
occasionally down. Every lookup therefore goes through here: a namespaced JSON
cache in the private data directory with a TTL, and a caller-supplied static
fallback so the UI is never empty just because the network is.
"""

from __future__ import annotations

import json
import threading
import time

from secure_paths import CATALOG_CACHE_FILE

DEFAULT_TTL_SECONDS = 24 * 60 * 60

_LOCK = threading.Lock()


def _read_all() -> dict:
    try:
        with open(CATALOG_CACHE_FILE, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_all(payload: dict) -> None:
    tmp = CATALOG_CACHE_FILE.with_suffix(".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        tmp.replace(CATALOG_CACHE_FILE)
        CATALOG_CACHE_FILE.chmod(0o600)
    except OSError as exc:
        print(f"[CATALOG] cache write failed: {exc}")
        try:
            tmp.unlink()
        except OSError:
            pass


def read(key: str, ttl_seconds: int = DEFAULT_TTL_SECONDS):
    """Returns (entries, fetched_at) or (None, None) when absent/expired."""
    with _LOCK:
        entry = _read_all().get(key)
    if not isinstance(entry, dict):
        return None, None
    fetched_at = entry.get("fetched_at", 0)
    if not isinstance(fetched_at, (int, float)):
        return None, None
    if time.time() - fetched_at > ttl_seconds:
        return None, None
    items = entry.get("items")
    return (items, fetched_at) if isinstance(items, list) else (None, None)


def write(key: str, items: list) -> float:
    fetched_at = time.time()
    with _LOCK:
        payload = _read_all()
        payload[key] = {"fetched_at": fetched_at, "items": items}
        _write_all(payload)
    return fetched_at


def clear(key: str | None = None) -> None:
    with _LOCK:
        if key is None:
            _write_all({})
            return
        payload = _read_all()
        payload.pop(key, None)
        _write_all(payload)


def resolve(key, fetcher, fallback, *, refresh=False, ttl_seconds=DEFAULT_TTL_SECONDS):
    """Cache-through catalog lookup.

    Returns a dict describing where the data came from so the UI can tell the
    user "live from provider" vs "built-in list (provider unreachable)".
    """
    if not refresh:
        cached, fetched_at = read(key, ttl_seconds)
        if cached:
            return {"items": cached, "source": "cache", "fetchedAt": fetched_at, "error": None}

    if fetcher is not None:
        try:
            items = fetcher()
        except Exception as exc:  # noqa: BLE001 - any provider failure degrades to static
            stale, fetched_at = read(key, ttl_seconds=10**9)
            if stale:
                return {"items": stale, "source": "stale", "fetchedAt": fetched_at, "error": str(exc)}
            return {"items": list(fallback), "source": "static", "fetchedAt": None, "error": str(exc)}
        if items:
            fetched_at = write(key, items)
            return {"items": items, "source": "live", "fetchedAt": fetched_at, "error": None}

    return {"items": list(fallback), "source": "static", "fetchedAt": None, "error": None}
