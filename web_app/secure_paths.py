"""Per-user private storage paths, kept outside the distributable source tree."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

try:
    from .branding import BRAND
except ImportError:  # frozen application imports modules from web_app on sys.path
    from branding import BRAND


# The v6.x product name. Existing installs keep every credential, the license
# activation marker, and the OAuth tokens under this directory, so the rebrand
# copies the contents forward once and never deletes the original.
LEGACY_DATA_DIR_NAME = "Ez AmazTube Pro"
DATA_DIR_NAME = BRAND["data_dir_name"]


def _platform_root(name: str) -> Path:
    if os.name == "nt":
        return Path(os.environ.get("APPDATA", Path.home())) / name
    return Path.home() / "Library" / "Application Support" / name


def _adopt_legacy_data(root: Path) -> None:
    """Copy a v6.x data directory into the rebranded one, exactly once.

    Anything already present in the new directory wins, so re-running this is
    safe and a user who has started using v7 never gets stale files pushed back
    over their current state.
    """
    if DATA_DIR_NAME == LEGACY_DATA_DIR_NAME:
        return
    marker = root / ".migrated_from_legacy"
    if marker.exists():
        return
    legacy = _platform_root(LEGACY_DATA_DIR_NAME)
    if not legacy.is_dir() or legacy.resolve() == root.resolve():
        marker.write_text("no legacy data\n", encoding="utf-8")
        return
    for source in legacy.rglob("*"):
        target = root / source.relative_to(legacy)
        try:
            if source.is_dir():
                target.mkdir(parents=True, exist_ok=True, mode=0o700)
            elif not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                shutil.copy2(source, target)
                target.chmod(0o600)
        except OSError as exc:  # a single unreadable file must not block startup
            print(f"[MIGRATE] skipped {source.name}: {exc}")
    marker.write_text(f"copied from {legacy}\n", encoding="utf-8")


def data_dir() -> Path:
    override = os.environ.get("AMZFLOW_DATA_DIR") or os.environ.get(
        "EZ_AMAZTUBE_DATA_DIR"
    )
    if override:
        root = Path(override).expanduser()
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
    else:
        root = _platform_root(DATA_DIR_NAME)
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        _adopt_legacy_data(root)
    try:
        root.chmod(0o700)
    except OSError:
        pass
    return root


DATA_DIR = data_dir()
SETTINGS_FILE = DATA_DIR / "settings.json"
SERVICE_ACCOUNT_FILE = DATA_DIR / "config.json"
OAUTH_DIR = DATA_DIR / "youtube_oauth"
ACTIVATION_FILE = DATA_DIR / ".activated"
UPLOADED_VIDEOS_FILE = DATA_DIR / "uploaded_videos.json"
LOGIN_TOKEN_FILE = DATA_DIR / "login_token.json"
KEYWORDS_FILE = DATA_DIR / "keyword-asin.txt"
CATALOG_CACHE_FILE = DATA_DIR / "provider_catalog_cache.json"
PREVIEW_CACHE_DIR = DATA_DIR / "tts_preview_cache"
RENDER_JOBS_DB = DATA_DIR / "render_jobs.sqlite3"
PRESETS_FILE = DATA_DIR / "presets.json"
