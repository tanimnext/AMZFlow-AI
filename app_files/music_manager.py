"""Deterministic routing for the bundled, license-tracked music library."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


MOOD_KEYWORDS = {
    "tech": {"tech", "laptop", "computer", "gaming", "camera", "phone", "audio"},
    "home": {"home", "kitchen", "vacuum", "bedroom", "furniture", "appliance"},
    "fitness": {"fitness", "gym", "running", "bike", "workout", "sport"},
    "beauty": {"beauty", "skin", "hair", "makeup", "wellness"},
    "premium": {"premium", "luxury", "professional", "flagship"},
}


def infer_mood(keyword: str) -> str:
    words = set(str(keyword).lower().replace("-", " ").split())
    for mood, candidates in MOOD_KEYWORDS.items():
        if words & candidates:
            return mood
    return "general"


def select_track(
    keyword: str,
    manifest_path: str | Path,
    *,
    recent_track_ids: list[str] | None = None,
    required_mood: str | None = None,
) -> dict[str, Any]:
    manifest = Path(manifest_path).resolve()
    library = manifest.parent
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    tracks = payload.get("tracks")
    if not isinstance(tracks, list) or not tracks:
        raise ValueError("Music library contains no tracks")

    valid = []
    for track in tracks:
        path = (library / str(track.get("file", ""))).resolve()
        try:
            path.relative_to(library)
        except ValueError:
            # One malformed `file` entry (e.g. a path-traversal attempt) used
            # to raise here and abort track selection for the *entire* run,
            # shipping every video in the batch without music. Skip just this
            # entry, same as the is_file()/license checks below do.
            print(f"[MUSIC] Skipping track with an invalid file path: {track.get('file')!r}")
            continue
        if not path.is_file() or not track.get("license"):
            continue
        item = dict(track)
        item["path"] = str(path)
        valid.append(item)
    if not valid:
        raise ValueError("Music library has no usable licensed tracks")

    if required_mood:
        # A hard filter, not a preference: "nature" means the user asked for
        # non-musical ambience specifically, so falling back to a melodic bed
        # would quietly hand them the thing they opted out of.
        restricted = [t for t in valid if required_mood in t.get("moods", [])]
        if not restricted:
            raise ValueError(f"Music library has no '{required_mood}' tracks")
        valid = restricted

    mood = infer_mood(keyword)
    matching = [track for track in valid if mood in track.get("moods", [])]
    candidates = matching or [
        track for track in valid if "general" in track.get("moods", [])
    ] or valid
    recent = set((recent_track_ids or [])[-5:])
    non_recent = [track for track in candidates if track.get("id") not in recent]
    if not non_recent:
        non_recent = [
            track for track in valid if track.get("id") not in recent
        ] or candidates
    digest = hashlib.sha256(str(keyword).lower().encode("utf-8")).digest()
    chosen = non_recent[int.from_bytes(digest[:4], "big") % len(non_recent)]
    return chosen
