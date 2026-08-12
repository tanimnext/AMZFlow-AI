"""LLM provider registry with live model discovery.

v6 hardcoded five free-text model inputs in settings.html and duplicated the
provider list in three places (amazon_video_maker._LLM_PROVIDERS,
app.provider_defaults, and the template's <select>). This module is the single
registry: adding a provider is one dict entry, and the settings UI renders
itself from it.
"""

from __future__ import annotations

import requests

import catalog_cache

FETCH_TIMEOUT = 12

# Ordered so the free/local-friendly options come first in the UI.
PROVIDERS = {
    "gemini": {
        "label": "Google Gemini",
        "key_field": "gemini_api_key",
        "model_field": "gemini_model",
        "endpoint_field": None,
        # "-latest" aliases always resolve to Google's current recommended
        # model, so they stay valid without a code change as Google ships new
        # versions. The static list is only the offline/no-key fallback --
        # list_models() replaces it with the live /v1beta/models catalog the
        # moment a key is present.
        "default_model": "gemini-flash-latest",
        "keys_help": "One API key per line. Keys are tried in order.",
        "console_url": "https://aistudio.google.com/apikey",
        "static_models": [
            "gemini-flash-latest",
            "gemini-pro-latest",
            "gemini-2.5-flash",
            "gemini-2.5-pro",
            "gemini-2.0-flash",
        ],
    },
    "openrouter": {
        "label": "OpenRouter",
        "key_field": "openrouter_api_key",
        "model_field": "openrouter_model",
        "endpoint_field": None,
        "default_model": "deepseek/deepseek-v4-flash",
        "keys_help": "OpenRouter keys start with sk-or-.",
        "console_url": "https://openrouter.ai/keys",
        "static_models": [
            "deepseek/deepseek-v4-flash",
            "inclusionai/ling-3.0-flash:free",
            "google/gemini-2.0-flash-exp:free",
            "x-ai/grok-4.1-fast",
            "meta-llama/llama-3.3-70b-instruct:free",
        ],
    },
    "openai": {
        "label": "OpenAI",
        "key_field": "openai_api_key",
        "model_field": "openai_model",
        "endpoint_field": None,
        "default_model": "gpt-4o-mini",
        "keys_help": "One API key per line.",
        "console_url": "https://platform.openai.com/api-keys",
        "static_models": ["gpt-4o-mini", "gpt-4o", "gpt-4.1-mini", "o4-mini"],
    },
    "deepseek": {
        "label": "DeepSeek",
        "key_field": "deepseek_api_key",
        "model_field": "deepseek_model",
        "endpoint_field": "deepseek_endpoint",
        "default_endpoint": "https://api.deepseek.com/v1/chat/completions",
        "default_model": "deepseek-v4-flash",
        "keys_help": "One API key per line.",
        "console_url": "https://platform.deepseek.com/api_keys",
        "static_models": ["deepseek-v4-flash", "deepseek-chat", "deepseek-reasoner"],
    },
    "longcat": {
        "label": "LongCat (OpenAI-compatible)",
        "key_field": "longcat_api_key",
        "model_field": "longcat_model",
        "endpoint_field": "longcat_endpoint",
        "default_endpoint": "https://api.longcat.chat/openai/v1/chat/completions",
        "default_model": "LongCat-Flash-Chat",
        "keys_help": "One API key per line.",
        "console_url": None,
        "static_models": ["LongCat-Flash-Chat", "LongCat-Flash-Thinking"],
    },
    "vertex_gemini": {
        "label": "Google Cloud (Vertex AI)",
        "key_field": "vertex_service_account_private_key",
        "model_field": "vertex_llm_model",
        "endpoint_field": None,
        "default_model": "gemini-2.0-flash-001",
        "keys_help": (
            "Paste the full service-account JSON (Google Cloud Console -> IAM & Admin "
            "-> Service Accounts -> Keys -> Add key -> JSON). Grant it the Vertex AI "
            "User role. Billed to that project, so its $300 free-trial credit applies "
            "-- separate from the Gemini API's own free tier."
        ),
        "console_url": "https://console.cloud.google.com/vertex-ai",
        "static_models": [
            "gemini-2.0-flash-001", "gemini-2.0-flash-lite-001",
            "gemini-1.5-flash-002", "gemini-1.5-pro-002",
        ],
        "extra_fields": [
            {"field": "vertex_project_id", "label": "Google Cloud Project ID", "placeholder": "my-project-id"},
            {"field": "vertex_location", "label": "Region", "placeholder": "us-central1"},
        ],
    },
}

PROVIDER_IDS = tuple(PROVIDERS)


def default_model(provider: str) -> str:
    return PROVIDERS.get(provider, {}).get("default_model", "")


def _models_base(endpoint: str) -> str:
    """Turn a chat-completions endpoint into its sibling /models endpoint."""
    endpoint = (endpoint or "").strip()
    if "/chat/completions" in endpoint:
        return endpoint.split("/chat/completions")[0].rstrip("/") + "/models"
    return endpoint.rstrip("/") + "/models" if endpoint else ""


def _entry(model_id, label=None, group=None, note=None):
    return {
        "id": str(model_id),
        "label": label or str(model_id),
        "group": group or "",
        "note": note or "",
    }


def _fetch_openrouter(_key, _endpoint):
    resp = requests.get("https://openrouter.ai/api/v1/models", timeout=FETCH_TIMEOUT)
    resp.raise_for_status()
    out = []
    for item in resp.json().get("data", []):
        model_id = item.get("id")
        if not model_id:
            continue
        pricing = item.get("pricing") or {}
        try:
            free = float(pricing.get("prompt", 1)) == 0 and float(pricing.get("completion", 1)) == 0
        except (TypeError, ValueError):
            free = str(model_id).endswith(":free")
        context = item.get("context_length")
        note = f"{int(context) // 1000}k ctx" if isinstance(context, (int, float)) and context else ""
        if free:
            note = (note + " · free").strip(" ·")
        out.append(
            _entry(
                model_id,
                item.get("name") or model_id,
                group="Free" if free else "Paid",
                note=note,
            )
        )
    out.sort(key=lambda entry: (entry["group"] != "Free", entry["label"].lower()))
    return out


def _fetch_gemini(api_key, _endpoint):
    if not api_key:
        raise ValueError("A Gemini API key is required to list models")
    resp = requests.get(
        "https://generativelanguage.googleapis.com/v1beta/models",
        params={"key": api_key, "pageSize": 200},
        timeout=FETCH_TIMEOUT,
    )
    resp.raise_for_status()
    out = []
    for item in resp.json().get("models", []):
        name = str(item.get("name", "")).split("/")[-1]
        methods = item.get("supportedGenerationMethods") or []
        if not name or "generateContent" not in methods:
            continue
        if "tts" in name or "embedding" in name or "imagen" in name:
            continue
        out.append(_entry(name, item.get("displayName") or name, group="Gemini"))
    out.sort(key=lambda entry: entry["id"])
    return out


def _fetch_openai_compatible(api_key, endpoint, base=None):
    if not api_key:
        raise ValueError("An API key is required to list models")
    url = base or _models_base(endpoint)
    if not url:
        raise ValueError("No models endpoint is configured for this provider")
    resp = requests.get(
        url, headers={"Authorization": f"Bearer {api_key}"}, timeout=FETCH_TIMEOUT
    )
    resp.raise_for_status()
    payload = resp.json()
    rows = payload.get("data") if isinstance(payload, dict) else payload
    out = []
    for item in rows or []:
        model_id = item.get("id") if isinstance(item, dict) else item
        if model_id:
            out.append(_entry(model_id))
    out.sort(key=lambda entry: entry["id"])
    return out


FETCHERS = {
    "openrouter": _fetch_openrouter,
    "gemini": _fetch_gemini,
    "openai": lambda key, endpoint: _fetch_openai_compatible(
        key, endpoint, "https://api.openai.com/v1/models"
    ),
    "deepseek": _fetch_openai_compatible,
    "longcat": _fetch_openai_compatible,
}


def list_models(provider: str, api_key: str = "", endpoint: str = "", refresh: bool = False) -> dict:
    """Returns {items, source, fetchedAt, error} for one provider."""
    spec = PROVIDERS.get(provider)
    if not spec:
        raise KeyError(provider)

    fallback = [_entry(model, group="Built-in") for model in spec["static_models"]]
    fetcher = FETCHERS.get(provider)
    # A provider that needs a key but has none goes straight to the built-in
    # list: attempting the call would only produce a 401 the user cannot act on.
    if fetcher is not None and provider != "openrouter" and not api_key:
        fetcher = None

    result = catalog_cache.resolve(
        f"llm:{provider}",
        (lambda: fetcher(api_key, endpoint)) if fetcher else None,
        fallback,
        refresh=refresh,
    )
    result["provider"] = provider
    result["defaultModel"] = spec["default_model"]
    return result


def public_registry() -> list:
    """Registry shape the settings page renders from (never includes secrets)."""
    return [
        {
            "id": provider_id,
            "label": spec["label"],
            "keyField": spec["key_field"],
            "modelField": spec["model_field"],
            "endpointField": spec["endpoint_field"],
            "defaultEndpoint": spec.get("default_endpoint", ""),
            "defaultModel": spec["default_model"],
            "keysHelp": spec["keys_help"],
            "consoleUrl": spec["console_url"],
            "extraFields": spec.get("extra_fields", []),
        }
        for provider_id, spec in PROVIDERS.items()
    ]
