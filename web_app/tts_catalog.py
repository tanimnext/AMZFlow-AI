"""TTS provider registry with live voice/model discovery.

Describes what each provider *can do* (needs a key? has a model list? supports
rate/pitch?) so the settings UI renders itself from data. v6 had six
hand-written panels plus two hardcoded <option> lists, and the creator page
carried a third, divergent copy of the same dropdown.
"""

from __future__ import annotations

import asyncio

import requests

import catalog_cache
from voice_config import ACCENTS, GEMINI_TTS_MODELS, GEMINI_TTS_VOICES, VOICE_STYLES

FETCH_TIMEOUT = 12

# The curated Edge subset shipped in v6. Still used as the offline fallback and
# as the "Recommended" group when the live catalog is available.
EDGE_RECOMMENDED = [
    ("en-US-AndrewMultilingualNeural", "Andrew Multilingual (US Male)"),
    ("en-US-AvaMultilingualNeural", "Ava Multilingual (US Female)"),
    ("en-US-BrianMultilingualNeural", "Brian Multilingual (US Male)"),
    ("en-US-EmmaMultilingualNeural", "Emma Multilingual (US Female)"),
    ("en-US-AndrewNeural", "Andrew (US Male)"),
    ("en-US-GuyNeural", "Guy (US Male)"),
    ("en-US-JennyNeural", "Jenny (US Female)"),
    ("en-US-AriaNeural", "Aria (US Female)"),
    ("en-US-AvaNeural", "Ava (US Female)"),
    ("en-US-BrianNeural", "Brian (US Male)"),
    ("en-US-ChristopherNeural", "Christopher (US Male)"),
    ("en-US-EmmaNeural", "Emma (US Female)"),
    ("en-US-EricNeural", "Eric (US Male)"),
    ("en-US-MichelleNeural", "Michelle (US Female)"),
    ("en-US-RogerNeural", "Roger (US Male)"),
    ("en-US-SteffanNeural", "Steffan (US Male)"),
    ("en-US-AnaNeural", "Ana (US Female)"),
]

KOKORO_VOICES = [
    ("af_heart", "Heart (US Female)", "American English"),
    ("af_bella", "Bella (US Female)", "American English"),
    ("af_nicole", "Nicole (US Female)", "American English"),
    ("af_sarah", "Sarah (US Female)", "American English"),
    ("af_sky", "Sky (US Female)", "American English"),
    ("am_adam", "Adam (US Male)", "American English"),
    ("am_michael", "Michael (US Male)", "American English"),
    ("bf_emma", "Emma (UK Female)", "British English"),
    ("bf_isabella", "Isabella (UK Female)", "British English"),
    ("bm_george", "George (UK Male)", "British English"),
    ("bm_lewis", "Lewis (UK Male)", "British English"),
]

PROVIDERS = {
    "kokoro": {
        "label": "Kokoro (Free · Local)",
        "blurb": "Runs entirely on this Mac. No key, no internet, no usage limit.",
        "needs_key": False,
        "key_field": None,
        "voice_field": "kokoro_voice",
        "model_field": None,
        "voices": "static",
        "models": None,
        "supports_rate": False,
        "supports_pitch": False,
        "director": False,
        "paid": False,
    },
    "edge": {
        "label": "Edge TTS (Free)",
        "blurb": "Microsoft neural voices. Free, online, supports speed and pitch.",
        "needs_key": False,
        "key_field": None,
        "voice_field": "edge_voice",
        "model_field": None,
        "voices": "dynamic",
        "models": None,
        "supports_rate": True,
        "supports_pitch": True,
        "director": False,
        "paid": False,
    },
    "gemini": {
        "label": "Gemini TTS (Free tier)",
        "blurb": "Uses your Gemini API key. Adds style, accent, pace and director notes.",
        "needs_key": True,
        "key_field": "gemini_api_key",
        "voice_field": "gemini_tts_voice",
        "model_field": "gemini_tts_model",
        "voices": "static",
        "models": "static",
        "supports_rate": False,
        "supports_pitch": False,
        "director": True,
        "paid": False,
    },
    "elevenlabs": {
        "label": "ElevenLabs (Paid)",
        "blurb": "Your ElevenLabs voice library is listed live once a key is saved.",
        "needs_key": True,
        "key_field": "elevenlabs_api_key",
        "voice_field": "elevenlabs_voice_id",
        "model_field": "elevenlabs_model_id",
        "voices": "dynamic",
        "models": "dynamic",
        "supports_rate": False,
        "supports_pitch": False,
        "director": False,
        "paid": True,
    },
    "cartesia": {
        "label": "Cartesia (Paid)",
        "blurb": "Ultra-low-latency voices.",
        "needs_key": True,
        "key_field": "cartesia_api_key",
        "voice_field": "cartesia_voice_id",
        "model_field": "cartesia_model_id",
        "voices": "dynamic",
        "models": "static",
        "supports_rate": False,
        "supports_pitch": False,
        "director": False,
        "paid": True,
    },
    "ai33pro": {
        "label": "AI33 Pro (Paid)",
        "blurb": "Asynchronous rendering API; previews take a few seconds longer.",
        "needs_key": True,
        "key_field": "ai33pro_api_key",
        "voice_field": "ai33pro_voice_id",
        "model_field": "ai33pro_model_id",
        "voices": "static",
        "models": "static",
        "supports_rate": False,
        "supports_pitch": False,
        "director": False,
        "paid": True,
    },
    "google_cloud_tts": {
        "label": "Google Cloud TTS (Chirp3-HD / WaveNet)",
        "blurb": (
            "Chirp3-HD is the most human-like free tier Google offers (1M characters/month "
            "free, then paid). Billed through the same Google Cloud project configured under "
            "AI Provider -> Google Cloud (Vertex AI) -- set the service account JSON, project "
            "ID, and region there; a different Google API from \"Gemini TTS via Google Cloud\" "
            "above, same billing account."
        ),
        # Same reasoning as vertex_gemini below: no key_field/extra_fields --
        # the AI Provider panel already renders the shared vertex_project_id/
        # vertex_location/vertex_service_account_private_key inputs.
        "needs_key": False,
        "key_field": None,
        "voice_field": "google_tts_voice_id",
        "model_field": None,
        "voices": "static",
        "models": None,
        "supports_rate": False,
        "supports_pitch": False,
        "director": False,
        "paid": False,
    },
    "deepgram": {
        "label": "Deepgram Aura-2 (Paid)",
        "blurb": "Fast, natural-sounding voices; the model id doubles as the voice choice.",
        "needs_key": True,
        "key_field": "deepgram_api_key",
        "voice_field": "deepgram_voice_id",
        "model_field": None,
        "voices": "static",
        "models": None,
        "supports_rate": False,
        "supports_pitch": False,
        "director": False,
        "paid": True,
    },
    "vertex_gemini": {
        "label": "Gemini TTS via Google Cloud (Vertex AI)",
        "blurb": (
            "Same Gemini voices as \"Gemini TTS\", billed through the Google Cloud project "
            "configured under AI Provider -> Google Cloud (Vertex AI) -- set the service "
            "account JSON, project ID, and region there; both LLM and TTS share one config."
        ),
        # No key_field/extra_fields here on purpose: the AI Provider panel
        # already renders inputs with these exact ids (vertex_project_id,
        # vertex_location, vertex_service_account_private_key) for the same
        # underlying settings. Two panels rendering elements with the same
        # id at once meant getElementById() only ever reached the first one
        # (the AI Provider panel) on page load -- the Voice panel's
        # identical-id inputs stayed empty, which looked like a saved
        # config "disappearing" after every reload.
        "needs_key": False,
        "key_field": None,
        "voice_field": "gemini_tts_voice",
        "model_field": "vertex_tts_model",
        "voices": "static",
        "models": "static",
        "supports_rate": False,
        "supports_pitch": False,
        "director": True,
        "paid": False,
    },
}

PROVIDER_IDS = tuple(PROVIDERS)

STATIC_MODELS = {
    "gemini": [{"id": key, "label": label} for key, label in GEMINI_TTS_MODELS.items()],
    "vertex_gemini": [{"id": key, "label": label} for key, label in GEMINI_TTS_MODELS.items()],
    "cartesia": [
        {"id": "sonic-3.5", "label": "Sonic 3.5"},
        {"id": "sonic-3", "label": "Sonic 3"},
        {"id": "sonic-2", "label": "Sonic 2"},
    ],
    "ai33pro": [
        {"id": "eleven_multilingual_v2", "label": "Multilingual v2"},
        {"id": "eleven_turbo_v2_5", "label": "Turbo v2.5"},
    ],
    "elevenlabs": [
        {"id": "eleven_multilingual_v2", "label": "Multilingual v2"},
        {"id": "eleven_turbo_v2_5", "label": "Turbo v2.5"},
        {"id": "eleven_flash_v2_5", "label": "Flash v2.5"},
    ],
}


def _voice(voice_id, label=None, group="", note=""):
    return {"id": str(voice_id), "label": label or str(voice_id), "group": group, "note": note}


# ------------------------------------------------------------------ voices ---


def _edge_static():
    return [_voice(vid, label, "Recommended") for vid, label in EDGE_RECOMMENDED]


def _fetch_edge():
    """Full Microsoft catalog (~300 voices) via edge_tts.

    edge_tts.list_voices() is a coroutine; this runs on a Flask worker thread
    with no event loop of its own, so a private loop is created and closed.
    """
    import edge_tts

    loop = asyncio.new_event_loop()
    try:
        raw = loop.run_until_complete(edge_tts.list_voices())
    finally:
        loop.close()

    recommended = {vid for vid, _ in EDGE_RECOMMENDED}
    out = _edge_static()
    for item in raw:
        vid = item.get("ShortName")
        if not vid or vid in recommended:
            continue
        locale = item.get("Locale", "")
        gender = item.get("Gender", "")
        friendly = str(item.get("FriendlyName", "")).replace("Microsoft ", "")
        name = friendly.split(" Online")[0] or vid
        personalities = ", ".join(
            (item.get("VoiceTag") or {}).get("VoicePersonalities") or []
        )
        out.append(
            _voice(vid, f"{name} ({gender})", group=locale, note=personalities)
        )
    out.sort(key=lambda entry: (entry["group"] != "Recommended", entry["group"], entry["label"]))
    return out


def _fetch_elevenlabs(api_key):
    if not api_key:
        raise ValueError("An ElevenLabs API key is required to list voices")
    resp = requests.get(
        "https://api.elevenlabs.io/v1/voices",
        headers={"xi-api-key": api_key},
        timeout=FETCH_TIMEOUT,
    )
    resp.raise_for_status()
    out = []
    for item in resp.json().get("voices", []):
        vid = item.get("voice_id")
        if not vid:
            continue
        labels = item.get("labels") or {}
        note = ", ".join(
            str(labels[key]) for key in ("accent", "age", "gender", "use_case") if labels.get(key)
        )
        out.append(_voice(vid, item.get("name") or vid, item.get("category") or "", note))
    out.sort(key=lambda entry: (entry["group"], entry["label"].lower()))
    return out


def _fetch_cartesia(api_key):
    if not api_key:
        raise ValueError("A Cartesia API key is required to list voices")
    resp = requests.get(
        "https://api.cartesia.ai/voices",
        headers={"X-API-Key": api_key, "Cartesia-Version": "2026-03-01"},
        timeout=FETCH_TIMEOUT,
    )
    resp.raise_for_status()
    payload = resp.json()
    rows = payload.get("data") if isinstance(payload, dict) else payload
    out = []
    for item in rows or []:
        vid = item.get("id")
        if vid:
            out.append(
                _voice(vid, item.get("name") or vid, item.get("language") or "", item.get("description") or "")
            )
    out.sort(key=lambda entry: (entry["group"], entry["label"].lower()))
    return out


DEEPGRAM_AURA2_VOICES = [
    ("aura-2-thalia-en", "Thalia (US Female)"),
    ("aura-2-luna-en", "Luna (US Female)"),
    ("aura-2-stella-en", "Stella (US Female)"),
    ("aura-2-athena-en", "Athena (UK Female)"),
    ("aura-2-hera-en", "Hera (US Female)"),
    ("aura-2-orion-en", "Orion (US Male)"),
    ("aura-2-arcas-en", "Arcas (US Male)"),
    ("aura-2-perseus-en", "Perseus (US Male)"),
    ("aura-2-angus-en", "Angus (Irish Male)"),
    ("aura-2-orpheus-en", "Orpheus (US Male)"),
    ("aura-2-helios-en", "Helios (UK Male)"),
    ("aura-2-zeus-en", "Zeus (US Male)"),
]

GOOGLE_CLOUD_TTS_VOICES = [
    ("en-US-Chirp3-HD-Sulafat", "Sulafat (Warm Female)", "Chirp3-HD"),
    ("en-US-Chirp3-HD-Umbriel", "Umbriel (Calm Male)", "Chirp3-HD"),
    ("en-US-Chirp3-HD-Vindemiatrix", "Vindemiatrix (Gentle Female)", "Chirp3-HD"),
    ("en-US-Chirp3-HD-Zubenelgenubi", "Zubenelgenubi (Casual Male)", "Chirp3-HD"),
    ("en-US-Chirp3-HD-Schedar", "Schedar (Even Male)", "Chirp3-HD"),
    ("en-US-Chirp3-HD-Achernar", "Achernar (Female)", "Chirp3-HD"),
    ("en-US-Chirp3-HD-Algenib", "Algenib (Male)", "Chirp3-HD"),
    ("en-US-Wavenet-D", "Wavenet D (Male, higher free quota)", "WaveNet"),
    ("en-US-Wavenet-F", "Wavenet F (Female, higher free quota)", "WaveNet"),
    ("en-US-Standard-C", "Standard C (Female, largest free quota)", "Standard"),
]

VOICE_FALLBACKS = {
    "edge": _edge_static,
    "deepgram": lambda: [_voice(vid, label, "Aura-2") for vid, label in DEEPGRAM_AURA2_VOICES],
    "google_cloud_tts": lambda: [_voice(vid, label, group) for vid, label, group in GOOGLE_CLOUD_TTS_VOICES],
    "kokoro": lambda: [_voice(v, label, group) for v, label, group in KOKORO_VOICES],
    "gemini": lambda: [
        _voice(name, f"{name} — {style}", "Gemini") for name, style in GEMINI_TTS_VOICES.items()
    ],
    "vertex_gemini": lambda: [
        _voice(name, f"{name} — {style}", "Gemini") for name, style in GEMINI_TTS_VOICES.items()
    ],
    "elevenlabs": lambda: [],
    "cartesia": lambda: [],
    "ai33pro": lambda: [],
}

VOICE_FETCHERS = {
    "edge": lambda _key: _fetch_edge(),
    "elevenlabs": _fetch_elevenlabs,
    "cartesia": _fetch_cartesia,
}


CUSTOM_PREFIX = "custom:"


def custom_provider_id(provider: str) -> str | None:
    return provider.split(":", 1)[1] if provider.startswith(CUSTOM_PREFIX) else None


def custom_provider_spec(settings: dict, provider: str) -> dict | None:
    """Looks up a saved custom-provider entry by its `custom:<id>` provider
    string. Always reads from STORED settings, never from an unsaved request
    body -- a preview/test must not be able to smuggle in an arbitrary
    endpoint/key that was never persisted."""
    pid = custom_provider_id(provider)
    if not pid:
        return None
    for entry in settings.get("custom_tts_providers") or []:
        if isinstance(entry, dict) and str(entry.get("id")) == pid:
            return entry
    return None


def custom_registry_entries(settings: dict) -> list:
    """Custom providers rendered into the same shape as the built-in
    registry, so the settings/creator pages don't need a second code path."""
    out = []
    for entry in settings.get("custom_tts_providers") or []:
        if not isinstance(entry, dict) or not entry.get("id"):
            continue
        out.append(
            {
                "id": f"{CUSTOM_PREFIX}{entry['id']}",
                "label": f"Custom: {entry.get('label') or entry['id']}",
                "blurb": entry.get("endpoint", "") or "No endpoint configured yet -- edit it below.",
                "needsKey": True,
                "keyField": None,
                "voiceField": None,
                "modelField": None,
                "hasVoiceCatalog": False,
                "hasModelCatalog": False,
                "supportsRate": False,
                "supportsPitch": False,
                "director": False,
                "paid": True,
                "custom": True,
            }
        )
    return out


def list_voices(provider: str, api_key: str = "", refresh: bool = False) -> dict:
    if provider.startswith(CUSTOM_PREFIX):
        return {"items": [], "source": "none", "fetchedAt": None, "error": None, "provider": provider, "allowCustom": True}
    spec = PROVIDERS.get(provider)
    if not spec:
        raise KeyError(provider)

    fallback = VOICE_FALLBACKS[provider]()
    fetcher = VOICE_FETCHERS.get(provider) if spec["voices"] == "dynamic" else None
    if fetcher is not None and spec["needs_key"] and not api_key:
        fetcher = None

    result = catalog_cache.resolve(
        f"tts-voices:{provider}",
        (lambda: fetcher(api_key)) if fetcher else None,
        fallback,
        refresh=refresh,
    )
    result["provider"] = provider
    # Providers with no catalog at all still accept a hand-entered voice ID.
    result["allowCustom"] = not result["items"] or provider in {"elevenlabs", "cartesia", "ai33pro", "deepgram", "google_cloud_tts"}
    return result


# ------------------------------------------------------------------ models ---


def _fetch_elevenlabs_models(api_key):
    if not api_key:
        raise ValueError("An ElevenLabs API key is required to list models")
    resp = requests.get(
        "https://api.elevenlabs.io/v1/models",
        headers={"xi-api-key": api_key},
        timeout=FETCH_TIMEOUT,
    )
    resp.raise_for_status()
    out = []
    for item in resp.json() or []:
        model_id = item.get("model_id")
        if model_id and item.get("can_do_text_to_speech", True):
            out.append({"id": model_id, "label": item.get("name") or model_id, "group": "", "note": ""})
    return out


MODEL_FETCHERS = {"elevenlabs": _fetch_elevenlabs_models}


def list_models(provider: str, api_key: str = "", refresh: bool = False) -> dict:
    if provider.startswith(CUSTOM_PREFIX):
        return {"items": [], "source": "none", "fetchedAt": None, "error": None, "provider": provider}
    spec = PROVIDERS.get(provider)
    if not spec:
        raise KeyError(provider)
    if not spec["models"]:
        return {"items": [], "source": "none", "fetchedAt": None, "error": None, "provider": provider}

    fallback = [
        {"id": row["id"], "label": row["label"], "group": "", "note": ""}
        for row in STATIC_MODELS.get(provider, [])
    ]
    fetcher = MODEL_FETCHERS.get(provider) if spec["models"] == "dynamic" else None
    if fetcher is not None and spec["needs_key"] and not api_key:
        fetcher = None

    result = catalog_cache.resolve(
        f"tts-models:{provider}",
        (lambda: fetcher(api_key)) if fetcher else None,
        fallback,
        refresh=refresh,
    )
    result["provider"] = provider
    return result


# ---------------------------------------------------------------- registry ---


def public_registry(enabled: dict | None = None, custom: list | None = None) -> list:
    """Registry the settings/creator pages render from.

    `enabled` maps provider id -> bool for the paid-provider build flags that
    used to be Jinja `{% if %}` guards around whole panels. `custom` is the
    caller's already-built list of user-defined providers (see
    custom_registry_entries) appended after the built-in ones.
    """
    flags = enabled or {}
    out = []
    for provider_id, spec in PROVIDERS.items():
        if not flags.get(provider_id, True):
            continue
        out.append(
            {
                "id": provider_id,
                "label": spec["label"],
                "blurb": spec["blurb"],
                "needsKey": spec["needs_key"],
                "keyField": spec["key_field"],
                "voiceField": spec["voice_field"],
                "modelField": spec["model_field"],
                "hasVoiceCatalog": bool(spec["voices"]),
                "hasModelCatalog": bool(spec["models"]),
                "supportsRate": spec["supports_rate"],
                "supportsPitch": spec["supports_pitch"],
                "director": spec["director"],
                "paid": spec["paid"],
                "custom": False,
                "extraFields": spec.get("extra_fields", []),
            }
        )
    out.extend(custom or [])
    return out


def director_options() -> dict:
    """Gemini style/accent options, sourced from voice_config instead of being
    re-typed in two templates."""
    return {
        "styles": [{"id": key, "label": key.replace("_", " ").title()} for key in VOICE_STYLES],
        "accents": [{"id": key, "label": label.title()} for key, label in ACCENTS.items()],
    }
