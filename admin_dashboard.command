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

# Point the local dashboard at the production license API (release builds get
# this from release_config.json instead; this script runs from source).
export AMZFLOW_LICENSE_API_URL="${AMZFLOW_LICENSE_API_URL:-https://amzflow-license-api.tanimnext2.workers.dev}"

# Start the admin dashboard in the background, then open it in the browser
"$PYBIN" web_app/admin_app.py &
APP_PID=$!

sleep 2
open "http://127.0.0.1:7510"

# Keep the terminal open; stop the server when this window closes
trap "kill $APP_PID" EXIT
wait $APP_PID
