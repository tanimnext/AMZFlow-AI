"""One TTS implementation, shared by the preview route and the render pipeline.

v6 had the same six provider branches written twice -- once in
`app.preview_tts` and once in `amazon_video_maker._tts_provider_once` -- and
they had already drifted (the preview honoured `elevenlabs` voice settings the
render ignored, the render supported key rotation the preview did not). A
preview that does not match the final render is worse than no preview, so both
now call `synthesize()`.

Every function writes an mp3 to `output_path` and raises TTSError on failure.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import subprocess
import threading
import time
import unicodedata
from datetime import datetime, timezone

import requests

from runtime_support import quiet_subprocess_kwargs
from voice_config import build_gemini_tts_prompt, normalize_gemini_tts_settings

HTTP_TIMEOUT = 45
GEMINI_TIMEOUT = 90
AI33PRO_POLL_SECONDS = 120
MAX_AUDIO_BYTES = 25 * 1024 * 1024


class TTSError(Exception):
    """A synthesis attempt failed. `provider` names which one."""

    def __init__(self, message, provider=""):
        super().__init__(message)
        self.provider = provider


# ------------------------------------------------------------------- utils ---


def _split_keys(value) -> list:
    """API-key textareas are newline separated; every provider gets rotation."""
    return [line.strip() for line in str(value or "").splitlines() if line.strip()]


def normalize_for_speech(text: str) -> str:
    """ASCII-fold smart punctuation that several engines mispronounce.

    Kept lossless-ish: if folding empties the string we return the original.
    """
    folded = unicodedata.normalize("NFKD", str(text or ""))
    for old, new in (
        ("’", "'"), ("‘", "'"), ("`", "'"),
        ("“", '"'), ("”", '"'),
        ("–", "-"), ("—", "-"),
        ("™", ""), ("®", ""), ("©", ""),
        ("\xa0", " "),
    ):
        folded = folded.replace(old, new)
    folded = folded.encode("ascii", "ignore").decode("ascii").strip()
    return folded or str(text or "")


def _run_ffmpeg(args, timeout=120):
    try:
        subprocess.run(
            args, check=True, capture_output=True, timeout=timeout,
            **quiet_subprocess_kwargs(),
        )
    except subprocess.TimeoutExpired as exc:
        raise TTSError(f"ffmpeg timed out after {timeout}s") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or b"").decode(errors="replace")[-400:]
        raise TTSError(f"ffmpeg failed: {detail}") from exc


def _write_audio(response, output_path, provider):
    content = response.content
    if len(content) > MAX_AUDIO_BYTES:
        raise TTSError("Audio response exceeded 25 MB", provider)
    with open(output_path, "wb") as handle:
        handle.write(content)


def cache_key(text: str, config: dict) -> str:
    """Identity of a synthesis request.

    Deliberately includes every parameter that changes the *sound*: the v6
    render cache keyed only on service/text/rate/pitch with `voice or ""`, so
    switching voice replayed the old audio.
    """
    provider = config.get("service", "edge")
    material = {
        "v": 2,
        "provider": provider,
        "text": text,
        "voice": _resolve_voice(provider, config) or "",
        "model": _resolve_model(provider, config) or "",
        "rate": config.get("edge_rate", "+0%"),
        "pitch": config.get("edge_pitch", "+0Hz"),
    }
    if provider in ("gemini", "vertex_gemini"):
        material["director"] = normalize_gemini_tts_settings(config)
    if str(provider).startswith("custom:"):
        # Voice/model live inside the custom spec, not a fixed settings
        # field, so fold the whole (non-secret) spec into the identity --
        # editing the endpoint or voice id must bust the cache too.
        spec = config.get("_custom_spec") or {}
        material["custom_spec"] = {k: v for k, v in spec.items() if k != "api_key"}
    return hashlib.sha256(
        json.dumps(material, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


DEFAULT_VOICES = {
    "edge": "en-US-AndrewMultilingualNeural",
    "kokoro": "af_heart",
    "elevenlabs": "pNInz6obpgnuMvYJZZ7t",
    "cartesia": "a0e9987c-56f7-4141-9fa0-81932f79c20b",
    "ai33pro": "Xb7hH8MSUJpSbSDYk0k2",
    "deepgram": "aura-2-thalia-en",
    "google_cloud_tts": "en-US-Chirp3-HD-Sulafat",
}

VOICE_FIELDS = {
    "edge": "edge_voice",
    "kokoro": "kokoro_voice",
    "gemini": "gemini_tts_voice",
    "vertex_gemini": "gemini_tts_voice",  # same Chirp3-HD voice set as "gemini"
    "elevenlabs": "elevenlabs_voice_id",
    "cartesia": "cartesia_voice_id",
    "ai33pro": "ai33pro_voice_id",
    "deepgram": "deepgram_voice_id",
    "google_cloud_tts": "google_tts_voice_id",
}

MODEL_FIELDS = {
    "gemini": "gemini_tts_model",
    "vertex_gemini": "vertex_tts_model",
    "elevenlabs": "elevenlabs_model_id",
    "cartesia": "cartesia_model_id",
    "ai33pro": "ai33pro_model_id",
    "deepgram": "deepgram_model_id",
}

DEFAULT_MODELS = {
    "elevenlabs": "eleven_multilingual_v2",
    "cartesia": "sonic-3.5",
    "ai33pro": "eleven_multilingual_v2",
}


def _resolve_voice(provider, config):
    if provider in ("gemini", "vertex_gemini"):
        return normalize_gemini_tts_settings(config)["voice"]
    field = VOICE_FIELDS.get(provider)
    value = str(config.get(field) or "").strip() if field else ""
    # `voice` is the generic override the render pipeline passes per call.
    return str(config.get("voice") or "").strip() or value or DEFAULT_VOICES.get(provider, "")


def _resolve_model(provider, config):
    if provider == "gemini":
        return normalize_gemini_tts_settings(config)["model"]
    if provider == "vertex_gemini":
        return str(config.get("vertex_tts_model") or "").strip() or "gemini-2.5-flash-preview-tts"
    field = MODEL_FIELDS.get(provider)
    value = str(config.get(field) or "").strip() if field else ""
    return value or DEFAULT_MODELS.get(provider, "")


# --------------------------------------------------------------- providers ---


def synth_edge(text, output_path, config):
    import edge_tts

    voice = _resolve_voice("edge", config)
    rate = str(config.get("edge_rate") or "+0%")
    pitch = str(config.get("edge_pitch") or "+0Hz")
    speech = normalize_for_speech(text)

    async def run():
        communicate = edge_tts.Communicate(speech, voice, rate=rate, pitch=pitch)
        await communicate.save(output_path)

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(run())
    except Exception as exc:  # noqa: BLE001 - edge_tts raises a wide range
        raise TTSError(f"Edge TTS failed: {exc}", "edge") from exc
    finally:
        loop.close()


def synth_kokoro(text, output_path, config):
    # The heavy model lives in the render module; importing it here keeps a
    # single copy of the load-once pipeline and its lock.
    import amazon_video_maker as avm

    try:
        avm._kokoro_synthesize(text, output_path, _resolve_voice("kokoro", config))
    except Exception as exc:  # noqa: BLE001
        raise TTSError(f"Kokoro TTS failed: {exc}", "kokoro") from exc


def synth_elevenlabs(text, output_path, config):
    keys = _split_keys(config.get("elevenlabs_api_key"))
    if not keys:
        raise TTSError("An ElevenLabs API key is required", "elevenlabs")
    voice_id = _resolve_voice("elevenlabs", config)
    model_id = _resolve_model("elevenlabs", config)

    last = ""
    for key in keys:
        resp = requests.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
            headers={"Accept": "audio/mpeg", "Content-Type": "application/json", "xi-api-key": key},
            json={
                "text": text,
                "model_id": model_id,
                "voice_settings": {"stability": 0.5, "similarity_boost": 0.5},
            },
            timeout=HTTP_TIMEOUT,
        )
        if resp.status_code == 200:
            _write_audio(resp, output_path, "elevenlabs")
            return
        last = resp.text[:300]
        if resp.status_code not in (401, 403, 429):
            break
    raise TTSError(f"ElevenLabs error: {last}", "elevenlabs")


def synth_cartesia(text, output_path, config):
    keys = _split_keys(config.get("cartesia_api_key"))
    if not keys:
        raise TTSError("A Cartesia API key is required", "cartesia")
    last = ""
    for key in keys:
        resp = requests.post(
            "https://api.cartesia.ai/tts/bytes",
            headers={
                "X-API-Key": key,
                "Cartesia-Version": "2026-03-01",
                "Content-Type": "application/json",
            },
            json={
                "model_id": _resolve_model("cartesia", config),
                "transcript": text,
                "voice": {"mode": "id", "id": _resolve_voice("cartesia", config)},
                "output_format": {"container": "mp3", "bit_rate": 128000, "sample_rate": 44100},
            },
            timeout=HTTP_TIMEOUT,
        )
        if resp.status_code == 200:
            _write_audio(resp, output_path, "cartesia")
            return
        last = resp.text[:300]
        if resp.status_code not in (401, 403, 429):
            break
    raise TTSError(f"Cartesia error: {last}", "cartesia")


GOOGLE_TTS_DEFAULT_CHAR_LIMIT = 1_000_000  # Chirp3-HD's monthly free allowance
_usage_lock = threading.Lock()


def _google_tts_usage_path():
    from secure_paths import DATA_DIR

    return os.path.join(str(DATA_DIR), "google_tts_usage.json")


def google_tts_usage(now=None):
    """{"month": "YYYY-MM", "chars": int} for the current calendar month.

    Google's free tier resets monthly and a budget alert does NOT stop
    spending -- it only emails after the fact. So the only thing that
    actually prevents a surprise bill is refusing to send the request, which
    means keeping our own count.
    """
    month = (now or datetime.now(timezone.utc)).strftime("%Y-%m")
    try:
        with open(_google_tts_usage_path(), "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if data.get("month") == month:
            return {"month": month, "chars": int(data.get("chars") or 0)}
    except (OSError, ValueError, TypeError):
        pass
    return {"month": month, "chars": 0}  # missing/corrupt/stale -> new month


def _google_tts_record(chars, now=None):
    usage = google_tts_usage(now)
    usage["chars"] += max(0, int(chars))
    try:
        path = _google_tts_usage_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(usage, handle)
    except OSError:
        pass  # a counter we can't persist must never break the render
    return usage


def _google_tts_char_limit(config):
    raw = str(config.get("google_tts_monthly_char_limit") or "").strip()
    if not raw:
        return GOOGLE_TTS_DEFAULT_CHAR_LIMIT
    try:
        # 0 (or negative) is the explicit "no cap, I accept billing" opt-out.
        return max(0, int(float(raw)))
    except ValueError:
        return GOOGLE_TTS_DEFAULT_CHAR_LIMIT


def synth_google_cloud_tts(text, output_path, config):
    """Google Cloud Text-to-Speech (Chirp3-HD / WaveNet / Standard voices) --
    a different API from "Gemini TTS via Google Cloud" above
    (texttospeech.googleapis.com's text:synthesize, not generateContent),
    but authenticated and billed through the same Vertex AI service-account
    JSON already configured under AI Provider -> Google Cloud, so no
    separate key field is needed."""
    import vertex_auth

    service_account_json = config.get("vertex_service_account_private_key")
    project_id = config.get("vertex_project_id")
    try:
        token = vertex_auth.get_access_token(service_account_json)
    except ValueError as exc:
        raise TTSError(str(exc), "google_cloud_tts") from exc
    if not project_id:
        raise TTSError(
            "No Vertex AI project ID configured (Settings -> AI Provider -> Google Cloud)",
            "google_cloud_tts",
        )

    limit = _google_tts_char_limit(config)
    if limit:
        with _usage_lock:
            used = google_tts_usage()["chars"]
            if used + len(text) > limit:
                raise TTSError(
                    f"Google Cloud TTS monthly character cap reached "
                    f"({used:,}/{limit:,} this month). Raise or clear the limit in "
                    f"Settings -> Voice, or let the fallback chain take over.",
                    "google_cloud_tts",
                )

    voice_name = _resolve_voice("google_cloud_tts", config)
    language_code = "-".join(voice_name.split("-")[:2]) or "en-US"
    resp = requests.post(
        "https://texttospeech.googleapis.com/v1/text:synthesize",
        headers={
            "Authorization": f"Bearer {token}",
            # Required when calling with an OAuth access token instead of an
            # API key -- otherwise Google can't tell which project's quota/
            # billing to charge the request against.
            "X-Goog-User-Project": str(project_id),
            "Content-Type": "application/json",
        },
        json={
            "input": {"text": text},
            "voice": {"languageCode": language_code, "name": voice_name},
            "audioConfig": {"audioEncoding": "MP3"},
        },
        timeout=HTTP_TIMEOUT,
    )
    if resp.status_code != 200:
        raise TTSError(f"Google Cloud TTS error: {resp.text[:300]}", "google_cloud_tts")
    try:
        audio_b64 = resp.json()["audioContent"]
    except (KeyError, ValueError, TypeError) as exc:
        raise TTSError(
            f"Google Cloud TTS response parse error: {exc} -- raw response: {resp.text[:300]}",
            "google_cloud_tts",
        ) from exc
    audio_bytes = base64.b64decode(audio_b64)
    if len(audio_bytes) > MAX_AUDIO_BYTES:
        raise TTSError("Audio response exceeded 25 MB", "google_cloud_tts")
    with open(output_path, "wb") as handle:
        handle.write(audio_bytes)
    # Only bill ourselves for characters Google actually synthesized.
    with _usage_lock:
        _google_tts_record(len(text))


def synth_deepgram(text, output_path, config):
    """Deepgram Aura-2 TTS. The "model" IS the voice choice (e.g.
    aura-2-thalia-en) -- Deepgram has no separate voice/model split like
    ElevenLabs or Cartesia, so this reuses the voice field for both."""
    keys = _split_keys(config.get("deepgram_api_key"))
    if not keys:
        raise TTSError("A Deepgram API key is required", "deepgram")
    # One value, two settings fields: an explicit Model wins, otherwise the
    # Voice picker (which lists the identical catalogue) supplies it.
    model = str(config.get("deepgram_model_id") or "").strip() or _resolve_voice("deepgram", config)
    last = ""
    for key in keys:
        resp = requests.post(
            "https://api.deepgram.com/v1/speak",
            params={"model": model, "encoding": "mp3"},
            headers={"Authorization": f"Token {key}", "Content-Type": "application/json"},
            json={"text": text},
            timeout=HTTP_TIMEOUT,
        )
        if resp.status_code == 200:
            _write_audio(resp, output_path, "deepgram")
            return
        last = resp.text[:300]
        if resp.status_code not in (401, 403, 429):
            break
    raise TTSError(f"Deepgram error: {last}", "deepgram")


def synth_gemini(text, output_path, config, ffmpeg_bin="ffmpeg"):
    keys = _split_keys(config.get("gemini_api_key"))
    if not keys:
        raise TTSError(
            "A Gemini API key is required (set it in the AI Provider section)", "gemini"
        )
    voice_config = normalize_gemini_tts_settings(config)
    prompt = build_gemini_tts_prompt(text, config)
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice_config["voice"]}}
            },
        },
    }

    last = ""
    pcm_bytes = None
    # Rotate keys exactly like the LLM path; v6 used GEMINI_API_KEYS[0] only, so
    # a rate-limited first key failed the whole render with keys still unused.
    for key in keys:
        resp = requests.post(
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{voice_config['model']}:generateContent",
            params={"key": key},
            json=body,
            timeout=GEMINI_TIMEOUT,
        )
        if resp.status_code == 200:
            try:
                inline = resp.json()["candidates"][0]["content"]["parts"][0]["inlineData"]
                pcm_bytes = base64.b64decode(inline["data"])
                break
            except (KeyError, IndexError, ValueError, TypeError) as exc:
                raise TTSError(f"Gemini TTS response parse error: {exc}", "gemini") from exc
        last = resp.text[:300]
        if resp.status_code not in (429, 500, 502, 503, 504):
            break

    if pcm_bytes is None:
        raise TTSError(f"Gemini TTS error: {last}", "gemini")

    raw_path = output_path + ".pcm"
    try:
        with open(raw_path, "wb") as handle:
            handle.write(pcm_bytes)
        _run_ffmpeg(
            [ffmpeg_bin, "-y", "-f", "s16le", "-ar", "24000", "-ac", "1",
             "-i", raw_path, output_path]
        )
    finally:
        if os.path.exists(raw_path):
            try:
                os.remove(raw_path)
            except OSError:
                pass


def synth_vertex_gemini(text, output_path, config, ffmpeg_bin="ffmpeg"):
    """Same Gemini TTS voices as synth_gemini, but through Vertex AI (Google
    Cloud project billing/service-account) instead of an AI Studio API key --
    so usage draws on that project's $300 free-trial credit."""
    import vertex_auth

    service_account_json = config.get("vertex_service_account_private_key")
    project_id = config.get("vertex_project_id")
    configured_location = config.get("vertex_location") or "us-central1"
    # RESOURCE_EXHAUSTED (429) on Vertex is almost always a per-region quota,
    # not a project-wide one -- retrying the identical request against a
    # different region/the pooled "global" endpoint routinely succeeds where
    # hammering the same region would just 429 again. Try the user's chosen
    # region first (respects their setting/latency choice), then fall back.
    locations_to_try = list(dict.fromkeys(
        [configured_location, "global", "us-central1"]
    ))

    voice_config = normalize_gemini_tts_settings(config)
    prompt = build_gemini_tts_prompt(text, config)
    body = {
        # Vertex AI requires an explicit role on the content part (the AI
        # Studio "gemini" TTS request above does not).
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice_config["voice"]}}
            },
        },
    }

    try:
        token = vertex_auth.get_access_token(service_account_json)
        model = _resolve_model("vertex_gemini", config)
        api_version = "v1beta1" if "preview" in model else "v1"
    except ValueError as exc:
        raise TTSError(str(exc), "vertex_gemini") from exc

    resp = None
    last = ""
    for loc in locations_to_try:
        url = vertex_auth.generate_content_url(project_id, loc, model, api_version=api_version)
        resp = requests.post(
            url,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=body,
            timeout=GEMINI_TIMEOUT,
        )
        if resp.status_code == 200:
            break
        last = resp.text[:300]
        print(f"[VERTEX TTS] {loc} failed ({resp.status_code}): {last}")
        if resp.status_code not in (429, 500, 502, 503, 504):
            break

    if resp is None or resp.status_code != 200:
        raise TTSError(f"Vertex AI TTS error: {last}", "vertex_gemini")
    try:
        inline = resp.json()["candidates"][0]["content"]["parts"][0]["inlineData"]
        pcm_bytes = base64.b64decode(inline["data"])
    except (KeyError, IndexError, ValueError, TypeError) as exc:
        raise TTSError(
            f"Vertex AI TTS response parse error: {exc} -- raw response: {resp.text[:300]}",
            "vertex_gemini",
        ) from exc

    raw_path = output_path + ".pcm"
    try:
        with open(raw_path, "wb") as handle:
            handle.write(pcm_bytes)
        _run_ffmpeg(
            [ffmpeg_bin, "-y", "-f", "s16le", "-ar", "24000", "-ac", "1",
             "-i", raw_path, output_path]
        )
    finally:
        if os.path.exists(raw_path):
            try:
                os.remove(raw_path)
            except OSError:
                pass


def synth_ai33pro(text, output_path, config, on_progress=None):
    keys = _split_keys(config.get("ai33pro_api_key"))
    if not keys:
        raise TTSError("An AI33 Pro API key is required", "ai33pro")
    api_key = keys[0]
    voice_id = _resolve_voice("ai33pro", config)

    resp = requests.post(
        f"https://api.ai33.pro/v1/text-to-speech/{voice_id}",
        headers={"Content-Type": "application/json", "xi-api-key": api_key},
        json={
            "text": text,
            "model_id": _resolve_model("ai33pro", config),
            "with_transcript": False,
        },
        timeout=HTTP_TIMEOUT,
    )
    if resp.status_code != 200:
        raise TTSError(f"AI33 Pro HTTP error: {resp.text[:300]}", "ai33pro")
    payload = resp.json()
    if not payload.get("success"):
        raise TTSError(f"AI33 Pro error: {payload.get('message')}", "ai33pro")

    task_id = payload.get("task_id")
    check_url = f"https://api.ai33.pro/v1/task/{task_id}"
    for attempt in range(AI33PRO_POLL_SECONDS):
        if on_progress:
            on_progress(attempt, AI33PRO_POLL_SECONDS)
        poll = requests.get(check_url, headers={"xi-api-key": api_key}, timeout=15)
        if poll.status_code == 200:
            state = poll.json()
            status = state.get("status")
            if status == "done":
                audio_url = (state.get("metadata") or {}).get("audio_url")
                _download_result(audio_url, output_path)
                return
            if status in {"failed", "error"}:
                raise TTSError(
                    f"AI33 Pro task failed: {state.get('error_message')}", "ai33pro"
                )
        time.sleep(1)
    raise TTSError("AI33 Pro task did not finish within 120 seconds", "ai33pro")


def _download_result(audio_url, output_path):
    # SSRF guard: the audio URL comes from a third-party API response, so it is
    # untrusted input even though we initiated the task.
    from product_core import is_safe_https_url

    if not audio_url or not is_safe_https_url(audio_url):
        raise TTSError("AI33 Pro returned an unsafe or missing audio URL", "ai33pro")
    resp = requests.get(audio_url, timeout=HTTP_TIMEOUT, allow_redirects=False, stream=True)
    try:
        content_type = resp.headers.get("content-type", "").lower()
        if resp.status_code != 200 or not content_type.startswith("audio/"):
            raise TTSError(
                f"AI33 Pro audio download failed (HTTP {resp.status_code}, {content_type})",
                "ai33pro",
            )
        total = 0
        with open(output_path, "wb") as handle:
            for chunk in resp.iter_content(65536):
                total += len(chunk)
                if total > MAX_AUDIO_BYTES:
                    raise TTSError("AI33 Pro audio exceeded 25 MB", "ai33pro")
                handle.write(chunk)
    finally:
        resp.close()


def synth_custom(text, output_path, config):
    """User-defined HTTP TTS provider (Settings -> Custom TTS Providers).

    Supports the common shape most simple TTS APIs use: POST JSON, get raw
    audio bytes back with an audio/* content-type. Providers that return a
    task id to poll, or a JSON wrapper around an audio URL, aren't supported
    here -- those need a dedicated integration like ai33pro's above.
    """
    from product_core import is_safe_https_url

    spec = config.get("_custom_spec") or {}
    endpoint = str(spec.get("endpoint") or "").strip()
    if not endpoint:
        raise TTSError("This custom provider has no endpoint configured", "custom")
    if not is_safe_https_url(endpoint):
        raise TTSError(
            "Custom provider endpoint must be a public HTTPS URL (no localhost/private IPs)",
            "custom",
        )

    api_key = str(spec.get("api_key") or "").strip()
    header_name = str(spec.get("auth_header") or "Authorization").strip() or "Authorization"
    auth_scheme = spec.get("auth_scheme")
    auth_scheme = "Bearer" if auth_scheme is None else str(auth_scheme).strip()
    headers = {"Content-Type": "application/json", "Accept": "audio/mpeg, audio/*"}
    if api_key:
        headers[header_name] = f"{auth_scheme} {api_key}".strip() if auth_scheme else api_key

    text_field = str(spec.get("text_field") or "text").strip() or "text"
    body = {text_field: text}
    if spec.get("voice_id"):
        body["voice"] = spec["voice_id"]
    if spec.get("model_id"):
        body["model"] = spec["model_id"]

    resp = requests.post(endpoint, headers=headers, json=body, timeout=HTTP_TIMEOUT)
    content_type = resp.headers.get("content-type", "").lower()
    if resp.status_code == 200 and content_type.startswith("audio/"):
        _write_audio(resp, output_path, "custom")
        return
    raise TTSError(
        f"Custom provider error (HTTP {resp.status_code}, {content_type or 'unrecognized content-type'}): "
        f"{resp.text[:300]}",
        "custom",
    )


PROVIDERS = {
    "edge": synth_edge,
    "kokoro": synth_kokoro,
    "elevenlabs": synth_elevenlabs,
    "cartesia": synth_cartesia,
    "gemini": synth_gemini,
    "vertex_gemini": synth_vertex_gemini,
    "ai33pro": synth_ai33pro,
    "deepgram": synth_deepgram,
    "google_cloud_tts": synth_google_cloud_tts,
}


# ---------------------------------------------------------------- dispatch ---


def synthesize(text, output_path, config, *, ffmpeg_bin="ffmpeg", on_progress=None):
    """Render `text` to an mp3 at `output_path` using config['service'].

    Raises TTSError. Callers decide whether to fall back to Edge -- this
    function never silently substitutes a different voice.
    """
    provider = str(config.get("service") or config.get("tts_service") or "edge").strip()
    is_custom = provider.startswith("custom:")
    handler = synth_custom if is_custom else PROVIDERS.get(provider)
    if handler is None:
        raise TTSError(f"Unknown TTS provider '{provider}'", provider)

    text = str(text or "").strip()
    if not text:
        raise TTSError("Nothing to synthesize", provider)

    started = time.time()
    if is_custom:
        handler(text, output_path, config)
    elif provider in ("gemini", "vertex_gemini"):
        handler(text, output_path, config, ffmpeg_bin=ffmpeg_bin)
    elif provider == "ai33pro":
        handler(text, output_path, config, on_progress=on_progress)
    else:
        handler(text, output_path, config)

    if not os.path.exists(output_path) or os.path.getsize(output_path) < 256:
        raise TTSError("Audio file was not generated", provider)
    return {"provider": provider, "seconds": round(time.time() - started, 2)}
