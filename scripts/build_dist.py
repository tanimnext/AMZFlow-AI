#!/usr/bin/env python3
"""Build a versioned, portable AmzFlow AI ZIP on the current OS."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def host_platform_key() -> str:
    machine = platform.machine().lower()
    arch = "arm64" if machine in {"arm64", "aarch64"} else "x64"
    if sys.platform == "win32":
        return f"windows-{arch}"
    if sys.platform == "darwin":
        return f"macos-{arch}"
    raise RuntimeError("Portable desktop builds are supported on Windows and macOS only")


def artifact_base(version: str, target: str) -> str:
    if not SEMVER.fullmatch(version):
        raise ValueError("Version must use X.Y.Z semantic version format")
    return f"AmzFlow-AI-{version}-{target}"


def _set_version(release_version: str) -> None:
    (ROOT / "VERSION").write_text(f"{release_version}\n", encoding="utf-8")


def copy_model_snapshot(snapshot: Path, destination: Path) -> Path:
    shutil.copytree(snapshot, destination, symlinks=False)
    return destination


def write_checksum(archive: Path) -> Path:
    digest = hashlib.sha256()
    with archive.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    checksum = archive.with_suffix(f"{archive.suffix}.sha256")
    # write_text() translates LF to CRLF on Windows. A byte write keeps the
    # checksum compatible with shasum on Windows, macOS, and Linux.
    checksum.write_bytes(f"{digest.hexdigest()}  {archive.name}\n".encode("ascii"))
    return checksum


def _stage_kokoro_model(destination: Path) -> Path:
    from huggingface_hub import snapshot_download

    voices = (
        "af_heart", "af_bella", "af_nicole", "af_sarah", "af_sky",
        "am_adam", "am_michael", "bf_emma", "bf_isabella", "bm_george", "bm_lewis",
    )
    patterns = ["config.json", "kokoro-v1_0.pth", "LICENSE"]
    patterns.extend(f"voices/{voice}.pt" for voice in voices)
    snapshot = Path(snapshot_download(repo_id="hexgrad/Kokoro-82M", allow_patterns=patterns))
    return copy_model_snapshot(snapshot, destination / "kokoro")


def _find_tool(name: str, ffmpeg_dir: Path | None) -> Path:
    executable = f"{name}.exe" if os.name == "nt" else name
    if ffmpeg_dir:
        candidate = ffmpeg_dir / executable
        if candidate.is_file():
            return candidate.resolve()
    found = shutil.which(name)
    if found:
        return Path(found).resolve()
    raise RuntimeError(f"{name} not found. Install FFmpeg or pass --ffmpeg-dir.")


def _pyinstaller(args: list[str]) -> None:
    # Keep PyInstaller's binary cache inside the project. This avoids locked or
    # read-only user-profile caches on managed Macs and Windows build agents.
    cache_dir = ROOT / "build" / "pyinstaller-cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    os.environ["PYINSTALLER_CONFIG_DIR"] = str(cache_dir)
    try:
        import PyInstaller.__main__
    except ImportError as exc:
        raise RuntimeError("PyInstaller is missing; install requirements-build.txt") from exc
    PyInstaller.__main__.run(args)


def _data_arg(source: Path, destination: str) -> str:
    return f"{source}{os.pathsep}{destination}"


def _binary_arg(source: Path) -> str:
    return f"{source}{os.pathsep}bin"


def build(release_version: str, repository: str, ffmpeg_dir: Path | None) -> Path:
    target = host_platform_key()
    base = artifact_base(release_version, target)
    ffmpeg = _find_tool("ffmpeg", ffmpeg_dir)
    ffprobe = _find_tool("ffprobe", ffmpeg_dir)
    _set_version(release_version)

    build_root = ROOT / "build" / "desktop"
    dist_root = build_root / "dist"
    work_root = build_root / "work"
    specs_root = build_root / "specs"
    release_root = ROOT / "release"
    shutil.rmtree(build_root, ignore_errors=True)
    release_root.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="amzflow-build-") as temp:
        generated = Path(temp)
        config = generated / "release_config.json"
        config.write_text(
            json.dumps({"github_repository": repository}, indent=2) + "\n",
            encoding="utf-8",
        )
        kokoro_model = _stage_kokoro_model(generated / "models")
        common = [
            "--noconfirm", "--clean", f"--distpath={dist_root}",
            f"--workpath={work_root}", f"--specpath={specs_root}",
            f"--paths={ROOT / 'web_app'}", f"--paths={ROOT / 'app_files'}",
        ]
        _pyinstaller([
            str(ROOT / "desktop_updater.py"), "--onefile", "--console",
            "--name=AmzFlowUpdater", *common,
        ])

        app_args = [
            str(ROOT / "desktop_main.py"), "--onedir", "--windowed",
            "--name=AmzFlow AI", "--osx-bundle-identifier=ai.amzflow.desktop",
            f"--add-data={_data_arg(ROOT / 'VERSION', '.')}",
            f"--add-data={_data_arg(config, '.')}",
            f"--add-data={_data_arg(kokoro_model, 'models/kokoro')}",
            f"--add-data={_data_arg(ROOT / 'web_app' / 'templates', 'web_app/templates')}",
            f"--add-data={_data_arg(ROOT / 'web_app' / 'static', 'web_app/static')}",
            f"--add-data={_data_arg(ROOT / 'web_app' / 'settings.json', 'web_app')}",
            f"--add-data={_data_arg(ROOT / 'app_files', 'app_files')}",
            f"--add-binary={_binary_arg(ffmpeg)}",
            f"--add-binary={_binary_arg(ffprobe)}",
            "--hidden-import=amazon_video_maker", "--hidden-import=rembg",
            "--hidden-import=kokoro", "--collect-all=kokoro",
            "--hidden-import=misaki.en", "--collect-data=misaki",
            "--collect-all=espeakng_loader",
            *common,
        ]
        if sys.platform != "darwin":
            app_args.remove("--osx-bundle-identifier=ai.amzflow.desktop")
        _pyinstaller(app_args)

    wrapper = release_root / base
    shutil.rmtree(wrapper, ignore_errors=True)
    wrapper.mkdir(parents=True)
    updater_suffix = ".exe" if os.name == "nt" else ""
    updater = dist_root / f"AmzFlowUpdater{updater_suffix}"
    if sys.platform == "darwin":
        app_bundle = dist_root / "AmzFlow AI.app"
        if not app_bundle.is_dir():
            raise RuntimeError(f"Expected macOS bundle was not created: {app_bundle}")
        shutil.copytree(app_bundle, wrapper / app_bundle.name, symlinks=True)
        updater_target = wrapper / app_bundle.name / "Contents" / "MacOS" / updater.name
        shutil.copy2(updater, updater_target)
    else:
        app_folder = dist_root / "AmzFlow AI"
        if not app_folder.is_dir():
            raise RuntimeError(f"Expected Windows folder was not created: {app_folder}")
        shutil.copytree(app_folder, wrapper, dirs_exist_ok=True)
        shutil.copy2(updater, wrapper / updater.name)

    archive = Path(shutil.make_archive(str(release_root / base), "zip", release_root, base))
    write_checksum(archive)
    shutil.rmtree(wrapper, ignore_errors=True)
    return archive


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default=(ROOT / "VERSION").read_text().strip())
    parser.add_argument("--github-repository", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--ffmpeg-dir", type=Path, default=os.environ.get("FFMPEG_DIR"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    target = host_platform_key()
    base = artifact_base(args.version, target)
    if args.dry_run:
        print(json.dumps({"version": args.version, "target": target, "artifact": f"{base}.zip"}))
        return 0
    archive = build(args.version, args.github_repository, args.ffmpeg_dir)
    print(f"Created {archive}")
    print(f"Created {archive}.sha256")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
