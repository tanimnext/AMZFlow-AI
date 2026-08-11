"""Shared Gemini narration controls used by preview and final rendering."""

from __future__ import annotations

import re


GEMINI_TTS_MODELS = {
    "gemini-3.1-flash-tts-preview": "Gemini 3.1 Flash TTS",
    "gemini-2.5-pro-preview-tts": "Gemini 2.5 Pro TTS",
    "gemini-2.5-flash-preview-tts": "Gemini 2.5 Flash TTS",
}

# name -> (style descriptor, gender). Gender per Google's Chirp3-HD voice
# list (the same voice set Gemini TTS uses) -- ai.google.dev's own Gemini
# TTS docs don't state gender, but Google Cloud's Chirp3-HD reference does.
GEMINI_TTS_VOICE_INFO = {
    "Zephyr": ("Bright", "Female"),
    "Puck": ("Upbeat", "Male"),
    "Charon": ("Informative", "Male"),
    "Kore": ("Firm", "Female"),
    "Fenrir": ("Excitable", "Male"),
    "Leda": ("Youthful", "Female"),
    "Orus": ("Firm", "Male"),
    "Aoede": ("Breezy", "Female"),
    "Callirrhoe": ("Easy-going", "Female"),
    "Autonoe": ("Bright", "Female"),
    "Enceladus": ("Breathy", "Male"),
    "Iapetus": ("Clear", "Male"),
    "Umbriel": ("Easy-going", "Male"),
    "Algieba": ("Smooth", "Male"),
    "Despina": ("Smooth", "Female"),
    "Erinome": ("Clear", "Female"),
    "Algenib": ("Gravelly", "Male"),
    "Rasalgethi": ("Informative", "Male"),
    "Laomedeia": ("Upbeat", "Female"),
    "Achernar": ("Soft", "Female"),
    "Alnilam": ("Firm", "Male"),
    "Schedar": ("Even", "Male"),
    "Gacrux": ("Mature", "Female"),
    "Pulcherrima": ("Forward", "Female"),
    "Achird": ("Friendly", "Male"),
    "Zubenelgenubi": ("Casual", "Male"),
    "Vindemiatrix": ("Gentle", "Female"),
    "Sadachbia": ("Lively", "Male"),
    "Sadaltager": ("Knowledgeable", "Male"),
    "Sulafat": ("Warm", "Female"),
}

# Backwards-compatible: name -> "Style (Gender)", e.g. "Bright (Female)".
GEMINI_TTS_VOICES = {
    name: f"{style} ({gender})" for name, (style, gender) in GEMINI_TTS_VOICE_INFO.items()
}

VOICE_STYLES = {
    "TRUSTED_EXPERT": (
        "A trusted product-review expert: knowledgeable, conversational, "
        "measured, and candid about trade-offs."
    ),
    "FRIENDLY_BUYER_GUIDE": (
        "A friendly buyer guide speaking naturally to one listener, with clear "
        "advice and no sales hype."
    ),
    "PREMIUM_REVIEW": (
        "A polished premium-product narrator: mature, warm, restrained, and "
        "confident."
    ),
    "ENERGETIC_ROUNDUP": (
        "An energetic roundup host: lively and engaging without shouting or "
        "rushing product names."
    ),
    "DOCUMENTARY": (
        "An informative documentary narrator: clear, composed, and precise."
    ),
}

ACCENTS = {
    "US_NEUTRAL": "neutral American English",
    "US_WARM": "warm conversational American English",
    "UK_NEUTRAL": "neutral British English",
    "AU_NEUTRAL": "neutral Australian English",
}


def _bounded_int(value, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(0, min(100, parsed))


def _clean_instruction(value, limit=500) -> str:
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(value or "")).strip()[
        :limit
    ]


def _parse_pronunciations(value) -> dict[str, str]:
    parsed = {}
    for line in str(value or "").splitlines()[:50]:
        if "=" not in line:
            continue
        term, pronunciation = (_clean_instruction(part, 80) for part in line.split("=", 1))
        if term and pronunciation:
            parsed[term] = pronunciation
    return parsed


def normalize_gemini_tts_settings(settings: dict | None) -> dict:
    source = settings if isinstance(settings, dict) else {}
    model = str(source.get("gemini_tts_model", "")).strip()
    voice = str(source.get("gemini_tts_voice", "")).strip()
    style = str(source.get("gemini_voice_style", "")).strip()
    accent = str(source.get("gemini_voice_accent", "")).strip()
    return {
        "model": model
        if model in GEMINI_TTS_MODELS
        else "gemini-3.1-flash-tts-preview",
        "voice": voice if voice in GEMINI_TTS_VOICES else "Sadaltager",
        "style": style if style in VOICE_STYLES else "TRUSTED_EXPERT",
        "pace": _bounded_int(source.get("gemini_voice_pace"), 50),
        "energy": _bounded_int(source.get("gemini_voice_energy"), 45),
        "warmth": _bounded_int(source.get("gemini_voice_warmth"), 60),
        "accent": accent if accent in ACCENTS else "US_NEUTRAL",
        "instruction": _clean_instruction(source.get("gemini_voice_instruction")),
        "pronunciations": _parse_pronunciations(
            source.get("gemini_pronunciations")
        ),
    }


def _level(value: int, low: str, medium: str, high: str) -> str:
    if value < 34:
        return low
    if value > 66:
        return high
    return medium


def build_gemini_tts_prompt(text: str, settings: dict | None) -> str:
    config = normalize_gemini_tts_settings(settings)
    notes = [
        VOICE_STYLES[config["style"]],
        f"Use a {ACCENTS[config['accent']]} accent.",
        "Keep the pace "
        + _level(config["pace"], "deliberate", "natural", "brisk but intelligible")
        + ".",
        "Keep the energy "
        + _level(config["energy"], "calm", "engaged", "high and controlled")
        + ".",
        "Make the delivery "
        + _level(config["warmth"], "neutral", "warm", "very warm and personable")
        + ".",
        "Use natural sentence-to-sentence variation and brief pauses at punctuation.",
    ]
    for term, pronunciation in config["pronunciations"].items():
        notes.append(f'Pronounce "{term}" as "{pronunciation}".')
    if config["instruction"]:
        notes.append(config["instruction"])
    notes.append("Read the script exactly. Do not add, remove, or summarize words.")
    return "Director's notes:\n- " + "\n- ".join(notes) + "\n\nScript:\n" + str(text)
