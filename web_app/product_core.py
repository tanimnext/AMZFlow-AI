"""Safe project, metadata, ordering, and publishing primitives.

This module deliberately has no Flask dependency so the critical behaviour can
be tested without starting the desktop web application.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import shutil
import socket
import tempfile
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SECRET_MARKERS = (
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "client_id",
    "client_secret",
    "private_key",
    "password",
)
VALID_PRIVACY = {"public", "unlisted", "private"}
VALID_ORDER = {"list", "countdown"}


def slugify(value: str, fallback: str = "project") -> str:
    value = re.sub(r"[^a-zA-Z0-9]+", "-", str(value or "")).strip("-").lower()
    return (value[:80] or fallback).strip("-")


def is_safe_https_url(value: str) -> bool:
    """Reject non-HTTPS and local/private destinations before remote downloads."""
    from urllib.parse import urlparse

    try:
        parsed = urlparse(str(value or ""))
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.port not in (None, 443)
        ):
            return False
        host = parsed.hostname.rstrip(".")
        try:
            addresses = [ipaddress.ip_address(host)]
        except ValueError:
            addresses = {
                ipaddress.ip_address(item[4][0])
                for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
            }
        return bool(addresses) and all(address.is_global for address in addresses)
    except (OSError, ValueError):
        return False


def resolve_project_dir(root: os.PathLike[str] | str, project_id: str) -> Path:
    """Resolve an existing project ID while guaranteeing containment."""
    if not isinstance(project_id, str) or not project_id.strip():
        raise ValueError("A project ID is required")
    candidate_id = project_id.strip().replace("\\", "/")
    if candidate_id.startswith("/") or "\x00" in candidate_id:
        raise ValueError("Invalid project ID")
    parts = candidate_id.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError("Invalid project ID")

    safe_root = Path(root).resolve()
    candidate = safe_root.joinpath(*parts).resolve()
    try:
        candidate.relative_to(safe_root)
    except ValueError as exc:
        raise ValueError("Project path escapes the project library") from exc
    return candidate


def validate_output_root(
    value: os.PathLike[str] | str, *, home: os.PathLike[str] | str | None = None
) -> Path:
    """Allow project libraries only inside a user's home, never home itself."""
    if not str(value or "").strip():
        raise ValueError("An output folder is required")
    safe_home = Path(home or Path.home()).expanduser().resolve()
    candidate = Path(value).expanduser().resolve()
    if candidate == safe_home:
        raise ValueError("Choose a folder inside your home, not the home folder itself")
    try:
        candidate.relative_to(safe_home)
    except ValueError as exc:
        raise ValueError("Output folder must be inside your home folder") from exc
    return candidate


def order_products(products: Iterable[dict[str, Any]], mode: str) -> list[dict[str, Any]]:
    if mode not in VALID_ORDER:
        raise ValueError("product_order must be 'list' or 'countdown'")
    copied = [deepcopy(product) for product in products]
    total = len(copied)
    if mode == "countdown":
        copied.reverse()
        ranks = range(total, 0, -1)
    else:
        ranks = range(1, total + 1)
    for product, rank in zip(copied, ranks):
        product["rank"] = rank
    return copied


def public_settings(value: Any) -> Any:
    """Return a recursively redacted settings object suitable for a browser."""
    if isinstance(value, dict):
        return {
            key: public_settings(item)
            for key, item in value.items()
            if not any(marker in key.lower() for marker in SECRET_MARKERS)
        }
    if isinstance(value, list):
        return [public_settings(item) for item in value]
    return value


def _atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def atomic_json(path: Path, value: Any) -> None:
    _atomic_text(path, json.dumps(value, indent=2, ensure_ascii=False))


def format_youtube_text(metadata: dict[str, Any]) -> str:
    tags = metadata.get("tags", [])
    if isinstance(tags, str):
        tags_text = tags
    else:
        tags_text = ", ".join(str(tag).strip() for tag in tags if str(tag).strip())
    hashtags = metadata.get("hashtags", [])
    if isinstance(hashtags, str):
        hashtags_text = hashtags
    else:
        hashtags_text = " ".join(
            tag if str(tag).startswith("#") else f"#{tag}"
            for tag in hashtags
            if str(tag).strip()
        )
    chapters = metadata.get("chapters", [])
    if isinstance(chapters, str):
        chapters_text = chapters
    else:
        chapters_text = "\n".join(str(chapter) for chapter in chapters)
    return (
        f"[TITLE]\n{metadata.get('title', '').strip()}\n\n"
        f"[DESCRIPTION]\n{metadata.get('description', '').strip()}\n\n"
        f"[TAGS]\n{tags_text.strip()}\n\n"
        f"[HASHTAGS]\n{hashtags_text.strip()}\n\n"
        f"[CHAPTERS]\n{chapters_text.strip()}\n"
    )


def parse_youtube_text(text: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    current: str | None = None
    lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            if current:
                sections[current] = "\n".join(lines).strip()
            current = stripped[1:-1].lower()
            lines = []
        elif current:
            lines.append(line)
    if current:
        sections[current] = "\n".join(lines).strip()
    return {
        "title": sections.get("title", ""),
        "description": sections.get("description", ""),
        "tags": sections.get("tags", ""),
        "hashtags": sections.get("hashtags", ""),
        "chapters": sections.get("chapters", ""),
    }


def create_project_package(
    *,
    output_root: os.PathLike[str] | str,
    keyword: str,
    video_path: os.PathLike[str] | str,
    thumbnail_path: os.PathLike[str] | str,
    metadata: dict[str, Any],
    products: list[dict[str, Any]],
    sources: list[dict[str, Any]],
    qc_report: dict[str, Any],
    now: datetime | None = None,
) -> Path:
    video = Path(video_path)
    thumbnail = Path(thumbnail_path)
    if not video.is_file() or not thumbnail.is_file():
        raise ValueError("A rendered video and thumbnail are required")
    if qc_report.get("status") != "PASSED":
        raise ValueError("Only QC-passed renders can become ready projects")

    timestamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%d-%H%M%S")
    project_id = f"{timestamp}-{uuid.uuid4().hex[:8]}"
    project = Path(output_root).expanduser().resolve() / slugify(keyword) / project_id
    project.mkdir(parents=True, exist_ok=False)

    video_name = f"{slugify(keyword)}.mp4"
    shutil.copy2(video, project / video_name)
    shutil.copy2(thumbnail, project / "Thumbnail.jpg")
    _atomic_text(project / "youtube.txt", format_youtube_text(metadata))
    return project


def validate_publish_options(
    privacy: str, publish_at: str | None, *, now: datetime | None = None
) -> tuple[str, str | None]:
    if privacy not in VALID_PRIVACY:
        raise ValueError("privacy must be public, unlisted, or private")
    if not publish_at:
        return privacy, None
    if privacy != "private":
        raise ValueError("Scheduled videos must use private privacy status")
    try:
        parsed = datetime.fromisoformat(publish_at.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError("publish_at must be an ISO-8601 date-time") from exc
    if parsed.tzinfo is None:
        raise ValueError("publish_at must include a timezone")
    current = now or datetime.now(timezone.utc)
    if parsed.astimezone(timezone.utc) <= current.astimezone(timezone.utc):
        raise ValueError("publish_at must be in the future")
    return privacy, parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
