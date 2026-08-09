"""External updater used after the frozen desktop app has stopped."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path


def safe_extract(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.infolist():
            target = (root / member.filename).resolve()
            if target != root and root not in target.parents:
                raise ValueError(f"Unsafe archive member: {member.filename}")
        bundle.extractall(root)


def _wait_for_exit(pid: int, timeout: int = 60) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            return
        time.sleep(0.25)
    raise TimeoutError("AmzFlow AI did not close before update timeout")


def _payload_root(extracted: Path) -> Path:
    children = [item for item in extracted.iterdir() if item.name != "__MACOSX"]
    if len(children) == 1 and children[0].is_dir():
        return children[0]
    return extracted


def install_update(archive: Path, install_root: Path, restart: str, pid: int) -> None:
    staging = archive.parent / "extracted"
    safe_extract(archive, staging)
    payload = _payload_root(staging)
    _wait_for_exit(pid)

    install_root = install_root.resolve()
    backup = install_root.with_name(f"{install_root.name}.previous")
    if backup.exists():
        shutil.rmtree(backup)
    install_root.rename(backup)
    try:
        shutil.move(str(payload), str(install_root))
        executable = install_root / restart
        executable.chmod(executable.stat().st_mode | 0o111)
        subprocess.Popen([str(executable)], close_fds=True, start_new_session=os.name != "nt")
    except Exception:
        if install_root.exists():
            shutil.rmtree(install_root, ignore_errors=True)
        backup.rename(install_root)
        raise
    shutil.rmtree(backup, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pid", required=True, type=int)
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--install-root", required=True, type=Path)
    parser.add_argument("--restart", required=True)
    args = parser.parse_args()
    try:
        install_update(args.archive, args.install_root, args.restart, args.pid)
        return 0
    except Exception as exc:
        (args.archive.parent / "update-error.txt").write_text(str(exc), encoding="utf-8")
        return 1


if __name__ == "__main__":
    sys.exit(main())
