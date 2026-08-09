"""Verified GitHub Release updates for portable frozen distributions."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

try:
    from .runtime_support import distribution_location, is_frozen, resource_dir, version
except ImportError:  # frozen app imports web_app modules from sys.path
    from runtime_support import distribution_location, is_frozen, resource_dir, version


SEMVER = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?$")


def _semver(value: str) -> tuple[int, int, int, int, str]:
    match = SEMVER.fullmatch(str(value).strip())
    if not match:
        raise ValueError(f"Invalid semantic version: {value}")
    major, minor, patch, prerelease = match.groups()
    return int(major), int(minor), int(patch), 1 if prerelease is None else 0, prerelease or ""


def is_newer_version(candidate: str, current: str) -> bool:
    try:
        return _semver(candidate) > _semver(current)
    except ValueError:
        return False


def platform_key() -> str:
    machine = platform.machine().lower()
    arch = "arm64" if machine in {"arm64", "aarch64"} else "x64"
    if sys.platform == "win32":
        return f"windows-{arch}"
    if sys.platform == "darwin":
        return f"macos-{arch}"
    return f"linux-{arch}"


def select_release_assets(release: dict, target: str) -> tuple[dict | None, dict | None]:
    suffix = f"-{target}.zip"
    assets = release.get("assets") or []
    archive = next((item for item in assets if str(item.get("name", "")).endswith(suffix)), None)
    if not archive:
        return None, None
    checksum_name = f"{archive['name']}.sha256"
    checksum = next((item for item in assets if item.get("name") == checksum_name), None)
    return archive, checksum


def verify_checksum(archive: Path, checksum_text: str) -> bool:
    expected = str(checksum_text).strip().split()[0].lower() if checksum_text.strip() else ""
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        return False
    digest = hashlib.sha256()
    with Path(archive).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest() == expected


def _repository() -> str:
    override = os.environ.get("AMZFLOW_UPDATE_REPOSITORY", "").strip()
    if override:
        return override
    try:
        config = json.loads((resource_dir() / "release_config.json").read_text(encoding="utf-8"))
        return str(config.get("github_repository", "")).strip()
    except (OSError, ValueError):
        return ""


def _request(url: str, *, timeout: int = 8):
    request = urllib.request.Request(url, headers={"User-Agent": f"AmzFlow-AI/{version()}"})
    return urllib.request.urlopen(request, timeout=timeout)


def _download(url: str, target: Path) -> None:
    with _request(url, timeout=30) as response, target.open("wb") as handle:
        shutil.copyfileobj(response, handle, length=1024 * 1024)


def maybe_start_update() -> bool:
    """Stage and hand off an update. Return True when this process must exit."""
    if not is_frozen() or os.environ.get("AMZFLOW_DISABLE_AUTO_UPDATE") == "1":
        return False
    repository = _repository()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        return False
    try:
        with _request(f"https://api.github.com/repos/{repository}/releases/latest") as response:
            release = json.load(response)
        if release.get("draft") or release.get("prerelease"):
            return False
        if not is_newer_version(str(release.get("tag_name", "")), version()):
            return False
        archive_asset, checksum_asset = select_release_assets(release, platform_key())
        if not archive_asset or not checksum_asset:
            return False

        staging = Path(tempfile.mkdtemp(prefix="amzflow-update-"))
        archive = staging / archive_asset["name"]
        _download(archive_asset["browser_download_url"], archive)
        with _request(checksum_asset["browser_download_url"]) as response:
            checksum_text = response.read(4096).decode("ascii", errors="strict")
        if not verify_checksum(archive, checksum_text):
            shutil.rmtree(staging, ignore_errors=True)
            return False

        updater_name = "AmzFlowUpdater.exe" if os.name == "nt" else "AmzFlowUpdater"
        bundled_updater = Path(sys.executable).resolve().parent / updater_name
        if sys.platform == "darwin":
            bundled_updater = Path(sys.executable).resolve().with_name(updater_name)
        if not bundled_updater.is_file():
            return False
        updater = staging / updater_name
        shutil.copy2(bundled_updater, updater)
        updater.chmod(updater.stat().st_mode | 0o111)
        install_root, restart_relative = distribution_location()
        command = [
            str(updater), "--pid", str(os.getpid()), "--archive", str(archive),
            "--install-root", str(install_root), "--restart", restart_relative,
        ]
        kwargs = {"close_fds": True, "start_new_session": True}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
            kwargs.pop("start_new_session", None)
        subprocess.Popen(command, **kwargs)
        return True
    except Exception as exc:
        print(f"[UPDATE] Continuing without update: {exc}", flush=True)
        return False
