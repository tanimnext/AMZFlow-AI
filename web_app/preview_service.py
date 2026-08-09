"""Async, cached voice previews.

v6 synthesized inline on the request thread with a random output filename every
time, so comparing two voices cost a full round trip each way and an AI33 Pro
preview pinned a Flask worker for up to two minutes. Here a preview is a job:
POST starts it (or returns instantly from cache), GET polls it.
"""

from __future__ import annotations

import os
import secrets
import threading
import time

import tts_engine
from secure_paths import PREVIEW_CACHE_DIR

# Previews are small (a couple of sentences). Keeping ~120 of them is well under
# 50 MB and covers a long voice-comparison session.
MAX_CACHED_PREVIEWS = 120
JOB_RETENTION_SECONDS = 900

_JOBS: dict[str, dict] = {}
_JOBS_LOCK = threading.Lock()


def _ensure_dir():
    PREVIEW_CACHE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)


def cache_path(key: str):
    return PREVIEW_CACHE_DIR / f"{key}.mp3"


def cached_audio(key: str):
    path = cache_path(key)
    if path.exists() and path.stat().st_size > 256:
        os.utime(path, None)  # LRU: touch on hit
        return path
    return None


def _prune_cache():
    try:
        files = sorted(
            PREVIEW_CACHE_DIR.glob("*.mp3"), key=lambda p: p.stat().st_mtime, reverse=True
        )
    except OSError:
        return
    for stale in files[MAX_CACHED_PREVIEWS:]:
        try:
            stale.unlink()
        except OSError:
            pass


def _prune_jobs():
    cutoff = time.time() - JOB_RETENTION_SECONDS
    for job_id, job in list(_JOBS.items()):
        if job.get("finished_at") and job["finished_at"] < cutoff:
            _JOBS.pop(job_id, None)


def _public(job: dict) -> dict:
    return {
        "jobId": job["id"],
        "status": job["status"],
        "audioUrl": job.get("audio_url"),
        "error": job.get("error"),
        "provider": job.get("provider"),
        "seconds": job.get("seconds"),
        "progress": job.get("progress"),
        "cached": job.get("cached", False),
    }


def start(text: str, config: dict, *, ffmpeg_bin="ffmpeg") -> dict:
    """Begins (or resolves from cache) a preview. Never blocks on the network."""
    _ensure_dir()
    key = tts_engine.cache_key(text, config)
    provider = str(config.get("service") or "edge")

    hit = cached_audio(key)
    if hit is not None:
        return {
            "jobId": key,
            "status": "done",
            "audioUrl": f"/preview_audio/{key}.mp3",
            "error": None,
            "provider": provider,
            "seconds": 0,
            "progress": None,
            "cached": True,
        }

    job_id = secrets.token_hex(8)
    job = {
        "id": job_id,
        "status": "running",
        "provider": provider,
        "cached": False,
        "started_at": time.time(),
        "finished_at": None,
        "progress": None,
    }
    with _JOBS_LOCK:
        _prune_jobs()
        _JOBS[job_id] = job

    def on_progress(done, total):
        job["progress"] = round(100 * done / max(1, total))

    def run():
        target = cache_path(key)
        # Synthesize to a temp file so a crashed render never leaves a
        # truncated file that later reads as a valid cache hit. The temp name
        # MUST still end in .mp3: ffmpeg picks its output muxer from the
        # output path's extension, and every provider (Kokoro, Gemini) writes
        # straight to this path via ffmpeg. `with_suffix(".<hex>.part")`
        # replaced ".mp3" outright, so ffmpeg saw a ".part" path with no
        # recognizable extension and failed to open the output file at all.
        temp = target.with_name(f"{target.stem}.{secrets.token_hex(4)}.part.mp3")
        try:
            result = tts_engine.synthesize(
                text, str(temp), config, ffmpeg_bin=ffmpeg_bin, on_progress=on_progress
            )
            os.replace(temp, target)
            target.chmod(0o600)
            job.update(
                status="done",
                audio_url=f"/preview_audio/{key}.mp3",
                seconds=result["seconds"],
                provider=result["provider"],
            )
            _prune_cache()
        except tts_engine.TTSError as exc:
            job.update(status="error", error=str(exc))
        except Exception as exc:  # noqa: BLE001 - surface anything to the UI
            job.update(status="error", error=f"Unexpected preview failure: {exc}")
        finally:
            job["finished_at"] = time.time()
            if temp.exists():
                try:
                    temp.unlink()
                except OSError:
                    pass

    threading.Thread(target=run, name=f"tts-preview-{provider}", daemon=True).start()
    return _public(job)


def status(job_id: str):
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
    if job:
        return _public(job)
    # A cache key doubles as a job id for instant hits, so a client that polls
    # one after a page reload still resolves.
    if cached_audio(job_id) is not None:
        return {
            "jobId": job_id,
            "status": "done",
            "audioUrl": f"/preview_audio/{job_id}.mp3",
            "error": None,
            "provider": None,
            "seconds": 0,
            "progress": None,
            "cached": True,
        }
    return None
