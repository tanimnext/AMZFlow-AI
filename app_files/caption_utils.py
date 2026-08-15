"""Sentence-level SRT generation from the render timeline."""

from __future__ import annotations

import re


def _timestamp(seconds: float) -> str:
    millis = max(0, round(seconds * 1000))
    hours, remainder = divmod(millis, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


# Broadcast/YouTube convention: short cues, at most two lines, ~32-42
# characters per line. One cue per SENTENCE (the old behaviour) meant a long
# sentence rendered as one enormous block that filled the frame once burned
# in, which is exactly what it did.
MAX_CHARS_PER_LINE = 38
MAX_LINES_PER_CUE = 2
MAX_CHARS_PER_CUE = MAX_CHARS_PER_LINE * MAX_LINES_PER_CUE


def _split_to_cues(sentence: str, limit: int = MAX_CHARS_PER_CUE) -> list[str]:
    """Break one sentence into cue-sized pieces on word boundaries."""
    words = sentence.split()
    if not words:
        return []
    cues: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) > limit and current:
            cues.append(current)
            current = word
        else:
            current = candidate
    if current:
        cues.append(current)
    return cues


def wrap_cue(text: str, width: int = MAX_CHARS_PER_LINE, max_lines: int = MAX_LINES_PER_CUE) -> str:
    """Hard-wrap a cue onto at most `max_lines` lines.

    SRT/ASS renderers only break on the newlines actually present in the
    cue, so without this a long cue is laid out as a single line that the
    renderer then overflows across the whole frame.
    """
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) > width and current:
            lines.append(current)
            current = word
            if len(lines) == max_lines:
                break
        else:
            current = candidate
    if current and len(lines) < max_lines:
        lines.append(current)
    return "\n".join(lines)


def build_srt(entries: list[dict]) -> str:
    captions = []
    index = 1
    for entry in entries:
        text = re.sub(r"\s+", " ", str(entry.get("text") or "")).strip()
        duration = max(0.0, float(entry.get("duration") or 0))
        if not text or duration <= 0:
            continue
        sentences = [
            sentence.strip()
            for sentence in re.split(r"(?<=[.!?])\s+", text)
            if sentence.strip()
        ]
        # Each sentence is further split into cue-sized pieces, and timing is
        # shared out by word count across ALL pieces so the cues still track
        # the narration.
        pieces: list[str] = []
        for sentence in sentences:
            pieces.extend(_split_to_cues(sentence))
        if not pieces:
            continue
        total_words = max(1, sum(len(piece.split()) for piece in pieces))
        cursor = float(entry.get("start") or 0)
        entry_end = cursor + duration
        for piece_index, piece in enumerate(pieces):
            if piece_index == len(pieces) - 1:
                end = entry_end
            else:
                share = len(piece.split()) / total_words
                end = min(entry_end, cursor + duration * share)
            captions.append(
                f"{index}\n{_timestamp(cursor)} --> {_timestamp(end)}\n{wrap_cue(piece)}\n"
            )
            index += 1
            cursor = end
    return "\n".join(captions)
