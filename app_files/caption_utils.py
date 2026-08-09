"""Sentence-level SRT generation from the render timeline."""

from __future__ import annotations

import re


def _timestamp(seconds: float) -> str:
    millis = max(0, round(seconds * 1000))
    hours, remainder = divmod(millis, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


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
        total_words = max(1, sum(len(sentence.split()) for sentence in sentences))
        cursor = float(entry.get("start") or 0)
        entry_end = cursor + duration
        for sentence_index, sentence in enumerate(sentences):
            if sentence_index == len(sentences) - 1:
                end = entry_end
            else:
                share = len(sentence.split()) / total_words
                end = min(entry_end, cursor + duration * share)
            captions.append(
                f"{index}\n{_timestamp(cursor)} --> {_timestamp(end)}\n{sentence}\n"
            )
            index += 1
            cursor = end
    return "\n".join(captions)
