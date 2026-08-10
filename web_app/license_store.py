"""HTTPS client for the server-side license service.

The desktop distribution contains only the public API URL. Google credentials,
the signing secret, and the license database remain on the Cloudflare Worker.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from urllib.parse import quote

import requests

try:
    from .runtime_support import resource_dir
    from .secure_paths import ACTIVATION_FILE, DATA_DIR
except ImportError:  # frozen application imports modules from web_app on sys.path
    from runtime_support import resource_dir
    from secure_paths import ACTIVATION_FILE, DATA_DIR


REQUEST_TIMEOUT_SECONDS = 12
ADMIN_TOKEN_FILE = DATA_DIR / "license_admin_token.txt"
_HTTPS_API = re.compile(r"^https://[A-Za-z0-9.-]+(?::\d+)?(?:/[A-Za-z0-9._~/-]*)?$")


def _api_url():
    override = os.environ.get("AMZFLOW_LICENSE_API_URL", "").strip().rstrip("/")
    if override:
        return override if _HTTPS_API.fullmatch(override) else ""
    try:
        config = json.loads((resource_dir() / "release_config.json").read_text(encoding="utf-8"))
        value = str(config.get("license_api_url", "")).strip().rstrip("/")
        return value if _HTTPS_API.fullmatch(value) else ""
    except (OSError, ValueError, TypeError):
        return ""


def _activation_state():
    try:
        value = json.loads(Path(ACTIVATION_FILE).read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            return {}
        required = ("email", "machine_id", "activation_token")
        return value if all(isinstance(value.get(key), str) and value[key] for key in required) else {}
    except (OSError, ValueError, TypeError):
        return {}


def saved_activation_email():
    return _activation_state().get("email", "")


def _save_activation(email, machine_id, token):
    target = Path(ACTIVATION_FILE)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = target.with_name(f"{target.name}.tmp")
    temporary.write_text(json.dumps({
        "email": email.strip().lower(),
        "machine_id": machine_id,
        "activation_token": token,
    }), encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(target)
    target.chmod(0o600)


def _license(payload):
    if not isinstance(payload, dict):
        raise ValueError("Invalid license response")
    required = ("email", "name", "used", "quota", "expiryDate", "expiryTime")
    if any(key not in payload for key in required):
        raise ValueError("Invalid license response")
    return {
        "email": str(payload["email"]).strip().lower(),
        "name": str(payload["name"]).strip(),
        "used": payload["used"],
        "quota": payload["quota"],
        "expiry_date": str(payload["expiryDate"]),
        "expiry_time": str(payload["expiryTime"]),
    }


def _request(method, path, *, body=None, bearer=""):
    base = _api_url()
    if not base:
        return False, "License service is not configured. Please install the latest release."
    headers = {"Accept": "application/json", "User-Agent": "AmzFlow-AI-Desktop"}
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    try:
        response = requests.request(
            method, f"{base}{path}", json=body, headers=headers,
            timeout=REQUEST_TIMEOUT_SECONDS, allow_redirects=False,
        )
        payload = response.json()
    except (requests.RequestException, ValueError):
        return False, "License service is unavailable. Check your internet connection and try again."
    if 200 <= response.status_code < 300:
        return True, payload
    message = "License request failed. Please verify your details and try again."
    if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
        candidate = payload["error"].get("message")
        if isinstance(candidate, str) and 0 < len(candidate) <= 200:
            message = candidate
    return False, message


def activate_license(email, name, machine_id):
    success, payload = _request("POST", "/v1/activations", body={
        "email": str(email).strip().lower(), "name": str(name).strip(),
        "machineId": str(machine_id),
    })
    if not success:
        return False, payload
    try:
        data = payload["data"]
        token = data["activationToken"]
        if not isinstance(token, str) or not token.startswith("v1.") or len(token) > 4096:
            raise ValueError("Invalid activation token")
        result = _license(data["license"])
        _save_activation(result["email"], machine_id, token)
        return True, result
    except (KeyError, TypeError, ValueError, OSError):
        return False, "License service returned an invalid response."


def verify_activation_local(email, current_machine_id, user_name_input=None):
    state = _activation_state()
    if state.get("email") != str(email or "").strip().lower() or state.get("machine_id") != current_machine_id:
        return False, "Activation is required for this computer."
    success, payload = _request(
        "POST", "/v1/licenses/verify", body={"machineId": current_machine_id},
        bearer=state["activation_token"],
    )
    if not success:
        return False, payload
    try:
        return True, _license(payload["data"]["license"])
    except (KeyError, TypeError, ValueError):
        return False, "License service returned an invalid response."


def update_usage(email, count):
    state = _activation_state()
    if state.get("email") != str(email or "").strip().lower():
        return False, "Activation is required."
    success, payload = _request(
        "POST", "/v1/usage",
        body={"machineId": state["machine_id"], "used": int(count)},
        bearer=state["activation_token"],
    )
    return (True, "Updated") if success else (False, payload)


def _admin_token():
    value = os.environ.get("AMZFLOW_LICENSE_ADMIN_TOKEN", "").strip()
    if value:
        return value
    try:
        return ADMIN_TOKEN_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _admin_request(method, path, body=None):
    token = _admin_token()
    if len(token) < 32:
        return False, "License admin token is not configured on this computer."
    return _request(method, path, body=body, bearer=token)


def list_users():
    success, payload = _admin_request("GET", "/v1/admin/users?page=1&pageSize=100")
    if not success:
        return []
    try:
        users = []
        for user in payload["data"]:
            users.append(_license(user) | {
                "machine_id": str(user.get("machineId", "")),
                "last_login": str(user.get("lastLogin", "")),
            })
        return users
    except (KeyError, TypeError, ValueError):
        return []


def find_user(email):
    target = str(email).strip().lower()
    return next((user for user in list_users() if user["email"] == target), None)


def _quota(value):
    if str(value).strip().lower() == "unlimited":
        return "Unlimited"
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


def add_user(name, email, expiry_date="Lifetime", expiry_time="00:00", quota="Unlimited"):
    success, payload = _admin_request("POST", "/v1/admin/users", {
        "name": str(name).strip(), "email": str(email).strip().lower(), "quota": _quota(quota),
        "expiryDate": expiry_date or "Lifetime", "expiryTime": expiry_time or "00:00",
    })
    if not success:
        return False, payload
    return True, "User added. They can activate in the app with just their email."


def update_user(email, **fields):
    mapping = {"name": "name", "quota": "quota", "expiry_date": "expiryDate", "expiry_time": "expiryTime"}
    body = {mapping[key]: (_quota(value) if key == "quota" else value) for key, value in fields.items() if key in mapping}
    success, payload = _admin_request("PATCH", f"/v1/admin/users/{quote(str(email).strip().lower(), safe='')}", body)
    return (True, "Updated") if success else (False, payload)


def delete_user(email):
    success, payload = _admin_request("DELETE", f"/v1/admin/users/{quote(str(email).strip().lower(), safe='')}")
    return (True, "Deleted") if success else (False, payload)


def _admin_action(email, action, success_message):
    success, payload = _admin_request("POST", f"/v1/admin/users/{quote(str(email).strip().lower(), safe='')}/{action}", {})
    return (True, success_message) if success else (False, payload)


def reset_machine(email):
    return _admin_action(email, "reset-machine", "Machine binding reset. They can activate again with just their email.")


def reset_usage(email):
    return _admin_action(email, "reset-usage", "Usage counter reset")
