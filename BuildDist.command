#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"

pause_after_error() {
  status=$?
  if [[ $status -ne 0 && -t 0 ]]; then
    echo
    read -r -p "Build stopped. Press Enter to close..." _
  fi
}
trap pause_after_error EXIT

CURRENT_VERSION="$(tr -d '[:space:]' < VERSION)"
VERSION_ARG="${1:-}"
if [[ -z "$VERSION_ARG" ]]; then
  echo "AmzFlow AI - New Version Builder"
  echo "Current version: $CURRENT_VERSION"
  echo
  read -r -p "New Version (X.Y.Z, example 7.1.0): " VERSION_ARG
fi

if [[ ! "$VERSION_ARG" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "Version must use X.Y.Z format, for example 7.1.0."
  exit 1
fi

PLATFORMS="${2:-}"
if [[ -z "$PLATFORMS" ]]; then
  read -r -p "Also build macOS? Windows is always included. [y/N]: " BUILD_MAC
  if [[ "$BUILD_MAC" =~ ^[Yy]$ ]]; then
    PLATFORMS="both"
  else
    PLATFORMS="windows"
  fi
fi

PYTHON_BIN="${PYTHON_BIN:-python3}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "Python 3 is required only to start the remote builder."
  exit 1
fi

echo
echo "Building: $PLATFORMS"
echo "GitHub will build native apps, publish v$VERSION_ARG for auto-update,"
echo "and download the portable ZIP/checksum into the release folder."
echo

"$PYTHON_BIN" scripts/remote_release.py \
  --version "$VERSION_ARG" \
  --platforms "$PLATFORMS" \
  --download-dir release

echo
echo "New Version v$VERSION_ARG is ready in: $(pwd)/release"
if [[ -t 0 ]]; then
  read -r -p "Press Enter to close..." _
fi
