"""One-time migration of credentials out of the application source folder."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "web_app"))
from secure_paths import (
    ACTIVATION_FILE,
    DATA_DIR,
    KEYWORDS_FILE,
    LOGIN_TOKEN_FILE,
    OAUTH_DIR,
    SERVICE_ACCOUNT_FILE,
    SETTINGS_FILE,
    UPLOADED_VIDEOS_FILE,
)


SECRET_MARKERS = (
    "api_key",
    "apikey",
    "token",
    "client_secret",
    "private_key",
    "password",
)


def private_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    shutil.copy2(source, target)
    target.chmod(0o600)


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    OAUTH_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)

    source_settings = ROOT / "web_app" / "settings.json"
    if source_settings.exists():
        if not SETTINGS_FILE.exists():
            private_copy(source_settings, SETTINGS_FILE)
        payload = json.loads(source_settings.read_text(encoding="utf-8"))
        for key in list(payload):
            if any(marker in key.lower() for marker in SECRET_MARKERS):
                payload[key] = ""
        source_settings.write_text(
            json.dumps(payload, indent=4, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    for service_account in (ROOT / "web_app").glob("config*.json"):
        target = (
            SERVICE_ACCOUNT_FILE
            if service_account.name == "config.json"
            else DATA_DIR / "legacy_credentials" / service_account.name
        )
        if not target.exists():
            private_copy(service_account, target)
        service_account.unlink()

    for pattern in ("token_*.json",):
        for source in (ROOT / "web_app").glob(pattern):
            target = OAUTH_DIR / source.name
            if not target.exists():
                private_copy(source, target)
            source.unlink()
    for source in (ROOT / "app_files").glob("client_secrets*.json"):
        target = OAUTH_DIR / source.name
        if not target.exists():
            private_copy(source, target)
        source.unlink()

    runtime_files = (
        (ROOT / "web_app" / ".activated", ACTIVATION_FILE),
        (ROOT / "web_app" / "uploaded_videos.json", UPLOADED_VIDEOS_FILE),
        (ROOT / "web_app" / "login_token.json", LOGIN_TOKEN_FILE),
        (ROOT / "app_files" / "keyword-asin.txt", KEYWORDS_FILE),
    )
    for source, target in runtime_files:
        if not source.exists():
            continue
        if not target.exists():
            private_copy(source, target)
        source.unlink()

    if SETTINGS_FILE.exists():
        private_settings = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        private_settings.setdefault("tts_service", "kokoro")
        private_settings.setdefault("kokoro_voice", "af_heart")
        private_settings.setdefault("product_order", "countdown")
        private_settings.setdefault("content_mode", "spec_based")
        private_settings.setdefault("music_mode", "auto")
        private_settings.setdefault("enable_intro_clip", False)
        private_settings.setdefault("output_root", str(ROOT / "files_created"))
        if private_settings.get("cartesia_model_id") in {
            None,
            "",
            "sonic",
            "sonic-english",
            "sonic-3",
        }:
            private_settings["cartesia_model_id"] = "sonic-3.5"
        private_settings["migration_version"] = 1
        SETTINGS_FILE.write_text(
            json.dumps(private_settings, indent=4, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        SETTINGS_FILE.chmod(0o600)

    print(f"Private application data migrated to: {DATA_DIR}")


if __name__ == "__main__":
    main()
