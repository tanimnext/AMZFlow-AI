#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"

VERSION_ARG="${1:-$(tr -d '[:space:]' < VERSION)}"
PYTHON_BIN="${PYTHON_BIN:-python3.12}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  PYTHON_BIN="python3"
fi

if ! command -v ffmpeg >/dev/null 2>&1 || ! command -v ffprobe >/dev/null 2>&1; then
  echo "FFmpeg build tools are missing. Install once with: brew install ffmpeg"
  exit 1
fi

"$PYTHON_BIN" -m venv .build-venv
.build-venv/bin/python -m pip install --upgrade pip
.build-venv/bin/python -m pip install -r requirements-build.txt
.build-venv/bin/python scripts/build_dist.py --version "$VERSION_ARG"

echo "Portable ZIP is ready in: $(pwd)/release"
