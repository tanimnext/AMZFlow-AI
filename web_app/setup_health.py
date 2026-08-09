"""First-run readiness checks for the dashboard.

Every one of these used to fail deep inside a render with an opaque message
("[FATAL] keyword skipped", a filtergraph error, a 401 from a provider). Showing
them up front turns a 20-minute failed batch into a red dot on the dashboard.
"""

from __future__ import annotations

import os
import shutil
import subprocess

import model_catalog
import tts_catalog
from runtime_support import resolve_binary


def _check(check_id, label, ok, detail, fix=None, severity="error"):
    return {
        "id": check_id,
        "label": label,
        "ok": bool(ok),
        "detail": detail,
        "fix": fix,
        "severity": "ok" if ok else severity,
    }


def _binary(name):
    path = resolve_binary(name)
    if path == name and not shutil.which(name):
        path = None
    if not path:
        return None, ""
    try:
        out = subprocess.run(
            [path, "-version"], capture_output=True, timeout=10, text=True
        ).stdout
        version = out.splitlines()[0] if out else ""
    except (OSError, subprocess.SubprocessError):
        version = ""
    return path, version


def _first_key(settings, field):
    raw = settings.get(field) if field else ""
    if isinstance(raw, str) and raw.strip():
        return raw.strip().splitlines()[0].strip()
    return ""


def run_checks(settings: dict, output_root: str | None = None) -> dict:
    checks = []

    for binary in ("ffmpeg", "ffprobe"):
        path, version = _binary(binary)
        checks.append(
            _check(
                binary,
                f"{binary} available",
                bool(path),
                version or (path or f"{binary} was not found on PATH"),
                fix="Re-download the portable app" if os.environ.get("AMZFLOW_DESKTOP") else "Install FFmpeg",
            )
        )

    provider = settings.get("llm_service", "gemini")
    spec = model_catalog.PROVIDERS.get(provider)
    if spec:
        has_key = bool(_first_key(settings, spec["key_field"]))
        model = str(settings.get(spec["model_field"]) or spec["default_model"])
        checks.append(
            _check(
                "llm",
                f"AI provider · {spec['label']}",
                has_key,
                f"Model {model}" if has_key else f"No API key saved for {spec['label']}",
                fix="Settings → AI Provider",
            )
        )
    else:
        checks.append(
            _check("llm", "AI provider", False, f"Unknown provider '{provider}'", fix="Settings → AI Provider")
        )

    tts_provider = settings.get("tts_service", "kokoro")
    tts_spec = tts_catalog.PROVIDERS.get(tts_provider)
    if tts_spec:
        needs_key = tts_spec["needs_key"]
        has_key = bool(_first_key(settings, tts_spec["key_field"])) if needs_key else True
        voice = settings.get(tts_spec["voice_field"]) or "default voice"
        checks.append(
            _check(
                "tts",
                f"Voice · {tts_spec['label']}",
                has_key,
                f"Using {voice}" if has_key else f"No API key saved for {tts_spec['label']}",
                fix="Settings → Voice",
            )
        )
    else:
        checks.append(
            _check("tts", "Voice provider", False, f"Unknown provider '{tts_provider}'", fix="Settings → Voice")
        )

    tag = str(settings.get("partner_tag") or "").strip()
    checks.append(
        _check(
            "partner_tag",
            "Amazon partner tag",
            bool(tag),
            tag or "Without a tag every affiliate link in your descriptions earns nothing",
            fix="Settings → Amazon",
        )
    )

    root = output_root or settings.get("output_root") or ""
    writable = bool(root) and os.path.isdir(root) and os.access(root, os.W_OK)
    checks.append(
        _check(
            "output_root",
            "Output folder writable",
            writable,
            root or "No output folder selected",
            fix="Creator → Output folder",
        )
    )

    creators_ready = bool(
        _first_key(settings, "creators_api_client_id")
        and _first_key(settings, "creators_api_client_secret")
    )
    checks.append(
        _check(
            "creators_api",
            "Amazon Creators API",
            creators_ready,
            "ASINs are verified against the live catalog"
            if creators_ready
            else "Optional — without it, ASINs are flagged for manual review",
            fix="Settings → Amazon",
            severity="warn",
        )
    )

    blocking = [c for c in checks if not c["ok"] and c["severity"] == "error"]
    return {
        "checks": checks,
        "ready": not blocking,
        "blockingCount": len(blocking),
        "warningCount": len([c for c in checks if not c["ok"] and c["severity"] == "warn"]),
    }
