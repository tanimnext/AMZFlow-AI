#!/usr/bin/env python3
"""Dispatch a native GitHub Actions desktop release and download its artifacts."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path


VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
PLATFORMS = {"windows", "macos", "both"}


def validate_version(value: str) -> str:
    value = value.strip()
    if not VERSION_PATTERN.fullmatch(value):
        raise ValueError("Version must use X.Y.Z format, for example 7.1.0")
    return value


def validate_platforms(value: str) -> str:
    value = value.strip().lower()
    if value not in PLATFORMS:
        raise ValueError("Platforms must be windows, macos, or both")
    return value


def workflow_dispatch_args(version: str, platforms: str, branch: str) -> list[str]:
    return [
        "gh",
        "workflow",
        "run",
        "release.yml",
        "--ref",
        branch,
        "-f",
        f"version={validate_version(version)}",
        "-f",
        f"platforms={validate_platforms(platforms)}",
    ]


def artifact_names(platforms: str) -> list[str]:
    platforms = validate_platforms(platforms)
    names = []
    if platforms in {"windows", "both"}:
        names.append("portable-Windows")
    if platforms in {"macos", "both"}:
        names.append("portable-macOS")
    return names


def run(args: list[str], *, capture: bool = False) -> str:
    completed = subprocess.run(
        args,
        check=True,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )
    return completed.stdout.strip() if capture else ""


def git(*args: str) -> str:
    return run(["git", *args], capture=True)


def ensure_release_ready() -> str:
    if not shutil.which("gh"):
        raise RuntimeError(
            "GitHub CLI (gh) is missing. Install it once from https://cli.github.com/"
        )
    try:
        run(["gh", "auth", "status"], capture=True)
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or "").strip()
        raise RuntimeError(
            "GitHub login is not ready. Run: gh auth login"
            + (f"\n{detail}" if detail else "")
        ) from exc

    try:
        git("remote", "get-url", "origin")
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            "GitHub remote is missing. Create/push the repository, then add it as "
            "origin before building a Windows release."
        ) from exc

    branch = git("branch", "--show-current")
    if not branch:
        raise RuntimeError("Check out a named Git branch before building a release.")
    if git("status", "--porcelain"):
        raise RuntimeError(
            "Uncommitted files exist. Commit the intended release files first so the "
            "Windows runner builds exactly the same source."
        )
    try:
        remote_line = git(
            "ls-remote", "--heads", "origin", f"refs/heads/{branch}"
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            "Could not read the GitHub branch. Check the origin URL and Git access."
        ) from exc
    if not remote_line:
        raise RuntimeError(
            f"Branch '{branch}' is not pushed yet. Run: git push -u origin {branch}"
        )
    local_commit = git("rev-parse", "HEAD")
    remote_commit = remote_line.split()[0]
    if local_commit != remote_commit:
        raise RuntimeError(
            "Local and GitHub commits differ. Pull or push as appropriate before "
            "building, so the native runner uses exactly the reviewed source."
        )
    return branch


def workflow_run_ids(branch: str) -> set[int]:
    raw = run(
        [
            "gh",
            "run",
            "list",
            "--workflow",
            "release.yml",
            "--branch",
            branch,
            "--event",
            "workflow_dispatch",
            "--limit",
            "20",
            "--json",
            "databaseId",
        ],
        capture=True,
    )
    return {int(item["databaseId"]) for item in json.loads(raw or "[]")}


def wait_for_new_run(branch: str, previous: set[int], timeout: int = 90) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        new_ids = workflow_run_ids(branch) - previous
        if new_ids:
            return max(new_ids)
        time.sleep(2)
    raise RuntimeError("GitHub accepted the workflow but its run did not appear in time.")


def build_release(version: str, platforms: str, download_dir: Path) -> int:
    branch = ensure_release_ready()
    previous = workflow_run_ids(branch)
    print(f"Starting native {platforms} release v{version} on GitHub...", flush=True)
    run(workflow_dispatch_args(version, platforms, branch))
    run_id = wait_for_new_run(branch, previous)
    print(f"GitHub run: {run_id}", flush=True)
    run(["gh", "run", "watch", str(run_id), "--exit-status"])

    download_dir.mkdir(parents=True, exist_ok=True)
    for artifact in artifact_names(platforms):
        run(
            [
                "gh",
                "run",
                "download",
                str(run_id),
                "--name",
                artifact,
                "--dir",
                str(download_dir),
            ]
        )
    return run_id


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument(
        "--platforms", choices=sorted(PLATFORMS), default="windows"
    )
    parser.add_argument("--download-dir", type=Path, default=Path("release"))
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        version = validate_version(args.version)
        platforms = validate_platforms(args.platforms)
        if args.dry_run:
            print(" ".join(workflow_dispatch_args(version, platforms, "<current-branch>")))
            print("Artifacts: " + ", ".join(artifact_names(platforms)))
            return 0
        run_id = build_release(version, platforms, args.download_dir.resolve())
        print(f"Release v{version} completed (GitHub run {run_id}).")
        print(f"Portable files: {args.download_dir.resolve()}")
        return 0
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc)).strip()
        print(f"\nERROR: {detail}", file=sys.stderr)
        return 1
    except (ValueError, RuntimeError) as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
