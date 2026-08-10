import os
import json
import subprocess
import tempfile
import threading
import time
import secrets
import traceback
import requests
import base64
import sys
import glob
import re
import asyncio
import edge_tts
import unicodedata
import webbrowser
from io import BytesIO
from pathlib import Path
from PIL import Image
from datetime import datetime

try:
    from .runtime_support import is_frozen, resolve_binary, resource_dir
except ImportError:
    from runtime_support import is_frozen, resolve_binary, resource_dir

# --- CONFIGURATION TOGGLES ---
ENABLE_ELEVENLABS = True  # Set to False or True to disable/ enable ElevenLabs TTS option
ENABLE_CARTESIA = True  # Set to False or True to disable/ enable Cartesia TTS option
ENABLE_AI33PRO = True  # Set to False or True to disable/ enable AI33Pro TTS option
# -----------------------------

# Add app_files to sys.path for local imports
BASE_DIR = str(resource_dir() / "web_app") if is_frozen() else os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = str(resource_dir()) if is_frozen() else os.path.dirname(BASE_DIR)
sys.path.append(BASE_DIR)
sys.path.append(os.path.join(PROJECT_ROOT, 'app_files'))
import thumbnail_generator
import asin_lookup
import catalog_cache
import model_catalog
import preview_service
import setup_health as setup_health_module
import tts_catalog
import tts_engine
from branding import BRAND, PREVIEW_TEXT
from content_batch import BatchStore, ContentBatchManager
from voice_config import (
    GEMINI_TTS_MODELS,
    GEMINI_TTS_VOICES,
    build_gemini_tts_prompt,
    normalize_gemini_tts_settings,
)
from product_core import (
    atomic_json,
    format_youtube_text,
    is_safe_https_url,
    parse_youtube_text,
    public_settings,
    resolve_project_dir,
    validate_output_root,
    validate_publish_options,
)

from flask import Flask, render_template, request, jsonify, Response, redirect, url_for, session, send_from_directory, send_file, stream_with_context
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError, ResumableUploadError
from google.auth.transport.requests import Request, AuthorizedSession
from google.oauth2.credentials import Credentials
import license_store
from secure_paths import (
    ACTIVATION_FILE,
    DATA_DIR,
    KEYWORDS_FILE,
    LOGIN_TOKEN_FILE as PRIVATE_LOGIN_TOKEN_FILE,
    OAUTH_DIR,
    PREVIEW_CACHE_DIR,
    SETTINGS_FILE as PRIVATE_SETTINGS_FILE,
    UPLOADED_VIDEOS_FILE as PRIVATE_UPLOADED_VIDEOS_FILE,
)

app = Flask(
    __name__,
    template_folder=os.path.join(BASE_DIR, "templates"),
    static_folder=os.path.join(BASE_DIR, "static"),
)
app.secret_key = os.environ.get("EZ_AMAZTUBE_SESSION_SECRET") or secrets.token_hex(32)
app.config.update(
    MAX_CONTENT_LENGTH=25 * 1024 * 1024,
    SESSION_COOKIE_NAME="ez_amaztube_session",
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Strict",
)

# Global for tracking upload progress
UPLOAD_PROGRESS = {}
GENERATION_LOCK = threading.Lock()


def _project_keyword_slug(project_id, k_path):
    if project_id and "/" in project_id:
        return project_id.split("/", 1)[0]
    if project_id:
        return project_id
    return os.path.basename(os.path.dirname(k_path)) or os.path.basename(k_path)


def _video_file_candidates(project_id, k_path):
    keyword_slug = _project_keyword_slug(project_id, k_path)
    names = [f"{keyword_slug}.mp4", "video.mp4", f"{os.path.basename(k_path)}.mp4"]
    return list(dict.fromkeys(names))


def _find_video_file(project_id, k_path):
    for name in _video_file_candidates(project_id, k_path):
        if os.path.isfile(os.path.join(k_path, name)):
            return name
    return None


def _thumbnail_file_candidates(k_path):
    names = ["Thumbnail.jpg", "thumbnail.jpg", f"{os.path.basename(k_path)}.jpg"]
    return list(dict.fromkeys(names))


def _find_thumbnail_file(k_path):
    for name in _thumbnail_file_candidates(k_path):
        if os.path.isfile(os.path.join(k_path, name)):
            return name
    return None


def _load_project_metadata(k_path):
    meta_file = os.path.join(k_path, "metadata.json")
    if os.path.exists(meta_file):
        try:
            with open(meta_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    youtube_file = os.path.join(k_path, "youtube.txt")
    if os.path.exists(youtube_file):
        try:
            with open(youtube_file, "r", encoding="utf-8") as f:
                return parse_youtube_text(f.read())
        except Exception:
            pass
    try:
        meta = {}
        for key, filename in (
            ("title", "yt_title.txt"),
            ("description", "yt_description.txt"),
            ("tags", "yt_tags.txt"),
        ):
            path = os.path.join(k_path, filename)
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    meta[key] = f.read().strip()
        return meta
    except Exception:
        return {}


def _save_compact_metadata(k_path, meta):
    with open(os.path.join(k_path, "youtube.txt"), "w", encoding="utf-8") as f:
        f.write(format_youtube_text(meta))

# Custom adapter to use 'requests' instead of 'httplib2' for Google API calls.
# This is a robust fix for the [SSL: WRONG_VERSION_NUMBER] error common on Windows.
class RequestsHttpAdapter:
    def __init__(self, session):
        self.session = session
    def request(self, uri, method="GET", body=None, headers=None, **kwargs):
        # Redirect the body to data for requests
        resp = self.session.request(method=method, url=uri, data=body, headers=headers, **kwargs)
        class ResponseWrapper(dict):
            def __init__(self, r):
                # httplib2 (which googleapiclient expects) uses lowercase headers
                super().__init__({k.lower(): v for k, v in r.headers.items()})
                self.status = r.status_code
                self.reason = r.reason
        return ResponseWrapper(resp), resp.content

# Build absolute paths
BASE_DIR = str(resource_dir() / "web_app") if is_frozen() else os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = str(resource_dir()) if is_frozen() else os.path.dirname(BASE_DIR)
SETTINGS_FILE = str(PRIVATE_SETTINGS_FILE)
UPLOADED_VIDEOS_FILE = str(PRIVATE_UPLOADED_VIDEOS_FILE)

FFMPEG_BIN = resolve_binary("ffmpeg")
FFPROBE_BIN = resolve_binary("ffprobe")

# --- YouTube Client Secrets Rotation Config ---
CLIENT_SECRETS_LIST = [
    str(OAUTH_DIR / 'client_secrets_1.json'),
    str(OAUTH_DIR / 'client_secrets_2.json'),
    str(OAUTH_DIR / 'client_secrets_3.json'),
    str(OAUTH_DIR / 'client_secrets_4.json')
]
TOKEN_FILES_LIST = [
    str(OAUTH_DIR / 'token_1.json'),
    str(OAUTH_DIR / 'token_2.json'),
    str(OAUTH_DIR / 'token_3.json'),
    str(OAUTH_DIR / 'token_4.json')
]
# Primary/Default files (compat)
CLIENT_SECRETS_FILE = CLIENT_SECRETS_LIST[0]
TOKEN_FILE = TOKEN_FILES_LIST[0]
# ----------------------------------------------

CREATED_FILES_DIR = os.path.join(PROJECT_ROOT, 'files_created')
LOGIN_TOKEN_FILE = str(PRIVATE_LOGIN_TOKEN_FILE)

import hashlib

# --- ACTIVATION CONFIG ---
# Backed by the server-side license API. The local file stores only the signed
# activation token and machine binding, never Google credentials.

def verify_activation(email, user_name_input=None, activation_code=None):
    current_machine = get_machine_id()
    if activation_code:
        success, res = license_store.activate_license(
            email, user_name_input or "", current_machine, activation_code
        )
    else:
        success, res = license_store.verify_activation_local(email, current_machine)
    if success:
        email_clean = email.strip().lower()
        session['is_activated'] = True
        session['user_email'] = email_clean
        session['user_name'] = res['name']
        session['video_quota'] = res['quota']
        session['video_used'] = res['used']
        session['expiry_date'] = res['expiry_date']
        session['expiry_time'] = res['expiry_time']
        session['last_activation_check'] = time.time()

    return success, res

def update_usage_on_sheet(email, count):
    license_store.update_usage(email, count)


SCOPES = [
    'https://www.googleapis.com/auth/youtube.upload',
    'https://www.googleapis.com/auth/youtube.readonly',
    'https://www.googleapis.com/auth/youtube.force-ssl',
]

def get_settings():
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    template = os.path.join(BASE_DIR, "settings.json")
    if os.path.exists(template):
        with open(template, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}


def _template_setting_keys():
    """Every key the shipped settings.json template defines.

    save_settings_route()'s allow-list used to be `set(current) | {a hardcoded
    literal set}` -- `current` is the user's PRIVATE settings.json, migrated
    once and never retroactively updated. Any setting added to the template
    after that migration (custom_tts_providers, video_speed,
    elevenlabs_model_id, ...) was invisible to `set(current)`, and unless
    someone remembered to also add it to the literal set, saving it failed
    with "Unknown setting(s)" on every pre-existing install. Reading the
    template directly makes it the actual source of truth.
    """
    template = os.path.join(BASE_DIR, "settings.json")
    try:
        with open(template, 'r', encoding='utf-8') as f:
            return set(json.load(f))
    except (OSError, ValueError):
        return set()

def save_settings(data):
    current = get_settings()
    current.update(data)
    atomic_json(PRIVATE_SETTINGS_FILE, current)
    os.chmod(PRIVATE_SETTINGS_FILE, 0o600)


CONTENT_BATCH_STORE = None
CONTENT_BATCH_MANAGER = None


def _content_batch_services():
    global CONTENT_BATCH_STORE, CONTENT_BATCH_MANAGER
    if CONTENT_BATCH_STORE is None:
        CONTENT_BATCH_STORE = BatchStore(DATA_DIR / "content_jobs.sqlite3")
    if CONTENT_BATCH_MANAGER is None:
        CONTENT_BATCH_MANAGER = ContentBatchManager(CONTENT_BATCH_STORE, get_settings)
        CONTENT_BATCH_MANAGER.resume_pending()
    return CONTENT_BATCH_STORE, CONTENT_BATCH_MANAGER


def library_root():
    configured = get_settings().get("output_root")
    if configured:
        try:
            return str(validate_output_root(configured))
        except ValueError:
            pass
    return CREATED_FILES_DIR


def project_path(project_id):
    return str(resolve_project_dir(library_root(), project_id))


def require_json_object():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValueError("A JSON object is required")
    return data


def save_validated_image(upload, target):
    payload = upload.read(10 * 1024 * 1024 + 1)
    if not payload or len(payload) > 10 * 1024 * 1024:
        raise ValueError("Image must be between 1 byte and 10 MB")
    try:
        with Image.open(BytesIO(payload)) as image:
            image.verify()
            if image.format not in {"JPEG", "PNG", "WEBP"}:
                raise ValueError("Only JPEG, PNG, and WebP images are allowed")
    except (OSError, ValueError) as exc:
        raise ValueError("Uploaded file is not a valid image") from exc
    with open(target, "wb") as handle:
        handle.write(payload)


@app.context_processor
def inject_security_context():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    # `brand` and the signed-in identity are needed by base.html on every page,
    # so they are injected once here instead of being threaded through each
    # render_template call (and re-typed as literals in each template).
    return {
        "csrf_token": session["csrf_token"],
        "brand": BRAND,
        "user_email": session.get("user_email"),
        "user_name": session.get(
            "user_name", (session.get("user_email") or "").split("@")[0]
        ),
    }


@app.before_request
def enforce_request_security():
    endpoint = request.endpoint or ""
    public_endpoints = {"activate", "static"}
    if endpoint not in public_endpoints:
        authorized, _ = check_user_license()
        if not authorized:
            if endpoint in {"index", "settings_page", "upload_page"}:
                return redirect(url_for("activate"))
            return jsonify({"error": "License required"}), 403

    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        supplied = request.headers.get("X-CSRF-Token", "") or request.form.get("csrf_token", "")
        expected = session.get("csrf_token", "")
        if not expected or not secrets.compare_digest(supplied, expected):
            return jsonify({"error": "Invalid CSRF token"}), 403


@app.after_request
def add_security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Cache-Control"] = response.headers.get(
        "Cache-Control", "no-store"
    )
    return response

def get_uploaded_videos():
    if os.path.exists(UPLOADED_VIDEOS_FILE):
        try:
            with open(UPLOADED_VIDEOS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_uploaded_videos(uploaded):
    atomic_json(PRIVATE_UPLOADED_VIDEOS_FILE, uploaded)
    os.chmod(PRIVATE_UPLOADED_VIDEOS_FILE, 0o600)


def mark_video_uploaded(keyword, video_id, processing_status="processing", account_index=0):
    uploaded = get_uploaded_videos()
    uploaded[keyword] = {
        "video_id": video_id,
        "processing_status": processing_status,
        "account_index": account_index,
        "timestamp": time.time()
    }
    save_uploaded_videos(uploaded)

def get_machine_id():
    """পিসির ইউনিক হার্ডওয়্যার আইডি বের করে (Windows এ একাধিক পদ্ধতিতে চেষ্টা করবে)"""
    try:
        import subprocess
        if os.name == 'nt': # Windows
            # প্রথম চেষ্টা: WMIC UUID
            try:
                cmd = 'wmic csproduct get uuid'
                uuid_str = subprocess.check_output(cmd, shell=True, stderr=subprocess.DEVNULL).decode().split('\n')
                if len(uuid_str) > 1 and uuid_str[1].strip():
                    return uuid_str[1].strip()
            except: pass

            # দ্বিতীয় চেষ্টা: Registry MachineGuid (খুবই নির্ভরযোগ্য)
            try:
                import winreg
                registry = winreg.ConnectRegistry(None, winreg.HKEY_LOCAL_MACHINE)
                key = winreg.OpenKey(registry, r"SOFTWARE\Microsoft\Cryptography")
                value, _ = winreg.QueryValueEx(key, "MachineGuid")
                winreg.CloseKey(key)
                return value
            except: pass

            # তৃতীয় চেষ্টা: Disk Serial
            try:
                cmd = 'wmic diskdrive get serialnumber'
                serial = subprocess.check_output(cmd, shell=True).decode().split('\n')[1].strip()
                if serial: return f"DISK-{serial}"
            except: pass

        # Fallback for all OS
        import uuid
        return f"GEN-{str(uuid.getnode())}"
    except:
        return "ID-NOT-FOUND"

def check_user_license():
    """ইউজার এবং মেশিন অনুমোদিত কিনা তা চেক করে"""
    if session.get('is_activated'):
        last_check = session.get('last_activation_check', 0)
        # Fetch fresh data if more than 10 seconds have passed (allows fresh data on reload)
        if time.time() - last_check < 10: 
            return True, {
                "email": session.get('user_email'), 
                "quota": session.get('video_quota'),
                "used": session.get('video_used', 0),
                "expiry_date": session.get('expiry_date', ''),
                "expiry_time": session.get('expiry_time', '00:00')
            }
        else:
            success, res = verify_activation(session.get('user_email'))
            return success, res
            
    # সেশনে না থাকলে লোকাল ডট ফাইল চেক করা
    saved_email = license_store.saved_activation_email()
    if saved_email:
        success, res = verify_activation(saved_email)
        return success, res
        
    return False, "Not activated"

# The 32 xfade transitions the render pipeline accepts. Mirrors
# amazon_video_maker.SAFE_TRANSITIONS -- the settings page used to build this
# list in JavaScript, so the two could disagree silently.
ALL_TRANSITIONS = [
    "fade", "wipeleft", "wiperight", "wipeup", "wipedown", "slideleft",
    "slideright", "slideup", "slidedown", "circlecrop", "rectcrop", "distance",
    "fadeblack", "fadewhite", "radial", "smoothleft", "smoothright", "smoothup",
    "smoothdown", "circleopen", "circleclose", "vertopen", "vertclose",
    "horzopen", "horzclose", "dissolve", "pixelize", "diagtl", "diagtr",
    "diagbl", "diagbr", "hlslice",
]


def setup_health():
    return setup_health_module.run_checks(get_settings(), output_root())


def output_root():
    """Project library root, with a writable per-user desktop fallback."""
    configured = str(get_settings().get("output_root") or "").strip()
    if configured and os.path.isdir(configured):
        return configured
    if is_frozen():
        fallback = DATA_DIR / "Outputs"
        fallback.mkdir(parents=True, exist_ok=True)
        return str(fallback)
    return os.path.join(PROJECT_ROOT, "files_created")


def recent_projects(limit=6):
    """Newest project folders for the dashboard, cheapest-possible scan."""
    root = output_root()
    out = []
    try:
        entries = [
            entry for entry in os.scandir(root) if entry.is_dir() and not entry.name.startswith(".")
        ]
    except OSError:
        return out
    entries.sort(key=lambda entry: entry.stat().st_mtime, reverse=True)
    uploaded = get_uploaded_videos()
    for entry in entries[:limit]:
        video = _find_video_file(entry.name, entry.path)
        record = uploaded.get(entry.name) or {}
        out.append(
            {
                "keyword": entry.name,
                "modified": entry.stat().st_mtime,
                "hasVideo": bool(video),
                "hasThumbnail": bool(_find_thumbnail_file(entry.path)),
                "uploadState": record.get("processing_status") if record else None,
                "videoId": record.get("video_id") if record else None,
            }
        )
    return out


def _license_context():
    """License/quota block shared by every authenticated page."""
    authorized, result = check_user_license()
    if not authorized or not isinstance(result, dict):
        return None
    used_raw = result.get('used', 0)
    try:
        used = int(used_raw) if used_raw and str(used_raw).strip() else 0
    except (ValueError, TypeError):
        used = 0
    try:
        quota = int(result.get('quota'))
    except (TypeError, ValueError):
        quota = 0
    return {
        "user_email": result.get('email'),
        "user_name": session.get('user_name', str(result.get('email') or '').split('@')[0]),
        "video_quota": quota,
        "current_videos": used,
        "remaining_videos": max(0, quota - used),
        "expiry_date": result.get('expiry_date', 'Lifetime'),
        "expiry_time": result.get('expiry_time', ''),
    }


def _render_options_context():
    """Everything the shared render-options panel and the voice controls need.

    Both creation modules include the same panel, so it is built once here
    instead of being duplicated (and drifting) per template.
    """
    settings = get_settings()
    # has_api_key is computed from the unredacted settings before
    # public_settings() strips `api_key` out of each row, so the browser can
    # show "a key is saved" without ever seeing the key itself.
    custom_providers_raw = [
        {**row, "has_api_key": bool(str(row.get("api_key") or "").strip())}
        for row in (settings.get("custom_tts_providers") or [])
        if isinstance(row, dict)
    ]
    return {
        "tts_providers": tts_catalog.public_registry(
            {
                "elevenlabs": ENABLE_ELEVENLABS,
                "cartesia": ENABLE_CARTESIA,
                "ai33pro": ENABLE_AI33PRO,
            },
            custom=tts_catalog.custom_registry_entries(settings),
        ),
        "custom_tts_providers": public_settings(custom_providers_raw),
        "partner_tags": settings.get("partner_tags") or [],
        "llm_model_presets": settings.get("llm_model_presets") or [],
        "director_options": tts_catalog.director_options(),
        "gemini_tts_models": GEMINI_TTS_MODELS,
        "gemini_tts_voices": GEMINI_TTS_VOICES,
        "settings_preview": public_settings(settings),
    }


@app.route('/')
def index():
    context = _license_context()
    if context is None:
        return redirect(url_for('activate'))
    return render_template(
        'dashboard.html',
        active_page='dashboard',
        health=setup_health(),
        recent_projects=recent_projects(limit=6),
        **context,
    )


@app.route('/create/url')
def create_url_page():
    context = _license_context()
    if context is None:
        return redirect(url_for('activate'))
    return render_template(
        'create_url.html',
        active_page='create_url',
        **context,
        **_render_options_context(),
    )


@app.route('/create/keywords')
def create_keywords_page():
    context = _license_context()
    if context is None:
        return redirect(url_for('activate'))
    return render_template(
        'create_keywords.html',
        active_page='create_keywords',
        **context,
        **_render_options_context(),
    )


@app.route('/activate', methods=['GET', 'POST'])
def activate():
    if request.method == 'POST':
        try:
            data = request.get_json()
            email = data.get('email')
            user_name = data.get('name')
            activation_code = data.get('activationCode')
            if not email or not user_name or not activation_code:
                return jsonify({"success": False, "error": "Name, email, and activation code are required"})
            
            success, res = verify_activation(email, user_name, activation_code)
            if success:
                return jsonify({"success": True})
            else:
                return jsonify({"success": False, "error": str(res)})
        except Exception:
            app.logger.error("Activation failed internally")
            return jsonify({"success": False, "error": "Activation service is temporarily unavailable"})

    # GET
    if session.get('is_activated'):
        return redirect(url_for('index'))
    return render_template('activation.html')


@app.route('/studio')
@app.route('/upload')  # v6 URL, kept so existing bookmarks still resolve
def studio_page():
    context = _license_context()
    if context is None:
        return redirect(url_for('activate'))
    return render_template('upload.html', active_page='studio', **context)

@app.route('/logout', methods=['POST'])
def logout():
    """Clear session and remove local activation file"""
    session.clear()
    act_file = str(ACTIVATION_FILE)
    if os.path.exists(act_file):
        try:
            os.remove(act_file)
        except: pass
    return redirect(url_for('activate'))

@app.route('/settings')
def settings_page():
    context = _license_context()
    if context is None:
        return redirect(url_for('activate'))
    return render_template(
        'settings.html',
        active_page='settings',
        llm_providers=model_catalog.public_registry(),
        transitions=ALL_TRANSITIONS,
        **context,
        **_render_options_context(),
    )

@app.route('/api/browse-folders')
def api_browse_folders():
    """Server-side folder picker for the Local Project Library Folder field.

    A browser <input type="file" webkitdirectory> can only hand back a file
    LIST, never a real filesystem path Flask could reuse for rendering -- so
    picking a folder for a local desktop app has to be a small server-side
    browser instead. Scoped to the user's home directory, matching
    validate_output_root()'s own constraint (a folder outside home is
    rejected on save anyway, so there's no point letting the picker wander
    there).
    """
    home = Path.home().resolve()
    requested = request.args.get("path", "").strip()
    try:
        current = Path(requested).expanduser().resolve() if requested else home
    except (OSError, RuntimeError):
        current = home
    if current != home:
        try:
            current.relative_to(home)
        except ValueError:
            current = home
    if not current.is_dir():
        current = home

    entries = []
    try:
        for child in sorted(current.iterdir(), key=lambda p: p.name.lower()):
            if not child.is_dir() or child.is_symlink() or child.name.startswith("."):
                continue
            try:
                os.listdir(child)  # cheap readability probe
            except OSError:
                continue
            entries.append({"name": child.name, "path": str(child)})
    except OSError as exc:
        return jsonify({"error": f"Could not list this folder: {exc}"}), 422

    parent = str(current.parent) if current != home else None
    return jsonify({
        "path": str(current),
        "home": str(home),
        "parent": parent,
        "entries": entries,
        "writable": os.access(current, os.W_OK),
    })


@app.route('/get_settings')
def get_settings_route():
    return jsonify(public_settings(get_settings()))

@app.route('/save_settings', methods=['POST'])
def save_settings_route():
    try:
        data = require_json_object()
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if len(json.dumps(data)) > 200_000:
        return jsonify({"error": "Settings payload is too large"}), 413
    current = get_settings()
    allowed = set(current) | _template_setting_keys() | {
        "output_root", "product_order", "content_mode", "music_mode",
        "music_track", "enable_intro_clip", "hands_on_notes",
        "gemini_tts_voice", "llm_fallback_enabled", "llm_chain",
        "tts_service", "kokoro_voice", "edge_voice", "partner_tag",
        "logo_text", "channel_url", "shorts_mode",
        "creators_api_client_id", "creators_api_client_secret",
        "creators_api_credential_version", "gemini_tts_model",
        "gemini_voice_style", "gemini_voice_pace", "gemini_voice_energy",
        "gemini_voice_warmth", "gemini_voice_accent",
        "gemini_voice_instruction", "gemini_pronunciations",
        "custom_tts_providers", "partner_tags", "llm_model_presets",
    }
    unknown = sorted(set(data) - allowed)
    if unknown:
        return jsonify({"error": f"Unknown setting(s): {', '.join(unknown)}"}), 422
    if data.get("output_root"):
        try:
            data["output_root"] = str(validate_output_root(data["output_root"]))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 422
    if any(key.startswith("gemini_") for key in data):
        normalized_voice = normalize_gemini_tts_settings({**current, **data})
        canonical_voice_fields = {
            "gemini_tts_model": normalized_voice["model"],
            "gemini_tts_voice": normalized_voice["voice"],
            "gemini_voice_style": normalized_voice["style"],
            "gemini_voice_pace": str(normalized_voice["pace"]),
            "gemini_voice_energy": str(normalized_voice["energy"]),
            "gemini_voice_warmth": str(normalized_voice["warmth"]),
            "gemini_voice_accent": normalized_voice["accent"],
            "gemini_voice_instruction": normalized_voice["instruction"],
        }
        for key, value in canonical_voice_fields.items():
            if key in data:
                data[key] = value
        if "gemini_pronunciations" in data:
            data["gemini_pronunciations"] = str(data["gemini_pronunciations"])[
                :5_000
            ]
    if "custom_tts_providers" in data:
        if not isinstance(data["custom_tts_providers"], list):
            return jsonify({"error": "custom_tts_providers must be an array"}), 422
        # /get_settings redacts api_key out of every entry (public_settings()
        # recurses into lists/dicts by key name), so the browser round-trips
        # each row without its key. Preserve the previously stored key for
        # any row that comes back with an empty one instead of overwriting it
        # -- the same "empty means keep existing" rule the top-level secret
        # fields already get, just applied one level deeper.
        existing_by_id = {
            str(row.get("id")): row
            for row in (current.get("custom_tts_providers") or [])
            if isinstance(row, dict)
        }
        cleaned = []
        for row in data["custom_tts_providers"][:20]:
            if not isinstance(row, dict):
                continue
            provider_id = str(row.get("id") or "").strip()[:60]
            if not provider_id:
                continue
            api_key = str(row.get("api_key") or "").strip()
            if not api_key:
                api_key = str(existing_by_id.get(provider_id, {}).get("api_key") or "")
            cleaned.append({
                "id": provider_id,
                "label": str(row.get("label") or provider_id)[:80],
                "endpoint": str(row.get("endpoint") or "").strip()[:500],
                "auth_header": str(row.get("auth_header") or "Authorization").strip()[:80] or "Authorization",
                "auth_scheme": str(row.get("auth_scheme", "Bearer")).strip()[:40],
                "voice_id": str(row.get("voice_id") or "").strip()[:200],
                "model_id": str(row.get("model_id") or "").strip()[:200],
                "text_field": str(row.get("text_field") or "text").strip()[:60] or "text",
                "api_key": api_key,
            })
        data["custom_tts_providers"] = cleaned

    if "partner_tags" in data:
        if not isinstance(data["partner_tags"], list):
            return jsonify({"error": "partner_tags must be an array"}), 422
        cleaned_tags = []
        seen_ids = set()
        for row in data["partner_tags"][:20]:
            if not isinstance(row, dict):
                continue
            tag = str(row.get("tag") or "").strip()[:60]
            if not tag:
                continue
            tag_id = str(row.get("id") or "").strip()[:60] or tag
            if tag_id in seen_ids:
                continue
            seen_ids.add(tag_id)
            cleaned_tags.append({
                "id": tag_id,
                "label": str(row.get("label") or tag)[:80],
                "tag": tag,
            })
        data["partner_tags"] = cleaned_tags

    if "llm_model_presets" in data:
        if not isinstance(data["llm_model_presets"], list):
            return jsonify({"error": "llm_model_presets must be an array"}), 422
        cleaned_presets = []
        seen_preset_ids = set()
        for row in data["llm_model_presets"][:20]:
            if not isinstance(row, dict):
                continue
            provider = str(row.get("provider") or "").strip()[:40]
            model = str(row.get("model") or "").strip()[:200]
            if not provider or not model:
                continue
            preset_id = str(row.get("id") or "").strip()[:60] or f"{provider}:{model}"
            if preset_id in seen_preset_ids:
                continue
            seen_preset_ids.add(preset_id)
            cleaned_presets.append({
                "id": preset_id,
                "label": str(row.get("label") or f"{provider} / {model}")[:80],
                "provider": provider,
                "model": model,
            })
        data["llm_model_presets"] = cleaned_presets

    # Empty secret fields mean "keep the existing key", so the redacted
    # settings response never forces users to re-enter credentials.
    for key in list(data):
        if any(
            marker in key.lower()
            for marker in ("api_key", "token", "secret", "client_id")
        ):
            if not isinstance(data[key], str) or not data[key].strip():
                data.pop(key)
    save_settings(data)
    # Update keyword-asin.txt if keywords are provided
    if 'keywords_asin' in data:
        kw_file = str(KEYWORDS_FILE)
        with open(kw_file, 'w', encoding='utf-8') as f:
            f.write(data['keywords_asin'])
    return jsonify({"success": True})


def _api_error(code, message, status):
    return jsonify({"error": {"code": code, "message": message}}), status


@app.route("/api/content-batches", methods=["GET", "POST"])
def content_batches():
    store, manager = _content_batch_services()
    if request.method == "GET":
        try:
            limit = int(request.args.get("limit", "10"))
        except ValueError:
            return _api_error("VALIDATION_ERROR", "limit must be an integer", 422)
        return jsonify({"data": store.list_batches(limit)})
    try:
        data = require_json_object()
        unknown = set(data) - {"urls"}
        if unknown:
            return _api_error(
                "VALIDATION_ERROR",
                f"Unknown field(s): {', '.join(sorted(unknown))}",
                422,
            )
        batch = store.create_batch(data.get("urls"))
        manager.start_batch(batch["batchId"])
        return jsonify({"data": batch}), 202
    except ValueError as exc:
        return _api_error("VALIDATION_ERROR", str(exc), 422)


@app.route("/api/content-batches/<batch_id>")
def content_batch_detail(batch_id):
    if not re.fullmatch(r"[a-f0-9]{32}", batch_id):
        return _api_error("NOT_FOUND", "Content batch was not found", 404)
    try:
        store, _ = _content_batch_services()
        return jsonify({"data": store.get_batch(batch_id)})
    except KeyError:
        return _api_error("NOT_FOUND", "Content batch was not found", 404)


@app.route("/api/content-jobs/<job_id>", methods=["PATCH"])
def update_content_job(job_id):
    if not re.fullmatch(r"[a-f0-9]{32}", job_id):
        return _api_error("NOT_FOUND", "Content job was not found", 404)
    try:
        store, _ = _content_batch_services()
        data = require_json_object()
        allowed = {"keyword", "contentType", "products", "isApproved"}
        unknown = set(data) - allowed
        if unknown:
            return _api_error(
                "VALIDATION_ERROR",
                f"Unknown field(s): {', '.join(sorted(unknown))}",
                422,
            )
        job = store.update_job(job_id, data)
        return jsonify({"data": job})
    except KeyError:
        return _api_error("NOT_FOUND", "Content job was not found", 404)
    except ValueError as exc:
        return _api_error("VALIDATION_ERROR", str(exc), 422)


@app.route("/api/content-jobs/<job_id>/retry", methods=["POST"])
def retry_content_job(job_id):
    if not re.fullmatch(r"[a-f0-9]{32}", job_id):
        return _api_error("NOT_FOUND", "Content job was not found", 404)
    try:
        store, manager = _content_batch_services()
        manager.retry_job(job_id)
        return jsonify({"data": store.get_job(job_id)}), 202
    except KeyError:
        return _api_error("NOT_FOUND", "Content job was not found", 404)


@app.route("/api/content-batches/<batch_id>/prepare", methods=["POST"])
def prepare_content_batch(batch_id):
    if not re.fullmatch(r"[a-f0-9]{32}", batch_id):
        return _api_error("NOT_FOUND", "Content batch was not found", 404)
    try:
        store, _ = _content_batch_services()
        lines = store.approved_generator_lines(batch_id)
    except KeyError:
        return _api_error("NOT_FOUND", "Content batch was not found", 404)
    if not lines:
        return _api_error(
            "VALIDATION_ERROR",
            "Approve at least one ready video before generation",
            422,
        )
    _write_keywords_file(lines)
    return jsonify({"data": {"batchId": batch_id, "videoCount": len(lines)}})


def _write_keywords_file(lines):
    """Atomic write of `keyword, ASIN, ASIN…` lines -- the one format both
    creation modules hand off to the render pipeline (amazon_video_maker.py
    reads only this file)."""
    target = os.fspath(KEYWORDS_FILE)
    temp_target = f"{target}.{secrets.token_hex(6)}.tmp"
    try:
        with open(temp_target, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_target, target)
    finally:
        if os.path.exists(temp_target):
            os.remove(temp_target)


ASIN_RE = re.compile(r"[A-Za-z0-9]{10}")


@app.route('/api/asins/validate', methods=['POST'])
def validate_asins():
    """Module 2 (Keywords/ASINs) live product lookup.

    Reuses ContentBatchManager's cached CreatorsApiClient/token -- there is
    deliberately only one Amazon client in the app, so both modules see
    identical validation behaviour and the OAuth token is never fetched twice.
    """
    try:
        data = require_json_object()
    except ValueError as exc:
        return _api_error("VALIDATION_ERROR", str(exc), 400)

    rows = data.get("rows")
    if not isinstance(rows, list) or not rows:
        return _api_error("VALIDATION_ERROR", "rows must be a non-empty array", 422)
    if len(rows) > 20:
        return _api_error("VALIDATION_ERROR", "A maximum of 20 keyword rows can be validated at once", 422)

    flat_products = []
    row_spans = []
    for row in rows:
        if not isinstance(row, dict):
            return _api_error("VALIDATION_ERROR", "Each row must be an object", 422)
        keyword = str(row.get("keyword", "")).strip()[:200]
        asins = [
            a.strip().upper() for a in (row.get("asins") or []) if isinstance(a, str)
        ]
        asins = [a for a in asins if ASIN_RE.fullmatch(a)][:10]
        start = len(flat_products)
        flat_products.extend({"asin": asin} for asin in asins)
        row_spans.append({"keyword": keyword, "start": start, "count": len(asins)})

    _, manager = _content_batch_services()
    client = manager.creators_client()
    if client.is_configured:
        try:
            enriched = client.enrich_products(flat_products)
            error_note = None
        except ValueError as exc:
            enriched = [
                {**product, "validationStatus": "VALIDATION_FAILED", "availability": "UNKNOWN"}
                for product in flat_products
            ]
            error_note = str(exc)
    else:
        # No Amazon Creators API credentials configured -- most creators
        # don't have API access, and defaulting every ASIN to MANUAL_REVIEW
        # gave them nothing to look at. Scrape the public product page
        # instead (same technique the render pipeline already uses) so
        # there's a real title/image/price by default, API or not.
        scraped = asin_lookup.lookup_asins([p["asin"] for p in flat_products])
        enriched = [
            {**product, **scraped.get(product["asin"], {"validationStatus": "NOT_FOUND", "availability": "UNKNOWN"})}
            for product in flat_products
        ]
        error_note = None

    results = []
    for span in row_spans:
        products = enriched[span["start"]: span["start"] + span["count"]]
        results.append({"keyword": span["keyword"], "products": products})

    return jsonify(
        {
            "data": results,
            "configured": client.is_configured,
            "error": error_note,
        }
    )


@app.route('/run_process')
def run_process():
    supplied = request.args.get("csrf_token", "")
    expected = session.get("csrf_token", "")
    if not expected or not secrets.compare_digest(supplied, expected):
        return jsonify({"error": "Invalid CSRF token"}), 403
    # বাটন ক্লিক করার সাথে সাথে রিকোয়েস্ট কনটেক্সটের ভেতরেই লাইসেন্স এবং কোটা রিড করছি
    authorized, result = check_user_license()
    quota = result.get('quota', 'unlimited') if isinstance(result, dict) else 'unlimited'
    
    # Capture user info for the final sync
    user_email = result.get('email') if isinstance(result, dict) else session.get('user_email')
    user_name = session.get('user_name', 'User')
    machine_id = get_machine_id()
    
    # Get initial used count from Google Sheet result - DONT USE SESSION AS IT MAY BE STALE
    # We force a fresh check via check_user_license which should have fresh data if 10s passed
    used_raw = result.get('used', 0) if isinstance(result, dict) else 0
    try:
        initial_used = int(used_raw) if used_raw and str(used_raw).strip() else 0
    except (ValueError, TypeError):
        initial_used = 0

    def generate(is_auth, email, name, m_id, q, start_count):
        session_video_count = 0 
        lock_acquired = GENERATION_LOCK.acquire(blocking=False)
        try:
            if not is_auth:
                yield f"data: [ERROR] Unauthorized access\n\n"
                return
            if not lock_acquired:
                yield "data: [ERROR] Another generation job is already running. Wait for it to finish.\n\n"
                return

            current_used = start_count
            if q != "unlimited":
                try:
                    if current_used >= int(q):
                        yield f"data: [QUOTA REACHED] You have reached your limit of {q} videos.\n\n"
                        yield f"data: __SESSION_COUNT__:{session_video_count}\n\n"
                        return
                except: pass

            yield f"data: [SYSTEM] License verified for {email}\n\n"
            yield f"data: [SYSTEM] Total Videos Created: {current_used}\n\n"
            yield f"data: [SYSTEM] Allowed Quota (Personal): {q}\n\n"
            yield "data: [SYSTEM] Connecting to backend...\n\n"
            
            # ভিডিও জেনারেশন প্রসেস শুরু
            import sys
            python_exe = sys.executable
            script_path = os.path.join(PROJECT_ROOT, 'app_files', 'amazon_video_maker.py')
            
            env = os.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"
            env["PYTHONIOENCODING"] = "utf-8"
            
            settings = get_settings()
            shorts_arg = ["--shorts"] if settings.get('shorts_mode', False) else []
            
            worker_command = (
                [python_exe, "--render-worker"] if is_frozen()
                else [python_exe, script_path]
            )
            process = subprocess.Popen(
                worker_command + ["--quota", str(q), "--used", str(current_used)] + shorts_arg,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1, 
                universal_newlines=True,
                encoding='utf-8',
                errors='replace',
                env=env
            )
            
            for line in iter(process.stdout.readline, ''):
                if line:
                    if "__TERMINATE_NOW__" in line:
                        yield f"data: __SESSION_COUNT__:{session_video_count}\n\n"
                        break
                    yield f"data: {line.strip()}\n\n"
                    # Real-time Quota Update check: Look for success markers in script output
                    if "process finished for:" in line.lower():
                        try:
                            # Increment used count and update sheet immediately
                            current_used += 1
                            session_video_count += 1
                            update_usage_on_sheet(email, current_used)
                            
                            # Notify UI of the new count so it can update the progress bars
                            yield f"data: __SYNC_QUOTA__:{current_used}\n\n"
                            yield f"data: __SESSION_COUNT__:{session_video_count}\n\n"
                            
                            # Update session to keep it fresh
                            session['video_used'] = current_used
                        except Exception as e:
                            print(f"Real-time sync error: {e}")
            
            process.stdout.close()
            process.wait()

        finally:
            if lock_acquired:
                GENERATION_LOCK.release()
            yield "data: __DONE__\n\n"


    res = Response(
        stream_with_context(
            generate(
                authorized,
                user_email,
                user_name,
                machine_id,
                quota,
                initial_used,
            )
        ),
        mimetype='text/event-stream',
    )
    res.headers['Cache-Control'] = 'no-cache'
    res.headers['X-Accel-Buffering'] = 'no'
    return res


@app.route('/preview_tts', methods=['POST'])
def preview_tts():
    """Starts a voice preview. Returns immediately.

    A cache hit resolves in the same response; anything else returns a jobId the
    client polls. Live (unsaved) form values are merged over stored settings so
    the preview reflects exactly what the user is looking at.
    """
    try:
        data = require_json_object()
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400

    text = str(data.get("text") or PREVIEW_TEXT)[:1500]
    stored = get_settings()
    config = {**stored, **data}
    config["service"] = str(data.get("service") or stored.get("tts_service") or "edge")
    if config["service"].startswith(tts_catalog.CUSTOM_PREFIX):
        spec = tts_catalog.custom_provider_spec(stored, config["service"])
        if not spec:
            return jsonify({"success": False, "error": "Unknown or unsaved custom TTS provider -- save it in Settings first"}), 422
        config["_custom_spec"] = spec
    elif config["service"] not in tts_catalog.PROVIDER_IDS:
        return jsonify({"success": False, "error": f"Unknown TTS provider '{config['service']}'"}), 422

    try:
        job = preview_service.start(text, config, ffmpeg_bin=FFMPEG_BIN)
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        return jsonify({"success": False, "error": str(exc)}), 500

    job["success"] = job["status"] != "error"
    # Back-compat alias for any older client still reading audio_url.
    job["audio_url"] = job.get("audioUrl")
    return jsonify(job)


@app.route('/preview_tts/<job_id>')
def preview_tts_status(job_id):
    if not re.fullmatch(r"[a-f0-9]{16,64}", job_id or ""):
        return jsonify({"success": False, "error": "Unknown preview job"}), 404
    job = preview_service.status(job_id)
    if job is None:
        return jsonify({"success": False, "error": "Unknown preview job"}), 404
    job["success"] = job["status"] != "error"
    job["audio_url"] = job.get("audioUrl")
    return jsonify(job)


@app.route('/preview_audio/<path:filename>')
def serve_preview_audio(filename):
    """Serves cached previews out of the private data directory."""
    if not re.fullmatch(r"[a-f0-9]{16,64}\.mp3", filename or ""):
        return jsonify({"error": "Not found"}), 404
    response = send_from_directory(str(PREVIEW_CACHE_DIR), filename, mimetype="audio/mpeg")
    # Content is immutable per cache key, so the browser may keep it.
    response.headers["Cache-Control"] = "private, max-age=3600"
    return response


@app.route('/api/llm/models')
def api_llm_models():
    provider = request.args.get("provider", "")
    if provider not in model_catalog.PROVIDERS:
        return jsonify({"error": f"Unknown provider '{provider}'"}), 422
    spec = model_catalog.PROVIDERS[provider]
    settings = get_settings()
    # The key never leaves the server; the browser only ever asks by provider.
    api_key = ""
    raw_key = settings.get(spec["key_field"], "")
    if isinstance(raw_key, str) and raw_key.strip():
        api_key = raw_key.strip().splitlines()[0].strip()
    endpoint = ""
    if spec["endpoint_field"]:
        endpoint = settings.get(spec["endpoint_field"]) or spec.get("default_endpoint", "")
    result = model_catalog.list_models(
        provider, api_key=api_key, endpoint=endpoint,
        refresh=request.args.get("refresh") == "1",
    )
    return jsonify(result)


@app.route('/api/tts/voices')
def api_tts_voices():
    provider = request.args.get("provider", "")
    if provider.startswith(tts_catalog.CUSTOM_PREFIX):
        return jsonify(
            {
                "voices": tts_catalog.list_voices(provider),
                "models": tts_catalog.list_models(provider),
            }
        )
    if provider not in tts_catalog.PROVIDERS:
        return jsonify({"error": f"Unknown provider '{provider}'"}), 422
    settings = get_settings()
    key_field = tts_catalog.PROVIDERS[provider]["key_field"]
    api_key = ""
    if key_field:
        raw_key = settings.get(key_field, "")
        if isinstance(raw_key, str) and raw_key.strip():
            api_key = raw_key.strip().splitlines()[0].strip()
    refresh = request.args.get("refresh") == "1"
    return jsonify(
        {
            "voices": tts_catalog.list_voices(provider, api_key=api_key, refresh=refresh),
            "models": tts_catalog.list_models(provider, api_key=api_key, refresh=refresh),
        }
    )


@app.route('/api/tts/test', methods=['POST'])
def api_tts_test():
    """Test Connection for a TTS provider -- mirrors /test_llm, but for
    voice. Synthesizes a short fixed line synchronously and reports
    pass/fail + latency, without going through the preview cache/audio
    playback UI (the point is a fast yes/no on the credentials + endpoint)."""
    try:
        data = require_json_object()
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400

    stored = get_settings()
    config = {**stored, **data}
    service = str(data.get("service") or stored.get("tts_service") or "edge")
    config["service"] = service

    draft_spec = data.get("custom_spec")
    if isinstance(draft_spec, dict):
        # Lets the settings page test a custom-provider row before it's been
        # saved. Trusting a client-supplied spec here (rather than looking it
        # up from storage, as /preview_tts does) is safe specifically because
        # this is a synchronous, non-cached, CSRF-protected action on the
        # same settings page the user just typed these values into -- nothing
        # is persisted or reused across sessions/pages from this call.
        config["service"] = f"{tts_catalog.CUSTOM_PREFIX}__draft__"
        config["_custom_spec"] = draft_spec
    elif service.startswith(tts_catalog.CUSTOM_PREFIX):
        spec = tts_catalog.custom_provider_spec(stored, service)
        if not spec:
            return jsonify({"success": False, "error": "Unknown or unsaved custom TTS provider -- save it in Settings first"}), 422
        config["_custom_spec"] = spec
    elif service not in tts_catalog.PROVIDER_IDS:
        return jsonify({"success": False, "error": f"Unknown TTS provider '{service}'"}), 422

    fd, temp_path = tempfile.mkstemp(suffix=".mp3", prefix="tts_test_")
    os.close(fd)
    started = time.time()
    try:
        result = tts_engine.synthesize(
            "This is a connection test.", temp_path, config, ffmpeg_bin=FFMPEG_BIN
        )
        return jsonify({
            "success": True,
            "provider": result["provider"],
            "ms": int((time.time() - started) * 1000),
        })
    except tts_engine.TTSError as exc:
        return jsonify({
            "success": False,
            "provider": service,
            "ms": int((time.time() - started) * 1000),
            "error": str(exc),
        })
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        return jsonify({"success": False, "provider": service, "error": str(exc)}), 500
    finally:
        if os.path.exists(temp_path):
            try: os.remove(temp_path)
            except OSError: pass


@app.route('/test_llm', methods=['POST'])
def test_llm():
    """Tests the primary provider and every fallback-chain entry using whatever
    is currently typed into the settings form, before it is saved -- so a bad
    key or model name is caught here instead of mid-render.

    Unlike v6 this reports one result per chain entry (with latency) rather than
    a single pass/fail for the chain as a whole.
    """
    try:
        data = request.get_json() or {}
        import llm_client

        stored = get_settings()

        def keys_for(provider):
            spec = model_catalog.PROVIDERS[provider]
            raw = data.get(spec["key_field"])
            if not isinstance(raw, str) or not raw.strip():
                raw = stored.get(spec["key_field"], "")
            return [k.strip() for k in str(raw or "").split("\n") if k.strip()]

        def entry_for(provider, model=None):
            spec = model_catalog.PROVIDERS[provider]
            endpoint = None
            if spec["endpoint_field"]:
                endpoint = (
                    data.get(spec["endpoint_field"])
                    or stored.get(spec["endpoint_field"])
                    or spec.get("default_endpoint")
                )
            chosen = model or data.get(spec["model_field"]) or stored.get(spec["model_field"])
            return {
                "provider": provider,
                "model": (chosen or spec["default_model"]).strip(),
                "api_keys": keys_for(provider),
                "endpoint": endpoint,
            }

        primary = data.get("llm_service", "gemini")
        if primary not in model_catalog.PROVIDERS:
            return jsonify({"success": False, "error": f"Unknown provider '{primary}'"}), 422

        chain = [entry_for(primary)]
        seen = {primary}
        if data.get("llm_fallback_enabled") and data.get("llm_chain"):
            for line in str(data["llm_chain"]).split("\n"):
                line = line.strip()
                if not line or "|" not in line:
                    continue
                prov, _, mdl = line.partition("|")
                prov = prov.strip().lower()
                if prov in seen or prov not in model_catalog.PROVIDERS:
                    continue
                seen.add(prov)
                chain.append(entry_for(prov, mdl.strip() or None))

        prompt = "Reply with exactly one short sentence confirming you received this test message."
        results = []
        first_success = None
        for entry in chain:
            started = time.time()
            if not entry["api_keys"]:
                results.append({
                    "provider": entry["provider"], "model": entry["model"],
                    "ok": False, "ms": 0, "detail": "No API key configured",
                })
                continue
            try:
                text = llm_client.call_with_keys(
                    entry["provider"], prompt, entry["api_keys"], entry["model"],
                    endpoint=entry["endpoint"], timeout=20,
                )
                elapsed = int((time.time() - started) * 1000)
                results.append({
                    "provider": entry["provider"], "model": entry["model"],
                    "ok": True, "ms": elapsed, "detail": text[:120],
                })
                if first_success is None:
                    first_success = entry["provider"]
            except llm_client.LLMCallError as exc:
                results.append({
                    "provider": entry["provider"], "model": entry["model"],
                    "ok": False, "ms": int((time.time() - started) * 1000),
                    "detail": str(exc)[:200],
                })

        return jsonify({
            "success": first_success is not None,
            "provider_used": first_success,
            "fallback_used": bool(first_success and first_success != primary),
            "results": results,
        })
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        return jsonify({"success": False, "error": str(exc)}), 500


# --- YouTube Logic ---

# Global variable for cached service
YOUTUBE_SERVICE_CACHE = {} # Cache per account index {index: service_object}
CURRENT_AUTH_INDEX = 0

def get_youtube_service(allow_manual_auth=True, index=None):
    global YOUTUBE_SERVICE_CACHE, CURRENT_AUTH_INDEX
    
    # Use specified index or current global index
    idx = index if index is not None else CURRENT_AUTH_INDEX
    
    # Ensure index is within range [0, 1, 2, 3]
    idx = max(0, min(idx, len(CLIENT_SECRETS_LIST) - 1))
    
    token_path = TOKEN_FILES_LIST[idx]
    secrets_path = CLIENT_SECRETS_LIST[idx]
    
    creds = None
    if os.path.exists(token_path):
        try:
            creds = Credentials.from_authorized_user_file(token_path, SCOPES)
        except Exception as e:
            print(f"[AUTH ERROR] Account {idx+1} token error: {e}")
            creds = None
    
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                print(f"[AUTH ERROR] Account {idx+1} refresh failed: {e}")
                creds = None
        else:
            if not allow_manual_auth:
                return None
            
            if not os.path.exists(secrets_path):
                print(f"[CRITICAL] Account {idx+1} client secret missing: {secrets_path}")
                return None
            
            try:
                print(f"[AUTH] Starting flow for Account {idx+1} using {os.path.basename(secrets_path)}...")
                flow = InstalledAppFlow.from_client_secrets_file(secrets_path, SCOPES)
                creds = flow.run_local_server(port=0)
            except Exception as e:
                print(f"[AUTH ERROR] Account {idx+1} setup failed: {e}")
                return None
            
        if creds:
            with open(token_path, 'w') as token:
                token.write(creds.to_json())
            os.chmod(token_path, 0o600)
        
        # Invalidate cache for this index if we re-authed or refreshed
        if idx in YOUTUBE_SERVICE_CACHE:
            del YOUTUBE_SERVICE_CACHE[idx]
    
    if idx not in YOUTUBE_SERVICE_CACHE:
        try:
            session_obj = AuthorizedSession(creds)
            adapter = RequestsHttpAdapter(session_obj)
            YOUTUBE_SERVICE_CACHE[idx] = build('youtube', 'v3', http=adapter, static_discovery=True)
            CURRENT_AUTH_INDEX = idx # Update global current index on success
        except Exception as e:
            print(f"[AUTH ERROR] YT Service Build Error (Idx {idx}): {e}")
            return None
    
    return YOUTUBE_SERVICE_CACHE[idx]

@app.route('/logout_youtube', methods=['POST'])
def logout_youtube():
    global YOUTUBE_SERVICE_CACHE
    # Clear tokens for all accounts
    for token_path in TOKEN_FILES_LIST:
        if os.path.exists(token_path):
            try:
                os.remove(token_path)
            except Exception as e:
                print(f"Error deleting token file: {e}")
    
    YOUTUBE_SERVICE_CACHE = {}
    return jsonify({"success": True})

@app.route('/check_auth')
def check_auth():
    auth_exists = os.path.exists(TOKEN_FILES_LIST[0])
    channel_name = "Auth Needed"
    error_msg = None
    if auth_exists:
        try:
            # Pass False to avoid opening browser during a background check
            service = get_youtube_service(allow_manual_auth=False, index=0)
            if service:
                request_channel = service.channels().list(part='snippet', mine=True)
                response = request_channel.execute()
                if 'items' in response and len(response['items']) > 0:
                    channel_name = response['items'][0]['snippet']['title']
                else:
                    channel_name = "Channel Not Found"
            else:
                auth_exists = False
        except Exception as e:
            print(f"Auth Check Error: {e}")
            error_msg = str(e)
            auth_exists = False
            # Pass False to avoid opening browser during a background check
            service = get_youtube_service(allow_manual_auth=False)
            if service:
                request_channel = service.channels().list(part='snippet', mine=True)
                response = request_channel.execute()
                if 'items' in response and len(response['items']) > 0:
                    channel_name = response['items'][0]['snippet']['title']
                else:
                    channel_name = "Channel Not Found"
            else:
                auth_exists = False
        except Exception as e:
            print(f"Auth Check Error: {e}")
            error_msg = str(e)
            
            # If we get an SSL or Connection error, don't necessarily delete the token
            # unless it's an explicit "invalid_grant"
            if "invalid_grant" in str(e).lower() or "token expired" in str(e).lower():
                if os.path.exists(TOKEN_FILE):
                    try: os.remove(TOKEN_FILE)
                    except: pass
                auth_exists = False
            
            if "WRONG_VERSION_NUMBER" in str(e) or "SSL" in str(e):
                channel_name = "Network/SSL Error"
                # Keep auth_exists = True so the UI shows the specific error instead of just "Auth Needed"
            else:
                auth_exists = False
                channel_name = "Auth Needed"
            
    return jsonify({"authenticated": auth_exists, "channel_name": channel_name, "error": error_msg})

@app.route('/get_playlists')
def get_playlists():
    try:
        service = get_youtube_service()
        request_pl = service.playlists().list(part='snippet', mine=True, maxResults=50)
        response = request_pl.execute()
        playlists = [{"id": "", "title": "-- None --"}]
        for item in response.get('items', []):
            playlists.append({"id": item['id'], "title": item['snippet']['title']})
        return jsonify(playlists)
    except:
        return jsonify([])

@app.route('/login', methods=['POST'])
def login():
    if not os.path.exists(CLIENT_SECRETS_FILE):
        return jsonify({"error": "client_secrets.json missing"}), 404
    
    try:
        flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRETS_FILE, SCOPES)
        creds = flow.run_local_server(port=0)
        
        with open(LOGIN_TOKEN_FILE, 'w') as token:
            token.write(creds.to_json())
        os.chmod(LOGIN_TOKEN_FILE, 0o600)
            
        # Clear session to force re-fetch user info and sync to WP
        session.pop('user_email', None)
        session.pop('user_name', None)
        session.pop('wp_synced', None)
        
        return jsonify({"authenticated": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/authenticate', methods=['POST'])
def authenticate():
    if not os.path.exists(CLIENT_SECRETS_FILE):
        return jsonify({"error": "client_secrets.json missing"}), 404
    
    try:
        # Clear existing token to force a fresh login
        if os.path.exists(TOKEN_FILE):
            try: os.remove(TOKEN_FILE)
            except: pass
        YOUTUBE_SERVICE_CACHE.pop(0, None)

        # Explicitly allow manual auth here
        service = get_youtube_service(allow_manual_auth=True)
        if service:
            # Verify it actually works
            try:
                service.channels().list(part='id', mine=True).execute()
                return jsonify({"authenticated": True})
            except Exception as ve:
                print(f"Post-auth verification failed: {ve}")
                return jsonify({"error": f"Auth completed but verification failed: {str(ve)}"}), 401
        
        return jsonify({"error": "Auth failed. Could not initialize YouTube service."}), 401
    except Exception as e:
        print(f"Auth Route Error: {traceback.format_exc()}")
        return jsonify({"error": str(e)}), 500

@app.route('/list_videos')
def list_videos():
    folders = []
    uploaded_data = get_uploaded_videos()
    projects_root = library_root()
    if os.path.exists(projects_root):
        project_dirs = []
        for keyword_dir in sorted(os.listdir(projects_root)):
            keyword_path = os.path.join(projects_root, keyword_dir)
            if not os.path.isdir(keyword_path):
                continue
            if os.path.isfile(os.path.join(keyword_path, "project.json")):
                project_dirs.append((keyword_dir, keyword_path))
                continue
            children = [
                name for name in sorted(os.listdir(keyword_path), reverse=True)
                if os.path.isdir(os.path.join(keyword_path, name))
                and (
                    os.path.isfile(os.path.join(keyword_path, name, "project.json"))
                    or (
                        _find_video_file(f"{keyword_dir}/{name}", os.path.join(keyword_path, name))
                        and os.path.isfile(os.path.join(keyword_path, name, "youtube.txt"))
                    )
                )
            ]
            if children:
                project_dirs.extend(
                    (f"{keyword_dir}/{child}", os.path.join(keyword_path, child))
                    for child in children
                )
            else:
                # Legacy project folders remain visible, but publishing them
                # requires a new QC report.
                project_dirs.append((keyword_dir, keyword_path))

        for project_id, k_path in project_dirs:
            try:
                video_file = _find_video_file(project_id, k_path)
                has_video = video_file is not None
                has_metadata = any(
                    os.path.exists(os.path.join(k_path, name))
                    for name in ("youtube.txt", "metadata.json", "yt_title.txt")
                )
                has_thumb = _find_thumbnail_file(k_path) is not None
                
                yt_title = project_id.split("/")[0].replace("-", " ").title()
                file_size = "0 MB"
                duration = "00:00"
                v_name = ""
                title_variants = []

                if has_video:
                    v_filename = video_file
                    v_name = v_filename
                    full_v_path = os.path.join(k_path, v_filename)
                    # Size
                    size_bytes = os.path.getsize(full_v_path)
                    file_size = f"{size_bytes / (1024*1024):.1f} MB"
                    
                    # Duration (Simple FFprobe)
                    try:
                        cmd = [FFPROBE_BIN, "-v", "error", "-show_entries", "format=duration",
                               "-of", "default=noprint_wrappers=1:nokey=1", full_v_path]
                        dur_sec = float(
                            subprocess.check_output(cmd, timeout=20).decode().strip()
                        )
                        mins = int(dur_sec // 60)
                        secs = int(dur_sec % 60)
                        duration = f"{mins:02d}:{secs:02d}"
                    except (OSError, ValueError, subprocess.SubprocessError) as exc:
                        print(f"[LIBRARY] duration probe failed for {v_filename}: {exc}")
                        duration = "N/A"

                if has_metadata:
                    metadata_payload = _load_project_metadata(k_path)
                    yt_title = metadata_payload.get('title') or yt_title
                    title_variants = metadata_payload.get("title_variants") or []

                folders.append({
                    "keyword": project_id,
                    "yt_title": yt_title,
                    "has_video": has_video,
                    "has_metadata": has_metadata,
                    "has_thumb": has_thumb,
                    "video_path": video_file if has_video else "",
                    "video_name": v_name,
                    "file_size": file_size,
                    "duration": duration,
                    "title_variants": title_variants,
                    "is_uploaded": project_id in uploaded_data,
                    "processing_status": (uploaded_data.get(project_id) or {}).get("processing_status"),
                    "can_delete_local": (uploaded_data.get(project_id) or {}).get("processing_status") == "succeeded",
                })
            except (OSError, ValueError, TypeError):
                continue
    folders.sort(key=lambda item: item["keyword"], reverse=True)
    return jsonify(folders)

@app.route('/thumbnail/<path:keyword>')
def get_thumbnail(keyword):
    """ভিডিও থাম্বনেইল সার্ভ করে"""
    try:
        k_path = project_path(keyword)
    except ValueError:
        return "Not Found", 404
    
    thumb_file = _find_thumbnail_file(k_path)
    if thumb_file:
        return send_from_directory(k_path, thumb_file)
    
    return "Not Found", 404

@app.route('/get_metadata')
def get_metadata():
    keyword = request.args.get('keyword')
    try:
        k_path = project_path(keyword)
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    if os.path.exists(k_path):
        data = _load_project_metadata(k_path)
        if data:
            data['success'] = True
            return jsonify(data)
        
    return jsonify({"success": True, "title": keyword, "description": "", "tags": ""})

@app.route('/bg_list')
def get_bg_list():
    bg_dir = os.path.join(PROJECT_ROOT, 'app_files', 'bg_img')
    files = []
    if os.path.exists(bg_dir):
        files = [f for f in os.listdir(bg_dir) if f.lower().endswith(('.jpg', '.png', '.jpeg'))]
    return jsonify(files)

@app.route('/bg_image/<filename>')
def serve_bg_image(filename):
    bg_dir = os.path.join(PROJECT_ROOT, 'app_files', 'bg_img')
    return send_from_directory(bg_dir, filename)

@app.route('/save_metadata', methods=['POST'])
def save_metadata():
    try:
        data = require_json_object()
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    keyword = data.get('keyword')
    try:
        k_path = project_path(keyword)
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    if os.path.exists(k_path):
        meta = _load_project_metadata(k_path)
        
        meta['title'] = str(data.get('title', meta.get('title', '')))[:100].strip()
        meta['description'] = str(data.get('description', meta.get('description', '')))[:5000].strip()
        meta['tags'] = str(data.get('tags', meta.get('tags', '')))[:500].strip()
        _save_compact_metadata(k_path, meta)
            
        return jsonify({"success": True})
    return jsonify({"success": False, "error": "Folder not found"})

def _get_video_is_shorts(k_path):
    """Detects Shorts projects for thumbnail re-editing."""
    meta_path = os.path.join(k_path, 'metadata.json')
    if os.path.exists(meta_path):
        try:
            with open(meta_path, 'r', encoding='utf-8') as f:
                return bool(json.load(f).get('is_shorts', False))
        except Exception:
            pass
    thumb_file = _find_thumbnail_file(k_path)
    if thumb_file:
        try:
            with Image.open(os.path.join(k_path, thumb_file)) as img:
                return img.height > img.width
        except Exception:
            pass
    return False


def _get_or_create_cutout(source_path, model_name, k_path):
    """Background-removal result cache, keyed by (source path, model, source
    mtime). Without this, every slider tweak in the live preview re-ran full
    rembg inference on the same unmodified source image, because only an
    explicitly-uploaded product file ever got its cutout persisted to disk.
    generate_thumbnail()'s own is_transparent check already skips re-running
    rembg on an already-transparent image, so this cache only needs to cover
    the very first (still-opaque) pass."""
    cutout_path = os.path.join(k_path, '_thumb_cutout_cache.png')
    marker_path = os.path.join(k_path, '_thumb_cutout_cache.marker')
    try:
        src_mtime = os.path.getmtime(source_path)
    except OSError:
        return source_path, False

    marker_value = f"{os.path.abspath(source_path)}|{model_name}|{src_mtime}"
    if os.path.exists(cutout_path) and os.path.exists(marker_path):
        try:
            with open(marker_path, 'r') as f:
                if f.read().strip() == marker_value:
                    return cutout_path, True  # cache hit
        except Exception:
            pass

    try:
        from rembg import remove
        session = thumbnail_generator._get_rembg_session(model_name)
        with open(source_path, 'rb') as f:
            input_data = f.read()
        output_data = remove(input_data, session=session, alpha_matting=False)
        with open(cutout_path, 'wb') as f:
            f.write(output_data)
        with open(marker_path, 'w') as f:
            f.write(marker_value)
        return cutout_path, True
    except Exception as e:
        print(f"[THUMBNAIL] Cutout cache miss, rembg failed: {e}")
        return source_path, False


def _resolve_existing_thumbnail_sources(k_path, keyword, selected_bg, model_name):
    """Resolves bg_path/prod_path from whatever is already on disk (custom
    uploads from a prior request, or auto-picked product photos) -- used by
    both the no-new-upload branch of /edit_thumbnail and by /save_thumbnail,
    which never receives file uploads (any upload already happened during an
    earlier live-preview tick)."""
    bg_path = None
    if selected_bg:
        bg_root = os.path.join(PROJECT_ROOT, 'app_files', 'bg_img')
        try:
            bg_path = str(resolve_project_dir(bg_root, selected_bg))
        except ValueError:
            bg_path = None
    if not bg_path:
        cb = os.path.join(k_path, 'custom_bg.png')
        bg_path = cb if os.path.exists(cb) else os.path.join(PROJECT_ROOT, 'app_files', 'bg_img', 'bg.jpg')

    cp = os.path.join(k_path, 'custom_prod.png')
    remove_bg_flag = True
    if os.path.exists(cp):
        prod_path = cp
    else:
        # Product photos actually live in per-ASIN subfolders (e.g.
        # {keyword}/{ASIN}/{ASIN}_img_1.jpg), not directly in k_path -- a
        # top-level-only glob here basically never found anything for a real
        # generated video. Prefer a top-level match (from a prior manual
        # upload) if one exists, otherwise fall back one level down.
        img_files = glob.glob(os.path.join(k_path, "*.jpg")) + glob.glob(os.path.join(k_path, "*.png"))
        excluded_markers = ('thumbnail', f'{os.path.basename(k_path)}.jpg', 'custom_bg', 'temp_thumb_preview', '_thumb_cutout_cache')
        prod_path = next((f for f in img_files if not any(m in os.path.basename(f) for m in excluded_markers)), None)
        if not prod_path:
            sub_files = sorted(glob.glob(os.path.join(k_path, "*", "*.jpg")) + glob.glob(os.path.join(k_path, "*", "*.png")))
            prod_path = next((f for f in sub_files if not any(m in os.path.basename(f).lower() for m in ('video', 'thumb'))), None)

        if prod_path:
            cutout, ok = _get_or_create_cutout(prod_path, model_name, k_path)
            if ok:
                prod_path = cutout
                remove_bg_flag = False

    return bg_path, prod_path, remove_bg_flag


def _collect_thumbnail_params(data, settings):
    """Shared param-building for /edit_thumbnail and /save_thumbnail, so a
    save always renders with exactly the same params the last preview used
    (previously /save_thumbnail ignored these entirely and just copied
    whatever temp preview file happened to be on disk -- stale if a render
    was still in flight or had failed)."""
    def bounded(value, minimum, maximum, cast=float):
        number = cast(value)
        return max(minimum, min(maximum, number))

    return {
        "bg_overlay_color": data.get('bg_overlay_color', settings.get('thumb_overlay_color', '#000000')),
        "bg_overlay_opacity": bounded(data.get('bg_overlay_opacity', settings.get('thumb_overlay_opacity', 0.4)), 0, 1),
        "text_colors": json.loads(data.get('text_colors')) if isinstance(data.get('text_colors'), str) else (data.get('text_colors') or [
            settings.get('thumb_text_top', '#facc15'),
            settings.get('thumb_text_bot', '#FFFFFF')
        ]),
        "text_bg_color": data.get('text_bg_color', settings.get('thumb_text_bg_color', '#1e293b')),
        "text_bg_opacity": bounded(data.get('text_bg_opacity', settings.get('thumb_text_bg_opacity', 0.9)), 0, 1),
        "glow_color": data.get('glow_color', settings.get('thumb_glow_color', '#FFFFFF')),
        "glow_radius_mult": bounded(data.get('glow_radius_mult', settings.get('thumb_glow_radius', 1.0)), 0, 2),
        "glow_opacity": bounded(data.get('glow_opacity', settings.get('thumb_glow_opacity', 0.8)), 0, 1),
        "product_x_offset": bounded(data.get('product_x_offset', 0), -2000, 2000),
        "product_y_offset": bounded(data.get('product_y_offset', 0), -2000, 2000),
        "product_scale_mult": bounded(data.get('product_scale_mult', 1.0), 0.2, 3),
        "font_name": data.get('font_name', settings.get('thumb_font', 'Roboto-Bold.ttf')),
        "font_size_mult": bounded(data.get('font_size_mult', 1.0), 0.5, 2),
        "wrap_width_override": bounded(data['wrap_width_override'], 4, 30, int) if data.get('wrap_width_override') else None,
        "line_spacing_mult": bounded(data.get('line_spacing_mult', 1.0), 0.5, 2),
        "text_stroke_color": data.get('text_stroke_color') or None,
        "text_stroke_width": bounded(data.get('text_stroke_width', 0), 0, 20, int),
        "text_shadow_color": data.get('text_shadow_color') or None,
        "text_shadow_offset": bounded(data.get('text_shadow_offset', 4), 0, 50, int),
        "box_radius": bounded(data.get('box_radius', 10), 0, 100, int),
        "box_padding_mult": bounded(data.get('box_padding_mult', 1.0), 0.2, 3),
        "output_quality": bounded(data.get('output_quality', 95), 50, 100, int),
    }


@app.route('/edit_thumbnail', methods=['POST'])
def edit_thumbnail():
    data = request.form
    keyword = data.get('keyword')
    try:
        k_path = project_path(keyword)
    except ValueError as exc:
        return str(exc), 400
    if not os.path.exists(k_path): return "Folder not found", 404

    bg_path = None
    if 'bg_file' in request.files and request.files['bg_file'].filename != '':
        bg_path = os.path.join(k_path, 'custom_bg.png')
        try:
            save_validated_image(request.files['bg_file'], bg_path)
        except ValueError as exc:
            return str(exc), 400

    # Check for selected background from gallery
    selected_bg = data.get('selected_bg')
    if not bg_path and selected_bg:
        try:
            bg_path = str(resolve_project_dir(
                os.path.join(PROJECT_ROOT, 'app_files', 'bg_img'), selected_bg
            ))
        except ValueError:
            return "Invalid background selection", 400

    file_uploaded_this_request = 'prod_file' in request.files and request.files['prod_file'].filename != ''
    prod_path = None
    remove_bg_flag = True
    settings = get_settings()
    selected_model = settings.get('rembg_model', 'birefnet-general')

    if file_uploaded_this_request:
        prod_path = os.path.join(k_path, 'custom_prod.png')
        try:
            save_validated_image(request.files['prod_file'], prod_path)
        except ValueError as exc:
            return str(exc), 400
        remove_bg_flag = data.get('rmbg') == 'true'
        if remove_bg_flag:
            try:
                from rembg import remove
                session = thumbnail_generator._get_rembg_session(selected_model)
                with open(prod_path, 'rb') as i:
                    input_data = i.read()
                    # Disabling alpha_matting or tuning it significantly to avoid trimming subject edges
                    output_data = remove(input_data, session=session, alpha_matting=False)
                with open(prod_path, 'wb') as o:
                    o.write(output_data)
                remove_bg_flag = False  # already done above; generate_thumbnail shouldn't redo it
            except Exception as e:
                print(f"Rembg error: {e}")

    # For whichever of bg/prod wasn't just uploaded this request, resolve
    # from what's already on disk (custom upload from an earlier tick, or
    # auto-pick + cached cutout).
    resolved_bg, resolved_prod, resolved_remove_bg = _resolve_existing_thumbnail_sources(
        k_path, keyword, selected_bg, selected_model
    )
    if not bg_path:
        bg_path = resolved_bg
    if not prod_path:
        prod_path = resolved_prod
        remove_bg_flag = resolved_remove_bg

    is_shorts = _get_video_is_shorts(k_path)
    bg_folder_for_shorts = os.path.join(PROJECT_ROOT, 'app_files', 'shorts_bg_img' if is_shorts else 'bg_img')

    try:
        params = _collect_thumbnail_params(data, settings)
    except Exception as e:
        print(f"Params error: {e}")
        return f"Invalid params: {str(e)}", 400

    temp_thumb = os.path.join(k_path, 'temp_thumb_preview.png')
    # Remove old temp if exists to ensure we see a fresh one
    if os.path.exists(temp_thumb):
        try: os.remove(temp_thumb)
        except: pass

    # Text source priority: custom_title > compact metadata > keyword
    title = data.get('custom_title')
    if not title:
        title = _load_project_metadata(k_path).get("title") or keyword

    try:
        thumbnail_generator.generate_thumbnail(
            product_image_path=prod_path,
            title_text=title,
            output_path=temp_thumb,
            bg_path=bg_path,
            bg_folder=bg_folder_for_shorts,
            remove_bg=remove_bg_flag,
            model_name=selected_model,
            **params
        )
        if os.path.exists(temp_thumb):
            return send_file(temp_thumb, mimetype='image/png')
        else:
            return "Thumbnail file not generated", 500
    except Exception as e:
        print(f"Generation error: {e}")
        return f"Generation failed: {str(e)}", 500

@app.route('/save_thumbnail', methods=['POST'])
def save_thumbnail():
    """Re-renders straight to the final {keyword}.jpg from the submitted
    params, rather than copying temp_thumb_preview.png. The old copy-based
    save could persist a STALE render if this was clicked inside the 500ms
    preview debounce window, or right after a failed preview call -- the
    file on disk wouldn't match what the user was actually looking at."""
    try:
        data = require_json_object()
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    keyword = data.get('keyword')
    try:
        k_path = project_path(keyword)
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    if not os.path.exists(k_path):
        return jsonify({"success": False, "error": "Folder not found"})

    final_thumb = os.path.join(k_path, "Thumbnail.jpg")
    settings = get_settings()
    selected_model = settings.get('rembg_model', 'birefnet-general')
    selected_bg = data.get('selected_bg')

    bg_path, prod_path, remove_bg_flag = _resolve_existing_thumbnail_sources(
        k_path, keyword, selected_bg, selected_model
    )
    is_shorts = _get_video_is_shorts(k_path)
    bg_folder_for_shorts = os.path.join(PROJECT_ROOT, 'app_files', 'shorts_bg_img' if is_shorts else 'bg_img')

    try:
        params = _collect_thumbnail_params(data, settings)
    except Exception as e:
        return jsonify({"success": False, "error": f"Invalid params: {e}"})

    title = data.get('custom_title')
    if not title:
        title = _load_project_metadata(k_path).get("title") or keyword

    try:
        thumbnail_generator.generate_thumbnail(
            product_image_path=prod_path,
            title_text=title,
            output_path=final_thumb,
            bg_path=bg_path,
            bg_folder=bg_folder_for_shorts,
            remove_bg=remove_bg_flag,
            model_name=selected_model,
            **params
        )
        # temp preview is now stale relative to the freshly-saved final; drop it
        temp_thumb = os.path.join(k_path, 'temp_thumb_preview.png')
        if os.path.exists(temp_thumb):
            try: os.remove(temp_thumb)
            except Exception: pass
        return jsonify({"success": True})
    except Exception as e:
        print(f"Save thumbnail generation error: {e}")
        return jsonify({"success": False, "error": str(e)})

@app.route('/upload_progress/<path:keyword>')
def get_upload_progress(keyword):
    return jsonify({"progress": UPLOAD_PROGRESS.get(keyword, 0)})


@app.route('/upload_status/<path:keyword>', methods=['POST'])
def get_upload_status(keyword):
    uploaded = get_uploaded_videos()
    record = uploaded.get(keyword)
    if not record or not record.get("video_id"):
        return jsonify({"error": "Upload record not found"}), 404
    try:
        account_index = int(record.get("account_index", 0))
        service = get_youtube_service(allow_manual_auth=False, index=account_index)
        if not service:
            return jsonify({"error": "Selected YouTube account is not authenticated"}), 401
        response = service.videos().list(
            part="processingDetails,status", id=record["video_id"]
        ).execute()
        item = (response.get("items") or [{}])[0]
        details = item.get("processingDetails") or {}
        processing_status = details.get(
            "processingStatus",
            item.get("status", {}).get("uploadStatus", "processing"),
        )
        record["processing_status"] = processing_status
        record["checked_at"] = time.time()
        save_uploaded_videos(uploaded)
        return jsonify({
            "success": True,
            "processingStatus": processing_status,
            "canDeleteLocal": processing_status == "succeeded",
            "error": details.get("processingFailureReason")
            or item.get("status", {}).get("rejectionReason"),
        })
    except Exception:
        return jsonify({"error": "Could not check YouTube processing status"}), 502

import glob
@app.route('/upload_video', methods=['POST'])
def upload_video():
    authorized, message = check_user_license()
    if not authorized:
        return jsonify({"error": "License required"}), 403

    try:
        data = require_json_object()
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    keyword = data.get('keyword')
    try:
        privacy, publish_at = validate_publish_options(
            data.get('privacy', 'unlisted'), data.get('publish_at', '')
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 422
    playlist_id = data.get('playlist_id', '')
    try:
        account_index = int(data.get("account_index", 0))
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid YouTube account"}), 422
    if account_index < 0 or account_index >= len(CLIENT_SECRETS_LIST):
        return jsonify({"error": "Invalid YouTube account"}), 422
    
    try:
        k_path = project_path(keyword)
        if not os.path.exists(k_path):
            return jsonify({"error": f"Folder not found: {keyword}"}), 404

        # Upload only an explicitly finalized video. Never fall back to a
        # temporary segment when a render stopped half-way.
        video_file = _find_video_file(keyword, k_path)
        if not video_file: return jsonify({"error": "Video file not found"}), 404
        qc_path = os.path.join(k_path, "qc_report.json")
        if os.path.isfile(qc_path):
            with open(qc_path, "r", encoding="utf-8") as handle:
                if json.load(handle).get("status") != "PASSED":
                    return jsonify({"error": "Video did not pass QC"}), 409
        
        meta = _load_project_metadata(k_path)

        # Prepare Body
        title = meta.get('title') or keyword
        description = meta.get('description') or ""
        raw_tags = meta.get('tags') or ""
        
        if isinstance(raw_tags, str):
            # Split by comma, newline, or semicolon
            tags_list = [t.strip() for t in re.split(r'[,\n;]', raw_tags) if t.strip()]
        elif isinstance(raw_tags, list):
            tags_list = [str(t).strip() for t in raw_tags if str(t).strip()]
        else:
            tags_list = []

        # Sanitize and limit tags (YouTube 500 char limit total)
        tags = []
        total_len = 0
        seen_tags = set()
        for t in tags_list:
            # YouTube tags cannot have < or >
            t = re.sub(r'[<>\n\r\t]', '', t)
            # Remove any characters that aren't letters, numbers, spaces, or dots/dashes
            t = re.sub(r'[^a-zA-Z0-9\s\.\-]', ' ', t)
            t = " ".join(t.split()) # Clean double spaces
            
            if not t: continue
            if not any(c.isalnum() for c in t): continue # Must have at least one letter/number
            if t.lower() in seen_tags: continue
            if len(t) > 100: t = t[:100].strip()
            
            # YouTube API 500 characters limit includes all tags and commas joining them
            if total_len + len(t) + len(tags) <= 470 and len(tags) < 45: 
                tags.append(t)
                total_len += len(t)
                seen_tags.add(t.lower())
            else:
                break

        print(f"[UPLOAD] Keyword: {keyword} | Tags Count: {len(tags)} | Total Tags Length: {total_len + max(0, len(tags)-1)}")

        # Final sanitization for Title/Description (YouTube rejects < >)
        title = re.sub(r'[<>]', '', title)
        description = re.sub(r'[<>]', '', description)

        body = {
            'snippet': {
                'title': title[:100], 
                'description': description[:5000],
                'tags': tags,
                'categoryId': '22'
            },
            'status': {
                'privacyStatus': privacy,
                'selfDeclaredMadeForKids': False
            }
        }

        if privacy == 'private' and publish_at:
            body['status']['publishAt'] = publish_at
        
        # Upload only through the explicitly selected account. Automatic
        # credential rotation could publish to the wrong channel.
        success = False
        response = None
        last_error = ""

        for i in [account_index]:
            print(f"[UPLOAD] Attempting upload with selected Account {i+1}...")
            service = get_youtube_service(allow_manual_auth=False, index=i)
            if not service:
                print(f"[ROTATION] Account {i+1} not authenticated/available. Skipping.")
                continue
                
            try:
                media = MediaFileUpload(os.path.join(k_path, video_file), mimetype='video/mp4', chunksize=1024*1024, resumable=True)
                request_upload = service.videos().insert(part='snippet,status', body=body, media_body=media)
                session_dir = DATA_DIR / "upload_sessions"
                session_dir.mkdir(parents=True, exist_ok=True)
                session_key = hashlib.sha256(
                    f"{account_index}:{keyword}".encode("utf-8")
                ).hexdigest()
                session_file = session_dir / f"{session_key}.json"
                if session_file.exists():
                    try:
                        session_data = json.loads(session_file.read_text(encoding="utf-8"))
                        if session_data.get("videoSize") == os.path.getsize(os.path.join(k_path, video_file)):
                            request_upload.resumable_uri = session_data.get("resumableUri")
                    except (OSError, ValueError):
                        pass
                
                UPLOAD_PROGRESS[keyword] = 0
                chunk_response = None
                transient_attempts = 0
                while chunk_response is None:
                    try:
                        status, chunk_response = request_upload.next_chunk()
                        transient_attempts = 0
                    except (HttpError, ResumableUploadError) as chunk_error:
                        status_code = getattr(getattr(chunk_error, "resp", None), "status", 0)
                        if status_code not in {500, 502, 503, 504} or transient_attempts >= 5:
                            raise
                        transient_attempts += 1
                        time.sleep(min(16, 2 ** transient_attempts))
                        continue
                    if getattr(request_upload, "resumable_uri", None):
                        session_file.write_text(
                            json.dumps({
                                "resumableUri": request_upload.resumable_uri,
                                "videoSize": os.path.getsize(os.path.join(k_path, video_file)),
                                "updatedAt": time.time(),
                            }),
                            encoding="utf-8",
                        )
                        session_file.chmod(0o600)
                    if status:
                        progress = int(status.progress() * 100)
                        UPLOAD_PROGRESS[keyword] = progress
                        # print(f"[UPLOAD] Acc {i+1} | {keyword} | {progress}%...")

                response = chunk_response
                if session_file.exists():
                    session_file.unlink()
                success = True
                print(f"[UPLOAD] Upload successful with Account {i+1}!")
                break # Exit loop on success
                
            except ResumableUploadError as e:
                # Check for Quota Exceeded (403)
                error_content = str(e.content) if e.content else ""
                if e.resp.status == 403 or "quotaExceeded" in error_content:
                    last_error = f"Selected Account {i+1} reached its daily quota"
                    break
                elif e.resp.status in [200, 201]:
                    # Handle edge case where it actually worked but raised exception
                    success = True
                    try: response = json.loads(e.content)
                    except: response = {'id': 'uploaded'}
                    break
                else:
                    print(f"[UPLOAD ERROR] Account {i+1} Failed: {str(e)}")
                    last_error = str(e)
                    break # Fatal error, don't rotate for non-quota issues? Or maybe do?
                    # The prompt says: "If a 403 Quota Exceeded error occurs... it should switch"
                    # So for other errors we might want to stop or continue. 
                    # Usually better to stop on real errors.
            except Exception as e:
                print(f"[UPLOAD ERROR] Account {i+1} Exception: {str(e)}")
                last_error = str(e)
                # If it's a "quota" message in a generic exception
                if "quota" in str(e).lower():
                    continue
                break

        if not success:
            UPLOAD_PROGRESS.pop(keyword, None)
            return jsonify({"error": last_error or "Upload failed"}), 500

        video_id = response.get('id') if response else None
        UPLOAD_PROGRESS[keyword] = 100 
        
        # 1. Thumbnail (Don't stop if fails - often due to 'Advanced Features' not enabled)
        thumb_file = _find_thumbnail_file(k_path)
        thumb_path = os.path.join(k_path, thumb_file) if thumb_file else None

        if video_id and thumb_path and os.path.exists(thumb_path):
            try:
                # Use a small chunksize for thumbnail upload too
                thumb_media = MediaFileUpload(thumb_path, mimetype='image/jpeg')
                service.thumbnails().set(videoId=video_id, media_body=thumb_media).execute()
                print(f"[SUCCESS] Thumbnail uploaded for {video_id}")
            except Exception as te:
                print(f"[WARNING] Thumbnail upload skipped/failed: {str(te)}")
                # This ensures video upload succeeds even if user doesn't have custom thumbnails enabled
            
        # 2. Playlist
        if video_id and playlist_id:
            try:
                service.playlistItems().insert(
                    part='snippet',
                    body={
                        'snippet': {
                            'playlistId': playlist_id,
                            'resourceId': {'kind': 'youtube#video', 'videoId': video_id}
                        }
                    }
                ).execute()
                print(f"[SUCCESS] Added to playlist {playlist_id}")
            except Exception as pe:
                print(f"[WARNING] Playlist addition skipped/failed: {str(pe)}")

        processing_status = "processing"
        processing_error = ""
        if video_id:
            for _ in range(10):
                try:
                    status_response = service.videos().list(
                        part="processingDetails,status", id=video_id
                    ).execute()
                    item = (status_response.get("items") or [{}])[0]
                    details = item.get("processingDetails") or {}
                    processing_status = details.get(
                        "processingStatus",
                        item.get("status", {}).get("uploadStatus", "processing"),
                    )
                    if processing_status in {"succeeded", "failed", "rejected"}:
                        processing_error = (
                            details.get("processingFailureReason")
                            or item.get("status", {}).get("rejectionReason")
                            or ""
                        )
                        break
                except Exception as status_exc:
                    processing_error = str(status_exc)
                    break
                time.sleep(2)

        mark_video_uploaded(keyword, video_id, processing_status, account_index)
        if processing_status in {"failed", "rejected"}:
            return jsonify({
                "error": processing_error or "YouTube rejected video processing",
                "videoId": video_id,
                "processingStatus": processing_status,
            }), 502
        return jsonify({
            "success": True,
            "videoId": video_id,
            "processingStatus": processing_status,
            "canDeleteLocal": processing_status == "succeeded",
        })
    except Exception as e:
        print(f"UPLOAD ERROR: {str(e)}")
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@app.route('/delete_folder', methods=['POST'])
def delete_folder():
    try:
        data = require_json_object()
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    keyword = data.get('keyword')
    if not keyword: return jsonify({"error": "No keyword"}), 400
    
    import shutil
    try:
        k_path = project_path(keyword)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if os.path.exists(k_path):
        try:
            uploaded = get_uploaded_videos()
            upload_record = uploaded.get(keyword) or {}
            if upload_record and upload_record.get("processing_status") != "succeeded":
                return jsonify({
                    "error": "Local deletion is blocked until YouTube processing succeeds"
                }), 409
            shutil.rmtree(k_path)
            
            # Remove from uploaded_videos.json if exists
            if keyword in uploaded:
                del uploaded[keyword]
                save_uploaded_videos(uploaded)
                    
            return jsonify({"success": True})
        except Exception as e:
            return jsonify({"error": str(e)}), 500
    return jsonify({"error": "Folder not found"}), 404

@app.route('/update_title', methods=['POST'])
def update_title():
    try:
        data = require_json_object()
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    keyword = data.get('keyword')
    new_title = data.get('title')
    if not keyword or not new_title:
        return jsonify({"error": "Missing data"}), 400
    new_title = str(new_title).strip()[:100]
    
    try:
        k_path = project_path(keyword)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if not os.path.exists(k_path):
        return jsonify({"error": "Folder not found"}), 404
    
    try:
        meta = _load_project_metadata(k_path)
        meta["title"] = new_title
        meta.setdefault("description", "")
        meta.setdefault("tags", "")
        _save_compact_metadata(k_path, meta)

        # Keep legacy metadata.json in sync if it exists.
        meta_file = os.path.join(k_path, 'metadata.json')
        if os.path.exists(meta_file):
            with open(meta_file, 'r', encoding='utf-8') as f:
                legacy_meta = json.load(f)
            legacy_meta['title'] = new_title
            with open(meta_file, 'w', encoding='utf-8') as f:
                json.dump(legacy_meta, f, indent=4, ensure_ascii=False)
            
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/upload_all', methods=['POST'])
def upload_all_route():
    data = request.json
    privacy = data.get('privacy', 'unlisted')
    playlist_id = data.get('playlist_id', '')
    
    # The frontend is already doing a loop in uploadAll() so this route is less critical
    return jsonify({"success": True})

def open_browser_after_start(url, delay=1.0):
    def _open():
        time.sleep(delay)
        try:
            webbrowser.open(url)
        except Exception as exc:
            print(f"Browser auto-open failed: {exc}", flush=True)

    threading.Thread(target=_open, daemon=True).start()


if __name__ == '__main__':
    host = "127.0.0.1"
    port = 7503
    open_browser_after_start(f"http://{host}:{port}")
    app.run(debug=False, host=host, port=port)
