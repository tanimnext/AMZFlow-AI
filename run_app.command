#!/bin/bash

# Navigate to the script's directory
cd "$(dirname "$0")"

PYBIN="venv/bin/python3"

if [ ! -x "$PYBIN" ]; then
    echo "ERROR: $PYBIN not found or not executable."
    echo "Your venv looks missing/broken. Rebuild it with:"
    echo "  /opt/homebrew/bin/python3.12 -m venv venv"
    echo "  ./venv/bin/python3 -m pip install -r web_app/requirements_web.txt"
    read -p "Press enter to exit..."
    exit 1
fi

# Point the local dev run at the production license API (release builds get
# this from release_config.json instead; this script runs from source, which
# has no release_config.json, so the license page shows "not configured"
# without this).
export AMZFLOW_LICENSE_API_URL="${AMZFLOW_LICENSE_API_URL:-https://amzflow-license-api.tanimnext2.workers.dev}"

# Run the app using the venv's own interpreter directly (not via 'activate' +
# bare 'python3', which silently falls back to system Python if the venv is
# broken and gives no warning).
"$PYBIN" scripts/migrate_secrets.py || {
    echo "ERROR: Private-data migration failed. The app was not started."
    read -p "Press enter to exit..."
    exit 1
}
"$PYBIN" web_app/app.py

# Keep the terminal open if the app crashes
read -p "Press enter to continue..."
