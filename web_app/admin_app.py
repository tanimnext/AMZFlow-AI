"""Local admin dashboard for managing Ez AmazTube Pro licensed users.

Standalone Flask app, separate port from the main app. Launch with
admin_dashboard.command (opens the browser automatically). Runs only on
localhost — meant for the tool owner's own machine.
"""
import os
import sys
import hashlib
import hmac
import secrets

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(BASE_DIR)

from flask import Flask, render_template, render_template_string, request, redirect, url_for, flash, session
import license_store
from branding import BRAND
from secure_paths import DATA_DIR

app = Flask(__name__)
ADMIN_PASSWORD_FILE = DATA_DIR / "admin_password.txt"
if not ADMIN_PASSWORD_FILE.exists():
    ADMIN_PASSWORD_FILE.write_text(secrets.token_urlsafe(18), encoding="utf-8")
    ADMIN_PASSWORD_FILE.chmod(0o600)
ADMIN_PASSWORD = os.environ.get("EZ_AMAZTUBE_ADMIN_PASSWORD") or ADMIN_PASSWORD_FILE.read_text(encoding="utf-8").strip()
app.secret_key = os.environ.get("EZ_AMAZTUBE_ADMIN_SESSION_SECRET") or hashlib.sha256(
    ADMIN_PASSWORD.encode("utf-8")
).hexdigest()
app.config.update(
    SESSION_COOKIE_NAME="ez_admin_session",
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Strict",
)


@app.before_request
def require_admin_login():
    if request.remote_addr not in {"127.0.0.1", "::1"}:
        return "Local access only", 403
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        supplied = request.form.get("csrf_token", "") or request.headers.get(
            "X-CSRF-Token", ""
        )
        if not secrets.compare_digest(supplied, session["csrf_token"]):
            return "Invalid CSRF token", 403
    if request.endpoint not in {"login", "static"} and not session.get("is_admin"):
        return redirect(url_for("login"))


@app.context_processor
def inject_csrf_token():
    return {"csrf_token": session.get("csrf_token", ""), "brand": BRAND}


@app.after_request
def secure_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@app.route("/login", methods=["GET", "POST"])
def login():
    error = ""
    if request.method == "POST":
        supplied = request.form.get("password", "")
        if hmac.compare_digest(supplied, ADMIN_PASSWORD):
            session.clear()
            session["is_admin"] = True
            return redirect(url_for("dashboard"))
        error = "Incorrect admin password"
    return render_template_string(
        """<!doctype html><html lang="en"><head><meta charset="utf-8">
        <meta name="viewport" content="width=device-width,initial-scale=1">
        <title>Admin Login &middot; {{ brand.name }}</title>
        <style>
            body { font: 15px -apple-system, system-ui, sans-serif; background: #f8fafc; color: #0f172a;
                   display: grid; place-items: center; min-height: 100vh; margin: 0; }
            form { width: min(360px, 90vw); padding: 28px; background: #fff; border: 1px solid #e2e8f0;
                   border-radius: 16px; box-shadow: 0 4px 16px rgba(15,23,42,.06); }
            h1 { font-size: 18px; margin: 0 0 4px; }
            .eyebrow { font-size: 11px; font-weight: 700; letter-spacing: .06em; text-transform: uppercase; color: #f97316; margin: 0 0 12px; }
            p.error { color: #dc2626; font-size: 13px; }
            label { font-size: 12px; font-weight: 600; color: #475569; display: block; margin-bottom: 5px; }
            input { display: block; width: 100%; box-sizing: border-box; padding: 10px 12px; margin: 0 0 16px;
                    border: 1px solid #cbd5e1; border-radius: 8px; font-size: 14px; }
            input:focus { outline: none; border-color: #f97316; box-shadow: 0 0 0 3px rgba(249,115,22,.25); }
            button { padding: 10px 18px; border: none; border-radius: 8px; background: #f97316; color: #fff;
                     font-weight: 700; font-size: 14px; cursor: pointer; width: 100%; }
            button:hover { background: #ea580c; }
        </style>
        </head><body>
        <form method="post">
        <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
        <p class="eyebrow">{{ brand.name }}</p>
        <h1>Admin Login</h1>
        {% if error %}<p class="error">{{ error }}</p>{% endif %}
        <label for="password">Admin password</label>
        <input id="password" name="password" type="password" required autofocus>
        <button type="submit">Sign in</button></form></body></html>""",
        error=error,
        csrf_token=session["csrf_token"],
        brand=BRAND,
    )


@app.route('/')
def dashboard():
    users = license_store.list_users()
    return render_template('admin_dashboard.html', users=users)


@app.route('/users/add', methods=['POST'])
def add_user():
    name = request.form.get('name', '').strip()
    email = request.form.get('email', '').strip()
    quota = request.form.get('quota', '').strip() or "Unlimited"
    expiry_date = request.form.get('expiry_date', '').strip() or "Lifetime"
    expiry_time = request.form.get('expiry_time', '').strip() or "00:00"
    max_devices = request.form.get('max_devices', '').strip() or "1"

    if not email:
        flash("Email is required", "error")
        return redirect(url_for('dashboard'))

    success, msg = license_store.add_user(name, email, expiry_date, expiry_time, quota, max_devices)
    flash(msg, "success" if success else "error")
    return redirect(url_for('dashboard'))


@app.route('/users/<email>/edit', methods=['POST'])
def edit_user(email):
    fields = {
        "name": request.form.get('name', '').strip(),
        "quota": request.form.get('quota', '').strip() or "Unlimited",
        "expiry_date": request.form.get('expiry_date', '').strip() or "Lifetime",
        "expiry_time": request.form.get('expiry_time', '').strip() or "00:00",
        "max_devices": request.form.get('max_devices', '').strip() or "1",
    }
    success, msg = license_store.update_user(email, **fields)
    flash(msg, "success" if success else "error")
    return redirect(url_for('dashboard'))


@app.route('/users/<email>/delete', methods=['POST'])
def delete_user(email):
    success, msg = license_store.delete_user(email)
    flash(msg, "success" if success else "error")
    return redirect(url_for('dashboard'))


@app.route('/users/<email>/reset-machine', methods=['POST'])
def reset_machine(email):
    success, msg = license_store.reset_machine(email)
    flash(msg, "success" if success else "error")
    return redirect(url_for('dashboard'))


@app.route('/users/<email>/reset-usage', methods=['POST'])
def reset_usage(email):
    success, msg = license_store.reset_usage(email)
    flash("Usage counter reset" if success else msg, "success" if success else "error")
    return redirect(url_for('dashboard'))


@app.route('/users/<email>/remove-device', methods=['POST'])
def remove_device(email):
    machine_id = request.form.get('machine_id', '').strip()
    if not machine_id:
        flash("machine_id is required", "error")
        return redirect(url_for('dashboard'))
    success, msg = license_store.remove_device(email, machine_id)
    flash(msg, "success" if success else "error")
    return redirect(url_for('dashboard'))


if __name__ == '__main__':
    print(f"Admin password: {ADMIN_PASSWORD}", flush=True)
    app.run(debug=False, host="127.0.0.1", port=7510)
