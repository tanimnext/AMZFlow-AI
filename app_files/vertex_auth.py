"""OAuth2 access tokens for Google Cloud Vertex AI, from a pasted
service-account JSON.

Lets the LLM script-writer and Gemini TTS draw on a Google Cloud project's
billing (and its $300 free-trial credit) instead of the separate AI Studio
API key quota -- same Gemini models, different auth and endpoint.

Uses google-auth (already a dependency for YouTube OAuth) to sign the
JWT-bearer assertion and exchange it for an access token, instead of
hand-rolling RS256 signing.
"""
from __future__ import annotations

import json
import threading
import time

from google.auth.transport.requests import Request
from google.oauth2 import service_account

_SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]
_lock = threading.Lock()
_cache: dict[int, tuple[str, float]] = {}  # hash(service_account_json) -> (token, expiry_epoch)


def get_access_token(service_account_json: str) -> str:
    """Returns a cached, auto-refreshed OAuth2 access token for the given
    service-account JSON. Raises ValueError if the JSON is missing/invalid."""
    raw = str(service_account_json or "").strip()
    if not raw:
        raise ValueError(
            "No Vertex AI service account JSON configured (Settings -> AI Provider -> Google Cloud)"
        )
    cache_key = hash(raw)
    with _lock:
        cached = _cache.get(cache_key)
        if cached and cached[1] - 60 > time.time():
            return cached[0]
        try:
            info = json.loads(raw)
        except ValueError as exc:
            raise ValueError(f"Vertex AI service account JSON is invalid: {exc}") from exc
        try:
            credentials = service_account.Credentials.from_service_account_info(info, scopes=_SCOPES)
            credentials.refresh(Request())
        except Exception as exc:  # google-auth raises its own broad exception types here
            raise ValueError(f"Vertex AI authentication failed: {exc}") from exc
        expiry = credentials.expiry.timestamp() if credentials.expiry else time.time() + 3000
        _cache[cache_key] = (credentials.token, expiry)
        return credentials.token


def generate_content_url(project_id: str, location: str, model: str, api_version: str = "v1") -> str:
    """Builds the Vertex AI publishers/google/models generateContent URL.

    Preview models (e.g. the "-preview-tts" Gemini TTS models) are only
    served on the v1beta1 surface -- v1 answers with a 200 that has no
    "candidates" key, since the model isn't recognized there yet.
    """
    project = str(project_id or "").strip()
    if not project:
        raise ValueError("No Vertex AI project ID configured (Settings -> AI Provider -> Google Cloud)")
    loc = str(location or "us-central1").strip() or "us-central1"
    host = "aiplatform.googleapis.com" if loc == "global" else f"{loc}-aiplatform.googleapis.com"
    version = str(api_version or "v1").strip() or "v1"
    return (
        f"https://{host}/{version}/projects/{project}/locations/{loc}"
        f"/publishers/google/models/{model}:generateContent"
    )
