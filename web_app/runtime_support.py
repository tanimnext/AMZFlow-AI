"""Paths and bundled tools shared by source and frozen desktop builds."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def quiet_subprocess_kwargs() -> dict:
    """Keyword args that stop a child process from opening a console window.

    The frozen Windows app is built --windowed, so it has no console of its
    own; every subprocess it spawns (ffmpeg, ffprobe, the render worker)
    therefore gets a brand-new console window. One render fires hundreds of
    those, and each one flashes on screen AND steals keyboard focus, which
    is what makes the machine unusable mid-render. CREATE_NO_WINDOW keeps
    them headless. No-op off Windows.
    """
    if os.name != "nt":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    return {
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
        "startupinfo": startupinfo,
    }


def resource_dir() -> Path:
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        return Path(frozen_root).resolve()
    return Path(__file__).resolve().parents[1]


def version() -> str:
    try:
        return (resource_dir() / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        return "0.0.0"


def resolve_binary(name: str, *, resource_root: Path | None = None) -> str:
    root = Path(resource_root) if resource_root else resource_dir()
    executable = f"{name}.exe" if os.name == "nt" else name
    bundled = root / "bin" / executable
    if bundled.is_file():
        return str(bundled)

    found = shutil.which(name)
    if found:
        return found
    for prefix in ("/opt/homebrew/bin", "/usr/local/bin"):
        candidate = Path(prefix) / name
        if candidate.is_file():
            return str(candidate)
    return name


def kokoro_files(voice: str, *, resource_root: Path | None = None) -> dict:
    root = Path(resource_root) if resource_root else resource_dir()
    model_root = root / "models" / "kokoro"
    files = {
        "config": model_root / "config.json",
        "model": model_root / "kokoro-v1_0.pth",
        "voice": model_root / "voices" / f"{voice}.pt",
    }
    files["complete"] = all(path.is_file() for path in files.values())
    return files


def distribution_location() -> tuple[Path, str]:
    """Return the replaceable wrapper directory and executable within it."""
    executable = Path(sys.executable).resolve()
    if sys.platform == "darwin":
        for parent in executable.parents:
            if parent.suffix == ".app":
                wrapper = parent.parent
                return wrapper, str(executable.relative_to(wrapper))
    return executable.parent, executable.name
