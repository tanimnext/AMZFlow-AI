import requests
import re
import os
import json
import base64
import html
import sys
import csv
import io
import time
from datetime import datetime
from pathlib import Path

# Fix for Windows Unicode printing errors
if sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except AttributeError:
        # Fallback for older python or specific environments
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# Add current folder to path so it can find metadata_generator and thumbnail_generator
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import asyncio
import math
import subprocess
import glob
from concurrent.futures import ThreadPoolExecutor, as_completed
# Not importing moviepy to keep it fast and dependency-free for rendering
# We still need edge_tts
import edge_tts
import shutil
import random
import threading
import hashlib
from urllib.parse import urlparse
import llm_client
import metadata_generator
import thumbnail_generator
import media_qc
import music_manager
from caption_utils import build_srt
sys.path.append(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web_app"))
import tts_engine
from runtime_support import kokoro_files, quiet_subprocess_kwargs, resolve_binary
from product_core import (
    atomic_json,
    format_youtube_text,
    is_safe_https_url,
    order_products,
    slugify,
    validate_output_root,
)
from secure_paths import DATA_DIR, KEYWORDS_FILE, SETTINGS_FILE as PRIVATE_SETTINGS_FILE
from voice_config import build_gemini_tts_prompt, normalize_gemini_tts_settings
from PIL import Image

# Custom print that flushes immediately for real-time web logs
def print(*args, **kwargs):
    if 'flush' not in kwargs:
        kwargs['flush'] = True
    
    # Add timestamp [HH:MM:SS] to the beginning of the message
    timestamp = datetime.now().strftime("[%H:%M:%S]")
    if args:
        args = (f"{timestamp} {args[0]}",) + args[1:]
    else:
        args = (f"{timestamp}",)
        
    import builtins
    builtins.print(*args, **kwargs)

# Configuration for AI
LLM_SERVICE = "longcat" # "longcat", "gemini", or "openai"
LONGCAT_API_KEYS = []
LONGCAT_ENDPOINT = "https://api.longcat.chat/v1/chat/completions"
LONGCAT_MODEL = "longcat-flash"
GEMINI_API_KEYS = []
GEMINI_MODEL = "gemini-1.5-flash"
OPENAI_API_KEYS = []
OPENAI_MODEL = "gpt-4o-mini"
OPENROUTER_API_KEYS = []
OPENROUTER_MODEL = "google/gemini-2.0-flash-exp:free"
DEEPSEEK_API_KEYS = []
DEEPSEEK_MODEL = "deepseek-chat"
DEEPSEEK_ENDPOINT = "https://api.deepseek.com/chat/completions"
# Vertex AI (Google Cloud) -- same Gemini models as the "gemini" provider,
# but billed to a GCP project (so a fresh project's $300 free-trial credit
# applies) via a service-account instead of a simple AI Studio API key.
VERTEX_PROJECT_ID = ""
VERTEX_LOCATION = "us-central1"
VERTEX_SERVICE_ACCOUNT_JSON = ""
VERTEX_LLM_MODEL = "gemini-2.5-flash"
# Optional multi-model fallback chain. When enabled, if the primary
# LLM_SERVICE provider's keys are all exhausted, these additional
# "provider|model" lines (each reusing that provider's OWN already-configured
# key list) are tried in order before giving up entirely.
LLM_FALLBACK_ENABLED = False
LLM_CHAIN_RAW = ""
LLM_TIMEOUT_SECONDS = 120
# Silence inserted between separately-voiced narration beats, so they land as
# spoken thoughts with a breath between them rather than one flat run-on.
BEAT_BREATH_SECONDS = 0.28
# How long a "Number 3" countdown card stays on screen.
RANK_SLIDE_SECONDS = 3.0
# Background-music bed level. Narration is loudness-normalized to -14 LUFS
# in the concat stage, so a bed averaging about -32 dBFS sits well under it
# -- audible in the gaps, never competing with speech.
MUSIC_BED_TARGET_DBFS = -32.0
# Used only when a track's level can't be measured at all.
MUSIC_BED_FALLBACK_GAIN_DB = -24.0
# Keystroke ticks under the typed caption bar. Quiet on purpose -- it is a
# texture behind narration, not a sound effect competing with it.
TYPING_SFX_GAIN = 0.22
# Opt-in: write the opener/closer per video with the LLM instead of the
# fixed template. Off by default -- it costs two extra LLM calls per video
# and the template output is predictable, which some users prefer.
AI_INTRO_OUTRO = False
# On-screen captions. Off by default: burning them in costs a full extra
# encode pass, and the .srt sidecar is always written regardless.
# Two alternating narrators. Off by default: one voice is the normal
# shape of a product review, and an unintended two-person read was a
# bug users reported, not a feature they asked for.
DUAL_VOICE_ENABLED = False
DUAL_VOICE_SECOND = ""
CAPTIONS_ENABLED = False
# Sizing/colour for the typed key-point bar under the product title (NOT a
# subtitle overlay -- see create_product_segment_ffmpeg's caption block).
# Deliberately small: multiplied by TEXT_SCALE (1.5) at render time, so 26
# lands around 39px on the 1080-tall canvas.
CAPTIONS_FONT_SIZE = 26
COLOR_CAPTIONS_TEXT = "#FFE95C"
COLOR_CAPTIONS_BG = "#000000"
VAL_CAPTIONS_BG_OPACITY = 0.55
# Playback rate applied to narration audio only (not the video). TTS engines
# read at a measured, even pace that sounds sluggish next to how a real
# review host talks; a small speed-up lands closer to natural without the
# artifacts a larger stretch introduces. 1.0 disables it.
NARRATION_SPEED = 1.08


def _positive_float(value, fallback, low, high):
    """Same string-from-a-form coercion as _positive_int, clamped to a
    sane range so a typo cannot produce a 0.01s slide or 50x audio."""
    try:
        parsed = float(str(value).strip())
    except (TypeError, ValueError):
        return fallback
    if parsed <= 0:
        return fallback
    return max(low, min(high, parsed))


def _positive_int(value, fallback):
    """Settings arrive as strings from the form; a blank or junk
    timeout must not crash the render, it must fall back."""
    try:
        parsed = int(float(str(value).strip()))
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed > 0 else fallback
PARTNER_TAG = "your-tag-20"
USE_YEAR = True
USE_BEST = True
YEAR = "2026"

# TTS Configuration
TTS_SERVICE = "edge" # "edge", "elevenlabs", or "cartesia"
ELEVENLABS_API_KEY = ""
ELEVENLABS_VOICE_ID = "21m00Tcm4TlvDq8ikWAM" # Default Rachel (Stable ID)
AI33PRO_API_KEY = ""
AI33PRO_VOICE_ID = "Xb7hH8MSUJpSbSDYk0k2"
AI33PRO_MODEL_ID = "eleven_multilingual_v2"
ELEVENLABS_MODEL_ID = "eleven_multilingual_v2"
CARTESIA_API_KEY = ""
CARTESIA_VOICE_ID = "a0e9987c-56f7-4141-9fa0-81932f79c20b"
CARTESIA_MODEL_ID = "sonic-english"
DEEPGRAM_API_KEY = ""
DEEPGRAM_VOICE_ID = "aura-2-thalia-en"
DEEPGRAM_MODEL_ID = ""
GOOGLE_TTS_VOICE_ID = "en-US-Chirp3-HD-Sulafat"
GOOGLE_TTS_MONTHLY_CHAR_LIMIT = "1000000"
# AndrewMultilingualNeural sounds noticeably more natural than the plain
# (non-multilingual) neural voices, and a neutral rate/pitch avoids the
# robotic resampling artifact a pitch shift introduces on neural TTS output.
EDGE_VOICE = "en-US-AndrewMultilingualNeural"
EDGE_RATE = "+0%"
EDGE_PITCH = "+0Hz"
# Gemini TTS reuses the same Google Generative Language API key(s) already
# used for the LLM (GEMINI_API_KEYS below) -- no separate credential needed.
GEMINI_TTS_MODEL = "gemini-3.1-flash-tts-preview"
# Vertex AI TTS model -- same voice set, billed through VERTEX_SERVICE_ACCOUNT_JSON.
VERTEX_TTS_MODEL = "gemini-2.5-flash-preview-tts"
GEMINI_TTS_VOICE = "Sadaltager"
GEMINI_VOICE_STYLE = "TRUSTED_EXPERT"
GEMINI_VOICE_PACE = "50"
GEMINI_VOICE_ENERGY = "45"
GEMINI_VOICE_WARMTH = "60"
GEMINI_VOICE_ACCENT = "US_NEUTRAL"
GEMINI_VOICE_INSTRUCTION = ""
GEMINI_PRONUNCIATIONS = ""
# Kokoro is a free, local, offline TTS model (no API key, no network call).
KOKORO_VOICE = "af_heart"
# Optional user-ordered TTS fallback chain (Settings -> Voice -> Fallback
# Chain), one provider id per line -- mirrors LLM_FALLBACK_ENABLED/
# LLM_CHAIN_RAW. Edge TTS is always appended as the final safety net
# regardless of this list (free, no key, effectively never unavailable).
TTS_FALLBACK_ENABLED = False
TTS_CHAIN_RAW = ""

# Branding/Visual Defaults (Can be overridden via settings.json)
LOGO_TEXT = "Top Picks"
INTRO_TEXT = "Discover the best products for your lifestyle. We've curated the top selections just for you."
# The shipped default. Recognised so build_conclusion_text() can replace it
# with a real verdict + price-check close, while a user's own custom outro
# text is always left exactly as they wrote it.
DEFAULT_OUTRO_TEXT = "Thanks for watching! Check the links in description to find these products on Amazon."
OUTRO_TEXT = DEFAULT_OUTRO_TEXT
COLOR_INTRO_TITLE = "#FFFFFF"
COLOR_INTRO_BG = "#000000"
VAL_INTRO_BG_OPACITY = 0.5
ENABLE_INTRO_BG = True

COLOR_OUTRO_TITLE = "#FFFFFF"
# Orange reads as the call-to-action colour the rest of the UI uses,
# and the closing card IS the call to action.
COLOR_OUTRO_BG = "#F97316"
VAL_OUTRO_BG_OPACITY = 0.5
ENABLE_OUTRO_BG = True

COLOR_PRODUCT_TITLE = "#FFFFFF"
COLOR_PRODUCT_BG = "#000000"
VAL_PRODUCT_BG_OPACITY = 0.8

# 0.7 (then 0.10) made the intro/outro background image read as dimmed --
# default is now 0 (no scrim at all, full original color). Settings ->
# Intro/Outro -> Opacity still lets a user dial dimming back in per-project.
COLOR_INTRO_OVERLAY_BG = "#000000"
VAL_INTRO_OVERLAY_OPACITY = 0.0
COLOR_OUTRO_OVERLAY_BG = "#000000"
VAL_OUTRO_OVERLAY_OPACITY = 0.0

COLOR_BLUEBAR = "#007bff"
COLOR_RANK_BG = "#FFD700"
COLOR_LOGO_TEXT = "#000000"
COLOR_LOGO_BG = "#FFFFFF"
VAL_LOGO_BG_OPACITY = 0.6
COLOR_LINK_CHECK_TEXT = "#000000"
COLOR_LINK_CHECK_BG = "#FFFFFF"
INTRO_FONT = "Roboto-Regular.ttf"
OUTRO_FONT = "Roboto-Regular.ttf"

# Thumbnail Defaults
COLOR_THUMB_TEXT_TOP = "#facc15"
COLOR_THUMB_TEXT_BOT = "#FFFFFF"
COLOR_THUMB_TEXT_BG = "#000000"
VAL_THUMB_TEXT_BG_OPACITY = 0.9
THUMB_FONT = "Roboto-Bold.ttf"

# Thumbnail Overlay & Glow
COLOR_THUMB_OVERLAY = "#000000"
VAL_THUMB_OVERLAY_OPACITY = 0.4
COLOR_THUMB_GLOW = "#FFFFFF"
VAL_THUMB_GLOW_RADIUS = 1.0
VAL_THUMB_GLOW_OPACITY = 0.8
REMBG_MODEL = "birefnet-general"
ACTIVE_TRANSITIONS = ["fade", "wipeleft", "wiperight", "wipeup", "wipedown", "slideleft", "slideright", "slideup", "slidedown", "circlecrop", "rectcrop", "distance", "fadeblack", "fadewhite", "radial", "smoothleft", "smoothright", "smoothup", "smoothdown", "circleopen", "circleclose", "vertopen", "vertclose", "horzopen", "horzclose", "dissolve", "pixelize", "diagtl", "diagtr", "diagbl", "diagbr", "hlslice"]
SAFE_TRANSITIONS = frozenset(ACTIVE_TRANSITIONS)
SHORTS_MODE = False
# Output resolution: 1080x1920 (Shorts, 9:16) / 1920x1080 (Normal, 16:9).
# zoompan works at 2x this and downsamples on export, which is what keeps
# slow pans/zooms smooth instead of visibly stepping.
def output_resolution():
    return (1080, 1920) if SHORTS_MODE else (1920, 1080)


def zoom_working_resolution():
    w, h = output_resolution()
    return w * 2, h * 2


# Scales absolute font/padding sizes that were tuned for the legacy
# 720x1280/1280x720 canvas up to the new 1080x1920/1920x1080 one.
TEXT_SCALE = 1.5
CONTENT_MODE = "spec_based"
HANDS_ON_NOTES = ""
# Whole-video speed multiplier (picture + narration together), 0.75-1.5x.
# Applied as a single setpts/atempo pass after final assembly -- see
# apply_video_speed().
VIDEO_SPEED = 1.0

def load_settings_from_external():
    """Loads configuration from web_app/settings.json if it exists."""
    global LLM_SERVICE, LONGCAT_API_KEYS, LONGCAT_ENDPOINT, LONGCAT_MODEL, PARTNER_TAG
    global GEMINI_API_KEYS, GEMINI_MODEL, OPENAI_API_KEYS, OPENAI_MODEL, OPENROUTER_API_KEYS, OPENROUTER_MODEL, DEEPSEEK_API_KEYS, DEEPSEEK_MODEL, DEEPSEEK_ENDPOINT
    global VERTEX_PROJECT_ID, VERTEX_LOCATION, VERTEX_SERVICE_ACCOUNT_JSON, VERTEX_LLM_MODEL, VERTEX_TTS_MODEL
    global LLM_FALLBACK_ENABLED, LLM_CHAIN_RAW, LLM_TIMEOUT_SECONDS
    global RANK_SLIDE_SECONDS, NARRATION_SPEED, AI_INTRO_OUTRO
    global DUAL_VOICE_ENABLED, DUAL_VOICE_SECOND
    global CAPTIONS_ENABLED, CAPTIONS_FONT_SIZE
    global COLOR_CAPTIONS_TEXT, COLOR_CAPTIONS_BG, VAL_CAPTIONS_BG_OPACITY
    global USE_YEAR, USE_BEST, YEAR
    global TTS_SERVICE, ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID, ELEVENLABS_MODEL_ID, AI33PRO_API_KEY, AI33PRO_VOICE_ID, AI33PRO_MODEL_ID, CARTESIA_API_KEY, CARTESIA_VOICE_ID, CARTESIA_MODEL_ID, DEEPGRAM_API_KEY, DEEPGRAM_VOICE_ID, DEEPGRAM_MODEL_ID, GOOGLE_TTS_VOICE_ID, GOOGLE_TTS_MONTHLY_CHAR_LIMIT, EDGE_VOICE, EDGE_RATE, EDGE_PITCH
    global GEMINI_TTS_MODEL, GEMINI_TTS_VOICE, GEMINI_VOICE_STYLE
    global GEMINI_VOICE_PACE, GEMINI_VOICE_ENERGY, GEMINI_VOICE_WARMTH
    global GEMINI_VOICE_ACCENT, GEMINI_VOICE_INSTRUCTION, GEMINI_PRONUNCIATIONS
    global KOKORO_VOICE
    global TTS_FALLBACK_ENABLED, TTS_CHAIN_RAW
    global LOGO_TEXT, INTRO_TEXT, OUTRO_TEXT, COLOR_INTRO_TITLE, COLOR_INTRO_BG, VAL_INTRO_BG_OPACITY, ENABLE_INTRO_BG, COLOR_OUTRO_TITLE, COLOR_OUTRO_BG, VAL_OUTRO_BG_OPACITY, ENABLE_OUTRO_BG, COLOR_PRODUCT_TITLE
    global COLOR_PRODUCT_BG, VAL_PRODUCT_BG_OPACITY
    global COLOR_INTRO_OVERLAY_BG, VAL_INTRO_OVERLAY_OPACITY, COLOR_OUTRO_OVERLAY_BG, VAL_OUTRO_OVERLAY_OPACITY, COLOR_BLUEBAR, COLOR_RANK_BG
    global COLOR_LOGO_TEXT, COLOR_LOGO_BG, VAL_LOGO_BG_OPACITY, COLOR_LINK_CHECK_TEXT, COLOR_LINK_CHECK_BG
    global INTRO_FONT, OUTRO_FONT
    global COLOR_THUMB_TEXT_TOP, COLOR_THUMB_TEXT_BOT, COLOR_THUMB_TEXT_BG, VAL_THUMB_TEXT_BG_OPACITY, THUMB_FONT
    global COLOR_THUMB_OVERLAY, VAL_THUMB_OVERLAY_OPACITY, COLOR_THUMB_GLOW, VAL_THUMB_GLOW_RADIUS, VAL_THUMB_GLOW_OPACITY, REMBG_MODEL
    global ACTIVE_TRANSITIONS, SHORTS_MODE, CONTENT_MODE, HANDS_ON_NOTES, VIDEO_SPEED
    
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(script_dir)
    settings_path = str(PRIVATE_SETTINGS_FILE)
    
    if os.path.exists(settings_path):
        try:
            with open(settings_path, 'r', encoding='utf-8') as f:
                s = json.load(f)
                LLM_SERVICE = s.get('llm_service', LLM_SERVICE)
                
                lk = s.get('longcat_api_key', "")
                if lk:
                    LONGCAT_API_KEYS = [k.strip() for k in lk.split('\n') if k.strip()]

                LONGCAT_ENDPOINT = s.get('longcat_endpoint', LONGCAT_ENDPOINT)
                LONGCAT_MODEL = s.get('longcat_model', LONGCAT_MODEL)
                
                gk = s.get('gemini_api_key', "")
                if gk:
                    GEMINI_API_KEYS = [k.strip() for k in gk.split('\n') if k.strip()]

                GEMINI_MODEL = s.get('gemini_model', GEMINI_MODEL)

                ok = s.get('openai_api_key', "")
                if ok:
                    OPENAI_API_KEYS = [k.strip() for k in ok.split('\n') if k.strip()]
                OPENAI_MODEL = s.get('openai_model', OPENAI_MODEL)

                ork = s.get('openrouter_api_key', "")
                if ork:
                    OPENROUTER_API_KEYS = [k.strip() for k in ork.split('\n') if k.strip()]
                OPENROUTER_MODEL = s.get('openrouter_model', OPENROUTER_MODEL)

                dk = s.get('deepseek_api_key', "")
                if dk:
                    DEEPSEEK_API_KEYS = [k.strip() for k in dk.split('\n') if k.strip()]
                DEEPSEEK_MODEL = s.get('deepseek_model', DEEPSEEK_MODEL)
                DEEPSEEK_ENDPOINT = s.get('deepseek_endpoint', DEEPSEEK_ENDPOINT)

                VERTEX_PROJECT_ID = s.get('vertex_project_id', VERTEX_PROJECT_ID)
                VERTEX_LOCATION = s.get('vertex_location', VERTEX_LOCATION) or "us-central1"
                VERTEX_SERVICE_ACCOUNT_JSON = s.get('vertex_service_account_private_key', VERTEX_SERVICE_ACCOUNT_JSON)
                VERTEX_LLM_MODEL = s.get('vertex_llm_model', VERTEX_LLM_MODEL)
                VERTEX_TTS_MODEL = s.get('vertex_tts_model', VERTEX_TTS_MODEL)

                LLM_FALLBACK_ENABLED = s.get('llm_fallback_enabled', LLM_FALLBACK_ENABLED)
                LLM_CHAIN_RAW = s.get('llm_chain', LLM_CHAIN_RAW)
                LLM_TIMEOUT_SECONDS = _positive_int(s.get('llm_timeout_seconds'), LLM_TIMEOUT_SECONDS)
                RANK_SLIDE_SECONDS = _positive_float(s.get('rank_slide_seconds'), RANK_SLIDE_SECONDS, 0.5, 15.0)
                NARRATION_SPEED = _positive_float(s.get('narration_speed'), NARRATION_SPEED, 0.5, 2.0)
                AI_INTRO_OUTRO = bool(s.get('ai_intro_outro', AI_INTRO_OUTRO))
                DUAL_VOICE_ENABLED = bool(s.get('dual_voice_enabled', DUAL_VOICE_ENABLED))
                DUAL_VOICE_SECOND = str(s.get('dual_voice_second', DUAL_VOICE_SECOND) or '').strip()
                CAPTIONS_ENABLED = bool(s.get('captions_enabled', CAPTIONS_ENABLED))
                CAPTIONS_FONT_SIZE = _positive_float(s.get('captions_font_size'), CAPTIONS_FONT_SIZE, 12, 120)
                COLOR_CAPTIONS_TEXT = s.get('captions_text_color', COLOR_CAPTIONS_TEXT)
                COLOR_CAPTIONS_BG = s.get('captions_bg_color', COLOR_CAPTIONS_BG)
                VAL_CAPTIONS_BG_OPACITY = _positive_float(s.get('captions_bg_opacity'), VAL_CAPTIONS_BG_OPACITY, 0.0, 1.0) if str(s.get('captions_bg_opacity', '')).strip() not in ('', '0') else 0.0

                PARTNER_TAG = s.get('partner_tag', PARTNER_TAG)
                USE_YEAR = s.get('use_year', USE_YEAR)
                USE_BEST = s.get('use_best', USE_BEST)
                YEAR = str(s.get('year', YEAR))
                
                # TTS
                TTS_SERVICE = s.get('tts_service', TTS_SERVICE)
                ELEVENLABS_API_KEY = s.get('elevenlabs_api_key', ELEVENLABS_API_KEY)
                ELEVENLABS_VOICE_ID = s.get('elevenlabs_voice_id', ELEVENLABS_VOICE_ID)
                ELEVENLABS_MODEL_ID = s.get('elevenlabs_model_id', ELEVENLABS_MODEL_ID)
                AI33PRO_API_KEY = s.get('ai33pro_api_key', AI33PRO_API_KEY)
                AI33PRO_VOICE_ID = s.get('ai33pro_voice_id', AI33PRO_VOICE_ID)
                AI33PRO_MODEL_ID = s.get('ai33pro_model_id', AI33PRO_MODEL_ID)
                CARTESIA_API_KEY = s.get('cartesia_api_key', CARTESIA_API_KEY)
                CARTESIA_VOICE_ID = s.get('cartesia_voice_id', CARTESIA_VOICE_ID)
                CARTESIA_MODEL_ID = s.get('cartesia_model_id', CARTESIA_MODEL_ID)
                DEEPGRAM_API_KEY = s.get('deepgram_api_key', DEEPGRAM_API_KEY)
                DEEPGRAM_VOICE_ID = s.get('deepgram_voice_id', DEEPGRAM_VOICE_ID)
                DEEPGRAM_MODEL_ID = s.get('deepgram_model_id', DEEPGRAM_MODEL_ID)
                GOOGLE_TTS_VOICE_ID = s.get('google_tts_voice_id', GOOGLE_TTS_VOICE_ID)
                GOOGLE_TTS_MONTHLY_CHAR_LIMIT = s.get('google_tts_monthly_char_limit', GOOGLE_TTS_MONTHLY_CHAR_LIMIT)
                EDGE_VOICE = s.get('edge_voice', EDGE_VOICE)
                EDGE_RATE = s.get('edge_rate', EDGE_RATE)
                EDGE_PITCH = s.get('edge_pitch', EDGE_PITCH)
                GEMINI_TTS_MODEL = s.get('gemini_tts_model', GEMINI_TTS_MODEL)
                GEMINI_TTS_VOICE = s.get('gemini_tts_voice', GEMINI_TTS_VOICE)
                GEMINI_VOICE_STYLE = s.get('gemini_voice_style', GEMINI_VOICE_STYLE)
                GEMINI_VOICE_PACE = s.get('gemini_voice_pace', GEMINI_VOICE_PACE)
                GEMINI_VOICE_ENERGY = s.get('gemini_voice_energy', GEMINI_VOICE_ENERGY)
                GEMINI_VOICE_WARMTH = s.get('gemini_voice_warmth', GEMINI_VOICE_WARMTH)
                GEMINI_VOICE_ACCENT = s.get('gemini_voice_accent', GEMINI_VOICE_ACCENT)
                GEMINI_VOICE_INSTRUCTION = s.get('gemini_voice_instruction', GEMINI_VOICE_INSTRUCTION)
                GEMINI_PRONUNCIATIONS = s.get('gemini_pronunciations', GEMINI_PRONUNCIATIONS)
                KOKORO_VOICE = s.get('kokoro_voice', KOKORO_VOICE)
                TTS_FALLBACK_ENABLED = s.get('tts_fallback_enabled', TTS_FALLBACK_ENABLED)
                TTS_CHAIN_RAW = s.get('tts_chain', TTS_CHAIN_RAW)

                LOGO_TEXT = s.get('logo_text', LOGO_TEXT)
                INTRO_TEXT = s.get('intro_text', INTRO_TEXT)
                OUTRO_TEXT = s.get('outro_text', OUTRO_TEXT)
                
                # Visuals
                COLOR_INTRO_TITLE = s.get('intro_title_color', COLOR_INTRO_TITLE)
                COLOR_INTRO_BG = s.get('intro_title_bg_color', COLOR_INTRO_BG)
                VAL_INTRO_BG_OPACITY = float(s.get('intro_title_bg_opacity', VAL_INTRO_BG_OPACITY))
                ENABLE_INTRO_BG = s.get('intro_title_bg_enable', ENABLE_INTRO_BG)
                
                COLOR_OUTRO_TITLE = s.get('outro_title_color', COLOR_OUTRO_TITLE)
                COLOR_OUTRO_BG = s.get('outro_title_bg_color', COLOR_OUTRO_BG)
                VAL_OUTRO_BG_OPACITY = float(s.get('outro_title_bg_opacity', VAL_OUTRO_BG_OPACITY))
                ENABLE_OUTRO_BG = s.get('outro_title_bg_enable', ENABLE_OUTRO_BG)

                COLOR_PRODUCT_TITLE = s.get('product_title_color', COLOR_PRODUCT_TITLE)
                COLOR_PRODUCT_BG = s.get('product_bg_color', COLOR_PRODUCT_BG)
                VAL_PRODUCT_BG_OPACITY = float(s.get('product_bg_opacity', VAL_PRODUCT_BG_OPACITY))
                COLOR_INTRO_OVERLAY_BG = s.get('intro_overlay_bg_color', COLOR_INTRO_OVERLAY_BG)
                VAL_INTRO_OVERLAY_OPACITY = float(s.get('intro_overlay_opacity', VAL_INTRO_OVERLAY_OPACITY))
                COLOR_OUTRO_OVERLAY_BG = s.get('outro_overlay_bg_color', COLOR_OUTRO_OVERLAY_BG)
                VAL_OUTRO_OVERLAY_OPACITY = float(s.get('outro_overlay_opacity', VAL_OUTRO_OVERLAY_OPACITY))
                COLOR_BLUEBAR = s.get('bluebar_color', COLOR_BLUEBAR)
                COLOR_RANK_BG = s.get('rank_bg_color', COLOR_RANK_BG)
                COLOR_LOGO_TEXT = s.get('logo_text_color', COLOR_LOGO_TEXT)
                COLOR_LOGO_BG = s.get('logo_bg_color', COLOR_LOGO_BG)
                VAL_LOGO_BG_OPACITY = float(s.get('logo_bg_opacity', VAL_LOGO_BG_OPACITY))
                COLOR_LINK_CHECK_TEXT = s.get('link_check_text_color', COLOR_LINK_CHECK_TEXT)
                COLOR_LINK_CHECK_BG = s.get('link_check_bg_color', COLOR_LINK_CHECK_BG)
                INTRO_FONT = s.get('intro_font', INTRO_FONT)
                OUTRO_FONT = s.get('outro_font', OUTRO_FONT)

                # Thumbnail Style Loading
                COLOR_THUMB_TEXT_TOP = s.get('thumb_text_top', COLOR_THUMB_TEXT_TOP)
                COLOR_THUMB_TEXT_BOT = s.get('thumb_text_bot', COLOR_THUMB_TEXT_BOT)
                COLOR_THUMB_TEXT_BG = s.get('thumb_text_bg_color', COLOR_THUMB_TEXT_BG)
                VAL_THUMB_TEXT_BG_OPACITY = float(s.get('thumb_text_bg_opacity', VAL_THUMB_TEXT_BG_OPACITY))
                THUMB_FONT = s.get('thumb_font', THUMB_FONT)

                # Thumbnail Overlay & Glow Loading
                COLOR_THUMB_OVERLAY = s.get('thumb_overlay_color', COLOR_THUMB_OVERLAY)
                VAL_THUMB_OVERLAY_OPACITY = float(s.get('thumb_overlay_opacity', VAL_THUMB_OVERLAY_OPACITY))
                COLOR_THUMB_GLOW = s.get('thumb_glow_color', COLOR_THUMB_GLOW)
                VAL_THUMB_GLOW_RADIUS = float(s.get('thumb_glow_radius', VAL_THUMB_GLOW_RADIUS))
                VAL_THUMB_GLOW_OPACITY = float(s.get('thumb_glow_opacity', VAL_THUMB_GLOW_OPACITY))
                REMBG_MODEL = s.get('rembg_model', REMBG_MODEL)
                SHORTS_MODE = s.get('shorts_mode', False)
                CONTENT_MODE = s.get('content_mode', CONTENT_MODE)
                HANDS_ON_NOTES = str(s.get('hands_on_notes', '')).strip()
                try:
                    VIDEO_SPEED = max(0.75, min(1.5, float(s.get('video_speed', VIDEO_SPEED))))
                except (TypeError, ValueError):
                    VIDEO_SPEED = 1.0
                
                at = s.get('active_transitions', [])
                if at and isinstance(at, list):
                    ACTIVE_TRANSITIONS = [
                        transition for transition in at
                        if transition in SAFE_TRANSITIONS
                    ] or ["fade"]
                
                print(f"[SYSTEM] Settings loaded successfully from {settings_path}")
                print(f"[DEBUG] Thumb Style - Overlay: {COLOR_THUMB_OVERLAY}, Glow: {COLOR_THUMB_GLOW}, Font: {THUMB_FONT}")
                print(f"[DEBUG] Intro Title Color: {COLOR_INTRO_TITLE}, Font: {INTRO_FONT}")
                print(f"[DEBUG] Intro Overlay Color: {COLOR_INTRO_OVERLAY_BG}, Box BG: {COLOR_INTRO_BG}")
        except Exception as e:
            print(f"Error loading settings.json: {e}")

# Call immediately
load_settings_from_external()

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
]

_ASSET_HOST_SUFFIXES = (
    ".amazon.com",
    ".media-amazon.com",
    ".ssl-images-amazon.com",
    ".amazonvideo.com",
    ".cloudfront.net",
)


def is_allowed_asset_url(raw_url):
    try:
        parsed = urlparse(raw_url)
        host = (parsed.hostname or "").lower()
        return parsed.scheme == "https" and any(
            host == suffix[1:] or host.endswith(suffix)
            for suffix in _ASSET_HOST_SUFFIXES
        )
    except Exception:
        return False

import shutil

# --- FFmpeg Helpers ---

import shutil
import glob
import textwrap

def setup_font(font_type="regular", bold=None):
    """Ensures font is available in app_files. font_type can be 'intro', 'outro', 'rank', 'regular', 'bold'"""
    global INTRO_FONT, OUTRO_FONT
    script_dir = os.path.dirname(os.path.abspath(__file__))

    # Backward compatibility for bold parameter
    if bold is not None:
        font_type = "bold" if bold else "regular"
    
    # Check custom fonts first (only for intro/outro)
    if font_type == "intro" and INTRO_FONT:
        f_name = str(INTRO_FONT).strip()
        font_path = os.path.join(script_dir, f_name)
        if os.path.exists(font_path):
            return font_path
    elif font_type == "outro" and OUTRO_FONT:
        f_name = str(OUTRO_FONT).strip()
        font_path = os.path.join(script_dir, f_name)
        if os.path.exists(font_path):
            return font_path
    
    # Default fonts
    if font_type in ["intro", "outro", "rank", "bold"]:
        font_name = "Roboto-Bold.ttf"
    else:
        font_name = "Roboto-Regular.ttf"
    
    script_dir = os.path.dirname(os.path.abspath(__file__))
    font_target_path = os.path.join(script_dir, font_name)
    
    if not os.path.exists(font_target_path):
        # Roboto is usually not a system font on Windows, so we'll try to find it 
        # but fallback to Arial if not found
        source_font = "Roboto-Bold.ttf" if font_type in ["intro", "outro", "rank", "bold"] else "Roboto-Regular.ttf"
        fallback_font = "arialbd.ttf" if font_type in ["intro", "outro", "rank", "bold"] else "arial.ttf"
        
        # Try local first, then user fonts, then system fonts
        user_fonts = os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Microsoft', 'Windows', 'Fonts')
        system_fonts = os.path.join(os.environ.get('WINDIR', 'C:\\Windows'), 'Fonts')
        
        possible_paths = [
            os.path.join(user_fonts, source_font),
            os.path.join(system_fonts, source_font),
            os.path.join(system_fonts, fallback_font)
        ]
        for p in possible_paths:
            if os.path.exists(p):
                try:
                    shutil.copy(p, font_target_path)
                    break
                except: pass
    
    return font_target_path if os.path.exists(font_target_path) else "arial"



# Resolve ffmpeg/ffprobe robustly. A bare "ffmpeg" call depends on PATH, which
# is frequently missing Homebrew's /opt/homebrew/bin when the app is launched
# from Finder (double-clicking a .command file) rather than an interactive
# shell. shutil.which() checks PATH first, then we fall back to the common
# Homebrew (Apple Silicon) and Intel install locations before giving up.
FFMPEG_BIN = resolve_binary("ffmpeg")
FFPROBE_BIN = resolve_binary("ffprobe")
AUDIO_SAMPLE_RATE = 48000
# Bumped to 3: the cache key now comes from tts_engine.cache_key(), which
# includes the *effective* voice/model/director settings instead of only the
# per-call `voice` override (almost always None) -- v6's key hashed `voice or
# ""`, so changing EDGE_VOICE/KOKORO_VOICE/GEMINI_TTS_VOICE in settings never
# busted the cache and a re-render would replay audio in the OLD voice.
TTS_CACHE_VERSION = 3


# (path, size, mtime_ns) -> duration. Probing is a process spawn, and the
# same file gets probed repeatedly -- slide_duration() alone runs once during
# timeline planning and again in the renderer for every slide, and the audio
# sanity checks probe each part again. On Windows every spawn also pays for
# Defender scanning the ~100 MB bundled ffprobe.exe, so the repeats were a
# real chunk of the wall clock. Keyed on size+mtime so a rewritten file
# (normalization pass, retry) re-probes instead of returning a stale value.
_DURATION_CACHE = {}
_DURATION_CACHE_LOCK = threading.Lock()


def get_audio_duration(file_path):
    """Get duration of audio file using ffprobe."""
    if not os.path.exists(file_path):
        return 0
    try:
        stat = os.stat(file_path)
        cache_key = (str(file_path), stat.st_size, stat.st_mtime_ns)
    except OSError:
        cache_key = None
    if cache_key is not None:
        with _DURATION_CACHE_LOCK:
            if cache_key in _DURATION_CACHE:
                return _DURATION_CACHE[cache_key]
    cmd = [
        FFPROBE_BIN, "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", file_path
    ]
    try:
        result = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=20,
            **quiet_subprocess_kwargs(),
        )
        duration = float(result.stdout.strip())
        if cache_key is not None:
            with _DURATION_CACHE_LOCK:
                _DURATION_CACHE[cache_key] = duration
        return duration
    except (subprocess.SubprocessError, ValueError, OSError) as e:
        # Was 3.0 (a fake non-zero duration). That let a genuinely broken/
        # unreadable audio file sail through duration-based sanity checks
        # (_audio_is_sane) as if it were a real ~3s clip. 0.0 makes a probe
        # failure visibly fail those checks instead of silently passing.
        # A hung ffprobe (corrupt container, network-mounted file) used to
        # block this render worker forever with no timeout at all.
        print(f"[WARN] ffprobe duration probe failed for {file_path}: {e}")
        return 0.0


def slide_duration(audio_path, is_rank=False, is_outro_single=False):
    """Single source of truth for how long a text-slide (intro/outro/rank)
    should play, given its narration's audio duration. Both the timeline
    planner (which decides where branding overlays turn on/off) and the
    actual renderer (create_text_slide_ffmpeg) must use this SAME formula --
    previously the multi-ASIN outro timeline estimate and the single-ASIN
    outro timeline estimate each used their own different constants that
    didn't match what the renderer actually produced, so branding overlay
    timing drifted on longer videos. Intro and rank were already consistent;
    outro was the one that diverged."""
    audio_dur = get_audio_duration(audio_path)
    if is_outro_single:
        return min(max(audio_dur + 0.3, 4.0), 5.0)
    extra_time = 0.8 if is_rank else 1.0
    min_dur = 1.8 if is_rank else 2.2
    return max(audio_dur + extra_time, min_dur)


def apply_video_speed(input_path, duration, base_dir, speed=None):
    """Whole-video speed pass: picture and narration together, one setpts +
    atempo stage after final assembly and before the music mix (so the music
    duration and every downstream QC check use the POST-speed duration).

    ffmpeg's atempo filter accepts 0.5-2.0 in a single instance; VIDEO_SPEED
    is clamped to 0.75-1.5 (see load_settings_from_external), so one stage
    always covers the whole configured range -- no chaining needed.
    """
    speed = VIDEO_SPEED if speed is None else speed
    if abs(speed - 1.0) < 1e-6:
        return input_path, duration
    print(f"Adjusting playback speed to {speed:.2f}x...")
    output_path = os.path.join(base_dir, "video_speed_adjusted.mp4")
    new_duration = duration / speed
    run_ffmpeg([
        "ffmpeg", "-y", "-i", input_path,
        "-filter_complex",
        f"[0:v]setpts=PTS/{speed}[v];[0:a]atempo={speed},aresample={AUDIO_SAMPLE_RATE}:async=1:first_pts=0[a]",
        "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-ar", str(AUDIO_SAMPLE_RATE), "-ac", "2",
        "-t", str(new_duration),
        "-movflags", "+faststart",
        output_path,
    ])
    if os.path.exists(input_path):
        try: os.remove(input_path)
        except OSError: pass
    return output_path, new_duration


def _caption_points_for_overlay(points, max_points=4, max_chars=52):
    """Trim key points to what fits on ONE line of the caption bar.

    The bar sits under the product title and is deliberately a single line
    -- a wrapping caption would grow into the footage and reintroduce
    exactly the screen-filling problem the burned-in SRT had.
    """
    out = []
    for raw in points or []:
        point = re.sub(r"\s+", " ", str(raw or "")).strip(" .;:-•")
        if not point:
            continue
        if len(point) > max_chars:
            point = point[:max_chars].rsplit(" ", 1)[0].strip()
        if point:
            out.append(point)
        if len(out) >= max_points:
            break
    return out


def _typewriter_reveal_steps(text, max_steps=26):
    """Cumulative prefixes of `text`, for a keyboard-style reveal.

    Capped rather than one-per-character: each step becomes its own
    drawtext filter, and an unbounded count would put hundreds of filters
    into a single filtergraph for no visible benefit.
    """
    text = str(text or "")
    if not text:
        return []
    steps = min(len(text), max_steps)
    per_step = max(1, math.ceil(len(text) / steps))
    prefixes = []
    cursor = per_step
    while cursor < len(text):
        prefixes.append(text[:cursor])
        cursor += per_step
    prefixes.append(text)
    return prefixes


def product_caption_points(product, narration_text="", max_points=5, max_chars=70):
    """On-screen caption text for a product segment: its key feature points.

    Burning the whole narration in duplicated everything the viewer was
    already hearing and filled the frame with text. Short spec points are
    what captions are actually good for -- they carry the numbers and
    features a viewer wants to read and screenshot, and they don't compete
    with the voice track.

    Falls back to the narration when a product has no usable feature
    bullets (a scrape-only product often won't), since some caption is
    better than a silent gap in the caption file.
    """
    points = []
    for raw in (product or {}).get("features") or []:
        point = re.sub(r"\s+", " ", str(raw or "")).strip(" .;:-•")
        if not point:
            continue
        # Marketing bullets run long; keep the front of the line, which is
        # where the actual spec almost always is.
        if len(point) > max_chars:
            point = point[:max_chars].rsplit(" ", 1)[0].strip() + "..."
        if not point.endswith((".", "!", "?", "...")):
            point += "."
        points.append(point)
        if len(points) >= max_points:
            break
    return " ".join(points) if points else narration_text


def write_captions_srt(base_dir, timeline_segments, speed):
    """Sidecar .srt for the finished video (also the source for burn-in).

    Timestamps are divided by the video-speed factor for the same reason
    the description chapter list is: they must describe the video as it
    actually plays, not the pre-speed timeline.
    """
    try:
        entries = [
            {
                "start": seg["start"] / speed,
                "duration": seg["dur"] / speed,
                "text": seg.get("caption_text", ""),
            }
            for seg in timeline_segments
        ]
        srt_text = build_srt(entries)
        if not srt_text.strip():
            return None
        srt_path = os.path.join(base_dir, "captions.srt")
        with open(srt_path, "w", encoding="utf-8") as handle:
            handle.write(srt_text)
        return srt_path
    except Exception as exc:
        print(f"[WARN] Caption generation failed: {exc}")
        return None


def measure_mean_dbfs(path):
    """Average level of an audio file in dBFS, or None if it can't be read.

    Used to place any music bed at a known level with a STATIC gain. A
    dynamic normalizer would do this too, but a previous fix established
    that dynamic loudness processing in this mix pumps audibly -- measuring
    once up front and applying a fixed gain gets the same level with no
    time-varying processing at all.
    """
    try:
        result = subprocess.run(
            [FFMPEG_BIN, "-hide_banner", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
            capture_output=True, text=True, timeout=60, **quiet_subprocess_kwargs(),
        )
    except (subprocess.SubprocessError, OSError) as exc:
        print(f"[MUSIC][WARN] Could not measure track level: {exc}")
        return None
    match = re.search(r"mean_volume:\s*(-?\d+(?:\.\d+)?)\s*dB", result.stderr or "")
    return float(match.group(1)) if match else None


def music_bed_gain_db(path, target_dbfs=None):
    """How much to attenuate/boost `path` so it sits at the bed target.

    The old mix applied a blind `volume=0.06` (-24 dB) to every track. That
    assumed all sources arrive at similar levels, which was wrong in both
    directions: the bundled beds average about -41 dBFS, so -24 dB on top
    put them near -65 dB -- mixed in but far below audibility, which is why
    background music seemed not to work at all. A commercially mastered
    custom upload would have had the opposite problem and buried the voice.
    """
    target = MUSIC_BED_TARGET_DBFS if target_dbfs is None else target_dbfs
    measured = measure_mean_dbfs(path)
    if measured is None:
        # Unmeasurable: fall back to a conservative fixed attenuation rather
        # than risking a full-volume track over the narration.
        return -24.0
    # Clamped so a pathological measurement (near-silent or clipped source)
    # can't produce an absurd boost or a total mute.
    return max(-40.0, min(12.0, target - measured))


def generate_ai_music(keyword, run_settings):
    """Text-to-music background bed via Vertex AI's Lyria-002, using the
    same service-account credentials already configured for Vertex TTS/LLM.

    music_mode == "ai_generated" is opt-in (default stays "nature", which is
    free and local) -- every call here is a real, billed Vertex API request,
    so this only fires when the user has explicitly chosen it in Settings.

    Cached per (project, keyword) in DATA_DIR/ai_music_cache: retrying a
    failed render, or regenerating the same keyword later, shouldn't pay for
    a fresh generation when the last one is still sitting on disk.
    """
    import vertex_auth

    service_account_json = run_settings.get("vertex_service_account_private_key")
    project_id = run_settings.get("vertex_project_id")
    location = run_settings.get("vertex_location") or "us-central1"
    if not service_account_json or not project_id:
        raise RuntimeError("Vertex AI project/credentials are not configured")

    cache_dir = DATA_DIR / "ai_music_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_key = hashlib.sha256(f"{project_id}|lyria-002|{keyword}".encode()).hexdigest()[:24]
    cache_path = cache_dir / f"{cache_key}.wav"
    if cache_path.is_file() and cache_path.stat().st_size > 0:
        print(f"[MUSIC][AI] Using cached Lyria track for '{keyword}'")
        return str(cache_path)

    prompt = (
        f"Calm, pleasant, unobtrusive instrumental background ambience for a "
        f"'{keyword}' product review video. Soft, airy, subtle texture. "
        f"No vocals, no melody hook, no drums, nothing distracting -- "
        f"background bed only, safe to talk over."
    )
    token = vertex_auth.get_access_token(service_account_json)
    url = (
        f"https://{location}-aiplatform.googleapis.com/v1/projects/{project_id}"
        f"/locations/{location}/publishers/google/models/lyria-002:predict"
    )
    resp = requests.post(
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"instances": [{"prompt": prompt}], "parameters": {"sample_count": 1}},
        timeout=60,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Lyria request failed: {resp.text[:300]}")
    prediction = resp.json()["predictions"][0]
    raw_wav = base64.b64decode(prediction["bytesBase64Encoded"])
    cache_path.write_bytes(raw_wav)
    return str(cache_path)


def build_music_mix_filter(duration, music_gain_db=None):
    """Build a deterministic voice-first mix on one continuous 48 kHz clock.

    `music_gain_db` is the per-track static gain from music_bed_gain_db();
    the sidechain below still ducks the bed whenever narration is present.
    """
    duration_text = str(float(duration))
    gain = MUSIC_BED_FALLBACK_GAIN_DB if music_gain_db is None else float(music_gain_db)
    return (
        f"[0:a]aresample={AUDIO_SAMPLE_RATE}:async=1:first_pts=0,"
        f"apad=whole_dur={duration_text},atrim=duration={duration_text}[voice];"
        f"[1:a]aresample={AUDIO_SAMPLE_RATE}:async=1:first_pts=0,"
        f"volume={gain:.2f}dB[bgm];"
        "[bgm][voice]sidechaincompress=threshold=0.015:ratio=10:"
        "attack=15:release=450[ducked];"
        "[voice][ducked]amix=inputs=2:duration=first:"
        "dropout_transition=0:normalize=0,"
        "alimiter=limit=0.95:attack=5:release=100:latency=1,"
        f"apad=whole_dur={duration_text},atrim=duration={duration_text}[a]"
    )


def format_timestamp(seconds):
    """Formats seconds into M:SS or H:MM:SS."""
    seconds = int(seconds)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours > 0:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"

# A stalled encode (corrupt input, a hung filter) used to hang a render
# worker forever -- there was no watchdog anywhere in the pipeline. 15 minutes
# comfortably covers even a long multi-product render on modest hardware
# while still eventually freeing the worker. Override via env for slower
# machines instead of raising it here for everyone.
FFMPEG_TIMEOUT_SECONDS = int(os.environ.get("AMZFLOW_FFMPEG_TIMEOUT", "900"))


def run_ffmpeg(cmd_args):
    """Run an ffmpeg command."""
    # print("Running:", " ".join(cmd_args))
    # Every call site in this file builds the command as ["ffmpeg", ...]; swap
    # in the resolved absolute path so it works regardless of the launching
    # process's PATH (see FFMPEG_BIN above).
    if cmd_args and cmd_args[0] == "ffmpeg":
        cmd_args = [FFMPEG_BIN] + cmd_args[1:]
    try:
        # Use errors='replace' and handle potential encoding issues on Windows
        subprocess.run(
            cmd_args, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding='utf-8', errors='replace', timeout=FFMPEG_TIMEOUT_SECONDS,
            **quiet_subprocess_kwargs(),
        )
    except subprocess.CalledProcessError as e:
        print(f"FFmpeg Error: {e.stderr}")
        raise e
    except subprocess.TimeoutExpired as e:
        print(f"FFmpeg Error: command timed out after {FFMPEG_TIMEOUT_SECONDS}s: {' '.join(cmd_args[:4])}...")
        raise e

def apply_seo_metadata(file_path, keyword, tags_str, title_str):
    """Applies SEO metadata to a file (video or image) for Windows Properties."""
    if not os.path.exists(file_path):
        return
    
    # Process tags: keyword + 5-6 relevant tags
    tags_list = [t.strip() for t in tags_str.split(',') if t.strip()]
    keyword_tags = [keyword] + tags_list[:6]
    # Windows Explorer prefers semicolon-separated tags for the "Tags" field
    final_tags = "; ".join(keyword_tags)

    if file_path.lower().endswith('.mp4'):
        # MP4 Metadata mapping for Windows properties using FFmpeg
        # Title -> title
        # Subtitle -> subject
        # Rating -> rating (100 = 5 stars)
        # Tags -> genre / tags
        # Comments -> comment
        temp_output = file_path + "_meta.mp4"
        cmd = [
            "ffmpeg", "-y", "-i", file_path,
            "-metadata", f"title={keyword}",         # Windows: Title
            "-metadata", f"subject={keyword}",       # Windows: Subtitle
            "-metadata", f"description={keyword}",   # Windows: Subtitle fallback
            "-metadata", f"comment={title_str}",     # Windows: Comments
            "-metadata", f"genre={final_tags}",      # Windows: Tags
            "-metadata", f"tags={final_tags}",       # Windows: Tags fallback
            "-metadata", "rating=100",               # Windows: Rating (5 stars)
            "-c", "copy",
            temp_output
        ]
        try:
            run_ffmpeg(cmd)
            if os.path.exists(temp_output):
                time.sleep(1) # Wait for file lock release
                os.replace(temp_output, file_path)
                print(f"[SEO] Applied video metadata to {os.path.basename(file_path)}")
        except Exception as e:
            print(f"[SEO] Video Metadata Error: {e}")
            if os.path.exists(temp_output):
                try: os.remove(temp_output)
                except: pass
                
    elif file_path.lower().endswith(('.jpg', '.jpeg')):
        # JPEG Metadata for Windows Properties using PIL (Exif XP Tags)
        try:
            # We use a temporary path because PIL can't save to the same file while open
            temp_img_path = file_path + "_temp.jpg"
            with Image.open(file_path) as img:
                exif = img.getexif()
                # Windows-specific EXIF tags (XPTitle, XPSubject, XPKeywords, XPComment)
                # These must be encoded as UTF-16LE.
                exif[0x9c9b] = keyword.encode('utf-16le')    # XPTitle -> Title
                exif[0x9c9f] = keyword.encode('utf-16le')    # XPSubject -> Subject/Subtitle
                exif[0x9c9e] = final_tags.encode('utf-16le') # XPKeywords -> Tags
                exif[0x9c9c] = title_str.encode('utf-16le')  # XPComment -> Comments
                exif[0x4746] = 5                             # Rating -> 5 Stars
                
                # Save with metadata
                img.save(temp_img_path, exif=exif, quality=95, subsampling=0)
            
            time.sleep(0.5)
            os.replace(temp_img_path, file_path)
            print(f"[SEO] Applied thumbnail metadata to {os.path.basename(file_path)}")
        except Exception as e:
            print(f"[SEO] Thumbnail Metadata Error: {e}")
            if os.path.exists(temp_img_path):
                try: os.remove(temp_img_path)
                except: pass

def sanitize_text(text):
    """Sanitize text for FFmpeg drawtext."""
    if not text: return ""
    # Exotic dash/hyphen codepoints (en/em dash, non-breaking hyphen, minus
    # sign, etc. -- common in scraped URL slugs and copy-pasted titles) have
    # no glyph in Roboto and render as a tofu box on screen. Normalize to a
    # plain ASCII hyphen before anything else touches the text.
    text = re.sub(r"[‐-―−]", "-", str(text))
    text = re.sub(r"-{2,}", "-", text)
    # Remove chars that crash FFmpeg drawtext or file paths
    # We remove ' and : and replace \ with space
    text = text.replace("'", "").replace('"', '').replace(":", " ").replace("\\", " ")
    # Escape % for FFmpeg
    text = text.replace("%", "%%")
    # Escape for filter strings (double escaping for backslashes if any remain)
    text = text.replace(":", "\\:").replace("'", "")
    return text.strip()

def escape_path(path):
    """Escape path for FFmpeg filter values (like fontfile) on Windows."""
    if not path: return ""
    # Ensure absolute path with forward slashes
    path = os.path.abspath(path).replace('\\', '/')
    # FFmpeg drawtext on Windows needs colon escaped
    path = path.replace(':', '\\:')
    # Escape single quotes
    path = path.replace("'", "\\'")
    return path

def title_case(text):
    """Capitalize words except for common prepositions and conjunctions."""
    exceptions = ["of", "in", "and", "for", "with", "without", "at", "on", "to", "by", "a", "an", "the", "from"]
    words = text.split()
    result = []
    for i, word in enumerate(words):
        # Always capitalize the first word, otherwise check exceptions
        if i == 0 or word.lower() not in exceptions:
            result.append(word.capitalize())
        else:
            result.append(word.lower())
    return " ".join(result)

def wrap_lines_for_overlay(text, width, max_lines):
    lines = textwrap.wrap(str(text or "").strip(), width=width)
    if len(lines) <= max_lines:
        return lines
    clipped = lines[:max_lines]
    clipped[-1] = textwrap.shorten(
        " ".join(lines[max_lines - 1:]),
        width=width,
        placeholder="...",
    )
    return clipped

def fit_and_pad_filter(w=None, h=None):
    if w is None or h is None:
        w, h = output_resolution()
    return f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=white,setsar=1"

def _dedupe_products_by_title(processed, keyword=""):
    """Drop products whose scraped title exactly matches an earlier one.

    Second dedup layer, by scraped title rather than input ASIN: Amazon
    often resolves several ASINs (colour/size variants) to the SAME
    underlying listing, which input-level ASIN dedup cannot catch since the
    ASINs genuinely differ. Two products that scraped to the literal same
    title are the same product appearing twice in the finished video.
    """
    seen_titles = set()
    deduped = []
    for prod in processed:
        key = re.sub(r"\s+", " ", str(prod.get("title") or "")).strip().lower()
        if key and key in seen_titles:
            print(
                f"[FIX] '{keyword}': dropping ASIN {prod.get('asin', '?')} -- same "
                f"product as an already-included ASIN (title: {prod.get('title', '')[:60]!r})."
            )
            continue
        if key:
            seen_titles.add(key)
        deduped.append(prod)
    return deduped


def _pick_intro_hook_video(processed):
    """Footage for the intro's post-thumbnail cut.

    Deliberately skips the FIRST product: the intro is immediately followed
    by product #1's own segment, so hooking with #1's clip showed the same
    footage twice in a row. Any later product's video is used instead.

    Product #1 is only used as a last resort -- if it owns the single
    available clip, reusing it still beats what happened before, which was
    the intro sitting on one frozen thumbnail for its whole duration.
    """
    def usable(entry):
        path = (entry or {}).get('video')
        return path and os.path.exists(path)

    for entry in (processed or [])[1:]:
        if usable(entry):
            return entry['video']
    if processed and usable(processed[0]):
        print("[INTRO] Only the first product has footage; reusing it for the intro hook.")
        return processed[0]['video']
    return None


def create_text_slide_ffmpeg(text, audio_path, output_path, bg_path=None, is_intro=False, is_rank=False, branding_filters=None, is_outro_single=False, draw_text=True, hook_video_path=None):
    """Creates a text slide using FFmpeg with image or video background."""
    # Duration formula lives in slide_duration() so the timeline planner and
    # this renderer never disagree (see slide_duration's docstring).
    duration = slide_duration(audio_path, is_rank=is_rank, is_outro_single=is_outro_single)
    
    clean_text = sanitize_text(text)
    if is_intro:
        clean_text = clean_text.upper()
    
    # Wrap text for non-rank slides (Intro/Outro) to prevent overflow
    if not is_rank:
        # Wrap for aspect ratio: tighter for 9:16 Shorts, slightly more for 16:9 Normal
        # Reduce wrap width to avoid overflowing screen edges
        wrap_w = 25 if SHORTS_MODE else 45
        # NEW: Handle multi-line headers without speech
        # If the text is a known header, we ensure it looks clean
        is_h = any(h.lower() in text.lower() for h in ["Key Features", "Performance", "Pros & Cons", "Final Verdict"])
        if is_h:
             clean_text = text.strip().upper()
             wrap_w = 35 # Force tighter wrap for headers to look more like a title
        
        wrapped_lines = textwrap.wrap(clean_text, width=wrap_w) or [""]
        # A literal newline embedded in one drawtext `text=` value renders as
        # a visible tofu box on this ffmpeg build (libharfbuzz shapes the
        # line-feed control character into a .notdef glyph before the line
        # break is applied) -- every wrapped intro/outro slide showed a box
        # glyph at each line break. Render one drawtext filter per line
        # instead, stacked by y offset, so no drawtext ever sees a newline.
        clean_text = wrapped_lines[0]

    # Determine font type
    if is_rank:
        font_type = "rank"
    elif is_intro:
        font_type = "intro"
    else:
        font_type = "outro"
    
    font_raw_path = setup_font(font_type)
    font_path = escape_path(font_raw_path)
    
    inputs = []
    bg_input_count = 0

    # Intro hook: a short (1-4s) look at the still background image, then a
    # cut to the first product's real footage for the rest of the slide,
    # instead of one static image for the whole intro. Falls back to the
    # plain single-image path below on any problem building this chain.
    hook_chain_ok = False
    if is_intro and hook_video_path and os.path.exists(hook_video_path) and bg_path and os.path.exists(bg_path) and \
            not bg_path.lower().endswith(('.mp4', '.mov', '.avi', '.mkv')):
        try:
            # Thumbnail hook stays short (1-3s) so the real footage gets the
            # bulk of the intro. At 0.35 of a ~17s intro this used to clamp
            # to a full 4s of frozen image before anything moved.
            hook_dur = min(3.0, max(1.0, duration * 0.2))
            video_dur = max(0.5, duration - hook_dur)
            inputs.extend(["-loop", "1", "-t", f"{hook_dur:.3f}", "-i", bg_path])
            inputs.extend(["-stream_loop", "-1", "-i", hook_video_path])
            bg_input_count = 2
            filter_base = (
                f"[0:v]{fit_and_pad_filter()},trim=0:{hook_dur:.3f},setpts=PTS-STARTPTS[introhook];"
                f"[1:v]{fit_and_pad_filter()},trim=0:{video_dur:.3f},setpts=PTS-STARTPTS[introvid];"
                f"[introhook][introvid]concat=n=2:v=1:a=0"
            )
            hook_chain_ok = True
        except Exception as e:
            print(f"[WARN] Intro hook chain skipped, falling back to still image: {e}")
            inputs = []
            bg_input_count = 0

    if not hook_chain_ok and bg_path and os.path.exists(bg_path):
        if bg_path.lower().endswith(('.mp4', '.mov', '.avi', '.mkv')):
            inputs.extend(["-stream_loop", "-1", "-i", bg_path])
            filter_base = f"[0:v]{fit_and_pad_filter()}"
        else:
            inputs.extend(["-loop", "1", "-i", bg_path])
            filter_base = f"[0:v]{fit_and_pad_filter()}"
        bg_input_count = 1

    if bg_input_count:
        # Apply the main background overlay (dimming effect) using global settings
        # This uses Intro or Outro specific settings
        # This is the FULL SCREEN darken/overlay, not the text box background.
        if is_intro:
            main_ov_color = COLOR_INTRO_OVERLAY_BG if str(COLOR_INTRO_OVERLAY_BG).startswith('#') else "black"
            main_ov_opacity = VAL_INTRO_OVERLAY_OPACITY
        else:
            main_ov_color = COLOR_OUTRO_OVERLAY_BG if str(COLOR_OUTRO_OVERLAY_BG).startswith('#') else "black"
            main_ov_opacity = VAL_OUTRO_OVERLAY_OPACITY

        # Rank/number transition screens keep the background image clean --
        # only intro/outro title slides get the full-screen dimming scrim.
        if is_rank:
            filter_chain = filter_base
        else:
            filter_chain = f"{filter_base},drawbox=t=fill:c={main_ov_color}@{main_ov_opacity}"
    else:
        # Generate solid color background if no background asset exists
        # In this case, we use the respective Title BG color as the whole background
        if is_intro:
            bg_solid_color = COLOR_INTRO_BG
        else:
            bg_solid_color = COLOR_OUTRO_BG

        if str(bg_solid_color).startswith('#'):
            bg_solid_color = bg_solid_color.replace('#', '0x')
        color_s = "1080x1920" if SHORTS_MODE else "1920x1080"
        inputs.extend(["-f", "lavfi", "-i", f"color=c={bg_solid_color}:s={color_s}:r=25:d={duration}"])
        bg_input_count = 1
        filter_chain = "[0:v]null"


    # Audio input -- index follows however many background inputs were used.
    if audio_path and os.path.exists(audio_path):
        inputs.extend(["-i", audio_path])
        audio_map_args = ["-map", f"{bg_input_count}:a"]
    else:
        # Silent audio
        inputs.extend([
            "-f", "lavfi", "-i",
            f"anullsrc=channel_layout=stereo:sample_rate={AUDIO_SAMPLE_RATE}:d={duration}",
        ])
        audio_map_args = ["-map", f"{bg_input_count}:a"]

    # Draw text
    typing_windows = []
    if is_rank:
        # Using a very large filled circle character and forcing it to be solid
        circle_size = round(750 * TEXT_SCALE)
        number_size = round(280 * TEXT_SCALE)
        circle_y_offset = round(15 * TEXT_SCALE)
        circle_draw = (
            f"drawtext=fontfile='{font_path}':text='●':fontcolor={COLOR_RANK_BG}:"
            f"fontsize={circle_size}:x=(w-text_w)/2:y=(h-text_h)/2-{circle_y_offset}:enable='gt(t,0.3)'"
        )
        number_draw = (
            f"drawtext=fontfile='{font_path}':text='{clean_text}':fontcolor=black:"
            f"fontsize={number_size}:x=(w-text_w)/2:y=(h-text_h)/2:enable='gt(t,0.3)'"
        )
        drawtext = f"{circle_draw},{number_draw}"
    else:
        # Helper to fix color for FFmpeg - 0xRRGGBB format is most reliable
        def fix_color_ffmpeg(c):
            if isinstance(c, str) and c.startswith('#'):
                return "0x" + c[1:]
            return c

        # Determine colors based on slide type
        if is_intro:
            text_color = fix_color_ffmpeg(COLOR_INTRO_TITLE)
            box_color = fix_color_ffmpeg(COLOR_INTRO_BG)
            opacity = VAL_INTRO_BG_OPACITY
            use_box = 1 if ENABLE_INTRO_BG else 0
            print(f"[DEBUG] Intro Slide: Font={INTRO_FONT}, TextColor={COLOR_INTRO_TITLE}, BoxColor={COLOR_INTRO_BG}, Opacity={opacity}, UseBox={use_box}")
        else:
            # Using Outro specific settings
            text_color = fix_color_ffmpeg(COLOR_OUTRO_TITLE)
            box_color = fix_color_ffmpeg(COLOR_OUTRO_BG)
            opacity = VAL_OUTRO_BG_OPACITY
            use_box = 1 if ENABLE_OUTRO_BG else 0
            print(f"[DEBUG] Outro Slide: Font={OUTRO_FONT}, TextColor={COLOR_OUTRO_TITLE}, BoxColor={COLOR_OUTRO_BG}, Opacity={opacity}, UseBox={use_box}")

        # Visibility fallback for empty background
        if not bg_path and (str(text_color).lower() in ["white", "0xffffff"]):
            text_color = "black"
        
        # Determine font size for intro/outro text
        # If text is long, shrink font size to prevent overflow
        text_len_for_sizing = sum(len(l) for l in wrapped_lines) if not is_rank else len(clean_text)
        # Font sizes below are tuned for the legacy 720x1280/1280x720 canvas;
        # scale them up with the output resolution (now 1080x1920/1920x1080)
        # so text stays the same relative size but renders sharp instead of
        # being upscaled from a smaller source.
        if SHORTS_MODE:
            # Task: Increase font size for BOTH Intro and Outro in SHORTS mode
            f_size = 50
            if not is_intro:
                f_size += 5 # Increase more 5 for outro font size of Shorts mode
            if text_len_for_sizing > 80: f_size -= 10
            if text_len_for_sizing > 120: f_size -= 5
        else:
            # Default logic for Normal mode
            f_size = 50
            if text_len_for_sizing > 80: f_size = 40
            if text_len_for_sizing > 120: f_size = 35
        f_size = round(f_size * TEXT_SCALE)

        # Build drawtext command - fontfile and text use single quotes, colors do not
        # Padding adjustments:
        if SHORTS_MODE:
            box_p_val = 40
        else:
            box_p_val = 70
        box_p_val = round(box_p_val * TEXT_SCALE)
        
        # x='(w-text_w)/2' and y='(h-text_h)/2' are used to center.
        # Fixed potential sub-pixel coordinate jitter by using floor/round with trunc() if needed, 
        # but usually fixed integer mapping is better.
        # Thumbnail-style pop: an accent-colored outline + drop shadow behind
        # the text, reusing the same glow color the thumbnail generator uses
        # (COLOR_THUMB_GLOW), so intro/outro slides read as part of the same
        # visual identity as the thumbnail instead of plain flat text.
        accent_color = fix_color_ffmpeg(COLOR_THUMB_GLOW)
        base_style = (
            f"fontfile='{font_path}':fontcolor={text_color}:fontsize={f_size}:"
            f"fix_bounds=1:text_align=center:box={use_box}:boxcolor={box_color}@{opacity}:"
            f"boxborderw={box_p_val}:borderw=3:bordercolor={accent_color}@0.9:"
            f"shadowx=3:shadowy=3:shadowcolor=black@0.6"
        )
        n_lines = len(wrapped_lines)
        line_gap = round(20 * TEXT_SCALE)
        line_step = f_size + line_gap
        total_block_h = n_lines * f_size + (n_lines - 1) * line_gap
        line_filters = []
        # Sequential typed reveal, one line at a time -- same mechanic as the
        # product caption bar (_typewriter_reveal_steps: cumulative prefixes,
        # each one visible in its own mutually-exclusive time window). A
        # longer AI-generated conclusion used to draw every wrapped line
        # always-on for the whole slide, which is both a wall of text to
        # read in the time given and, per user report, could visually smear
        # into a stacked/overlapping mess. One line revealing (then holding)
        # while the rest stay off keeps exactly one thing on screen at once.
        if n_lines:
            lead_in = 0.4
            tail_hold = max(0.6, duration * 0.08)
            available = max(0.5, duration - lead_in - tail_hold)
            per_line = available / n_lines
            for i, line in enumerate(wrapped_lines):
                y_expr = f"((h-{total_block_h})/2)+{i * line_step}"
                line_start = lead_in + i * per_line
                line_end = duration if i == n_lines - 1 else line_start + per_line
                steps = _typewriter_reveal_steps(line)
                if not steps:
                    continue
                type_span = min(per_line * 0.6, max(0.3, len(line) * 0.035))
                typing_windows.append((line_start, line_start + type_span))
                step_dt = type_span / len(steps)
                for step_index, prefix in enumerate(steps):
                    step_start = line_start + step_index * step_dt
                    is_last = step_index == len(steps) - 1
                    step_end = line_end if is_last else step_start + step_dt
                    line_filters.append(
                        f"drawtext={base_style}:text='{prefix}':x=((w-text_w)/2):y='{y_expr}':"
                        f"enable='between(t,{step_start:.3f},{step_end:.3f})'"
                    )
        drawtext = ",".join(line_filters) if line_filters else "null"

    if not draw_text and not is_rank:
        # The background image (e.g. the styled Thumbnail.jpg reused for the
        # intro) already carries its own baked-in title text -- drawing the
        # slide's title again on top of it would double up the text.
        drawtext = "null"
        typing_windows = []

    filter_complex = f"{filter_chain},{drawtext}"
    if branding_filters:
        filter_complex += "," + ",".join(branding_filters)
    filter_complex += "[v]"

    # Keystroke ticks under the typed reveal above -- same asset/mechanism
    # as the product segment caption bar (keytype_loop.wav, gated to the
    # typing windows only, muted the rest of the time).
    typing_loop = os.path.join(os.path.dirname(__file__), "sfx", "keytype_loop.wav")
    audio_af = (
        f"aresample={AUDIO_SAMPLE_RATE}:async=1:first_pts=0,"
        f"apad=whole_dur={duration},atrim=duration={duration}"
    )
    if typing_windows and os.path.isfile(typing_loop):
        typing_idx = sum(1 for a in inputs if a == "-i")
        inputs = inputs + ["-stream_loop", "-1", "-i", typing_loop]
        inside = "+".join(f"between(t,{s:.3f},{e:.3f})" for s, e in typing_windows)
        filter_complex += (
            f"; [{bg_input_count}:a]{audio_af}[voice_a]"
            f"; [{typing_idx}:a]aresample={AUDIO_SAMPLE_RATE},"
            f"volume=0:enable='eq(0,{inside})',volume={TYPING_SFX_GAIN},"
            f"apad=whole_dur={duration},atrim=duration={duration}[key_a]"
            f"; [voice_a][key_a]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[aout]"
        )
        cmd = ["ffmpeg", "-y"] + inputs + [
            "-filter_complex", filter_complex,
            "-map", "[v]", "-map", "[aout]",
        ]
    else:
        cmd = ["ffmpeg", "-y"] + inputs + [
            "-filter_complex", filter_complex,
            "-map", "[v]"] + audio_map_args + ["-af", audio_af]

    cmd += [
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-ar", str(AUDIO_SAMPLE_RATE), "-ac", "2",
        "-r", "25",
        "-t", str(duration),
        output_path
    ]
    
    try:
        run_ffmpeg(cmd)
        return output_path
    except Exception as e:
        print(f"Error creating slide {text}: {e}")
        return None

def _evenly_spaced_indices(total, count=3):
    """`count` 0-based positions spread across range(total), including both
    ends when possible. Used to pick which product segments show the CTA
    overlay -- every single segment showing it read as unnatural spam;
    2-3 appearances across the whole video is the ask."""
    if total <= 0 or count <= 0:
        return set()
    count = min(count, total)
    if count == 1:
        return {total // 2}
    return {round(i * (total - 1) / (count - 1)) for i in range(count)}


def create_product_segment_ffmpeg(video_path, image_paths, audio_paths, title, output_path, branding_filters=None, header_text=None, tail_pad=0.0, show_cta=True, caption_key_points=None):
    """Creates a product segment (video or slideshow) with audio and title overlay."""
    global COLOR_PRODUCT_TITLE, COLOR_PRODUCT_BG, VAL_PRODUCT_BG_OPACITY, COLOR_BLUEBAR
    global COLOR_LINK_CHECK_TEXT, COLOR_LINK_CHECK_BG
    
    # Determine total audio duration - unique name to avoid conflicts
    concat_audio = os.path.abspath(f"{output_path}_audio.wav").replace("\\", "/")
    
    # 1. Concatenate Audio Files
    # Check for file existence and valid size (> 100 bytes is a safe bet for a minimal MP3)
    valid_audios = [a for a in audio_paths if a and os.path.exists(a) and os.path.getsize(a) > 100]
    
    if not valid_audios:
        # If no valid audio, generate 5s of silence
        duration = 5.0
        try:
            run_ffmpeg([
                "ffmpeg", "-y", "-f", "lavfi", "-i",
                f"anullsrc=channel_layout=stereo:sample_rate={AUDIO_SAMPLE_RATE}",
                "-t", str(duration), "-c:a", "pcm_s16le", concat_audio,
            ])
        except: return None
    else:
        # Decode every independently generated MP3 before joining it. Stream-copy
        # concatenation preserves MP3 encoder delay/padding at every boundary and
        # can sound like repeated micro-stalls later in a long review.
        try:
            audio_inputs = []
            audio_filters = []
            for idx, audio_path in enumerate(valid_audios):
                audio_inputs.extend(["-i", audio_path])
                # A short silence after every beat except the last. Butt-joining
                # separately synthesized beats runs them together with no room
                # to breathe, which is one of the things that reads as
                # machine-generated; a real narrator pauses between thoughts.
                breath = (
                    f",apad=pad_dur={BEAT_BREATH_SECONDS}"
                    if idx < len(valid_audios) - 1 else ""
                )
                audio_filters.append(
                    f"[{idx}:a]aresample={AUDIO_SAMPLE_RATE}:async=1:first_pts=0,"
                    f"aformat=sample_fmts=s16:channel_layouts=stereo{breath}[a{idx}]"
                )
            if len(valid_audios) == 1:
                audio_filters.append("[a0]anull[aout]")
            else:
                joined = "".join(f"[a{idx}]" for idx in range(len(valid_audios)))
                audio_filters.append(
                    f"{joined}concat=n={len(valid_audios)}:v=0:a=1[aout]"
                )
            run_ffmpeg(
                ["ffmpeg", "-y"]
                + audio_inputs
                + [
                    "-filter_complex", ";".join(audio_filters),
                    "-map", "[aout]",
                    "-c:a", "pcm_s16le",
                    "-ar", str(AUDIO_SAMPLE_RATE), "-ac", "2",
                    concat_audio,
                ]
            )
        except Exception:
            return None

        duration = get_audio_duration(concat_audio)

    # tail_pad reserves extra silent time at the end of this segment (used by
    # single-ASIN paragraph segments, which the timeline gives a +1.0s
    # cinematic buffer). Previously the timeline predicted audio+1.0 but the
    # renderer only ever produced `audio` seconds, so branding overlay
    # enable-windows drifted for everything after a mismatched segment.
    duration += tail_pad

    font_path = escape_path(setup_font())
    clean_title = sanitize_text(title) if title else ""
    
    # 2. Prepare Video Stream
    cmd = ["ffmpeg", "-y"]
    
    if video_path and os.path.exists(video_path):
        v_dur = get_audio_duration(video_path)
        if v_dur >= 10 and image_paths:
            # INTERSPERSED MODE: Video (7s) -> Image (5s) -> Video (7s) -> Image (5s) ...
            # To avoid copyright, insert product images every 7 seconds. 
            trans_dur = 1.0
            vid_seg_dur = 7.0
            img_seg_dur = 5.0 # Set to 5s so image is visible for 3s between 1s transitions
            num_imgs = len(image_paths)
            
            cmd.extend(["-stream_loop", "-1", "-i", video_path]) # Index 0
            for img in image_paths:
                # Deliberately NOT "-loop 1": these images feed zoompan, which
                # emits its `d` frames for EVERY input frame it receives. With
                # -loop 1 a 5s image handed zoompan ~125 input frames and it
                # computed 125 x d frames, then threw ~99% of them away at the
                # trim -- one 5s slideshow segment took ~55s instead of ~0.4s.
                # A single input frame is all zoompan needs.
                cmd.extend(["-i", img]) # Indices 1 to N
            
            sequence = []
            curr_v_time = 0
            est_total = 0
            img_ptr = 0
            
            # Generate segments to cover the required duration
            while est_total < duration + trans_dur:
                # Video segment
                # Randomize video segment duration from the suggested jumpy values
                v_part_dur = random.choice([5.0, 5.3, 5.7, 6.0, 6.5, 6.9])
                sequence.append({'type': 'v', 'start': curr_v_time, 'dur': v_part_dur})
                est_total += v_part_dur - (trans_dur if len(sequence) > 1 else 0)
                curr_v_time += v_part_dur
                if est_total >= duration + trans_dur: break
                
                # Image segment
                sequence.append({'type': 'i', 'idx': (img_ptr % num_imgs) + 1, 'dur': img_seg_dur})
                est_total += img_seg_dur - trans_dur
                img_ptr += 1

            filter_parts = []
            for i, seg in enumerate(sequence):
                if seg['type'] == 'v':
                    filter_parts.append(f"[0:v]{fit_and_pad_filter()},fps=25,trim={seg['start']}:{seg['start']+seg['dur']},setpts=PTS-STARTPTS[p{i}]")
                else:
                    frames = int(seg['dur'] * 25) + 25
                    # Zoom filter: Fit to screen first (decrease) then zoom
                    res_w, res_h = output_resolution()
                    zoom_target_w, zoom_target_h = zoom_working_resolution()
                    zoom_filter = (
                        f"scale={zoom_target_w}:{zoom_target_h}:force_original_aspect_ratio=decrease,pad={zoom_target_w}:{zoom_target_h}:(ow-iw)/2:(oh-ih)/2:color=white,setsar=1,"
                        f"zoompan=z=''zoom+0.0004'':x=''iw/2-(iw/zoom/2)'':y=''ih/2-(ih/zoom/2)'':d={frames}:s={res_w}x{res_h}:fps=25,"
                        f"setsar=1"
                    )
                    filter_parts.append(f"[{seg['idx']}:v]{zoom_filter},trim=0:{seg['dur']},setpts=PTS-STARTPTS[p{i}]")
            
            # Chain segments with xfade
            if len(sequence) > 1:
                current_chain = "[p0]"
                running_dur = sequence[0]['dur']
                transitions = ACTIVE_TRANSITIONS if ACTIVE_TRANSITIONS else ["fade"]
                for i in range(1, len(sequence)):
                    offset = running_dur - trans_dur
                    out_label = f"[vchain{i}]" if i < len(sequence) - 1 else "[bg]"
                    trans_type = random.choice(transitions)
                    filter_parts.append(f"{current_chain}[p{i}]xfade=transition={trans_type}:duration={trans_dur}:offset={offset:.3f}{out_label}")
                    current_chain = out_label
                    running_dur = running_dur + sequence[i]['dur'] - trans_dur
            else:
                filter_parts.append("[p0]null[bg]")
            
            cmd.extend(["-i", concat_audio])
            audio_idx = num_imgs + 1
            filter_base = "; ".join(filter_parts)

        elif v_dur < 10 and image_paths:
            # OLD MIXED MODE: Video -> Slideshow -> Video Loop
            num_imgs = len(image_paths)
            trans_dur = 1.0
            
            v1_t = min(v_dur, duration)
            rem = duration - v1_t
            
            if rem > 2.0:
                slide_t = min(rem * 0.6, 12.0)
                v2_t = rem - slide_t
                
                cmd.extend(["-stream_loop", "-1", "-i", video_path])
                
                filter_parts = []
                filter_parts.append(f"[0:v]{fit_and_pad_filter()},trim=0:{v1_t},setpts=PTS-STARTPTS[vseg1]")
                
                img_dur = (slide_t + (num_imgs - 1) * trans_dur) / num_imgs if num_imgs > 1 else slide_t
                # Every other zoom block in this file branches on SHORTS_MODE
                # (portrait vs landscape target); this one didn't, so a Shorts
                # render concatenated a 1280x720 slideshow segment between two
                # 720x1280 video segments ([vseg1][vslide_final][vseg2]) and
                # either failed the filtergraph or produced a mangled frame.
                res_w, res_h = output_resolution()
                zoom_target_w, zoom_target_h = zoom_working_resolution()
                for i, img in enumerate(image_paths):
                    # One input frame only -- see the INTERSPERSED MODE note
                    # above: -loop 1 makes zoompan recompute its whole `d`
                    # frame run for every input frame it is handed.
                    cmd.extend(["-i", img])
                    frames = int(img_dur * 25) + 25
                    zoom_filter = (
                        f"scale={zoom_target_w}:{zoom_target_h}:force_original_aspect_ratio=decrease,pad={zoom_target_w}:{zoom_target_h}:(ow-iw)/2:(oh-ih)/2:white,setsar=1,"
                        f"zoompan=z='zoom+0.0004':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={res_w}x{res_h}:fps=25,"
                        f"setsar=1"
                    )
                    filter_parts.append(f"[{i+1}:v]{zoom_filter}[vimg{i}]")
                
                current_img = "[vimg0]"
                transitions = ACTIVE_TRANSITIONS if ACTIVE_TRANSITIONS else ["fade"]
                if num_imgs > 1:
                    for i in range(1, num_imgs):
                        offset = i * (img_dur - trans_dur)
                        out_img = f"[vims{i}]" if i < num_imgs - 1 else "[vslide_final]"
                        trans_type = random.choice(transitions)
                        filter_parts.append(f"{current_img}[vimg{i}]xfade=transition={trans_type}:duration={trans_dur}:offset={offset:.3f}{out_img}")
                        current_img = out_img
                else:
                    filter_parts.append("[vimg0]null[vslide_final]")
                
                filter_parts.append(f"[0:v]{fit_and_pad_filter()},trim={v1_t}:{v1_t+v2_t},setpts=PTS-STARTPTS[vseg2]")
                filter_parts.append(f"[vseg1][vslide_final][vseg2]concat=n=3:v=1:a=0[bg]")
                
                cmd.extend(["-i", concat_audio])
                audio_idx = num_imgs + 1
                filter_base = "; ".join(filter_parts)
            else:
                cmd.extend(["-stream_loop", "-1", "-i", video_path]) 
                cmd.extend(["-i", concat_audio])
                filter_base = f"[0:v]{fit_and_pad_filter()}[bg]"
                audio_idx = 1
        else:
            cmd.extend(["-stream_loop", "-1", "-i", video_path]) 
            cmd.extend(["-i", concat_audio])
            filter_base = f"[0:v]{fit_and_pad_filter()}[bg]"
            audio_idx = 1
    elif image_paths:
        num_imgs = len(image_paths)
        trans_dur = 1.0  # 1 second overlap for xfade
        # Adjust per-image duration to account for transitions
        # Total duration = (img_dur * num_imgs) - (trans_dur * (num_imgs - 1))
        # So img_dur = (total_duration + (num_imgs - 1) * trans_dur) / num_imgs
        img_dur = (duration + (num_imgs - 1) * trans_dur) / num_imgs if num_imgs > 1 else duration
        
        filter_parts = []
        for i, img in enumerate(image_paths):
            # STABLE ZOOM: Higher res input + slower zoom speed + fixed size
            frames = int(img_dur * 25) + 25 # buffer frames
            res_w, res_h = output_resolution()
            zoom_target_w, zoom_target_h = zoom_working_resolution()
            zoom_filter = (
                f"scale={zoom_target_w}:{zoom_target_h}:force_original_aspect_ratio=decrease,pad={zoom_target_w}:{zoom_target_h}:(ow-iw)/2:(oh-ih)/2:color=white,setsar=1,"
                f"zoompan=z='zoom+0.0004':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={res_w}x{res_h}:fps=25,"
                f"setsar=1"
            )
            # One input frame only -- see the INTERSPERSED MODE note above:
            # -loop 1 makes zoompan recompute its whole `d` frame run for
            # every input frame it is handed (this was the single biggest
            # cost in the whole render).
            cmd.extend(["-i", img])
            filter_parts.append(f"[{i}:v]{zoom_filter}[v{i}]")
        
        # Cross-fade transitions (xfade)
        transitions = ACTIVE_TRANSITIONS if ACTIVE_TRANSITIONS else ["fade"]
        if num_imgs > 1:
            current_node = "[v0]"
            for i in range(1, num_imgs):
                offset = i * (img_dur - trans_dur)
                next_node = f"[v{i}]"
                out_node = f"[bg{i}]" if i < num_imgs - 1 else "[bg]"
                trans_type = random.choice(transitions)
                filter_parts.append(f"{current_node}{next_node}xfade=transition={trans_type}:duration={trans_dur}:offset={offset:.3f}{out_node}")
                current_node = out_node
        else:
            filter_parts.append("[v0]setsar=1[bg]")
        
        cmd.extend(["-i", concat_audio]) 
        filter_base = "; ".join(filter_parts)
        audio_idx = num_imgs
    else:
        color_s = "1080x1920" if SHORTS_MODE else "1920x1080"
        cmd.extend(["-f", "lavfi", "-i", f"color=c=white:s={color_s}", "-i", concat_audio])
        filter_base = "[0:v]null[bg]"
        audio_idx = 1

    # Populated by the caption bar below (if there is one); read much later
    # when the audio chain is assembled, so it has to exist either way.
    typing_windows = []

    # 3. Add Title Overlay (News Style)
    # Captions no longer replace this. They render as a second, smaller bar
    # directly beneath it in the same visual language (see the caption block
    # further down), and the whole stack is lifted to make room -- so the
    # product title and its key points read as one unit instead of two
    # elements fighting for the same band.
    if clean_title:
        # Pixel constants below are tuned for the legacy 720x1280/1280x720
        # canvas; TEXT_SCALE keeps every dimension proportional on the new
        # 1080x1920/1920x1080 output instead of shrinking relative to frame.
        S = TEXT_SCALE
        if SHORTS_MODE:
            frame_h = round(1280 * S)
            title_font_size = round(30 * S)
            title_wrap_width = 32
            max_title_lines = 3
            bottom_margin = round(96 * S)
        else:
            frame_h = round(720 * S)
            title_font_size = round(34 * S)
            title_wrap_width = 58
            max_title_lines = 2
            bottom_margin = round(72 * S)
        wrapped_lines = wrap_lines_for_overlay(
            clean_title,
            width=title_wrap_width,
            max_lines=max_title_lines,
        )

        line_count = len(wrapped_lines)

        # Dynamic height calculation for the blue bar to match the black box exactly
        # Each line of text is approx 36 pixels with fontsize 35
        # Total height of black box = (line_count * 36) + (2 * box_p)
        box_p = round(20 * S)
        line_h = title_font_size + round(5 * S)
        overlay_h = (line_count * line_h) + (box_p * 2) - round(4 * S)

        cta_font_size = round((20 if SHORTS_MODE else 22) * S)
        cta_box_p = round((12 if SHORTS_MODE else 8) * S)
        cta_total_h = cta_font_size + (cta_box_p * 2) + round(8 * S)
        header_total_h = round(46 * S) if header_text else 0
        edge_pad = round(24 * S)

        # --- Key-point caption bar (sits directly under the title) ---
        # One step smaller than the title, same box + accent-bar language, so
        # the two read as a single stacked unit rather than competing
        # overlays. Everything above is lifted by exactly this bar's height
        # so the block still ends where it used to.
        caption_points = _caption_points_for_overlay(caption_key_points) if CAPTIONS_ENABLED else []
        # User-configurable (Settings -> Visual Style -> Key-Point Captions).
        # A blank/zero size falls back to a sensible ratio of the title so a
        # never-touched setting doesn't produce a 0px caption.
        caption_font_size = round(CAPTIONS_FONT_SIZE * S) if CAPTIONS_FONT_SIZE else round(title_font_size * 0.78)
        caption_box_p = round(12 * S)
        caption_gap = round(8 * S)
        caption_h = caption_font_size + (caption_box_p * 2) if caption_points else 0
        caption_block_h = (caption_h + caption_gap) if caption_points else 0

        y_pos = min(
            frame_h - edge_pad,
            max(
                bottom_margin,
                overlay_h + cta_total_h + header_total_h + edge_pad,
            ),
        ) + caption_block_h
        
        # Entrance/Exit timings
        bg_in_s, bg_in_e = 0.5, 1.0
        txt_in_s, txt_in_e = 0.8, 1.3
        txt_out_s = max(duration - 1.5, txt_in_e + 0.1)
        bg_out_s = max(duration - 1.0, bg_in_e + 0.1)
        
        # Call-to-Action (CTA) Settings
        cta_text = "Check the Links in Description for Best Prices"
        cta_dur = 7.0
        cta_in_s = max(duration - cta_dur, txt_in_e + 0.1)
        # `show_cta=False` keeps every geometry/layout calculation below
        # unchanged (so the title box and everything anchored to it doesn't
        # shift) but makes the CTA's enable window impossible to satisfy --
        # simpler and safer than threading a conditional through the
        # filtergraph string construction that follows.
        cta_enable_window = f"between(t,{cta_in_s},{duration})" if show_cta else "0"
        
        # Wrap x_pos and y_pos in logic for Shorts Mode
        slide_speed = round(130 * S)  # px/sec for the reveal animations
        reveal_offset = round(65 * S)
        text_edge_pad = round(15 * S)
        if SHORTS_MODE:
            y_coord = f"H-{y_pos}"
            layer_w = round(640 * S)
            blue_x_base = round(30 * S)
            txt_layer_x = round(45 * S)
        else:
            y_coord = f"H-{y_pos}"
            layer_w = round(1120 * S)
            blue_x_base = round(50 * S)
            txt_layer_x = round(65 * S)

        # News style sliding reveal logic:
        blue_x_expr = f"if(lt(t, {bg_in_e}), {blue_x_base-reveal_offset}+(t-{bg_in_s})*{slide_speed}, if(lt(t, {bg_out_s}), {blue_x_base}, {blue_x_base}-(t-{bg_out_s})*{slide_speed}))"
        txt_rel_x = f"if(lt(t, {txt_in_e}), -text_w+(t-{txt_in_s})*2*text_w, if(lt(t, {txt_out_s}), {text_edge_pad}, {text_edge_pad}-(t-{txt_out_s})*2*text_w))"

        # CTA Slide Up Logic
        # Sits below the caption bar, not on top of it: caption_block_h is 0
        # when there are no key points, so this is the original position
        # whenever captions are off.
        cta_gap = round((10 if SHORTS_MODE else 8) * S)
        cta_y_px = y_pos - overlay_h - caption_block_h - cta_gap
        if SHORTS_MODE:
            cta_y_expr = f"if(lt(t, {cta_in_s}+0.5), H, H-{cta_y_px})"
            cta_x_pos = round(60 * S)
        else:
            cta_y_expr = f"if(lt(t, {cta_in_s}+0.5), H, H-{cta_y_px})"
            cta_x_pos = round(72 * S)

        # Sink logic: moves down/up when title moves
        cta_final_y = f"if(gt(t, {bg_out_s}), {cta_y_expr}+(t-{bg_out_s})*{slide_speed}, {cta_y_expr})"

        # Helper to fix color for FFmpeg - 0xRRGGBB format is most reliable
        def fix_color_ffmpeg(c):
            if isinstance(c, str) and c.startswith('#'):
                return "0x" + c[1:]
            return c

        prod_title_color = fix_color_ffmpeg(COLOR_PRODUCT_TITLE)
        prod_box_color = fix_color_ffmpeg(COLOR_PRODUCT_BG)
        blue_bar_color = fix_color_ffmpeg(COLOR_BLUEBAR)
        cta_txt_color = fix_color_ffmpeg(COLOR_LINK_CHECK_TEXT)
        cta_bg_color = fix_color_ffmpeg(COLOR_LINK_CHECK_BG)
        caption_text_color = fix_color_ffmpeg(COLOR_CAPTIONS_TEXT)
        caption_bg_color = fix_color_ffmpeg(COLOR_CAPTIONS_BG)
        caption_bg_opacity = VAL_CAPTIONS_BG_OPACITY if VAL_CAPTIONS_BG_OPACITY > 0.01 else VAL_PRODUCT_BG_OPACITY

        # New overlay design using a separate layer for clipping the reveal effect
        blue_bar_w = round(15 * S)
        blue_bar_gen = f"color=c={blue_bar_color}:s={blue_bar_w}x{overlay_h}:r=25[bluebar]"
        blue_bar_ovl = f"[bg][bluebar]overlay=x='{blue_x_expr}':y={y_coord}:enable='between(t,{bg_in_s},{duration})'[with_blue]"
        
        # Transparent layer for text reveal 
        # Restrict width to layer_w to ensure it doesn't touch the frame
        text_layer_gen = f"color=c=black@0:s={layer_w}x{overlay_h}:r=25[txt_layer]"
        # Text box background color using PRODUCT specific settings
        # Removed boxw=2000 to keep the box bounded by the layer
        # `clean_title` (sanitized above, before wrapping) is the sanitized
        # text -- sanitize_text() must not run again here. It used to: once
        # on % turned an already-escaped %% into %%%% ("100% Cotton" rendered
        # as "100%% Cotton"), and its `\` -> ` ` replacement destroyed the
        # `\<newline>` escape wrap_lines_for_overlay's join relies on for
        # FFmpeg's multi-line drawtext.
        # One drawtext filter per wrapped line, stacked by y, instead of one
        # filter with the lines newline-joined: this ffmpeg build renders a
        # visible tofu box for the raw newline byte (libharfbuzz shapes it
        # into a .notdef glyph before the line break is applied). Same fix
        # already applied to intro/outro slides in create_text_slide_ffmpeg.
        title_line_draws = []
        for i, line in enumerate(wrapped_lines):
            title_line_draws.append(
                f"drawtext=fontfile='{font_path}':text='{line}':fontsize={title_font_size}:fontcolor={prod_title_color}:"
                f"box=1:boxcolor={prod_box_color}@{VAL_PRODUCT_BG_OPACITY}:boxborderw={box_p}:y={box_p + i * line_h}:x='{txt_rel_x}'"
            )
        text_draw = "[txt_layer]" + ",".join(title_line_draws) + "[txt_segment]"
        text_ovl = f"[with_blue][txt_segment]overlay=x={txt_layer_x}:y={y_coord}:enable='between(t,{txt_in_s},{duration})'[v_title]"

        # --- Key-point caption bar, typed out under the title ---
        # Each point gets an equal slice of the segment and is revealed a few
        # characters at a time. Exactly one prefix is enabled at any instant
        # (each step owns a half-open window), so the prefixes never stack on
        # top of each other -- which is what makes it read as typing rather
        # than as overlapping text.
        caption_chain_in = "v_title"
        caption_draws = []
        caption_windows = []
        if caption_points:
            # drawtext exposes the output height as `H`; drawbox does not --
            # it only knows `ih`. Same pixel row, two spellings.
            caption_y_px = y_pos - overlay_h - caption_gap
            caption_y = f"H-{caption_y_px}"
            caption_y_box = f"ih-{caption_y_px}"
            caption_start = txt_in_e + 0.2
            caption_end = max(caption_start + 0.5, bg_out_s)
            per_point = (caption_end - caption_start) / len(caption_points)
            for point_index, point in enumerate(caption_points):
                point_start = caption_start + point_index * per_point
                point_end = point_start + per_point
                caption_windows.append((point_start, point_end))
                steps = _typewriter_reveal_steps(point)
                if not steps:
                    continue
                # Type over the first 45% of the slot, hold the rest.
                type_span = per_point * 0.45
                step_dt = type_span / len(steps)
                typing_windows.append((point_start, point_start + type_span))
                for step_index, prefix in enumerate(steps):
                    step_start = point_start + step_index * step_dt
                    is_last = step_index == len(steps) - 1
                    step_end = point_end if is_last else step_start + step_dt
                    caption_draws.append(
                        f"drawtext=fontfile='{font_path}':text='{sanitize_text(prefix)}':"
                        f"fontsize={caption_font_size}:fontcolor={caption_text_color}:"
                        f"box=1:boxcolor={caption_bg_color}@{caption_bg_opacity}:"
                        f"boxborderw={caption_box_p}:x={txt_layer_x + text_edge_pad}:"
                        f"y='{caption_y}':enable='between(t,{step_start:.3f},{step_end:.3f})'"
                    )
        if caption_draws:
            # Accent bar matching the title's, spanning the caption's whole
            # on-screen life so the two bars line up as one unit.
            caption_bar_in = caption_windows[0][0]
            caption_bar_out = caption_windows[-1][1]
            caption_draws.insert(
                0,
                f"drawbox=x={blue_x_base}:y='{caption_y_box}':w={blue_bar_w}:h={caption_h}:"
                f"color={blue_bar_color}@1.0:t=fill:"
                f"enable='between(t,{caption_bar_in:.3f},{caption_bar_out:.3f})'",
            )
            caption_draw = f"[v_title]" + ",".join(caption_draws) + "[v_caption]"
            caption_chain_in = "v_caption"
        else:
            caption_draw = None

        # Add the CTA (Call to Action) text
        cta_draw = (
            f"[{caption_chain_in}]drawtext=fontfile='{font_path}':text='{cta_text}':fontsize={cta_font_size}:fontcolor={cta_txt_color}:"
            f"box=1:boxcolor={cta_bg_color}@{VAL_INTRO_OVERLAY_OPACITY}:boxborderw={cta_box_p}:x={cta_x_pos}:y='{cta_final_y}':enable='{cta_enable_window}'[v_cta]"
        )

        # 4. Add Section Header (Introduction, Key Features, etc.) above the title box
        if header_text:
            # Shift right to align exactly inline with the Blue Bar's text/shape.
            # Colored Purple (0x800080). Sliding animation (matching the title box sync).
            header_align = round(10 * S)
            header_offscreen = round(1000 * S)
            header_gap = round(42 * S)
            header_font_size = round(22 * S)
            header_box_p = round(10 * S)
            header_x = f"if(lt(t,{bg_in_s}+0.5),-{header_offscreen}+({blue_x_base}+{header_align}+{header_offscreen})*((t-{bg_in_s})/0.5),if(gt(t,{bg_out_s}-0.5),{blue_x_base}+{header_align}-({blue_x_base}+{header_align}+{header_offscreen})*((t-({bg_out_s}-0.5))/0.5),{blue_x_base}+{header_align}))"

            header_draw = (
                f"[v_cta]drawtext=fontfile='{font_path}':text='{sanitize_text(header_text).upper()}':fontsize={header_font_size}:fontcolor=white:"
                f"box=1:boxcolor=0x800080@1.0:boxborderw={header_box_p}:x='{header_x}':y={y_coord}-{header_gap}:enable='between(t,{bg_in_s},{bg_out_s})'[v_branded]"
            )
        caption_stage = f"{caption_draw}; " if caption_draw else ""
        if header_text:
            filter_complex = f"{filter_base}; {blue_bar_gen}; {blue_bar_ovl}; {text_layer_gen}; {text_draw}; {text_ovl}; {caption_stage}{cta_draw}; {header_draw}"
        else:
            filter_complex = f"{filter_base}; {blue_bar_gen}; {blue_bar_ovl}; {text_layer_gen}; {text_draw}; {text_ovl}; {caption_stage}{cta_draw}; [v_cta]null[v_branded]"
        
        # Append branding filters if any
        if branding_filters:
            filter_complex += f"; [v_branded]" + ",".join(branding_filters) + "[v_prebadge]"
        else:
            filter_complex += "; [v_branded]null[v_prebadge]"

    else:
        # If no title, use [bg] as base. filter_base already closes its
        # chain with the [bg] label (e.g. "[0:v]null[bg]") -- continuing
        # with a bare "," instead of "; [bg]" tried to hang a second output
        # label off a filter (null) that only has one, which ffmpeg rejects
        # outright ("More output link labels specified for filter than it
        # has outputs"). This was a real, if rare, pre-existing bug: it only
        # ever fired when a product segment had no title at all. Captions
        # mode routes every product through this branch (title overlay is
        # skipped so it can't collide with burned-in captions), which is
        # what actually surfaced it.
        if branding_filters:
            filter_complex = f"{filter_base}; [bg]" + ",".join(branding_filters) + "[v_prebadge]"
        else:
            filter_complex = f"{filter_base}; [bg]null[v_prebadge]"

    # 5. "Check Price" CTA badge, bottom-right corner. Color scheme is picked
    # deterministically per product (from the title text) so back-to-back
    # products in one video don't all show the identical badge. Gated by
    # show_cta same as the sliding banner above -- a badge on every single
    # product segment was the other half of the "CTA every product looks
    # unnatural" report; both now only appear on the 2-3 segments the
    # caller picked.
    badge_path = None
    if show_cta:
        try:
            from cta_badge import build_cta_badge
            badge_out = os.path.abspath(f"{output_path}_ctabadge.png").replace("\\", "/")
            badge_path = build_cta_badge(setup_font(), badge_out, seed_text=clean_title or output_path)
        except Exception as e:
            print(f"CTA badge generation skipped: {e}")

    if badge_path and os.path.exists(badge_path):
        cmd.extend(["-i", badge_path])
        badge_idx = sum(1 for a in cmd if a == "-i") - 1
        badge_w = round((220 if SHORTS_MODE else 260) * TEXT_SCALE)
        badge_margin = round(24 * TEXT_SCALE)
        badge_in_s = 0.6
        filter_complex += (
            f"; [{badge_idx}:v]scale={badge_w}:-1[badge]; "
            f"[v_prebadge][badge]overlay=x=W-w-{badge_margin}:y=H-h-{badge_margin}:"
            f"enable='gte(t,{badge_in_s})'[v]"
        )
    else:
        filter_complex += "; [v_prebadge]null[v]"

    # Every segment gets one continuous, exact-length 48 kHz audio clock.
    # This prevents cumulative gaps/drift when many segments are concatenated.
    voice_chain = (
        f"aresample={AUDIO_SAMPLE_RATE}:async=1:first_pts=0,"
        f"apad=whole_dur={duration},atrim=duration={duration}"
    )
    typing_loop = os.path.join(os.path.dirname(__file__), "sfx", "keytype_loop.wav")
    if typing_windows and os.path.isfile(typing_loop):
        # Keystroke ticks under the caption reveal. The loop runs the whole
        # segment and is muted outside the typing spans -- one gated input
        # rather than a separate delayed click per character, which would
        # mean hundreds of filters for the same result.
        typing_idx = sum(1 for a in cmd if a == "-i")
        cmd.extend(["-stream_loop", "-1", "-i", typing_loop])
        inside = "+".join(
            f"between(t,{start:.3f},{end:.3f})" for start, end in typing_windows
        )
        filter_complex += (
            f"; [{audio_idx}:a]{voice_chain}[voice_a]"
            f"; [{typing_idx}:a]aresample={AUDIO_SAMPLE_RATE},"
            f"volume=0:enable='eq(0,{inside})',volume={TYPING_SFX_GAIN},"
            f"apad=whole_dur={duration},atrim=duration={duration}[key_a]"
            f"; [voice_a][key_a]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[aout]"
        )
        cmd.extend(["-filter_complex", filter_complex, "-map", "[v]", "-map", "[aout]"])
    else:
        cmd.extend([
            "-filter_complex", filter_complex,
            "-map", "[v]", "-map", f"{audio_idx}:a",
        ])
        cmd.extend(["-af", voice_chain])
    cmd.extend([
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-ar", str(AUDIO_SAMPLE_RATE), "-ac", "2",
        "-r", "25",
        "-t", str(duration),
        output_path
    ])

    try:
        run_ffmpeg(cmd)
        if os.path.exists(concat_audio): os.remove(concat_audio)
        if badge_path and os.path.exists(badge_path): os.remove(badge_path)
        return output_path
    except Exception as e:
        print(f"Error creating product segment: {e}")
        if os.path.exists(concat_audio): os.remove(concat_audio)
        if badge_path and os.path.exists(badge_path): os.remove(badge_path)
        return None

_CSS_CODE_PATTERN = re.compile(
    r'[{};]|--[a-zA-Z][\w-]*\s*:|:\s*root\b|^\s*[.#@][\w-]|^[a-zA-Z-]+\s*:\s*[\w#]',
)


# Amazon site-chrome/accessibility boilerplate that reads as valid prose
# (passes both the CSS/code check and the letter-ratio check below) but is
# never a product feature -- e.g. "Select the department you want to search
# in" and "To move between items, use your keyboard up or down arrow keys"
# both showed up burned into a caption because the section-anchor search
# (see download_assets) landed near header/search nav markup instead of the
# real feature bullets. Lowercase, partial-match substrings so wording drift
# ("arrow keys" vs "up or down arrows") is still caught.
_AMAZON_BOILERPLATE_SNIPPETS = (
    "use your keyboard",
    "select the department you want to search",
    "compare with similar items",
    "to move between items",
    "skip to main",
    "skip to main search results",
    "hello, sign in",
    "returns & orders",
    "search amazon",
    "choose a language for shopping",
    "go back to filtering menu",
)


def _looks_like_junk_feature_text(text):
    """Reject scraped "feature" text that is actually CSS/JS/JSON, not a sentence.

    The aggressive feature scraper grabs any text sitting between '>' and '<'
    in a chunk of raw HTML; if that chunk still contains inline <style>/<script>
    text (or the regex catches a stray declaration block), the result reads
    like "root { -nav-desktop-header-tbg #131921;" -- syntactically a "sentence"
    (3+ space-separated tokens, no angle brackets) but never something we want
    narrated or typed onto a caption.
    """
    if _CSS_CODE_PATTERN.search(text):
        return True
    # Real feature bullets are prose: mostly letters/spaces. Code/CSS lines
    # skew heavy on punctuation/symbols relative to letters.
    letters = sum(1 for c in text if c.isalpha())
    if letters < len(text) * 0.55:
        return True
    lowered = text.lower()
    if any(snippet in lowered for snippet in _AMAZON_BOILERPLATE_SNIPPETS):
        return True
    return False


# --- Original Functions (Unchanged) ---

def download_assets(asin, base_dir="files_created"):
    url = f"https://www.amazon.com/dp/{asin}"
    
    content = ""
    print(f"Fetching page for ASIN: {asin}...")
    
    # Try multiple user agents
    for i, ua in enumerate(USER_AGENTS):
        headers = {
            "User-Agent": ua,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "Accept-Encoding": "gzip, deflate, br",
            "Accept-Language": "en-US,en;q=0.9",
            "Device-Memory": "8",
            "Viewport-Width": "1920",
        }

        try:
            response = requests.get(url, headers=headers, timeout=10)
            if response.status_code == 200 and len(response.text) > 10000:
                content = html.unescape(response.text)
                print(f"Successfully fetched page with User-Agent {i+1}")
                break
            else:
                print(f"Attempt {i+1} failed: Status {response.status_code}, Length {len(response.text)}. Trying next...")
        except Exception as e:
            print(f"Error on attempt {i+1}: {e}")
            continue

    if not content:
        print(f"Warning: All User-Agent attempts failed for ASIN: {asin}. Might be blocked.")
        return None, None, None, None

    print(f"Content length: {len(content)}")
    
    # 0. Extract Title and Features
    title = ""
    features = []
    
    title_match = re.search(r'<span id="productTitle"[^>]*>\s*(.*?)\s*</span>', content, re.DOTALL)
    if title_match:
        title = html.unescape(title_match.group(1)).strip()
        print(f"Title found: {title[:50]}...")
    else:
        # The page fetched (right size, no exception) but Amazon's markup
        # didn't match -- a CAPTCHA/interstitial page, a layout change, or a
        # dead ASIN. Treating this as success used to prompt the LLM with
        # "ORIGINAL TITLE: None" and ship a garbage video instead of skipping
        # the ASIN.
        print(f"Warning: productTitle not found for ASIN {asin}; page may be a CAPTCHA/interstitial.")
        return None, None, None, None

    # Extract bullet points/features (Aggressive search)
    print("Searching for product features (Aggressive Mode)...")
    features = []
    
    # 1. Look for 'About this item' text position
    about_mark = "About this item"
    pos = content.find(about_mark)
    if pos == -1:
        # Fallback to other precise, multi-word section headers only. Bare
        # single words like "About" or "Features" used to be accepted here
        # and could match header/nav markup near the top of the page (e.g.
        # an "About Amazon" link) long before the real feature section --
        # the 30k-char window from that wrong position then swept up site
        # chrome like "Select the department you want to search in" and
        # "use your keyboard up or down arrow keys", which got scraped as
        # if they were product features.
        m = re.search(r'(About this item|Product information|Technical Details)', content, re.IGNORECASE)
        if m: pos = m.start()
    
    if pos != -1:
        print(f"Target section found at position: {pos}")
        # Capture a very large chunk (30k chars) to ensure we get all features
        chunk = content[pos : pos + 30000]
        # Drop entire <script>/<style> blocks first -- their raw text content
        # (CSS rules, JSON, JS) otherwise sails through the tag-boundary regex
        # below and ends up looking like a valid "sentence" (e.g. a CSS rule
        # like "root { -nav-desktop-header-tbg #131921;" was showing up as a
        # caption because it has 3+ space-separated tokens and no '<'/'>').
        chunk = re.sub(r'<(script|style)\b[^>]*>.*?</\1>', ' ', chunk, flags=re.DOTALL | re.IGNORECASE)

        # Look for anything between tags that is 20-1000 chars long
        # This captures data even if Amazon changes <li> to <div> or <span>
        potential_points = re.findall(r'>(?:\s*)([^<>]{20,1000})(?:\s*)<', chunk, re.DOTALL)

        for p in potential_points:
            p_clean = html.unescape(p).strip()
            # Basic validation: must be a sentence, not JS or CSS or Nav items
            if (len(p_clean) > 20 and
                not p_clean.startswith(('{', 'if(', '.', 'function', 'var', 'window')) and
                "Check to see" not in p_clean and
                "About this item" not in p_clean and
                not _looks_like_junk_feature_text(p_clean) and
                p_clean not in features):

                # Check if it has at least 3 words to be a valid feature sentence
                if len(p_clean.split()) >= 3:
                    features.append(p_clean)
        
        # Limit to top 10 features to keep AI description focused
        features = features[:10]

    # 2. Final Fallback: If still 0, try the standard span method on the chunk
    if not features and pos != -1:
        fallback_matches = re.findall(r'<span[^>]*>(.*?)</span>', chunk, re.DOTALL)
        for fm in fallback_matches:
            fm_clean = re.sub(r'<[^>]+>', '', fm).strip()
            if (len(fm_clean) > 15 and fm_clean not in features and "About" not in fm_clean
                    and not _looks_like_junk_feature_text(fm_clean)):
                features.append(html.unescape(fm_clean))

    print(f"Features found: {len(features)}")

    # 1. Extract Videos
    potential_videos = []
    ib_matches = re.findall(r'jQuery\.parseJSON\(\'(.*?)\'\);', content, re.DOTALL)
    print(f"Found {len(ib_matches)} jQuery.parseJSON blocks")
    for ib_match in ib_matches:
        try:
            decoded_ib = ib_match.encode().decode('unicode_escape')
            data = json.loads(decoded_ib)
            if 'videos' in data:
                for v in data['videos']:
                    v_url = v.get('url')
                    v_title = v.get('title', 'product_video').replace(' ', '_')
                    if v_url:
                        potential_videos.append({'url': v_url, 'title': v_title})
        except:
            continue

    metadata_matches = re.findall(r'"videoURL"\s*:\s*"([^"]+)"', content)
    metadata_matches += re.findall(r'"url"\s*:\s*"([^"]+\.mp4)"', content)
    for m_url in metadata_matches:
        m_url = m_url.split('"')[0].split('&')[0]
        potential_videos.append({'url': m_url, 'title': 'video'})

    final_videos = []
    seen_urls = set()
    for item in potential_videos:
        v_url = item['url']
        if v_url in seen_urls: continue
        seen_urls.add(v_url)
        if v_url.endswith('.m3u8'):
            # Try to guess MP4 URLs for different resolutions
            for res in ['720', '1080', '480']:
                patterns = [
                    ('default.jobtemplate.hls.m3u8', f'default.jobtemplate.mp4.{res}.mp4'),
                    ('default.vertical.jobtemplate.hls.m3u8', f'default.vertical.jobtemplate.mp4.{res}.mp4'),
                    ('.hls.m3u8', f'.mp4.{res}.mp4'),
                    ('.m3u8', f'_{res}p.mp4'),
                    ('.m3u8', f'.{res}.mp4')
                ]
                for old, new in patterns:
                    if old in v_url:
                        guess = v_url.replace(old, new)
                        final_videos.append({'url': guess, 'title': f"{item['title']}_{res}p"})
                        # For this resolution, if we found one pattern that matches, we move to next resolution
                        break 
        elif v_url.endswith('.mp4'):
            final_videos.append({'url': v_url, 'title': item['title']})

    # 2. Extract Images
    # Search for colorImages JSON - more robustly
    print("Searching for images...")
    image_blocks = re.findall(r"'colorImages':\s*({.*?'originalColorImages')", content, re.DOTALL)
    if not image_blocks:
        image_blocks = re.findall(r'colorImages":\s*({.*?"originalColorImages")', content, re.DOTALL)
    
    potential_images = []
    for block in image_blocks:
        # Extract everything that looks like a hiRes URL
        found_urls = re.findall(r'"hiRes"\s*:\s*"([^"]+)"', block)
        potential_images.extend(found_urls)
    
    # Fallback image search - look for any large product images
    if not potential_images:
        print("Using fallback image search...")
        potential_images = re.findall(r'https://m\.media-amazon\.com/images/I/[^._]+\._AC_SL1500_\.jpg', content)
        potential_images += re.findall(r'https://m\.media-amazon\.com/images/I/[^._]+\.jpg', content)

    # Unique images only
    potential_images = list(dict.fromkeys(potential_images))
    print(f"Found {len(potential_images)} unique images.")

    if not os.path.exists(base_dir):
        os.makedirs(base_dir)

    # Download first video
    downloaded_video = None
    
    # Priority: 720p is preferred, then 1080p, then 480p, finally anything else.
    def video_priority(v):
        score = 0
        v_url = v.get('url', '').lower()
        v_title = v.get('title', '').lower()
        
        # Check for resolution markers in title or URL
        if '720p' in v_title or '720' in v_url:
            score = 100
        elif '1080p' in v_title or '1080' in v_url:
            score = 90
        elif '480p' in v_title or '480' in v_url:
            score = 80
        elif 'video' in v_title:
            score = 50
            
        # Heavily penalize previews
        if 'preview' in v_url:
            score -= 200
        return score

    final_videos.sort(key=video_priority, reverse=True)
    
    print(f"Checking {len(final_videos)} potential video URLs...")
    for v in final_videos:
        v_url = v['url']
        v_title = re.sub(r'[\\/*?:"<>|]', "", v['title'])
        filepath = None
        try:
            if not is_allowed_asset_url(v_url):
                continue
            # The HEAD is only a cheap pre-filter for oversized files -- the
            # streaming loop below enforces the same 200 MB cap regardless.
            # A 5s timeout against Amazon's transcoding CDN routinely expired
            # on perfectly good videos and threw the candidate away, so a
            # slow or unsupported HEAD now falls through to the real download
            # instead of skipping the product's only footage.
            try:
                head = requests.head(v_url, timeout=(5, 15), allow_redirects=True)
                head_status = head.status_code
                head_length = int(head.headers.get("content-length", 0) or 0)
            except requests.RequestException as head_error:
                print(f"[INFO] HEAD check failed for {v_title} ({head_error}); trying the download anyway.")
                head_status, head_length = 200, 0
            if head_status == 200:
                if head_length > 200 * 1024 * 1024:
                    continue
                print(f"Downloading Video: {v_title}...")
                filepath = os.path.join(base_dir, f"{asin}_{v_title}.mp4")
                downloaded = 0
                # `with` guarantees the connection is closed on every exit
                # path, including the size-cap raise below -- previously the
                # response was never closed and, on the raise, the partially
                # written file was left on disk for a later glob to trip over.
                with requests.get(v_url, stream=True, timeout=(10, 60)) as r:
                    r.raise_for_status()
                    with open(filepath, 'wb') as f:
                        for chunk in r.iter_content(chunk_size=1024*1024):
                            downloaded += len(chunk)
                            if downloaded > 200 * 1024 * 1024:
                                raise ValueError("Product video exceeded 200 MB")
                            f.write(chunk)
                downloaded_video = filepath
                print(f"Saved Video: {filepath}")
                break # Only need the first one
        except (requests.RequestException, ValueError, OSError) as e:
            print(f"[WARN] Video candidate {v_url} failed: {e}")
            if filepath and os.path.exists(filepath) and downloaded_video != filepath:
                try: os.remove(filepath)
                except OSError: pass
            continue

    # Download images
    downloaded_images = []
    # Check video duration if exists
    video_dur = 0
    if downloaded_video:
        video_dur = get_audio_duration(downloaded_video)
        print(f"Product video duration: {video_dur:.2f}s")

    # Always download 8 images to support image-insertion every 7s for long videos
    max_images = 8
    print(f"Found {len(potential_images)} images. Downloading top {max_images} in parallel...")
    
    def download_img(idx_url):
        idx, img_url = idx_url
        fpath = None
        try:
            if not is_allowed_asset_url(img_url):
                return None
            with requests.get(img_url, stream=True, timeout=10) as r:
                if r.status_code == 200 and r.headers.get("content-type", "").lower().startswith("image/"):
                    fpath = os.path.join(base_dir, f"{asin}_img_{idx}.jpg")
                    downloaded = 0
                    with open(fpath, 'wb') as f:
                        for chunk in r.iter_content(chunk_size=1024*1024):
                            downloaded += len(chunk)
                            if downloaded > 15 * 1024 * 1024:
                                raise ValueError("Product image exceeded 15 MB")
                            f.write(chunk)
                    return fpath
        except (requests.RequestException, ValueError, OSError) as e:
            print(f"[WARN] Image {img_url} failed: {e}")
            if fpath and os.path.exists(fpath):
                try: os.remove(fpath)
                except OSError: pass
        return None

    # Use a small ThreadPool for image downloads
    with ThreadPoolExecutor(max_workers=max_images) as img_ex:
        img_results = list(img_ex.map(download_img, enumerate(potential_images[:max_images])))
    
    downloaded_images = [img for img in img_results if img]
    print(f"Downloaded {len(downloaded_images)} images.")

    return downloaded_video, downloaded_images, title, features

_LLM_PROVIDERS = ("gemini", "vertex_gemini", "openrouter", "openai", "deepseek", "longcat")


def _provider_config(provider):
    """(api_keys, default_model, endpoint) for a provider, from the globals
    load_settings_from_external() already populated.

    For "vertex_gemini" the single "api key" is actually a short-lived OAuth
    access token minted from VERTEX_SERVICE_ACCOUNT_JSON, and the endpoint is
    the full Vertex AI generateContent URL (model is baked into the URL path,
    not passed as a separate model field like the other providers)."""
    if provider == "gemini":
        return GEMINI_API_KEYS, GEMINI_MODEL, None
    if provider == "vertex_gemini":
        # Nothing saved at all -> Vertex simply isn't one of this user's
        # providers. Staying silent here matters: this runs for every entry
        # of the automatic fallback scan on every LLM call, so warning
        # unconditionally printed "Vertex AI auth failed" several times per
        # keyword at users who had never opened the Google Cloud section,
        # making an unrelated failure look like a Vertex problem.
        if not VERTEX_SERVICE_ACCOUNT_JSON and not VERTEX_PROJECT_ID:
            return [], VERTEX_LLM_MODEL, None
        try:
            import vertex_auth
            token = vertex_auth.get_access_token(VERTEX_SERVICE_ACCOUNT_JSON)
            url = vertex_auth.generate_content_url(VERTEX_PROJECT_ID, VERTEX_LOCATION, VERTEX_LLM_MODEL)
            return [token], VERTEX_LLM_MODEL, url
        except Exception as e:
            print(f"[WARN] Vertex AI auth failed, provider unavailable: {e}")
            return [], VERTEX_LLM_MODEL, None
    if provider == "openrouter":
        return OPENROUTER_API_KEYS, OPENROUTER_MODEL, None
    if provider == "openai":
        return OPENAI_API_KEYS, OPENAI_MODEL, None
    if provider == "deepseek":
        return DEEPSEEK_API_KEYS, DEEPSEEK_MODEL, DEEPSEEK_ENDPOINT or "https://api.deepseek.com/chat/completions"
    if provider == "longcat":
        return LONGCAT_API_KEYS, LONGCAT_MODEL, LONGCAT_ENDPOINT
    return [], "", None


def _build_llm_chain():
    """Thin wrapper over the shared llm_client.build_chain -- see there for
    the ordering rules. Kept as a named function because the tests and the
    call sites below read better against it."""
    return llm_client.build_chain(
        LLM_SERVICE,
        _provider_config,
        fallback_enabled=LLM_FALLBACK_ENABLED,
        chain_raw=LLM_CHAIN_RAW,
        order=_LLM_PROVIDERS,
    )


def call_llm_local(prompt):
    """Bridge to the shared llm_client (see llm_client.py), using the
    configured provider plus optional fallback chain. Same string-in/
    string-out contract as before: returns "" only once every configured
    provider/key has failed."""
    chain = _build_llm_chain()
    try:
        text, provider_used = llm_client.call_chain(prompt, chain, timeout=LLM_TIMEOUT_SECONDS)
        if provider_used != LLM_SERVICE:
            print(f"[LLM] Primary provider '{LLM_SERVICE}' failed; succeeded via fallback provider '{provider_used}'.")
        return text
    except llm_client.LLMCallError as e:
        print(f"[LLM][FATAL] All configured providers/keys failed: {e}")
        return ""

def rewrite_content_with_ai(original_title, features, is_single_asin=False, seo_keyword=""):
    print(f"Rewriting content with {LLM_SERVICE.upper()} {'(Single ASIN Mode)' if is_single_asin else ''}...")
    
    evidence = "\n".join(f"- {feature}" for feature in (features or []))
    has_hands_on_evidence = CONTENT_MODE == "hands_on" and bool(HANDS_ON_NOTES)
    review_label = (
        "hands-on review using the creator's supplied observations"
        if has_hands_on_evidence
        else "spec-based review/buying guide"
    )
    title_prompt = (
        f"TASK: Rewrite this Amazon product title into a professional English (US) title.\n"
        f"CRITICAL REQUIREMENTS:\n"
        f"1. OUTPUT MUST BE ENTIRELY IN AMERICAN ENGLISH (US) REGARDLESS OF THE INPUT LANGUAGE.\n"
        f"2. Short (under 10 words)\n"
        f"3. Catchy and Professional\n"
        f"4. Include BRAND and MODEL only when present in the original title; never invent either.\n"
        f"5. Output ONLY the rewritten title, no quotes, no markdown, no explanation.\n\n"
        f"ORIGINAL TITLE: {original_title}"
    )
    
    # Adjust length based on single or multi-ASIN mode
    is_truly_single = is_single_asin
    
    # FORCED SHORTS MODE LOGIC
    if SHORTS_MODE:
        if is_truly_single:
            word_count = "between 80 and 110 words"
            instruction = "Open with the buyer problem, give one limitation, then an honest verdict."
        else:
            word_count = "between 20 and 30 words"
            instruction = "Give the best use and one real tradeoff in two short sentences."
    else:
        # Normal YouTube Mode
        if is_truly_single:
            word_count = "between 220 and 300 words"
            instruction = """Provide a useful, balanced long-form product review.
CRITICAL STRUCTURE: You MUST start each section with its EXACT header on a new line. 
REQUIRED HEADERS:
Key Features
Performance
Pros & Cons
Final Verdict

Example:
Key Features
[Your detailed text here]

Performance
[Your detailed text here]

Do NOT use markdown bolding (like **Key Features**) or leading hashtags (like # Key Features). Just the plain header text."""
        else:
            # Tightened from 90-130. At the old length every product in a
            # 5-product video ran long enough that the whole video dragged;
            # the shorter budget forces the script to lead with what is
            # actually different about this product.
            word_count = "between 60 and 85 words"
            instruction = "Lead with what makes THIS product stand out, back it with a real strength, name one honest limitation, and land a quick verdict."
            
    keyword_hint = (
        f"\nSEARCH TERM VIEWERS USED TO FIND THIS VIDEO: {seo_keyword}"
        if seo_keyword else ""
    )
    desc_prompt_template = f"""TASK: Write a YouTube product review script for voice-over in American English (US).
PRODUCT: [TITLE]
FEATURES: [FEATURES]
REVIEW TYPE: {review_label}
CREATOR HANDS-ON NOTES: {HANDS_ON_NOTES if has_hands_on_evidence else "None supplied"}{keyword_hint}
GOAL: This script has to make a viewer want to look the product up. Be
concrete and useful -- specifics persuade, adjectives don't.

LENGTH REQUIREMENT: {word_count}
STYLE: {instruction}

CRITICAL RULES:
1. OUTPUT MUST BE ENTIRELY IN AMERICAN ENGLISH (US) REGARDLESS OF THE INPUT LANGUAGE.
2. DO NOT exceed the word count.
3. Use ONLY facts present in PRODUCT, FEATURES, or supplied hands-on notes. Never invent a specification, test result, price, award, warranty, or comparison.
4. Never say "I tested", "we tested", or imply hands-on use unless creator hands-on notes are supplied above.
5. Tone: natural, specific, balanced, and conversational; avoid generic hype.
6. Include a buyer-focused hook, best use, meaningful strengths, at least one limitation, who should avoid it, and an honest verdict.
7. No intro/outro mentions, links, markdown, or calls to buy.
8. Write the way someone actually talks, not like a written article -- this
   script is read aloud by a TTS voice, and formal written sentences sound
   robotic out loud. Specifically:
   - Use contractions (it's, you're, doesn't, that's) instead of formal forms.
   - Keep sentences short. Break up any sentence with more than one idea.
   - Use natural pauses and punctuation a person would actually speak with:
     commas, em dashes for a beat ("Here's the good news -- you don't need
     an expensive model"), and the occasional question to re-engage the
     listener ("So is it worth the price?").
   - Vary sentence openings; don't start every sentence the same way.
   - Energy: sound genuinely interested, like recommending something to a
     friend. Not an announcer, not a manual being read out.
9. BANNED OPENINGS. Every product in this video is written by you, and they
   currently all start the same way, which makes the finished video sound
   like a template. Do NOT begin with any of these, in any wording:
   - "Looking for ..." / "If you're looking for ..."
   - "Need ..." / "If you need ..." / "Need a reliable ..."
   - "Introducing ..." / "Meet the ..." / "Say hello to ..."
   - "When it comes to ..." / "In today's world ..." / "Are you tired of ..."
   Open instead on something concrete and specific to THIS product: the
   number that matters, the thing it does that others don't, the situation
   it wins in, or a blunt judgment.
   NOT: "Looking for a reliable tire inflator? This one has 150 PSI."
   YES: "150 PSI, and it shuts itself off at the pressure you set."
   YES: "This one earns its price on the cordless motor alone."
10. PARAGRAPH SHAPE (this controls the voice-over, so it matters):
   - Write in short paragraphs of 2 to 4 sentences.
   - Separate every paragraph with ONE blank line.
   - Each paragraph is voiced as its own audio beat, so make each one a
     single complete thought that can stand on its own -- do not split a
     sentence or an idea across a blank line.
11. OPEN WITH THE REASON THIS ONE IS WORTH IT. The very first sentence must
   state, in plain words, what this product beats the others at -- the
   single claim a viewer would repeat to a friend. Keep that claim itself
   between 3 and 10 words, built from a real spec or feature above.
   Examples of the claim: "the quietest one here", "150 PSI in under a
   minute", "the only cordless pick on this list".
12. NATURAL KEYWORD USE. Say the product category in full at least once,
   the way someone would search for it, and work in the closely related
   wording a buyer would use (the job it does, the place they'd use it,
   who it's for). Never list keywords or repeat a phrase mechanically --
   if a sentence reads like SEO, rewrite it.
13. NO calls to buy, no links, no prices, and no mention of the
   description or comments. A separate closing section handles all of
   that -- adding it here produces the pitch twice in one video.
14. Output ONLY the script text in plain US English.
"""

    # Parallelize LLM calls for title and description to save time
    with ThreadPoolExecutor(max_workers=2) as executor:
        # We submit both tasks at once
        f_title = executor.submit(call_llm_local, title_prompt)
        # We use original_title for the description prompt to allow parallelization (since new_title doesn't exist yet)
        desc_prompt_parallel = desc_prompt_template.replace("[TITLE]", original_title).replace("[FEATURES]", evidence or "No verified features supplied")
        f_desc = executor.submit(call_llm_local, desc_prompt_parallel)
        
        # Wait for both to complete
        raw_title = f_title.result()
        raw_desc = f_desc.result()

    # A missing title is a reasonable degrade -- fall back to the scraped title.
    new_title = raw_title.strip('"').strip('*').strip() or original_title
    print(f"AI Title Generated: {new_title}")

    new_desc = raw_desc.replace("*", "").strip()
    if not new_desc:
        # Previously this silently fell back to the literal string "Check out
        # this product!" as the ENTIRE narration script, and the video still
        # got built and uploaded with that as its only content. Every
        # configured LLM provider/key having failed is a real problem the
        # user needs to see -- raise so the existing per-ASIN failure
        # handling (added for the audio-bug fix) drops this ASIN instead of
        # shipping a near-empty video.
        raise RuntimeError(
            f"LLM failed to generate a product description for '{original_title}' -- "
            f"all configured provider(s)/key(s) failed (see [LLM][FATAL] above)."
        )

    # Strip anything that is scaffolding rather than narration: section
    # headings, markdown, "Here's the script:", stray "Number 3" labels.
    # Long-form single-ASIN reviews keep their section headers, which that
    # mode splits on to drive on-screen labels (and excludes from audio).
    new_desc = strip_script_artifacts(new_desc, keep_section_headers=is_single_asin)
    if not new_desc:
        raise RuntimeError(
            f"LLM returned only scaffolding (no narratable sentences) for '{original_title}'."
        )

    # Also catch cases where it's a very short first sentence like "Introduction. This is a..."
    first_sentence_match = re.search(r'^Introduction[\s\.\-:]+', new_desc, flags=re.IGNORECASE)
    if first_sentence_match:
        new_desc = new_desc[first_sentence_match.end():].strip()


    # SMART TRUNCATION FOR SHORTS MULTI-ASIN
    # If the AI fails to follow the word limit, we trim to the last full sentence within 12 words
    if SHORTS_MODE and not is_single_asin:
        words = new_desc.split()
        if len(words) > 32:
            print(f"[FIX] AI Over-generated ({len(words)} words). Truncating near 30 words.")
            truncated_raw = " ".join(words[:32])
            # Find the last period, exclamation, or question mark
            sentences = re.split(r'(?<=[.!?])\s+', truncated_raw)
            if len(sentences) > 1:
                # Remove the last partial sentence
                new_desc = " ".join(sentences[:-1])
                # Safety check if truncation left it too short
                if len(new_desc.split()) < 4:
                    new_desc = " ".join(words[:30]) + "."
            else:
                new_desc = " ".join(words[:30]) + "."
            
    print(f"AI Description Generated ({len(new_desc.split())} words)")

    return new_title, new_desc

import unicodedata

# Section headings and meta lines an LLM emits around a script. In
# single-ASIN mode the headers are consumed deliberately (they drive the
# on-screen section labels), but in multi-product mode nothing stripped them
# and they were narrated aloud as if they were prose.
_SCRIPT_HEADER_WORDS = (
    "key features", "features", "performance", "pros & cons", "pros and cons",
    "pros", "cons", "final verdict", "the verdict", "verdict", "conclusion",
    "introduction", "intro", "outro", "summary", "overview", "specs",
    "specification", "specifications", "key specification", "why this product",
    "who should buy", "who should skip", "bottom line", "hook",
)

# Lines that are the model talking about the task instead of performing it.
_SCRIPT_META_PREFIXES = (
    "director's note", "directors note", "director note", "script:", "style:",
    "tone:", "note:", "notes:", "word count", "narrate as", "narration:",
    "voice-over:", "voiceover:", "output:", "task:", "here is", "here's the",
    "sure,", "certainly,", "of course,", "as requested", "product:", "title:",
    "length requirement", "critical rules", "banned opening", "paragraph shape",
)


def strip_script_artifacts(text, keep_section_headers=False):
    """Remove everything from an LLM script that is not meant to be spoken.

    Reviews were shipping with the narrator reading section headings and
    prompt scaffolding out loud -- "Key Features", "Number 3", and in the
    Gemini TTS case the style descriptor itself. Some of that came from the
    TTS prompt (fixed in voice_config.build_gemini_tts_prompt) and some from
    the script generator emitting headings that only single-ASIN mode knew
    how to consume. This is the last line of defence for both: whatever
    reaches the voice should be sentences a person would actually say.

    `keep_section_headers` is required by single-ASIN long-form reviews,
    which deliberately split on "Key Features"/"Performance"/... to drive
    the on-screen section labels -- there the headers are structure, not
    stray text, and are already excluded from narration by that splitter.
    """
    if not text:
        return ""

    cleaned_lines = []
    for raw_line in str(text).splitlines():
        line = raw_line.strip()
        if not line:
            cleaned_lines.append("")  # preserve paragraph breaks -- they drive beats
            continue

        # Markdown scaffolding: **bold**, ## headings, bullets, numbered lists.
        line = re.sub(r"^\s*#{1,6}\s*", "", line)
        line = re.sub(r"^\s*[-*•]\s+", "", line)
        line = line.replace("**", "").replace("__", "")
        probe = line.strip().strip(":*#-–— ").strip()
        if not probe:
            continue

        lowered = probe.lower()
        if any(lowered.startswith(prefix) for prefix in _SCRIPT_META_PREFIXES):
            continue
        # A heading is a SHORT standalone line, not a sentence that happens to
        # begin with one of these words ("Performance is where it wins." must
        # survive). Requiring no terminal punctuation and few words keeps
        # real narration intact.
        if lowered.rstrip(":") in _SCRIPT_HEADER_WORDS and not probe.endswith((".", "!", "?")):
            if not keep_section_headers:
                continue
            cleaned_lines.append(probe)
            continue
        # "Number 3", "Product 3:", "#3", "3." on their own line -- the rank
        # is already announced by its own slide and narration.
        if re.fullmatch(r"(?:#\s*)?(?:number|product|pick|rank|no\.?)?\s*\d+\s*[.:)-]?", lowered):
            continue

        cleaned_lines.append(probe)

    # Collapse runs of blank lines so paragraph splitting stays predictable.
    out = re.sub(r"\n{3,}", "\n\n", "\n".join(cleaned_lines)).strip()
    return out


def sanitize_tts_text(text):
    """Sanitizes text for TTS by converting non-ASCII symbols/accents into clean US English ASCII text to prevent Edge-TTS language switching."""
    if not text:
        return ""
    # Normalize unicode characters
    text = unicodedata.normalize('NFKD', text)
    replacements = {
        '’': "'", '‘': "'", '`': "'",
        '“': '"', '”': '"',
        '–': '-', '—': '-',
        '™': '', '®': '', '©': '',
        '\xa0': ' ', '\t': ' '
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    # Strip any non-ASCII characters that confuse TTS language detectors
    text = text.encode('ascii', 'ignore').decode('ascii')
    unit_rules = (
        (r"(?i)\b(\d+(?:\.\d+)?)\s*gb\b", r"\1 gigabytes"),
        (r"(?i)\b(\d+(?:\.\d+)?)\s*tb\b", r"\1 terabytes"),
        (r"(?i)\b(\d+(?:\.\d+)?)\s*mah\b", r"\1 milliamp hours"),
        (r"(?i)\b(\d+(?:\.\d+)?)\s*hz\b", r"\1 hertz"),
        (r"(?i)\b(\d+(?:\.\d+)?)\s*lbs?\b", r"\1 pounds"),
        (r"(?i)\b(\d+(?:\.\d+)?)\s*oz\b", r"\1 ounces"),
    )
    for pattern, replacement in unit_rules:
        text = re.sub(pattern, replacement, text)
    text = text.replace("%", " percent").replace("&", " and ")
    text = re.sub(r'\s+', ' ', text).strip()
    return text


# =====================================================================
# TTS reliability layer
#
# Root cause of "sound missing in parts" with the free edge_tts voice:
# up to 10 ASIN worker threads each fire ALL of a product's paragraph
# TTS calls simultaneously (asyncio.gather), so a single-ASIN review can
# open 10-20 concurrent WebSocket connections to Microsoft's free
# endpoint at once. Microsoft throttles/resets, generate_tts() had no
# retry, and a `None` result silently became either a dropped paragraph
# (single-ASIN) or 5s of hard silence (multi-ASIN). This layer adds a
# cross-thread concurrency cap, chunking for long paragraphs, retry with
# backoff, and duration-based validation that catches truncated files
# (which the old `getsize > 100` check let through).
# =====================================================================

TTS_MAX_CONCURRENCY = int(os.environ.get("TTS_MAX_CONCURRENCY", "3"))
# BoundedSemaphore is shared correctly across threads (unlike asyncio.Semaphore,
# which is bound to whichever event loop created it and can't span the 10
# separate asyncio.run() loops used by the ASIN worker threads).
_TTS_GATE = threading.BoundedSemaphore(TTS_MAX_CONCURRENCY)


class _TtsSlot:
    """Async context manager around a threading.BoundedSemaphore. Polls with
    a non-blocking acquire + async sleep so it never stalls the event loop
    (a blocking acquire() would freeze the whole thread's asyncio.gather)."""
    async def __aenter__(self):
        while not _TTS_GATE.acquire(blocking=False):
            await asyncio.sleep(0.15 + random.uniform(0, 0.2))
        return self

    async def __aexit__(self, exc_type, exc, tb):
        _TTS_GATE.release()


# Per-run audio health tracking, printed at the end of each keyword so
# failures are visible in the live SSE log instead of vanishing silently.
_AUDIO_STATS_LOCK = threading.Lock()
_AUDIO_STATS = {"ok": 0, "retried": 0, "failed": []}


def reset_audio_stats():
    with _AUDIO_STATS_LOCK:
        _AUDIO_STATS["ok"] = 0
        _AUDIO_STATS["retried"] = 0
        _AUDIO_STATS["failed"] = []


def _record_audio_stat(label, attempts, success):
    with _AUDIO_STATS_LOCK:
        if success:
            _AUDIO_STATS["ok"] += 1
            if attempts > 1:
                _AUDIO_STATS["retried"] += 1
        else:
            _AUDIO_STATS["failed"].append(label)


def print_audio_health(keyword):
    with _AUDIO_STATS_LOCK:
        ok, retried, failed = _AUDIO_STATS["ok"], _AUDIO_STATS["retried"], list(_AUDIO_STATS["failed"])
    total = ok + len(failed)
    msg = f"[AUDIO][HEALTH] keyword={keyword} segments={total} ok={ok} retried={retried} failed={len(failed)}"
    if failed:
        msg += f" failed_labels={failed}"
    print(msg)


def _chunk_text_for_tts(text, max_chars=1200):
    """Split long text on sentence boundaries so a single TTS call never has
    to hold one giant WebSocket stream open (which is more likely to be
    throttled/reset mid-way). Keeps chunks under max_chars where possible."""
    text = text.strip()
    if len(text) <= max_chars:
        return [text]

    sentences = re.split(r'(?<=[.!?])\s+', text)
    chunks = []
    current = ""
    for sent in sentences:
        # Hard-split a single over-long sentence on commas as a last resort.
        if len(sent) > max_chars:
            pieces = sent.split(", ")
            sent_chunks, buf = [], ""
            for piece in pieces:
                candidate = f"{buf}, {piece}" if buf else piece
                if len(candidate) > max_chars and buf:
                    sent_chunks.append(buf)
                    buf = piece
                else:
                    buf = candidate
            if buf:
                sent_chunks.append(buf)
        else:
            sent_chunks = [sent]

        for sc in sent_chunks:
            candidate = f"{current} {sc}".strip() if current else sc
            if len(candidate) > max_chars and current:
                chunks.append(current)
                current = sc
            else:
                current = candidate
    if current:
        chunks.append(current)
    return chunks or [text]


def _short_title(title, limit=7):
    words = [w for w in str(title or "").split() if w]
    return " ".join(words[:limit]) if words else ""


def build_ai_intro_text(human_kw, processed, is_single):
    """LLM-written opener following the retention structure that actually
    works for a "best X" affiliate video: hook the buyer's problem, promise
    what the video delivers, reassure the list isn't random, then get out of
    the way fast.

    Opt-in (AI_INTRO_OUTRO). Returns "" if the model gives nothing usable,
    so the caller can fall back to the fixed template rather than ship an
    empty opener.
    """
    count = len(processed or [])
    names = [_short_title(p.get("title"), 6) for p in (processed or [])[:5]]
    prompt = f"""TASK: Write the spoken OPENING of a YouTube product review video.

TOPIC: {human_kw}
NUMBER OF PRODUCTS: {count}
PRODUCTS: {'; '.join(n for n in names if n) or 'not listed'}
FORMAT: {'single product review' if is_single else f'top {count} roundup'}

Write 45-70 words, in this order, with NO labels or headings:
1. A hook naming the viewer's actual problem or search, phrased as a
   question or a direct statement.
2. What this video gives them -- say what the picks were compared on
   (performance, features, ease of use, value).
3. One line making clear the list is considered, not random: whatever
   they specifically need, there's a pick here for them.
4. A short push straight into the list. Nothing else after it.

RULES:
- American English, spoken register, contractions, short sentences.
- Do NOT mention links, the description, prices, liking or subscribing.
- Do NOT invent specs, test results, awards or comparisons.
- No markdown, no numbering, no stage directions. Prose only.
- Output ONLY the words to be spoken."""
    text = strip_script_artifacts(call_llm_local(prompt))
    return " ".join(text.split()) if text else ""


def build_ai_conclusion_text(human_kw, processed, is_single):
    """LLM-written close: a real buying recommendation instead of a generic
    sign-off. Names a best overall / best value / best for a specific need,
    then the description CTA and an engagement ask.

    Opt-in (AI_INTRO_OUTRO); "" means the caller should fall back."""
    ranked = sorted(
        (p for p in (processed or []) if p.get("title")),
        key=lambda p: p.get("rank", 999),
    )
    listing = "\n".join(
        f"- rank {p.get('rank', '?')}: {_short_title(p.get('title'), 8)}"
        for p in ranked[:10]
    ) or "- (no products listed)"
    prompt = f"""TASK: Write the spoken CLOSING of a YouTube product review video.

TOPIC: {human_kw}
FORMAT: {'single product review' if is_single else 'multi-product roundup'}
PRODUCTS (rank 1 is the top pick):
{listing}

Write 45-75 words, in this order, with NO labels or headings:
1. A one-line "so which should you get?" turn.
2. {'A clear verdict on whether this one is worth buying, and who for.'
   if is_single else
   'Name the best overall pick, then a best-value pick, then one pick for a '
   'specific kind of buyer. Use the product names given above -- do not '
   'invent products or reorder the ranking.'}
3. Tell them links to every product are in the description so they can
   check current prices.
4. A short like-and-subscribe ask.

RULES:
- American English, spoken register, contractions, short sentences.
- Do NOT invent specs, prices, test results or awards.
- Do NOT state a price -- prices change; say to check the link instead.
- No markdown, no numbering, no stage directions. Prose only.
- Output ONLY the words to be spoken."""
    text = strip_script_artifacts(call_llm_local(prompt))
    return " ".join(text.split()) if text else ""


def build_conclusion_text(human_kw, processed, is_single, top_pick_title=None):
    """Closing narration: name a winner, then send the viewer to the links.

    The old outro was a bare "Check the links in description for the best
    prices." -- no recommendation, and the CTA arrived with no reason to act
    on it. A roundup that never says which one to buy leaves the viewer with
    nothing to click for, and the whole point of the video is the click.

    Structure: a verdict naming the top pick, then a price-check CTA. Prices
    move constantly on Amazon, so "check current price" is both the honest
    phrasing and the stronger reason to click than a price we cannot know.
    """
    def _shorten(title, limit=7):
        words = [w for w in str(title or "").split() if w]
        return " ".join(words[:limit]) if words else ""

    pick = _shorten(top_pick_title)

    if is_single:
        lead = f"So that's the {human_kw}."
        verdict = "If it fits what you need, it's an easy recommendation."
        cta = "Check the link below for the current price on Amazon."
        return f"{lead} {verdict} {cta}"

    if pick:
        verdict = f"If you want one pick, go with the {pick}."
    else:
        verdict = f"Any of these is a solid choice for {human_kw}."
    lead = f"That's our roundup for {human_kw}."
    cta = "Links are in the description -- tap through to check today's price on Amazon."
    return f"{lead} {verdict} {cta}"


def _voice_for_beat(index, default_voice=None):
    """Which voice narrates beat `index`.

    Off by default -- one narrator for the whole review, which is what a
    product review normally sounds like. Gemini TTS was already producing
    an unintended two-person read on its own (now pinned to a single
    narrator in build_gemini_tts_prompt); this makes the two-host format an
    explicit choice instead of a random one.

    Alternating per beat rather than using a provider's multi-speaker API
    means it works on every provider, and beats are already whole thoughts,
    so the hand-off lands on a sentence boundary instead of mid-idea.
    """
    if not DUAL_VOICE_ENABLED or not DUAL_VOICE_SECOND:
        return default_voice
    return DUAL_VOICE_SECOND if index % 2 else default_voice


def _apply_narration_speed(path, speed=None):
    """Nudge narration playback rate in place.

    TTS engines read at an even, measured pace that sounds sluggish next to
    how a review host actually talks. atempo resamples time without shifting
    pitch, so a small speed-up reads as "a bit more energetic" rather than
    chipmunked. Applied to narration only -- VIDEO_SPEED is a separate
    control that retimes the whole video.

    Best-effort: a failure here leaves the original audio untouched rather
    than losing a clip that synthesized perfectly well.
    """
    speed = float(NARRATION_SPEED if speed is None else speed)
    if abs(speed - 1.0) < 0.01 or not path or not os.path.exists(path):
        return path
    # atempo only accepts 0.5-2.0 per instance; the settings loader already
    # clamps to that range, so one filter is always enough.
    speed = max(0.5, min(2.0, speed))
    temp_path = f"{path}.speed.mp3"
    try:
        run_ffmpeg([
            "ffmpeg", "-y", "-i", path,
            "-filter:a", f"atempo={speed:.3f}",
            "-ar", str(AUDIO_SAMPLE_RATE), "-ac", "2", "-ab", "128k",
            temp_path,
        ])
        if os.path.exists(temp_path) and os.path.getsize(temp_path) > 100:
            os.replace(temp_path, path)
    except Exception as exc:
        print(f"[AUDIO][WARN] Narration speed-up skipped for {os.path.basename(path)}: {exc}")
    finally:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass
    return path


def _split_script_into_paragraphs(text, max_sentences=4, min_chars=25):
    """Break a narration script into short 2-4 sentence beats.

    A whole product review used to go to the voice provider as ONE call, so
    the engine picked a single emotion and pace and held it for the entire
    stretch -- the flat, obviously-synthetic delivery. Voicing each beat
    separately lets the provider re-set intonation per beat, and the tiny
    joins between them read as natural breaths.

    Prefers the blank-line paragraphs the script prompt asks for, and falls
    back to grouping sentences when the model ignores that.
    """
    text = (text or "").strip()
    if not text:
        return []

    paragraphs = [p.strip() for p in re.split(r'\n\s*\n', text) if p.strip()]
    if len(paragraphs) <= 1:
        paragraphs = [text]

    out = []
    for para in paragraphs:
        para = re.sub(r'\s+', ' ', para).strip()
        sentences = [s for s in re.split(r'(?<=[.!?])\s+', para) if s.strip()]
        # An authored paragraph is a beat boundary and is never merged into
        # the one before it -- that blank line is the writer saying "new
        # thought, re-set the delivery here". Only over-long paragraphs are
        # subdivided, and only those subdivisions can glue back together.
        first_of_para = True
        for i in range(0, len(sentences), max_sentences):
            beat = " ".join(sentences[i:i + max_sentences]).strip()
            if not beat:
                continue
            # A stray tail fragment from THIS paragraph ("Worth it.") is
            # glued back on rather than voiced as its own clip, which would
            # land as an abrupt, disconnected blurt.
            if out and len(beat) < min_chars and not first_of_para:
                out[-1] = f"{out[-1]} {beat}".strip()
            else:
                out.append(beat)
            first_of_para = False
    return out or [text]


def _product_has_audio(p):
    """False if any REQUIRED narration segment (a normal spoken paragraph,
    not a silent header marker) is missing or fails the sanity check --
    including a single failed beat out of an otherwise-successful set, so a
    review that loses one sentence to a transient TTS failure fails loudly
    instead of shipping with a chunk of narration quietly missing."""
    segs = p.get('audio_segments') or []
    required = [seg for seg in segs if not (len(seg) > 2 and seg[2])]
    if not required:
        return False
    for seg in required:
        path = seg[0]
        text = seg[1] if len(seg) > 1 else ""
        sane, reason = _audio_is_sane(path, text)
        if not sane:
            print(
                f"[AUDIO][FAIL] ASIN {p.get('asin', '?')} required "
                f"paragraph failed validation: {reason}"
            )
            return False
    return True


def _audio_is_sane(path, text):
    """Reject files that pass the old `getsize > 100` check but are actually
    truncated (a mid-stream disconnect leaves a valid-looking short mp3)."""
    if not path or not os.path.exists(path):
        return False, "file missing"
    if os.path.getsize(path) <= 100:
        return False, "file too small"
    dur = get_audio_duration(path)
    if dur <= 0.4:
        return False, f"duration too short ({dur:.2f}s)"
    word_count = max(1, len(text.split()))
    expected_min = (word_count / 2.6) * 0.55  # generous floor: ~2.6 wps, allow 45% slack
    if dur < expected_min:
        return False, f"duration {dur:.2f}s implausible for {word_count} words (expected >= {expected_min:.2f}s)"
    return True, "ok"


def _rendered_segment_has_narration(path, expected_duration, label):
    if not path or not os.path.exists(path):
        return False, "segment missing"
    try:
        probe = media_qc.probe_media(path, FFPROBE_BIN)
    except Exception as exc:
        return False, f"probe failed: {exc}"
    if not probe.get("hasAudio"):
        return False, "audio stream missing"
    duration = float(probe.get("duration") or 0)
    if duration <= 0:
        return False, "duration invalid"
    try:
        silences = media_qc.detect_silences(
            path,
            FFMPEG_BIN,
            noise_db=-42,
            minimum_seconds=0.8,
        )
    except Exception as exc:
        return False, f"silence detection failed: {exc}"
    longest = max((float(item.get("duration") or 0) for item in silences), default=0.0)
    if longest >= max(3.0, min(duration, expected_duration) * 0.80):
        return False, f"mostly silent rendered segment ({longest:.2f}s silence in {duration:.2f}s)"
    return True, "ok"


def _concat_mp3(parts, out_path):
    """Decode, normalize, and join independent TTS chunks without MP3
    demuxer timestamps or encoder padding accumulating at each boundary."""
    if len(parts) == 1:
        try:
            shutil.copyfile(parts[0], out_path)
            return True
        except Exception as e:
            print(f"[AUDIO] Failed to copy single TTS chunk: {e}")
            return False

    try:
        inputs = []
        filters = []
        for idx, part in enumerate(parts):
            inputs.extend(["-i", part])
            filters.append(
                f"[{idx}:a]aresample={AUDIO_SAMPLE_RATE}:async=1:first_pts=0,"
                f"aformat=sample_fmts=fltp:channel_layouts=stereo[a{idx}]"
            )
        joined = "".join(f"[a{idx}]" for idx in range(len(parts)))
        filters.append(f"{joined}concat=n={len(parts)}:v=0:a=1[aout]")
        run_ffmpeg(
            ["ffmpeg", "-y"]
            + inputs
            + [
                "-filter_complex", ";".join(filters),
                "-map", "[aout]",
                "-c:a", "libmp3lame", "-ar", str(AUDIO_SAMPLE_RATE),
                "-ac", "2", "-ab", "128k", out_path,
            ]
        )
        return True
    except Exception as e:
        print(f"[AUDIO] Failed to concat {len(parts)} TTS chunks: {e}")
        return False


# --- Kokoro TTS (local, offline, free) ---
# Loaded lazily so importing this module doesn't pull in torch for users who
# never select Kokoro. Synthesis is serialized (not just concurrency-capped
# by _TTS_GATE) since a single KPipeline instance is shared across worker
# threads and this is a CPU-bound model, not a network call.
_KOKORO_PIPELINE = None
_KOKORO_INIT_LOCK = threading.Lock()
_KOKORO_SYNTH_LOCK = threading.Lock()


def _get_kokoro_pipeline():
    global _KOKORO_PIPELINE
    if _KOKORO_PIPELINE is None:
        with _KOKORO_INIT_LOCK:
            if _KOKORO_PIPELINE is None:
                from kokoro import KModel, KPipeline
                print("[SYSTEM] Loading Kokoro TTS model (first use only, ~1-2 min)...")
                bundled = kokoro_files("af_heart")
                model = None
                if bundled["complete"]:
                    model = KModel(
                        repo_id="hexgrad/Kokoro-82M",
                        config=str(bundled["config"]),
                        model=str(bundled["model"]),
                    )
                _KOKORO_PIPELINE = KPipeline(
                    lang_code='a', repo_id="hexgrad/Kokoro-82M", model=model or True
                )
    return _KOKORO_PIPELINE


def _kokoro_synthesize(text, output_path, voice):
    """Blocking; must be called via run_in_executor. Writes directly to
    output_path (mp3), matching every other provider's contract."""
    import soundfile as sf
    import numpy as np
    with _KOKORO_SYNTH_LOCK:
        pipeline = _get_kokoro_pipeline()
        bundled = kokoro_files(voice)
        voice_source = str(bundled["voice"]) if bundled["voice"].is_file() else voice
        chunks = [audio for _, _, audio in pipeline(text, voice=voice_source)]
    if not chunks:
        raise RuntimeError("Kokoro produced no audio")
    full_audio = np.concatenate(chunks) if len(chunks) > 1 else chunks[0]
    wav_path = output_path + ".wav"
    sf.write(wav_path, full_audio, 24000)
    try:
        run_ffmpeg([
            "ffmpeg", "-y", "-i", wav_path,
            "-af", f"aresample={AUDIO_SAMPLE_RATE}:async=1:first_pts=0",
            "-c:a", "libmp3lame", "-ar", str(AUDIO_SAMPLE_RATE),
            "-ac", "2", "-ab", "128k", output_path,
        ])
    finally:
        if os.path.exists(wav_path):
            try: os.remove(wav_path)
            except Exception: pass


def _current_tts_config(voice=None):
    """Builds the config dict tts_engine.py expects, from this process's live
    settings. This is the single place that turns the module-level TTS_*
    globals into the shape the shared engine (also used by the /preview_tts
    route) understands -- previously the render path and the preview route
    each read these globals/settings independently and had already drifted.
    """
    return {
        "service": TTS_SERVICE,
        "voice": voice,
        "edge_voice": EDGE_VOICE,
        "edge_rate": EDGE_RATE,
        "edge_pitch": EDGE_PITCH,
        "kokoro_voice": KOKORO_VOICE,
        "elevenlabs_api_key": ELEVENLABS_API_KEY,
        "elevenlabs_voice_id": ELEVENLABS_VOICE_ID,
        "elevenlabs_model_id": ELEVENLABS_MODEL_ID,
        "cartesia_api_key": CARTESIA_API_KEY,
        "cartesia_voice_id": CARTESIA_VOICE_ID,
        "cartesia_model_id": CARTESIA_MODEL_ID,
        "ai33pro_api_key": AI33PRO_API_KEY,
        "ai33pro_voice_id": AI33PRO_VOICE_ID,
        "ai33pro_model_id": AI33PRO_MODEL_ID,
        "deepgram_api_key": DEEPGRAM_API_KEY,
        "deepgram_voice_id": DEEPGRAM_VOICE_ID,
        "deepgram_model_id": DEEPGRAM_MODEL_ID,
        "google_tts_voice_id": GOOGLE_TTS_VOICE_ID,
        "google_tts_monthly_char_limit": GOOGLE_TTS_MONTHLY_CHAR_LIMIT,
        # Gemini TTS rotates through every configured key (matching the LLM
        # path); v6 used GEMINI_API_KEYS[0] only, so a rate-limited first key
        # failed the whole render with the rest of the keys sitting unused.
        "gemini_api_key": "\n".join(GEMINI_API_KEYS),
        "gemini_tts_model": GEMINI_TTS_MODEL,
        "gemini_tts_voice": voice or GEMINI_TTS_VOICE,
        "gemini_voice_style": GEMINI_VOICE_STYLE,
        "gemini_voice_pace": GEMINI_VOICE_PACE,
        "gemini_voice_energy": GEMINI_VOICE_ENERGY,
        "gemini_voice_warmth": GEMINI_VOICE_WARMTH,
        "gemini_voice_accent": GEMINI_VOICE_ACCENT,
        "gemini_voice_instruction": GEMINI_VOICE_INSTRUCTION,
        "gemini_pronunciations": GEMINI_PRONUNCIATIONS,
        "vertex_project_id": VERTEX_PROJECT_ID,
        "vertex_location": VERTEX_LOCATION,
        "vertex_service_account_private_key": VERTEX_SERVICE_ACCOUNT_JSON,
        "vertex_tts_model": VERTEX_TTS_MODEL,
    }


_TTS_PROVIDERS = ("edge", "kokoro", "gemini", "vertex_gemini", "elevenlabs", "cartesia", "ai33pro", "deepgram", "google_cloud_tts")
# Providers that need an actual saved credential -- edge/kokoro are
# deliberately excluded here (see _build_tts_chain): they always report
# "has credentials" (neither needs one), so including them in the
# auto-fallback scan would auto-insert them ahead of a paid provider the
# user actually configured, and Edge is already guaranteed as the final
# entry regardless.
_TTS_CREDENTIALED_PROVIDERS = ("gemini", "vertex_gemini", "elevenlabs", "cartesia", "ai33pro", "deepgram", "google_cloud_tts")


def _tts_provider_has_credentials(provider):
    """Whether `provider` is actually usable right now -- mirrors
    _provider_config's `if keys:` gate on the LLM side, so the automatic
    fallback below never wastes an attempt on a provider with nothing
    configured."""
    if provider in ("edge", "kokoro"):
        return True  # no key/credential needed
    if provider == "gemini":
        return bool(GEMINI_API_KEYS)
    if provider == "vertex_gemini":
        return bool(VERTEX_SERVICE_ACCOUNT_JSON and VERTEX_PROJECT_ID)
    if provider == "elevenlabs":
        return bool(ELEVENLABS_API_KEY)
    if provider == "cartesia":
        return bool(CARTESIA_API_KEY)
    if provider == "ai33pro":
        return bool(AI33PRO_API_KEY)
    if provider == "deepgram":
        return bool(DEEPGRAM_API_KEY)
    if provider == "google_cloud_tts":
        return bool(VERTEX_SERVICE_ACCOUNT_JSON and VERTEX_PROJECT_ID)
    return False


# Once a fallback provider has actually produced audio for this video, it is
# pinned as the voice for the REST of that video. Narration is synthesized as
# many separate beats (plus intro/outro/rank clips), each falling back
# independently -- so an intermittent primary failure (a rate limit, one
# dropped connection) used to voice some beats in the primary voice and
# others in a fallback voice, and the finished video audibly switched
# between two or three different narrators partway through. One consistent
# voice matters more than using the "best" provider for a few segments.
_PINNED_TTS_PROVIDER = None
_PINNED_TTS_LOCK = threading.Lock()


def reset_tts_provider_pin():
    """Called once per keyword, so a transient failure while rendering one
    video doesn't permanently downgrade every later video in the batch."""
    global _PINNED_TTS_PROVIDER
    with _PINNED_TTS_LOCK:
        _PINNED_TTS_PROVIDER = None


def _pin_tts_provider(provider):
    global _PINNED_TTS_PROVIDER
    with _PINNED_TTS_LOCK:
        if _PINNED_TTS_PROVIDER == provider:
            return
        _PINNED_TTS_PROVIDER = provider
    print(
        f"[VOICE] Pinning '{provider}' for the rest of this video so every "
        f"segment keeps the same narrator."
    )


def _pinned_tts_provider():
    with _PINNED_TTS_LOCK:
        return _PINNED_TTS_PROVIDER


def _build_tts_chain():
    """Primary provider (TTS_SERVICE) first, then:
    1. If opted in via TTS_FALLBACK_ENABLED, the user's own drag-ordered
       TTS_CHAIN_RAW (one provider id per line -- see the Settings UI).
    2. Automatically, every other CREDENTIALED provider that already has a
       saved key, in _TTS_CREDENTIALED_PROVIDERS order (edge/kokoro need no
       key, so they are not auto-inserted here -- see step 3).
    3. Edge TTS is always the final entry regardless of the above -- free,
       no key, and about as close to "always available" as an HTTP call
       gets, so a voice segment is never fully blocked."""
    primary = TTS_SERVICE if TTS_SERVICE in _TTS_PROVIDERS else "edge"
    seen = {primary}
    chain = [primary]

    if TTS_FALLBACK_ENABLED and TTS_CHAIN_RAW:
        for line in TTS_CHAIN_RAW.split('\n'):
            prov = line.strip()
            if prov and prov not in seen and prov in _TTS_PROVIDERS:
                seen.add(prov)
                chain.append(prov)

    for prov in _TTS_CREDENTIALED_PROVIDERS:
        if prov not in seen and _tts_provider_has_credentials(prov):
            seen.add(prov)
            chain.append(prov)

    if "edge" not in seen:
        chain.append("edge")
    return chain


_PERMANENT_TTS_ERROR_MARKERS = (
    "api key is required",
    "key is required",
    "not configured",
    "no vertex ai",
    "monthly character cap",
    "http 401",
    "401",
    "http 403",
    "403",
    "invalid api key",
    "unauthorized",
)


def _tts_error_is_permanent(exc):
    """Whether retrying this provider within the same run is pointless.

    A missing/rejected credential or a hit quota cap fails identically every
    time, so re-walking it on all four _tts_with_retry attempts just adds
    latency to a render that is already in trouble. Rate limits, timeouts
    and 5xx are deliberately NOT matched here -- those are exactly the cases
    a retry exists for.
    """
    message = str(exc).lower()
    return any(marker in message for marker in _PERMANENT_TTS_ERROR_MARKERS)


async def _tts_provider_once(text, output_path, voice=None, unusable=None):
    """Tries _build_tts_chain() in order, returning on the first provider
    that produces output. `voice` (a per-call override, e.g. a rank-slide
    voice pick) only applies to the PRIMARY provider -- a voice id from one
    provider's catalog is meaningless to a different provider's, so a
    fallback attempt uses that provider's own configured default voice
    instead.

    HTTP-based providers (edge/elevenlabs/cartesia/gemini/vertex_gemini/
    ai33pro/deepgram/google_cloud_tts) delegate to web_app/tts_engine.py -- the same module the
    /preview_tts route uses -- so there is exactly one implementation of
    "how to talk to provider X" instead of two that can silently drift
    apart. Kokoro is the one exception: this process (launched as __main__
    by the SSE render route) owns the only warm Kokoro pipeline, so it
    always uses the local _kokoro_synthesize directly rather than routing
    through tts_engine, which would re-import this file under a second
    module identity (__main__ vs amazon_video_maker) and end up with two
    unsynchronized pipeline locks.

    Raises the last provider's exception if every entry in the chain fails
    (Edge, always the final entry, would have to itself be unreachable).
    """
    chain = _build_tts_chain()
    if unusable:
        # Providers already proven unusable this run (bad/missing credential,
        # quota cap) are skipped instead of re-failing identically on every
        # retry. Never drop the whole chain, though -- if that filter would
        # empty it, fall through to the original chain so the caller still
        # gets a real error rather than "no provider available".
        filtered = [p for p in chain if p not in unusable]
        chain = filtered or chain
    pinned = _pinned_tts_provider()
    if pinned and pinned in chain:
        # An earlier segment of this same video already fell back to this
        # provider -- lead with it so the whole video keeps one narrator
        # instead of alternating whenever the primary recovers.
        chain = [pinned] + [p for p in chain if p != pinned]
    last_err = None
    for i, provider in enumerate(chain):
        # "Primary" means the provider the user actually configured, NOT
        # merely first in the chain: the per-call `voice` override is a
        # voice id from THAT provider's catalogue and is meaningless to any
        # other provider, so pinning a fallback to the front must not start
        # handing it a foreign voice id.
        is_primary = provider == TTS_SERVICE
        try:
            if provider == "kokoro":
                v_id = (voice if is_primary else None) or KOKORO_VOICE
                await asyncio.get_running_loop().run_in_executor(
                    None, _kokoro_synthesize, text, output_path, v_id
                )
            elif provider == "edge":
                communicate = edge_tts.Communicate(
                    text, EDGE_VOICE, rate=EDGE_RATE, pitch=EDGE_PITCH
                )
                await communicate.save(output_path)
            else:
                config = _current_tts_config(voice if is_primary else None)
                config["service"] = provider
                await asyncio.get_running_loop().run_in_executor(
                    None, lambda c=config: tts_engine.synthesize(text, output_path, c, ffmpeg_bin=FFMPEG_BIN)
                )
            if provider != TTS_SERVICE:
                _pin_tts_provider(provider)
            return
        except Exception as e:
            last_err = e
            if unusable is not None and _tts_error_is_permanent(e):
                unusable.add(provider)
            if i < len(chain) - 1:
                print(f"{provider} TTS Error: {e}")
                print(f"[FALLBACK] Switching to {chain[i + 1]} TTS for this segment...")
            continue
    raise last_err or RuntimeError("No TTS provider available")


async def _tts_with_retry(text, output_path, voice, label, attempts=4):
    """Runs one chunk of text through the TTS provider, retrying with
    backoff+jitter under the concurrency gate, and validating the result
    with _audio_is_sane (duration-based, catches truncated files)."""
    cache_root = DATA_DIR / "tts_cache"
    cache_root.mkdir(parents=True, exist_ok=True)
    # tts_engine.cache_key() resolves the *effective* voice/model/director
    # settings (not just the `voice` override, which is usually None here),
    # so a settings change actually busts the cache. See TTS_CACHE_VERSION.
    tts_config = _current_tts_config(voice)
    cache_key = hashlib.sha256(
        json.dumps(
            {
                "pipeline_version": TTS_CACHE_VERSION,
                "key": tts_engine.cache_key(text, tts_config),
                # Part of the identity: cached audio is stored already
                # sped-up, so changing the speed has to miss the cache.
                "narration_speed": round(float(NARRATION_SPEED), 3),
            },
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    cache_path = cache_root / f"{cache_key}.mp3"
    cache_ok, _ = _audio_is_sane(str(cache_path), text)
    if cache_ok:
        shutil.copyfile(cache_path, output_path)
        _record_audio_stat(label, 1, True)
        print(f"[AUDIO][CACHE] {label}")
        return output_path

    last_reason = "unknown"
    # Scoped to this chunk: a provider whose credential is missing/rejected
    # is skipped on the remaining attempts instead of failing identically
    # four times over.
    unusable = set()
    for attempt in range(1, attempts + 1):
        try:
            print(f"[AUDIO][START] {label} attempt {attempt}/{attempts}")
            async with _TtsSlot():
                await _tts_provider_once(text, output_path, voice, unusable=unusable)
        except Exception as e:
            last_reason = f"exception: {e}"
        else:
            # Speed up before validating and caching, so the sanity check
            # measures what actually ships and the cache stores the final
            # audio rather than something that needs re-processing on a hit.
            _apply_narration_speed(output_path)
            ok, reason = _audio_is_sane(output_path, text)
            if ok:
                temp_cache = cache_root / f".{cache_key}.{threading.get_ident()}.tmp"
                try:
                    shutil.copyfile(output_path, temp_cache)
                    os.replace(temp_cache, cache_path)
                    cache_path.chmod(0o600)
                except OSError:
                    try:
                        temp_cache.unlink()
                    except OSError:
                        pass
                _record_audio_stat(label, attempt, True)
                print(f"[AUDIO][OK] {label}")
                return output_path
            last_reason = reason
            if os.path.exists(output_path):
                try: os.remove(output_path)
                except Exception: pass

        if attempt < attempts:
            backoff = min(8.0, 1.5 * (2 ** (attempt - 1))) + random.uniform(0, 0.75)
            print(f"[AUDIO][RETRY {attempt}/{attempts}] {label}: {last_reason} -- retrying in {backoff:.1f}s")
            await asyncio.sleep(backoff)

    print(f"[AUDIO][FAIL] {label}: giving up after {attempts} attempts ({last_reason})")
    _record_audio_stat(label, attempts, False)
    return None


async def generate_tts(text, output_path, voice=None, label=None):
    """Generates TTS audio and verifies it's not empty/truncated.
    Same path-or-None contract as before; label is optional (derived from the
    output filename if not given) and only used for logging/health stats."""
    if not text or not text.strip():
        return None
    text = sanitize_tts_text(text)
    if not text:
        return None
    if not label:
        label = os.path.splitext(os.path.basename(output_path))[0]

    # Smaller Kokoro calls avoid long-utterance quality degradation. Its own
    # tokenizer also splits phonemes, but explicit sentence-safe chunks keep
    # synthesis and cache units short and independently verifiable.
    max_chars = 420 if TTS_SERVICE == "kokoro" else 1200
    chunks = _chunk_text_for_tts(text, max_chars=max_chars)
    if len(chunks) == 1:
        return await _tts_with_retry(chunks[0], output_path, voice, label)

    # Long paragraph: generate each chunk sequentially (chunks of the SAME
    # paragraph must not compete with each other for the concurrency gate)
    # into its own temp file, then concat them into the final output_path.
    chunk_paths = []
    for i, chunk in enumerate(chunks):
        chunk_path = f"{output_path}_chunk{i}.mp3"
        result = await _tts_with_retry(chunk, chunk_path, voice, f"{label}#{i+1}/{len(chunks)}")
        if not result:
            # Clean up whatever chunks did succeed before giving up.
            for cp in chunk_paths:
                if os.path.exists(cp):
                    try: os.remove(cp)
                    except Exception: pass
            return None
        chunk_paths.append(result)

    ok = _concat_mp3(chunk_paths, output_path)
    for cp in chunk_paths:
        if os.path.exists(cp):
            try: os.remove(cp)
            except Exception: pass

    if not ok:
        return None
    sane, reason = _audio_is_sane(output_path, text)
    if not sane:
        print(f"[AUDIO][FAIL] {label}: concatenated audio failed sanity check ({reason})")
        if os.path.exists(output_path):
            try: os.remove(output_path)
            except Exception: pass
        return None
    return output_path


def process_single_asin(asin, keyword, keyword_dir, is_single_asin=False):
    if not asin: return None
    print(f"Processing {asin}...")
    asin_dir = os.path.join(keyword_dir, asin)
    if not os.path.exists(asin_dir): os.makedirs(asin_dir)

    vid, imgs, title, feats = download_assets(asin, base_dir=asin_dir)
    if not title:
        # Scrape failed (blocked, dead ASIN, CAPTCHA/interstitial). Previously
        # this fell through with title=None and prompted the LLM with
        # "ORIGINAL TITLE: None", which happily wrote a review for a product
        # that was never actually fetched. Skip this ASIN instead.
        print(f"[SKIP] {asin}: could not fetch product data, skipping this ASIN.")
        return None

    # De-hyphenated slug -- the phrase viewers actually searched, so the
    # script can use that wording naturally instead of inventing its own.
    seo_keyword = title_case(str(keyword or "").replace("_", " ").replace("-", " "))
    new_title, desc = rewrite_content_with_ai(
        title, feats, is_single_asin=is_single_asin, seo_keyword=seo_keyword
    )
    
    t_aud = os.path.join(asin_dir, "title.mp3")
    
    # Run TTS in parallel for both title and description
    async def run_product_tts():
        # NORMAL MODE SINGLE ASIN: Split description by headers and generate separate audio for each paragraph
        if not SHORTS_MODE and is_single_asin:
            import re
            
            # First, sanitize description to remove common markdown artifacts
            # (asterisks/hashes from the LLM's markdown-flavored output) so
            # the split below doesn't have to fight them.
            desc_clean_total = desc.strip().replace("*", "").replace("#", "")

            # We add a fallback split if AI doesn't use newlines but uses bold/hashes
            full_pattern = r'(\n?(?:\*\*|#+ )?(?:Key Features|Performance|Pros & Cons|Pros and Cons|Final Verdict|The Verdict|Conclusion|Introduction|Key Specification|Specification)[\s:*#\-]*(?:\*\*|:)?(?:\n|$))'
            parts = re.split(full_pattern, desc_clean_total, flags=re.IGNORECASE)
            
            audio_tasks = []
            segment_data = [] 
            
            valid_headers = ["Key Features", "Performance", "Pros & Cons", "Pros and Cons", "Final Verdict", "The Verdict", "Conclusion", "Introduction", "Key Specification", "Specification"]
            
            # Start with INTRODUCTION by default
            last_header_found = "INTRODUCTION"
            has_seen_any_header_yet = False
            
            for p in parts:
                p_text = p.strip()
                if not p_text: continue
                
                # Check if this specific part is a header
                p_clean = p_text.replace("*", "").replace("#", "").strip(": ").strip()
                is_header = any(h.lower() == p_clean.lower() for h in valid_headers)
                
                if is_header:
                    has_seen_any_header_yet = True
                    last_header_found = p_clean.upper()
                    # Headers only change the label, they don't get an audio segment
                    segment_data.append((p_clean.upper(), True, None, last_header_found))
                else:
                    # Normal text paragraph (voiced)
                    idx = len(audio_tasks)
                    path = os.path.join(asin_dir, f"desc_part_{idx}.mp3")
                    audio_tasks.append(generate_tts(p_text, path))
                    # Each text segment gets the 'active' label (defaults to INTRODUCTION at start)
                    segment_data.append((p_text, False, idx, last_header_found))

            # Generate all TTS
            d_audios_res = await asyncio.gather(*audio_tasks)
            t_res = None
            
            # Re-encode all audio segments to ensure same sample rate/bitrate for concat
            # This fixes the low/normal volume issues when mixing AI33 and EdgeTTS (if any)
            # or even between segments.
            print(f"[SYSTEM] Normalizing audio for ASIN {asin}...")
            for i, res in enumerate(d_audios_res):
                if res and os.path.exists(res):
                    norm_path = res + "_norm.mp3"
                    try:
                        # Re-encode to standard 44.1kHz, 128k, Stereo
                        run_ffmpeg([
                            "ffmpeg", "-y", "-i", res,
                            "-af", f"aresample={AUDIO_SAMPLE_RATE}:async=1:first_pts=0",
                            "-ar", str(AUDIO_SAMPLE_RATE), "-ac", "2",
                            "-ab", "128k", norm_path,
                        ])
                        os.replace(norm_path, res)
                    except Exception as e:
                        print(f"[AUDIO][WARN] Normalization failed for {os.path.basename(res)}, keeping original: {e}")

            # Re-map correctly
            final_map = []
            for x in segment_data:
                # x struct: (text, is_header, audio_idx, active_label)
                label = x[3]
                is_hdr = x[1]
                
                if is_hdr:
                    # Header rows themselves have no audio file
                    final_map.append((None, x[0], True, label))
                else:
                    # Body text rows get their audio path
                    aud_idx = x[2]
                    aud_path = d_audios_res[aud_idx] if aud_idx < len(d_audios_res) else None
                    final_map.append((aud_path, x[0], False, label))
            
            return t_res, final_map
        
        else:
            # Default handling for Multi-ASIN or Shorts
            needs_title_audio = SHORTS_MODE and not is_single_asin
            # The whole review used to be one TTS call, which locked the
            # provider into a single emotion and pace for its entire length.
            # Voicing it as 2-4 sentence beats lets intonation re-set per
            # beat and puts a natural breath at each join.
            beats = _split_script_into_paragraphs(desc)
            voice_override = AI33PRO_VOICE_ID if (TTS_SERVICE == "ai33pro" and AI33PRO_API_KEY) else None
            beat_paths = [
                os.path.join(asin_dir, f"desc_beat_{i}.mp3") for i in range(len(beats))
            ]
            beat_results = await asyncio.gather(*[
                generate_tts(beat, path, _voice_for_beat(i, voice_override))
                for i, (beat, path) in enumerate(zip(beats, beat_paths))
            ])
            # Title narration is used only in multi-product Shorts. Avoid an
            # otherwise unused provider call in long-form and single reviews.
            t_res = (
                await generate_tts(new_title, t_aud, voice_override)
                if needs_title_audio else None
            )

            # Re-encode for volume/codec consistency if they exist
            for r in [t_res, *beat_results]:
                if r and os.path.exists(r):
                    norm_path = r + "_norm.mp3"
                    try:
                        run_ffmpeg([
                            "ffmpeg", "-y", "-i", r,
                            "-af", f"aresample={AUDIO_SAMPLE_RATE}:async=1:first_pts=0",
                            "-ar", str(AUDIO_SAMPLE_RATE), "-ac", "2",
                            "-ab", "128k", norm_path,
                        ])
                        os.replace(norm_path, r)
                    except Exception as e:
                        print(f"[AUDIO][WARN] Normalization failed for {os.path.basename(r)}, keeping original: {e}")

            # A failed beat is kept in the list with a None path rather than
            # dropped. Dropping it would silently ship a video missing
            # whatever that beat said -- a worse failure than the old
            # single-call path, which failed the WHOLE description (and
            # aborted the keyword via _product_has_audio below) the moment
            # any one chunk came back empty. Keeping the None entry routes a
            # beat failure through that same existing guard instead of
            # inventing a quieter, partial-content failure mode.
            beat_segments = [
                (path, beat, False)
                for path, beat in zip(beat_results, beats)
            ] or [(None, desc, False)]

            # IF SHORTS MODE and MULTI ASIN: Prepend the product title narration
            # The title.mp3 (t_res) is already generated above.
            if SHORTS_MODE and not is_single_asin:
                return t_res, [(t_res, new_title, False), *beat_segments]

            return t_res, beat_segments
    
    t_res, segment_audio_info = asyncio.run(run_product_tts())
    
    # Structure the product data
    product_data = {
        'asin': asin,
        'video': vid,
        'images': imgs,
        'title': new_title,
        'description': desc,
        'features': feats,
        'audio_segments': segment_audio_info # [(path, text, is_header, label), ...]
    }
    
    return product_data

async def main_pipeline():
    print(">>> Starting Amazon Video Maker Pipeline <<<")
    
    # --- Quota Check ---
    quota = "unlimited"
    used_count_arg = 0
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--quota", help="Video creation quota", default="unlimited")
    parser.add_argument("--used", help="Current used count", type=int, default=0)
    parser.add_argument("--shorts", action="store_true", help="Enable 9:16 Shorts Mode")
    args, unknown = parser.parse_known_args()
    quota = args.quota
    used_count_arg = args.used
    global SHORTS_MODE
    if args.shorts:
        SHORTS_MODE = True
        print("[SYSTEM] Shorts Mode (9:16) Enabled by CLI!")
    else:
        # Explicitly set to False if not passed via CLI to ensure global availability
        SHORTS_MODE = SHORTS_MODE if 'SHORTS_MODE' in globals() else False

    settings_path = str(PRIVATE_SETTINGS_FILE)
    try:
        with open(settings_path, "r", encoding="utf-8") as handle:
            run_settings = json.load(handle)
    except (OSError, ValueError):
        run_settings = {}
    product_order = run_settings.get("product_order", "countdown")
    if product_order not in ("list", "countdown"):
        product_order = "countdown"
    default_output_root = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "files_created",
    )
    try:
        output_root = str(
            validate_output_root(run_settings.get("output_root") or default_output_root)
        )
    except ValueError as exc:
        print(f"[WARN] Unsafe output_root ignored: {exc}")
        output_root = default_output_root
    
    # Track count internally instead of using mykota.dat
    internal_used_count = used_count_arg

    def get_current_count():
        return internal_used_count

    file_path = str(KEYWORDS_FILE)
    
    if not os.path.exists(file_path):
        print(f"[ERROR] {file_path} not found!")
        return
        
    with open(file_path, "r", encoding="utf-8") as f:
        lines = [l.strip() for l in f if l.strip()]

    recent_music_ids = []
    for line in lines:
        parts = [p.strip() for p in line.split(",")]
        keyword = slugify(parts[0], fallback="")
        if not keyword:
            print(f"[FATAL] Invalid or empty keyword in line: {line!r}")
            continue
        # Each video gets a fresh shot at the configured provider; the pin
        # only guarantees consistency WITHIN one video.
        reset_tts_provider_pin()
        raw_asins = [a.upper() for a in parts[1:] if re.fullmatch(r"[A-Za-z0-9]{10}", a)]
        # A repeated ASIN (typo, duplicate paste, or the same product
        # appearing twice on an Amazon results page) was never deduplicated
        # before being handed to the worker pool -- the exact same product
        # got scraped, scripted, and rendered twice as two separate segments
        # in one video. dict.fromkeys keeps first-seen order.
        asins = list(dict.fromkeys(raw_asins))
        if len(asins) < len(raw_asins):
            dupes = [a for a in dict.fromkeys(raw_asins) if raw_asins.count(a) > 1]
            print(f"[FIX] '{keyword}': removed {len(raw_asins) - len(asins)} duplicate ASIN(s): {', '.join(dupes)}")
        if not asins:
            print(f"[FATAL] No valid 10-character ASINs for '{keyword}'.")
            continue
        
        # Check quota before processing
        current_count = get_current_count()
        print(f"[DEBUG] Quota Check: Current={current_count}, Limit={quota}")
        # No extra timestamp subfolder in the common case -- files land
        # directly in output_root/keyword. Only fall back to a run_id
        # subfolder if that keyword folder already has a FINISHED video in
        # it (a true duplicate run), so a rerun can never clobber a
        # completed video. A folder left behind by a crash/OOM/force-quit
        # (no video.mp4 yet) is reused as-is instead -- whatever per-ASIN
        # images/video that download_assets() already pulled down survive,
        # so a resumed run has less to redo than starting over in a fresh
        # timestamped folder every time.
        base_dir = os.path.join(output_root, keyword)
        resuming = os.path.isdir(base_dir) and not os.path.isfile(os.path.join(base_dir, "video.mp4"))
        if os.path.isdir(base_dir) and not resuming:
            run_id = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{os.urandom(4).hex()}"
            base_dir = os.path.join(output_root, keyword, run_id)

        # We always check quota here, even if folder exists, to ensure we don't exceed limit
        if quota != "unlimited":
            try:
                q_val = int(quota)
                if current_count >= q_val:
                    print(f"\n[QUOTA REACHED] Cannot create more than {q_val} videos. Skipping: {keyword}")
                    # Special signal for web app to terminate the stream early
                    print("__TERMINATE_NOW__")
                    break # Stop processing all remaining keywords
            except: pass

        print(f"\n--- Keyword: {keyword} ---" + (" (resuming previous interrupted run)" if resuming else ""))
        reset_audio_stats()
        os.makedirs(base_dir, exist_ok=True)

        # Single source of truth for the rest of this keyword's processing.
        # Previously this was recomputed twice more below from
        # len(processed) *after* some requested ASINs could have failed --
        # so a 3-ASIN request that lost 2 ASINs got scripts written in
        # multi-product mode (via is_single_asin below) but was then rendered
        # through the single-ASIN timeline branch, which expects a
        # differently-shaped script. Keeping one value for the whole keyword
        # guarantees the script shape and the render branch always agree.
        is_single = len([a for a in asins if a]) == 1

        # Retry the whole ASIN batch once if every single ASIN failed. A
        # keyword used to be skipped forever on the first pass, even for a
        # purely transient cause (a misconfigured LLM provider that was then
        # fixed mid-run, a rate limit, a flaky Amazon fetch) -- so a single
        # bad minute anywhere lost the entire keyword with no way to recover
        # short of resubmitting it by hand. Reuses the SAME base_dir, so any
        # per-ASIN assets the first attempt did manage to download are not
        # re-fetched (see the folder-reuse comment above).
        processed = []
        keyword_attempts = 2
        for keyword_attempt in range(1, keyword_attempts + 1):
            processed = []
            process_start = time.time()
            # Reduced from 10 to 4: 10 parallel ASIN workers each fired every
            # paragraph's TTS simultaneously (asyncio.gather), opening dozens of
            # concurrent connections to the free edge_tts endpoint at once and
            # getting throttled/reset -- the root cause of "sound missing in
            # parts". The threading-based _TTS_GATE (see generate_tts) caps
            # actual TTS concurrency regardless of worker count, but fewer
            # workers means less scraping/LLM work piles up waiting on it.
            with ThreadPoolExecutor(max_workers=4) as ex:
                future_to_asin = {
                    ex.submit(process_single_asin, a, keyword, base_dir, is_single_asin=is_single): a
                    for a in asins
                    if a
                }
                for f in as_completed(future_to_asin):
                    asin = future_to_asin[f]
                    try:
                        res = f.result()
                    except Exception as e:
                        # Previously unguarded: one ASIN raising (e.g. Amazon
                        # blocked the scrape, or the LLM call failed) crashed the
                        # entire `for line in lines` loop, killing every other
                        # keyword still queued in this batch, not just this ASIN.
                        print(f"[ERROR] ASIN worker failed for {asin}: {e}")
                        res = None
                    if res:
                        processed.append(res)
                        print(f"[SUCCESS] ASIN {asin} ready ({len(processed)}/{len(future_to_asin)})")
                    else:
                        print(f"[WARN] ASIN {asin} produced no usable result")

            process_end = time.time()
            if processed:
                print(f"[SUCCESS] Processed {len(processed)} products (Scraping + AI + TTS) in {process_end - process_start:.2f}s")
                break
            if keyword_attempt < keyword_attempts:
                print(f"[RETRY] '{keyword}': every ASIN failed on attempt {keyword_attempt}/{keyword_attempts} -- retrying the whole keyword once.")

        if not processed:
            print(f"[FATAL] No products could be processed for '{keyword}' after {keyword_attempts} attempts -- skipping this keyword (no video produced, quota not used).")
            continue

        processed = _dedupe_products_by_title(processed, keyword)

        processed = order_products(processed, product_order)
        print(
            f"[SYSTEM] Product order: {product_order} -> "
            + ", ".join(str(p["rank"]) for p in processed)
        )

        # --- Audio Health Gate ---
        # A product with ZERO usable narration audio used to still get a
        # rendered segment (create_product_segment_ffmpeg falls back to 5s of
        # anullsrc silence), shipping a video with a silent chunk instead of
        # failing loudly. Abort the whole keyword instead: no video is better
        # than a broken one, and this is exactly the multi-ASIN failure mode
        # from the reported bug. Also the guard a single failed narration
        # beat relies on -- see beat_segments in process_single_asin.
        silent_asins = [p.get('asin', '?') for p in processed if not _product_has_audio(p)]
        if silent_asins:
            print(f"[FATAL] No usable narration audio for ASIN(s) {silent_asins} in '{keyword}' -- skipping this keyword rather than shipping a silent video.")
            continue

        # `keyword` is the filesystem-safe slug (slugify() turns spaces into
        # hyphens for the folder name) -- de-hyphenate it back into words for
        # anything the viewer actually sees (thumbnail, title, intro/outro,
        # SEO metadata). Otherwise "best-cold-air-intake" showed up literally
        # on screen and in narration instead of "Best Cold Air Intake".
        human_kw = title_case(keyword.replace("_", " ").replace("-", " "))
        
        # --- YouTube Meta & Thumbnail Generation (Move it up to use the title) ---
        print("\n--- Generating YouTube Metadata ---")
        meta = None
        try:
            meta = metadata_generator.generate_youtube_metadata(human_kw, processed, partner_tag=PARTNER_TAG)
        except Exception as e:
            print(f"[ERROR] Failed to generate YouTube metadata: {e}")
            if is_single:
                meta = {'title': f"{human_kw}", 'description': '', 'tags': ''}
            else:
                best_prefix = " Best" if USE_BEST else ""
                year_suffix = f" {YEAR}" if USE_YEAR else ""
                meta = {'title': f"Top {len(processed)}{best_prefix} {human_kw}{year_suffix}", 'description': '', 'tags': ''}

        # Use the first part of the SEO title for the Intro Slide text
        display_title = meta['title'].split('|')[0].strip()

        # --- Thumbnail (moved up ahead of segment rendering) ---
        # Generated here, before the intro slide renders, so the intro can
        # use the same styled thumbnail image as its background instead of a
        # plain product photo -- matching the look the user asked for.
        intro_thumb_path = None
        if processed and processed[0]['images']:
            try:
                intro_thumb_path = os.path.join(base_dir, "Thumbnail.jpg")
                preferred_bg_dir = os.path.join(
                    os.path.dirname(__file__),
                    "shorts_bg_img" if SHORTS_MODE else "bg_img",
                )
                bg_dir = (
                    preferred_bg_dir
                    if os.path.isdir(preferred_bg_dir)
                    else os.path.join(os.path.dirname(__file__), "bg_img")
                )
                thumbnail_text = " ".join(human_kw.upper().split()[:4])
                thumbnail_generator.generate_thumbnail(
                    processed[0]['images'][0],
                    thumbnail_text,
                    intro_thumb_path,
                    bg_folder=bg_dir,
                    bg_overlay_color=COLOR_THUMB_OVERLAY,
                    bg_overlay_opacity=VAL_THUMB_OVERLAY_OPACITY,
                    text_colors=[COLOR_THUMB_TEXT_TOP, COLOR_THUMB_TEXT_BOT],
                    text_bg_color=COLOR_THUMB_TEXT_BG,
                    text_bg_opacity=VAL_THUMB_TEXT_BG_OPACITY,
                    glow_color=COLOR_THUMB_GLOW,
                    glow_radius_mult=VAL_THUMB_GLOW_RADIUS,
                    glow_opacity=VAL_THUMB_GLOW_OPACITY,
                    font_name=THUMB_FONT,
                    model_name=REMBG_MODEL,
                )
                apply_seo_metadata(intro_thumb_path, human_kw, meta['tags'], meta['title'])
                if not os.path.isfile(intro_thumb_path):
                    intro_thumb_path = None
            except Exception as e:
                print(f"[ERROR] Failed to generate Thumbnail.jpg early for intro use: {e}")
                intro_thumb_path = None

        # Intro / Outro content
        count = len(processed)
        # Rank 1 is the top pick in BOTH orderings (order_products numbers a
        # countdown N..1 and a list 1..N), so the conclusion can name a
        # winner without caring which display order the user chose.
        top_pick_title = next(
            (p.get("title") for p in processed if p.get("rank") == 1), None
        )
        # is_single intentionally NOT recomputed from len(processed) here --
        # see the comment where it's first set, above the ASIN worker pool.
        if SHORTS_MODE:
            # Shorts: Use the full SEO title for intro to include important keywords
            intro_txt = f"{display_title}"
            outro_txt = build_conclusion_text(human_kw, processed, is_single, top_pick_title)
        else:
            if is_single:
                # Normal mode Single ASIN: only use the title in the intro voice-over
                intro_txt = f"{display_title}"
            else:
                intro_txt = f"{display_title}. {INTRO_TEXT}"
            # A verdict + price-check close, rather than the bare "check the
            # links" line the old outro produced. A custom outro_text in
            # Settings still wins -- only the shipped default is replaced.
            if OUTRO_TEXT.strip() and OUTRO_TEXT.strip() != DEFAULT_OUTRO_TEXT.strip():
                outro_txt = OUTRO_TEXT.replace("{keyword}", human_kw)
                if "{keyword}" not in OUTRO_TEXT:
                    outro_txt = outro_txt.replace(
                        "Thanks for watching!", f"Thanks for watching {human_kw}!"
                    )
            else:
                outro_txt = build_conclusion_text(human_kw, processed, is_single, top_pick_title)

        # Opt-in AI opener/closer. Written per video from the actual product
        # list instead of the fixed template, following the hook -> promise
        # -> reassurance -> "let's get into it" structure that holds
        # retention in the first 20 seconds. Anything that comes back empty
        # (LLM down, all providers exhausted) falls through to the template
        # text already computed above -- a missing opener is far worse than
        # a generic one.
        if AI_INTRO_OUTRO:
            print("[SCRIPT] Writing AI intro/conclusion...")
            ai_intro = build_ai_intro_text(human_kw, processed, is_single)
            if ai_intro:
                # Keep the title spoken first so the video still opens on the
                # keyword YouTube matched the viewer against.
                intro_txt = f"{display_title}. {ai_intro}"
            else:
                print("[SCRIPT][WARN] AI intro unavailable; using the template opener.")
            ai_outro = build_ai_conclusion_text(human_kw, processed, is_single)
            if ai_outro:
                outro_txt = ai_outro
            else:
                print("[SCRIPT][WARN] AI conclusion unavailable; using the template close.")

        intro_aud = os.path.join(base_dir, "intro.mp3")
        outro_aud = os.path.join(base_dir, "outro.mp3")
        
        # Parallel TTS Generation
        # Add voice ID if AI33Pro is selected
        if TTS_SERVICE == "ai33pro" and AI33PRO_API_KEY:
            tts_tasks = [(intro_txt, intro_aud, AI33PRO_VOICE_ID), (outro_txt, outro_aud, AI33PRO_VOICE_ID)]
        else:
            tts_tasks = [(intro_txt, intro_aud), (outro_txt, outro_aud)]
            
        for p in processed:
            rank = p["rank"]
            if not is_single:
                rank_txt = f"Number {rank}"
                rank_path = os.path.join(base_dir, f"rank_{rank}.mp3")
                
                # FORCE AI33PRO for Rank segments if selected
                if TTS_SERVICE == "ai33pro" and AI33PRO_API_KEY:
                    print(f"[DEBUG] Forcing AI33PRO for Rank {rank}")
                    tts_tasks.append((rank_txt, rank_path, AI33PRO_VOICE_ID))
                else:
                    tts_tasks.append((rank_txt, rank_path))
            else:
                # Still create the file path but with empty text or skip logic
                # To prevent errors in timeline_segments, we will skip adding it to timeline later
                pass
        
        print(f"[SYSTEM] Generating {len(tts_tasks)} TTS files in parallel...")
        # Unpack tasks correctly (could be 2 or 3 elements)
        # BUGFIX: the results of this gather used to be discarded entirely,
        # so a failed intro/outro TTS call was invisible -- create_text_slide_ffmpeg
        # would just render a silent slide (anullsrc) and the video shipped anyway.
        tts_results = await asyncio.gather(*(generate_tts(*task) for task in tts_tasks))

        failed_global = [
            os.path.basename(task[1])
            for task, result in zip(tts_tasks, tts_results)
            if not result
        ]
        if failed_global:
            print(
                f"[FATAL] Required narration failed for '{keyword}': "
                f"{failed_global}. No video produced."
            )
            continue

        # Re-encode global TTS (intro/outro/rank) for consistency
        for _, path, *rest in tts_tasks:
            if path and os.path.exists(path):
                norm_p = path + "_norm.mp3"
                try:
                    run_ffmpeg([
                        "ffmpeg", "-y", "-i", path,
                        "-af", f"aresample={AUDIO_SAMPLE_RATE}:async=1:first_pts=0",
                        "-ar", str(AUDIO_SAMPLE_RATE), "-ac", "2",
                        "-ab", "128k", norm_p,
                    ])
                    os.replace(norm_p, path)
                except Exception as e:
                    print(f"[AUDIO][WARN] Normalization failed for {os.path.basename(path)}, keeping original: {e}")

        # Save all generated descriptions to a text file
        try:
            desc_file_path = os.path.join(base_dir, "all_descriptions.txt")
            with open(desc_file_path, "w", encoding="utf-8") as df:
                for p in processed:
                    df.write(f"Product Title: {p['title']}\n")
                    df.write(f"Description:\n{p.get('description', '')}\n")
                    df.write("-" * 50 + "\n\n")
            print(f"[SYSTEM] All descriptions saved to: {desc_file_path}")
        except Exception as e:
            print(f"[ERROR] Could not save all_descriptions.txt: {e}")

        # --- PRE-CALCULATE TIMELINE FOR BRANDING ---
        timeline_segments = []
        current_time_total = 0.0
        
        # Background check
        bg_asset = None
        if processed[0]['video'] and os.path.exists(processed[0]['video']):
            bg_asset = processed[0]['video']
        else:
            for p_fallback in processed:
                if p_fallback['video'] and os.path.exists(p_fallback['video']):
                    bg_asset = p_fallback['video']
                    break
        if not bg_asset and processed[0]['images']:
            bg_asset = processed[0]['images'][0]

        # 1. Intro -- uses the styled Thumbnail.jpg (generated above) as its
        # background when available, so the intro visually matches the
        # thumbnail instead of a plain product photo/video. After a short
        # (1-3s) hook on that still image, it cuts to real product footage
        # for the rest of the intro -- see hook_video_path below.
        intro_bg_asset = intro_thumb_path if intro_thumb_path and os.path.exists(intro_thumb_path) else bg_asset
        intro_hook_video = _pick_intro_hook_video(processed)
        i_dur = max(get_audio_duration(intro_aud) + 1.0, 2.2)
        timeline_segments.append({'type': 'intro', 'start': current_time_total, 'dur': i_dur, 'data': (display_title, intro_aud, intro_bg_asset, intro_hook_video), 'caption_text': intro_txt})
        current_time_total += i_dur
        
        # 2. Intro Clip (Skip in Shorts Mode)
        # For Single ASIN Normal Mode: If Intro Clip exists, it plays after the Intro text slide.
        # We will keep this as is.
        if not SHORTS_MODE and run_settings.get("enable_intro_clip", False):
            intro_clip_src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "intro_clip.mp4")
            if os.path.exists(intro_clip_src):
                intro_clip_dest = os.path.join(base_dir, "segment_0_clip.mp4")
                res_w, res_h = output_resolution()
                normalize_cmd = [
                    "ffmpeg", "-y", "-i", intro_clip_src,
                    "-vf", f"scale={res_w}:{res_h}:force_original_aspect_ratio=decrease,pad={res_w}:{res_h}:(ow-iw)/2:(oh-ih)/2:color=white,setsar=1",
                    "-r", "25", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-ar", str(AUDIO_SAMPLE_RATE), "-ac", "2",
                    "-af", f"aresample={AUDIO_SAMPLE_RATE}:async=1:first_pts=0",
                    "-fflags", "+genpts", "-avoid_negative_ts", "make_zero",
                    intro_clip_dest
                ]
                try:
                    run_ffmpeg(normalize_cmd)
                    c_dur = get_audio_duration(intro_clip_dest)
                    timeline_segments.append({'type': 'clip', 'start': current_time_total, 'dur': c_dur, 'path': intro_clip_dest})
                    current_time_total += c_dur
                except: pass
            
        # 3. Products (is_single is the single request-time value set above)
        # Which product(s) show the on-screen CTA banner/badge -- 2-3 spread
        # across the video rather than on every product segment, which read
        # as unnatural repetition. is_single's several product_part
        # sections belong to ONE product, so they get their own spread
        # further below instead of one entry each here.
        cta_product_indices = _evenly_spaced_indices(len(processed), 3) if not is_single else set()
        for p_index, p in enumerate(processed):
            rank = p["rank"]
            if not is_single:
                r_aud = os.path.join(base_dir, f"rank_{rank}.mp3")
                # Fixed, configurable length. This used to be
                # audio_duration + 0.8s, so trailing silence in a two-word
                # "Number 3" clip (which several TTS providers pad
                # generously) stretched a countdown card that should be a
                # quick beat into several seconds of dead screen.
                r_dur = RANK_SLIDE_SECONDS
                timeline_segments.append({'type': 'rank', 'start': current_time_total, 'dur': r_dur, 'rank': rank, 'audio': r_aud, 'product': p, 'caption_text': f"Number {rank}"})
                current_time_total += r_dur
            
            # FOR SINGLE ASIN NOR"INTRODUCTION" # Default for first partMODE (Handling sections on-the-fly)
            if not SHORTS_MODE and is_single:
                current_header = None
                # Single-ASIN mode has one product split into several
                # sections (Key Features, Performance, ...) -- the CTA
                # spread applies across THOSE sections, not once per
                # product (there's only one product).
                valid_parts = [
                    a for a in p['audio_segments']
                    if not a[2] and a[0] and os.path.exists(a[0])
                ]
                cta_part_indices = _evenly_spaced_indices(len(valid_parts), 3)
                part_index = 0
                for a_info in p['audio_segments']:
                    audio_path, text, is_header = a_info[0], a_info[1], a_info[2]
                    forced_label = a_info[3] if len(a_info) > 3 else None

                    if is_header:
                        # Update the current section name (Key Features, etc.)
                        current_header = text
                        continue

                    # Normal product review part
                    if audio_path and os.path.exists(audio_path):
                        label_to_show = forced_label if forced_label else current_header
                        # Add 1s buffer for cinematic transition
                        part_dur = get_audio_duration(audio_path) + 1.0
                        timeline_segments.append({
                            'type': 'product_part',
                            'start': current_time_total,
                            'dur': part_dur,
                            'rank': rank,
                            'product': p,
                            'audio': audio_path,
                            'header': label_to_show, # Pass the section label (forced or current)
                            'caption_text': text,
                            'show_cta': part_index in cta_part_indices,
                        })
                        part_index += 1
                        current_time_total += part_dur
            else:
                # DEFAULT MULTI-ASIN / SHORTS
                # Re-integrate from p['audio_segments']
                valid_audios = [seg[0] for seg in p['audio_segments'] if seg[0] and os.path.exists(seg[0])]
                p_dur = sum(get_audio_duration(a) for a in valid_audios) or 5.0
                narration_text = " ".join(
                    seg[1] for seg in p['audio_segments']
                    if seg[0] in valid_audios and not (len(seg) > 2 and seg[2])
                )
                caption_text = product_caption_points(p, narration_text)
                timeline_segments.append({'type': 'product', 'start': current_time_total, 'dur': p_dur, 'rank': rank, 'product': p, 'audios': valid_audios, 'caption_text': caption_text, 'show_cta': p_index in cta_product_indices})
                current_time_total += p_dur
            
        # 4. Outro
        display_outro_text = outro_txt if outro_txt else "Thanks for Watching!"
        # Uses the same slide_duration() the renderer uses below, so the
        # predicted 'dur' here (used to time branding overlays) matches what
        # create_text_slide_ffmpeg actually produces.
        o_dur = slide_duration(outro_aud, is_outro_single=is_single)
        timeline_segments.append({'type': 'outro', 'start': current_time_total, 'dur': o_dur, 'data': (display_outro_text, outro_aud, bg_asset), 'is_outro_single': is_single, 'caption_text': outro_txt})
        current_time_total += o_dur
        
        # --- CALCULATE BRANDING FILTERS ---
        total_dur = current_time_total
        num_times = max(1, int(total_dur / 60))
        branding_font = escape_path(setup_font(bold=True))
        all_branding = []
        
        # Persistent Video Title for Shorts Mode at Top (y=0)
        # Removed for single ASIN mode
        if SHORTS_MODE and not is_single:
            all_branding.append({
                'start': 1.5, # Start after intro starts
                'end': total_dur - 1.5, # End before outro ends
                'text': sanitize_text(display_title).upper(), 
                'type': 'sticky_title'
            })

        for i in range(num_times):
            pct = (i + 0.5) / num_times
            start_t = max(2.0, total_dur * pct - 6)
            if start_t + 12 > total_dur: start_t = max(0.0, total_dur - 13)
            # Channel name flows straight into drawtext -- unlike every other
            # text path here it wasn't sanitized, so an apostrophe in the
            # configured channel name (e.g. "Dad's Garage") broke the
            # filtergraph and silently failed every keyword's render.
            all_branding.append({'start': start_t, 'end': start_t + 5.0, 'text': sanitize_text(LOGO_TEXT.upper()), 'type': 'logo'})
            all_branding.append({'start': start_t + 5.5, 'end': start_t + 10.5, 'text': 'SUBSCRIBE', 'type': 'sub'})

        def get_branding_for_seg(s_start, s_dur):
            filters = []
            for b in all_branding:
                if b['start'] < s_start + s_dur and b['end'] > s_start:
                    r_s = max(0.0, b['start'] - s_start)
                    r_e = min(s_dur, b['end'] - s_start)
                    if r_e - r_s < 0.5: continue
                    
                    if b['type'] == 'sticky_title':
                        # Sticky title at top:15 (Increased from 4px to 15px for better visibility)
                        # Reduced font size from 32 to 24-28 to prevent overflow on long keywords
                        b_fsize = round((28 if len(b['text']) < 40 else 24) * TEXT_SCALE)
                        b_top_pad = round(15 * TEXT_SCALE)
                        filters.append(
                            f"drawtext=fontfile='{branding_font}':text='{b['text']}':fontcolor=white:fontsize={b_fsize}:"
                            f"box=1:boxcolor=black@0.6:boxborderw={b_top_pad}:x=(w-text_w)/2:y={b_top_pad}:enable='between(t,{r_s},{r_e})'"
                        )
                    else:
                        b_edge = round(60 * TEXT_SCALE)
                        x_expr = f"if(lt(t,{r_s+0.5}),W-(t-{r_s})*(tw+{b_edge})*2,if(lt(t,{r_e-0.5}),W-tw-{b_edge},(W-tw-{b_edge})+(t-({r_e-0.5}))*(tw+{b_edge})*2))"
                        col = COLOR_LOGO_TEXT if b['type'] == 'logo' else 'white'
                        bg_col = f"{COLOR_LOGO_BG}@{VAL_LOGO_BG_OPACITY}" if b['type'] == 'logo' else 'red@0.8'
                        b_fsize2 = round(40 * TEXT_SCALE)
                        b_boxpad = round(10 * TEXT_SCALE)
                        b_top = round(100 * TEXT_SCALE)
                        filters.append(f"drawtext=fontfile='{branding_font}':text='{b['text']}':fontcolor={col}:fontsize={b_fsize2}:box=1:boxcolor={bg_col}:boxborderw={b_boxpad}:x='{x_expr}':y={b_top}:enable='between(t,{r_s},{r_e})'")
            return filters

        # --- RENDER SEGMENTS IN PARALLEL ---
        print(f"[SYSTEM] Rendering {len(timeline_segments)} segments in parallel...")
        segment_files = [None] * len(timeline_segments)
        planned_segment_ids = []
        for idx, seg in enumerate(timeline_segments):
            rank_suffix = f"-{seg.get('rank')}" if seg.get("rank") is not None else ""
            planned_segment_ids.append(f"{seg['type']}{rank_suffix}-{idx}")
        render_start = time.time()
        
        def render_task(idx, seg):
            try:
                b_filters = get_branding_for_seg(seg['start'], seg['dur'])
                if seg['type'] == 'intro':
                    using_thumb_bg = bool(intro_thumb_path) and seg['data'][2] == intro_thumb_path
                    hook_video = seg['data'][3] if len(seg['data']) > 3 else None
                    intro_out = os.path.join(base_dir, "segment_0_intro.mp4")
                    made = create_text_slide_ffmpeg(seg['data'][0], seg['data'][1], intro_out, bg_path=seg['data'][2], is_intro=True, branding_filters=b_filters, draw_text=not using_thumb_bg, hook_video_path=hook_video)
                    if not made and hook_video:
                        # The hook concat filtergraph is the only fragile part
                        # of this slide (odd product footage, weird pixel
                        # format, unreadable stream). Losing the whole intro
                        # over it is far worse than losing the motion, so
                        # rebuild it as the plain thumbnail slide.
                        print("[INTRO] Hook footage failed to render; falling back to the still thumbnail intro.")
                        made = create_text_slide_ffmpeg(seg['data'][0], seg['data'][1], intro_out, bg_path=seg['data'][2], is_intro=True, branding_filters=b_filters, draw_text=not using_thumb_bg, hook_video_path=None)
                    return made
                elif seg['type'] == 'clip':
                    if b_filters:
                        branded_clip = seg['path'].replace(".mp4", "_branded.mp4")
                        run_ffmpeg(["ffmpeg", "-y", "-i", seg['path'], "-vf", ",".join(b_filters), "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "copy", "-pix_fmt", "yuv420p", branded_clip])
                        return branded_clip
                    return seg['path']
                elif seg['type'] == 'rank':
                    p = seg['product']
                    r_bg = p['video'] if p['video'] and os.path.exists(p['video']) else (p['images'][0] if p['images'] else None)
                    return create_text_slide_ffmpeg(str(seg['rank']), seg['audio'], os.path.join(base_dir, f"segment_{seg['rank']}_rank.mp4"), bg_path=r_bg, is_rank=True, branding_filters=b_filters)
                elif seg['type'] == 'product':
                    p = seg['product']
                    return create_product_segment_ffmpeg(p['video'], p['images'], seg['audios'], p['title'], os.path.join(base_dir, f"segment_{seg['rank']}_product.mp4"), branding_filters=b_filters, show_cta=seg.get('show_cta', True), caption_key_points=p.get('features'))
                elif seg['type'] == 'product_part':
                    p = seg['product']
                    # Render a specific part of the product review with current section header
                    # Pass the 'header' from the segment data. tail_pad=1.0 matches the
                    # timeline's `part_dur = audio + 1.0` cinematic buffer (see slide_duration
                    # docstring for why this needs to match on both sides).
                    return create_product_segment_ffmpeg(p['video'], p['images'], [seg['audio']], p['title'], os.path.join(base_dir, f"segment_{seg['rank']}_part_{idx}.mp4"), branding_filters=b_filters, header_text=seg.get('header'), tail_pad=1.0, show_cta=seg.get('show_cta', True), caption_key_points=p.get('features'))
                elif seg['type'] == 'outro':
                    return create_text_slide_ffmpeg(seg['data'][0], seg['data'][1], os.path.join(base_dir, "segment_final_outro.mp4"), bg_path=seg['data'][2], branding_filters=b_filters, is_outro_single=seg.get('is_outro_single', False))
            except Exception as e:
                print(f"[ERROR] Task {idx} failed: {e}")
                return None

        with ThreadPoolExecutor(max_workers=max(2, os.cpu_count() // 2 if os.cpu_count() else 2)) as executor:
            idx_map = {executor.submit(render_task, i, seg): i for i, seg in enumerate(timeline_segments)}
            for future in as_completed(idx_map):
                idx = idx_map[future]
                segment_files[idx] = future.result()
        
        render_end = time.time()
        print(f"[SUCCESS] Parallel rendering completed in {render_end - render_start:.2f}s")
        failed_render_ids = [
            planned_segment_ids[i]
            for i, path in enumerate(segment_files)
            if not path or not os.path.isfile(path)
        ]
        if failed_render_ids:
            print(
                f"[FATAL] Required video segments failed: {failed_render_ids}. "
                "Final render aborted."
            )
            continue
        silent_render_ids = []
        for i, seg in enumerate(timeline_segments):
            if seg["type"] not in {"intro", "rank", "product", "product_part", "outro"}:
                continue
            ok, reason = _rendered_segment_has_narration(
                segment_files[i],
                float(seg.get("dur") or 0),
                planned_segment_ids[i],
            )
            if not ok:
                silent_render_ids.append(f"{planned_segment_ids[i]}: {reason}")
        if silent_render_ids:
            print(
                f"[FATAL] Rendered narration audio failed: {silent_render_ids}. "
                "Final render aborted."
            )
            continue
        rendered_segment_map = {
            planned_segment_ids[i]: path for i, path in enumerate(segment_files)
        }
        # Segment starts here are all in pre-speed-adjustment wall-clock time;
        # the whole-video speed pass runs later, after final assembly, purely
        # on the rendered file. Divide by VIDEO_SPEED so a description
        # timestamp still points at the right moment in the (possibly sped
        # up or slowed down) final video.
        timestamps = []
        for seg in timeline_segments:
            seg_start = seg['start'] / VIDEO_SPEED
            if seg['type'] == 'intro': timestamps.append(f"{format_timestamp(seg_start)} Intro")
            elif seg['type'] == 'rank': timestamps.append(f"{format_timestamp(seg_start)} - {seg['rank']} {seg['product']['title']}")
            elif seg['type'] == 'outro': timestamps.append(f"{format_timestamp(seg_start)} Outro")

        # Final Concatenation
        final_list_txt = os.path.join(base_dir, "final_files.txt")
        expected_final_duration = total_dur
        # Single-product long-form videos use real video and audio
        # crossfades. The previous implementation built an invalid graph and
        # then discarded it, so the advertised transitions never appeared.
        if not SHORTS_MODE and is_single and len(segment_files) > 1:
            print("Applying smooth video/audio crossfades...")
            durations = [get_audio_duration(path) for path in segment_files]
            trans_dur = min(0.6, max(0.25, min(durations) / 4))
            inputs = []
            filters = []
            for i, path in enumerate(segment_files):
                inputs.extend(["-i", path])
                filters.append(f"[{i}:v]settb=AVTB,setpts=PTS-STARTPTS[v{i}]")
                filters.append(
                    f"[{i}:a]aresample={AUDIO_SAMPLE_RATE}:async=1:first_pts=0,"
                    f"asetpts=PTS-STARTPTS[a{i}]"
                )

            running_duration = durations[0]
            previous_video = "v0"
            previous_audio = "a0"
            safe_transitions = ACTIVE_TRANSITIONS or ["fade"]
            for i in range(1, len(segment_files)):
                transition = random.choice(safe_transitions)
                video_out = f"vx{i}"
                audio_out = f"ax{i}"
                offset = max(0.0, running_duration - trans_dur)
                filters.append(
                    f"[{previous_video}][v{i}]xfade=transition={transition}:"
                    f"duration={trans_dur:.3f}:offset={offset:.3f}[{video_out}]"
                )
                filters.append(
                    f"[{previous_audio}][a{i}]acrossfade=d={trans_dur:.3f}:"
                    f"c1=tri:c2=tri[{audio_out}]"
                )
                previous_video, previous_audio = video_out, audio_out
                running_duration += durations[i] - trans_dur
            filters.append(
                f"[{previous_audio}]loudnorm=I=-14:TP=-1.5:LRA=11,"
                f"aresample={AUDIO_SAMPLE_RATE}:async=1:first_pts=0,"
                f"apad=whole_dur={running_duration},"
                f"atrim=duration={running_duration}[aout]"
            )
            expected_final_duration = running_duration
            final_output = os.path.join(base_dir, "video_branded.mp4")
            run_ffmpeg(
                ["ffmpeg", "-y"]
                + inputs
                + [
                    "-filter_complex", ";".join(filters),
                    "-map", f"[{previous_video}]", "-map", "[aout]",
                    "-c:v", "libx264", "-preset", "veryfast",
                    "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-ar", str(AUDIO_SAMPLE_RATE), "-ac", "2",
                    "-t", str(expected_final_duration),
                    "-movflags", "+faststart",
                    final_output,
                ]
            )
        else:
            with open(final_list_txt, "w", encoding='utf-8') as f:
                for s in segment_files:
                    clean_path = os.path.abspath(s).replace("\\", "/").replace("'", "'\\''")
                    f.write(f"file '{clean_path}'\n")

            final_output = os.path.join(base_dir, "video_branded.mp4")
            print(f"Concatenating segments...")
            run_ffmpeg([
                "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i",
                os.path.normpath(final_list_txt),
                "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-ar", str(AUDIO_SAMPLE_RATE), "-ac", "2",
                "-af",
                (
                    "loudnorm=I=-14:TP=-1.5:LRA=11,"
                    f"aresample={AUDIO_SAMPLE_RATE}:async=1:first_pts=0,"
                    f"apad=whole_dur={expected_final_duration},"
                    f"atrim=duration={expected_final_duration}"
                ),
                "-t", str(expected_final_duration),
                "-movflags", "+faststart", final_output,
            ])
        
        # --- Whole-Video Speed Adjustment ---
        # Deliberately after final assembly (both the crossfade and the
        # concat-demuxer path converge on `final_output` here) and before the
        # music mix, so the music-bed duration and every downstream QC check
        # naturally use the POST-speed duration instead of needing their own
        # separate adjustment.
        final_output, expected_final_duration = apply_video_speed(
            final_output, expected_final_duration, base_dir
        )

        # --- Captions ---
        # Written here rather than in post-processing because the burn-in
        # below needs the .srt, and it has to happen before the music mix
        # (which stream-copies the video track and would otherwise have to
        # be redone). Timestamps are already post-speed.
        # The .srt sidecar is always written (YouTube can use it, and it
        # costs nothing). Burning it into the picture is NOT done anymore:
        # key points now render as a typed bar under the product title
        # inside each segment, which is what "captions" means here. Burning
        # the full narration on top of that would put two competing blocks
        # of text on screen -- the exact overlap that made the old output
        # unreadable.
        write_captions_srt(base_dir, timeline_segments, VIDEO_SPEED)

        # --- Background Music Check ---
        selected_music = None
        music_path = None
        music_mode = run_settings.get("music_mode", "nature")
        if music_mode == "ai_generated":
            try:
                music_path = generate_ai_music(human_kw, run_settings)
                print(f"[MUSIC] Vertex AI (Lyria) track ready: {music_path}")
            except Exception as music_error:
                print(f"[MUSIC][WARN] Vertex AI music generation failed: {music_error}")
        elif music_mode == "custom":
            # A user-supplied file wins outright -- no library routing, no
            # mood inference. It still goes through the same loudness
            # normalization as a bundled bed, so a commercially mastered
            # track doesn't bury the narration.
            custom_music = str(run_settings.get("custom_music_path") or "").strip()
            if custom_music and os.path.isfile(custom_music):
                music_path = custom_music
                print(f"[MUSIC] Using custom track: {os.path.basename(custom_music)}")
            else:
                print(
                    "[MUSIC][WARN] Music mode is 'custom' but no readable file is set "
                    f"({custom_music or 'nothing selected'}) -- rendering without music."
                )
        elif music_mode in ("auto", "nature"):
            try:
                selected_music = music_manager.select_track(
                    human_kw,
                    os.path.join(os.path.dirname(__file__), "music", "music_library.json"),
                    recent_track_ids=recent_music_ids,
                    # "nature" restricts routing to the non-musical ambience
                    # beds -- no melody or performance for Content ID to
                    # match, which is the safest bed for a monetized channel.
                    required_mood="nature" if music_mode == "nature" else None,
                )
                music_path = selected_music["path"]
                recent_music_ids.append(selected_music["id"])
                recent_music_ids[:] = recent_music_ids[-5:]
                print(f"[MUSIC] Auto-routed track: {selected_music['id']}")
            except Exception as music_error:
                print(f"[MUSIC][WARN] No licensed music selected: {music_error}")
        if music_path and os.path.exists(music_path):
            print("Adding background music...")
            final_with_music = os.path.join(base_dir, "video.mp4")
            # Narration was already loudness-normalized in the concat stage.
            # A second dynamic loudnorm pass after mixing caused audible volume
            # pumping; a limiter preserves clarity without re-shaping speech.
            music_gain_db = music_bed_gain_db(music_path)
            print(f"[MUSIC] Bed gain {music_gain_db:+.1f} dB (target {MUSIC_BED_TARGET_DBFS:.0f} dBFS)")
            filter_complex_audio = build_music_mix_filter(expected_final_duration, music_gain_db)
            
            cmd = [
                "ffmpeg", "-y", 
                "-i", final_output,
                "-stream_loop", "-1", "-i", music_path,
                "-filter_complex", filter_complex_audio,
                "-map", "0:v", "-map", "[a]",
                "-c:v", "copy", "-c:a", "aac",
                "-ar", str(AUDIO_SAMPLE_RATE), "-ac", "2",
                "-t", str(expected_final_duration),
                "-movflags", "+faststart",
                final_with_music
            ]
            try:
                run_ffmpeg(cmd)
                print(f"Background music added. Final video: {final_with_music}")
                if os.path.exists(final_output): os.remove(final_output)
            except Exception as e:
                print(f"Error adding music: {e}")
        else:
            # If no music, rename the final_output to the final name
            clean_final = os.path.join(base_dir, "video.mp4")
            if os.path.exists(clean_final): os.remove(clean_final)
            os.rename(final_output, clean_final)
            print(f"Done! Video saved to: {clean_final}")

        final_video_path = os.path.join(base_dir, "video.mp4")
        qc_report = media_qc.run_media_qc(
            final_path=final_video_path,
            planned_segment_ids=planned_segment_ids,
            rendered_segments=rendered_segment_map,
            expected_duration=expected_final_duration,
            ffprobe_bin=FFPROBE_BIN,
            ffmpeg_bin=FFMPEG_BIN,
        )
        with open(os.path.join(base_dir, "qc_report.json"), "w", encoding="utf-8") as handle:
            json.dump(qc_report, handle, indent=2, ensure_ascii=False)
        if qc_report["status"] != "PASSED":
            print(f"[FATAL][QC] Final video rejected: {qc_report.get('errors', [])}")
            continue
        print("[SUCCESS][QC] Final video passed stream, duration, segment, and silence checks.")

        # --- Saving YouTube Meta & Thumbnail (Data was generated earlier) ---
        print("\n--- Saving YouTube Metadata & Thumbnail ---")
        try:
            # Add Timestamps to description
            if timestamps:
                ts_text = "Timestamps:\n" + "\n".join(timestamps)
                # Replace [TIMESTAMPS_HERE] if present
                if "[TIMESTAMPS_HERE]" in meta['description']:
                    meta['description'] = meta['description'].replace("[TIMESTAMPS_HERE]", ts_text)
                else:
                    # Place after product links section as fallback
                    desc = meta['description']
                    last_link_idx = desc.rfind("amazon.com/dp/")
                    if last_link_idx != -1:
                        # Find end of that line
                        eol = desc.find("\n", last_link_idx)
                        if eol == -1: eol = len(desc)
                        meta['description'] = desc[:eol] + "\n\n" + ts_text + desc[eol:]
                    else:
                        # Fallback to beginning if no links found
                        meta['description'] = f"{ts_text}\n\n{meta['description']}"

            # 1. Save compact YouTube metadata for manual use and upload.
            meta['is_shorts'] = SHORTS_MODE
            meta["chapters"] = timestamps
            with open(os.path.join(base_dir, "youtube.txt"), "w", encoding="utf-8") as f:
                f.write(format_youtube_text(meta))
            
            # 2. Thumbnail was already generated earlier (right after the
            # metadata/title were ready) so the intro slide could reuse it
            # as its background -- see intro_thumb_path above. Just verify
            # it actually landed on disk.
            if not os.path.isfile(os.path.join(base_dir, "Thumbnail.jpg")):
                raise RuntimeError("Required Thumbnail.jpg was not generated")

            # 2b. Captions were written (and optionally burned in) back in
            # the assembly stage -- they have to exist before the music mix,
            # which stream-copies the video track.

            # 3. Apply SEO Metadata to Video
            final_video_path = os.path.join(base_dir, "video.mp4")
            if os.path.exists(final_video_path):
                apply_seo_metadata(final_video_path, human_kw, meta['tags'], meta['title'])
                keyword_video_path = os.path.join(base_dir, f"{keyword}.mp4")
                if os.path.abspath(final_video_path) != os.path.abspath(keyword_video_path):
                    # os.replace() is atomic on POSIX/Windows and overwrites
                    # any existing target itself -- a manual os.remove() first
                    # only opens a window where the served path briefly
                    # doesn't exist at all (a 404 for anyone watching).
                    os.replace(final_video_path, keyword_video_path)

            keep_files = {"Thumbnail.jpg", f"{keyword}.mp4", "youtube.txt", "captions.srt"}
            for child in Path(base_dir).iterdir():
                try:
                    if child.is_dir():
                        shutil.rmtree(child)
                    elif child.name not in keep_files:
                        child.unlink()
                except Exception as cleanup_error:
                    print(f"[CLEANUP][WARN] Could not remove {child}: {cleanup_error}")
            
            print(f"[SUCCESS] YouTube Assets saved in: {base_dir}")
        except Exception as e:
            print(f"[FATAL] Failed to build complete local project package: {e}")
            continue

        # --- Cleanup System ---
        print("Cleaning up temporary segments...")
        # 1. Delete segment files
        for s in segment_files:
            if s and os.path.exists(s):
                try: os.remove(s)
                except: pass
        
        # 2. Delete other temp files
        # ("arial.ttf" removed from this list -- nothing in this pipeline
        # ever writes a font to a CWD-relative "arial.ttf"; setup_font() only
        # ever writes an absolute path inside app_files. It was dead cleanup
        # that could delete an unrelated file if the process's CWD happened
        # to contain one.)
        temp_files = [final_list_txt, intro_aud, outro_aud]
        # Add rank audios and product specific audios
        for p in processed:
            if 'audio_segments' in p:
                for a_info in p['audio_segments']:
                    # Handle varying numbers of elements in the tuple
                    a_path = a_info[0]
                    if a_path: temp_files.append(a_path)
            elif 'audio' in p:
                temp_files.extend(p['audio'])
            
            rank_file = os.path.join(base_dir, f"rank_{p.get('rank', processed.index(p)+1)}.mp3")
            if rank_file not in temp_files: temp_files.append(rank_file)
            
        for f in temp_files:
            if f and os.path.exists(f):
                try: os.remove(f)
                except: pass
        
        # 3. Remove .mp4_audio.mp3 and _audiolist.txt files
        # Unlike every other cleanup step above, these were unguarded -- a
        # permission error or a concurrently-removed file would raise out of
        # main_pipeline() and kill every other keyword still queued.
        for pattern in ("*_audio.mp3", "*_audio.wav", "*_audiolist.txt"):
            for f in glob.glob(os.path.join(base_dir, pattern)):
                try: os.remove(f)
                except OSError: pass
        
        print("Cleanup complete!")

        print_audio_health(keyword)

        # --- Internal Quota Increment ---
        internal_used_count += 1
        print(f"[SUCCESS] Video creation process finished for: {keyword}")

if __name__ == "__main__":
    asyncio.run(main_pipeline())

