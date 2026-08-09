"""Paths and bundled tools shared by source and frozen desktop builds."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


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
