"""FFmpeg-backed, fail-closed quality checks for generated videos."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

try:
    from runtime_support import quiet_subprocess_kwargs
except ImportError:  # pragma: no cover - source layout without web_app on sys.path
    def quiet_subprocess_kwargs() -> dict:
        return {}


def probe_media(path: str | Path, ffprobe_bin: str = "ffprobe") -> dict[str, Any]:
    result = subprocess.run(
        [
            ffprobe_bin,
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type,duration,sample_rate:format=duration",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
        **quiet_subprocess_kwargs(),
    )
    payload = json.loads(result.stdout)
    streams = payload.get("streams", [])
    stream_types = {stream.get("codec_type") for stream in streams}
    video_stream = next(
        (stream for stream in streams if stream.get("codec_type") == "video"),
        {},
    )
    audio_stream = next(
        (stream for stream in streams if stream.get("codec_type") == "audio"),
        {},
    )
    return {
        "hasVideo": "video" in stream_types,
        "hasAudio": "audio" in stream_types,
        "duration": float(payload.get("format", {}).get("duration") or 0),
        "videoDuration": float(video_stream.get("duration") or 0),
        "audioDuration": float(audio_stream.get("duration") or 0),
        "audioSampleRate": int(audio_stream.get("sample_rate") or 0),
    }


def detect_silences(
    path: str | Path,
    ffmpeg_bin: str = "ffmpeg",
    *,
    noise_db: int = -45,
    minimum_seconds: float = 1.0,
) -> list[dict[str, float]]:
    result = subprocess.run(
        [
            ffmpeg_bin,
            "-hide_banner",
            "-nostats",
            "-i",
            str(path),
            "-af",
            f"silencedetect=noise={noise_db}dB:d={minimum_seconds}",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        timeout=120,
        **quiet_subprocess_kwargs(),
    )
    # This is the one QC signal specifically meant to catch missing/broken
    # narration. Without check=True, a failed probe (corrupt file, missing
    # codec) returned no "silence_start" lines, so it silently reported zero
    # unexpected silences instead of failing the check -- QC passed on a
    # video it never actually managed to analyze.
    result.check_returncode()
    starts: list[float] = []
    silences: list[dict[str, float]] = []
    for line in result.stderr.splitlines():
        start_match = re.search(r"silence_start:\s*([0-9.]+)", line)
        if start_match:
            starts.append(float(start_match.group(1)))
            continue
        end_match = re.search(
            r"silence_end:\s*([0-9.]+)\s*\|\s*silence_duration:\s*([0-9.]+)",
            line,
        )
        if end_match:
            end = float(end_match.group(1))
            duration = float(end_match.group(2))
            start = starts.pop(0) if starts else max(0.0, end - duration)
            silences.append({"start": start, "end": end, "duration": duration})
    return silences


def evaluate_qc(
    *,
    final_path: str | Path,
    planned_segment_ids: list[str],
    rendered_segments: dict[str, str],
    probe: dict[str, Any],
    silences: list[dict[str, float]],
    expected_duration: float,
    max_silence_seconds: float = 3.0,
    duration_tolerance_seconds: float = 2.0,
) -> dict[str, Any]:
    missing = [
        segment_id
        for segment_id in planned_segment_ids
        if not rendered_segments.get(segment_id)
    ]
    errors: list[str] = []
    if missing:
        errors.append(f"{len(missing)} planned segment(s) were not rendered")
    if not probe.get("hasVideo"):
        errors.append("Final output has no video stream")
    if not probe.get("hasAudio"):
        errors.append("Final output has no audio stream")
    audio_sample_rate = int(probe.get("audioSampleRate") or 0)
    if audio_sample_rate and audio_sample_rate not in {44100, 48000}:
        errors.append(
            f"Nonstandard audio sample rate: {audio_sample_rate} Hz "
            "(expected 44.1 or 48 kHz)"
        )
    video_duration = float(probe.get("videoDuration") or 0)
    audio_duration = float(probe.get("audioDuration") or 0)
    if (
        video_duration > 0
        and audio_duration > 0
        and video_duration - audio_duration > 0.25
    ):
        errors.append(
            f"Audio ends {video_duration - audio_duration:.2f}s before video"
        )
    actual_duration = float(probe.get("duration") or 0)
    if actual_duration <= 0:
        errors.append("Final output duration is invalid")
    elif abs(actual_duration - expected_duration) > max(
        duration_tolerance_seconds, expected_duration * 0.05
    ):
        errors.append(
            f"Duration mismatch: expected {expected_duration:.2f}s, "
            f"rendered {actual_duration:.2f}s"
        )
    unexpected = [
        silence
        for silence in silences
        if silence.get("duration", 0) > max_silence_seconds
    ]
    if unexpected:
        errors.append(f"{len(unexpected)} unexpected long silence(s) detected")
    return {
        "status": "FAILED" if errors else "PASSED",
        "finalPath": str(final_path),
        "plannedSegments": planned_segment_ids,
        "renderedSegments": sorted(rendered_segments),
        "missingSegments": missing,
        "probe": probe,
        "silences": silences,
        "unexpectedSilences": unexpected,
        "expectedDuration": expected_duration,
        "errors": errors,
    }


def run_media_qc(
    *,
    final_path: str | Path,
    planned_segment_ids: list[str],
    rendered_segments: dict[str, str],
    expected_duration: float,
    ffprobe_bin: str = "ffprobe",
    ffmpeg_bin: str = "ffmpeg",
) -> dict[str, Any]:
    path = Path(final_path)
    if not path.is_file():
        return evaluate_qc(
            final_path=path,
            planned_segment_ids=planned_segment_ids,
            rendered_segments=rendered_segments,
            probe={"hasVideo": False, "hasAudio": False, "duration": 0},
            silences=[],
            expected_duration=expected_duration,
        )
    try:
        probe = probe_media(path, ffprobe_bin)
        silences = detect_silences(path, ffmpeg_bin)
    except Exception as exc:
        return {
            "status": "FAILED",
            "finalPath": str(path),
            "plannedSegments": planned_segment_ids,
            "renderedSegments": sorted(rendered_segments),
            "missingSegments": [],
            "errors": [f"Media QC could not run: {exc}"],
        }
    return evaluate_qc(
        final_path=path,
        planned_segment_ids=planned_segment_ids,
        rendered_segments=rendered_segments,
        probe=probe,
        silences=silences,
        expected_duration=expected_duration,
    )
