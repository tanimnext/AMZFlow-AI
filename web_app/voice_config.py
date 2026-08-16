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

# TRUSTED_EXPERT and ENERGETIC_ROUNDUP are the two paired-with-a-voice
# presets ("Ad Voiceover" -> Orus for single-product reviews, "Energetic
# Co-Host" -> Puck/Zephyr for best-of/list videos -- see
# amazon_video_maker.recommended_gemini_voice()). Wording matters here more
# than the other three: the earlier TRUSTED_EXPERT text still let the model
# read single-product reviews like a TV commercial voiceover, which is the
# one thing this style exists to avoid.
VOICE_STYLES = {
    "TRUSTED_EXPERT": (
        "Natural, confident and conversational product reviewer. Sound "
        "knowledgeable and trustworthy, not like a commercial announcer. Use "
        "a smooth medium pace, natural pauses and subtle emphasis. Present "
        "both strengths and limitations fairly. Avoid exaggerated "
        "excitement, hard-selling and overly promotional delivery."
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
        "Energetic and engaging product review host with a natural "
        "conversational style. Keep the pace lively but controlled. Use "
        "enthusiasm to maintain attention without sounding exaggerated, "
        "childish or salesy. Add natural pauses and emphasize important "
        "product differences, benefits and drawbacks."
    ),
    "DOCUMENTARY": (
        "An informative documentary narrator: clear, composed, and precise."
    ),
}

# Display labels for the settings UI -- distinct from the dict keys above
# (which stay stable for backward compatibility with already-saved settings
# files) and from the literal prompt text (which is written as an
# instruction to the model, not as a menu label).
VOICE_STYLE_LABELS = {
    "TRUSTED_EXPERT": "Ad Voiceover (single-product review)",
    "FRIENDLY_BUYER_GUIDE": "Friendly Buyer Guide",
    "PREMIUM_REVIEW": "Premium Review",
    "ENERGETIC_ROUNDUP": "Energetic Co-Host (best-of / list)",
    "DOCUMENTARY": "Documentary",
}

# Recommended Gemini/Vertex TTS voice for each of the two content-type-paired
# styles above. Orus is firm/male -- reads as a grounded, credible single
# reviewer rather than an announcer. Puck/Zephyr are upbeat/bright -- suit a
# faster-paced, energetic list format; picking between the two per render
# keeps a channel's list videos from all sounding identical.
RECOMMENDED_VOICES = {
    "TRUSTED_EXPERT": ("Orus",),
    "ENERGETIC_ROUNDUP": ("Puck", "Zephyr"),
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


# Each pronunciation is another quoted fragment sitting in the instruction
# line, and every one of them is something the model might decide to
# perform. Only the first few ride along.
_MAX_SPOKEN_PRONUNCIATIONS = 6


def build_gemini_tts_prompt(text: str, settings: dict | None) -> str:
    """Style direction plus the script, shaped so Gemini TTS does not mistake
    the direction for the performance.

    This used to emit a multi-line "Director's notes:" bullet list followed
    by a "Script:" heading. Gemini TTS regularly narrated that whole block,
    so finished videos opened by speaking the style descriptor out loud --
    "a friendly buyer guide speaking naturally to one listener, with clear
    advice and no sales hype" -- before any product narration started. A
    verbose, list-shaped preamble reads as prose, and prose is precisely
    what a TTS model is built to say.

    Google's documented pattern is one short instruction clause ending in a
    colon, with the words to speak after it, so that is what this builds
    now: a single line, one colon, then the script.
    """
    config = normalize_gemini_tts_settings(settings)

    style = VOICE_STYLES[config["style"]].rstrip(".")
    # Phrased as an order ("Narrate as ...") rather than a description, so
    # the line stays grammatically an instruction the model follows instead
    # of a sentence it recites.
    direction = [
        f"Narrate as {style[0].lower() + style[1:]}",
        f"in {ACCENTS[config['accent']]}",
        "at a " + _level(config["pace"], "deliberate", "natural", "brisk but clear") + " pace",
        "sounding " + _level(config["energy"], "calm", "engaged", "energetic but controlled"),
        "and " + _level(config["warmth"], "neutral", "warm", "very warm and personable"),
    ]

    extras = [
        f'say "{term}" as "{pronunciation}"'
        for term, pronunciation in list(config["pronunciations"].items())[
            :_MAX_SPOKEN_PRONUNCIATIONS
        ]
    ]
    if config["instruction"]:
        extras.append(config["instruction"].rstrip("."))

    line = ", ".join(direction)
    if extras:
        line += "; " + "; ".join(extras)
    # "one single narrator" is explicit because without it the model
    # sometimes performed a single-narrator script as a two-person read,
    # alternating male and female voices partway through a review.
    return (
        f"{line}. Speak as one single narrator, reading only the words after "
        f"this colon and nothing else: {str(text).strip()}"
    )
