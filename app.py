import importlib.util
import json
import os
import subprocess
from datetime import datetime, timezone, timedelta
from functools import wraps
from pathlib import Path

import jwt
from flask import Flask, request, jsonify, render_template, abort

BASE_DIR   = Path(__file__).parent
JOBS_FILE  = BASE_DIR / "config" / "jobs.json"

app = Flask(__name__)


def load_config():
    # Reload config.py fresh each call so changes take effect without restart
    spec = importlib.util.spec_from_file_location("config", BASE_DIR / "config" / "config.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return {"auth": mod.auth, "email": mod.email}


def load_jobs():
    with open(JOBS_FILE) as f:
        return json.load(f)


def save_jobs(data):
    with open(JOBS_FILE, "w") as f:
        json.dump(data, f, indent=2)


def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            abort(401)
        token = auth_header[7:]
        config = load_config()
        try:
            jwt.decode(token, config["auth"]["jwt_secret"], algorithms=["HS256"])
        except jwt.InvalidTokenError:
            abort(401)
        return f(*args, **kwargs)
    return decorated


# --- Auth ---

@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    config = load_config()
    if data.get("password") != config["auth"]["password"]:
        return jsonify({"error": "Invalid password"}), 401
    expiry = datetime.now(timezone.utc) + timedelta(hours=config["auth"]["token_expiry_hours"])
    token = jwt.encode(
        {"exp": expiry},
        config["auth"]["jwt_secret"],
        algorithm="HS256"
    )
    return jsonify({"token": token})


# --- Jobs API ---

@app.route("/api/jobs", methods=["GET"])
@require_auth
def get_jobs():
    return jsonify(load_jobs())


@app.route("/api/jobs/<job_id>", methods=["PATCH"])
@require_auth
def update_job(job_id):
    data = request.get_json(silent=True) or {}
    jobs_data = load_jobs()
    job = next((j for j in jobs_data["jobs"] if j["id"] == job_id), None)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    if "enabled" in data:
        job["enabled"] = bool(data["enabled"])
    if "params" in data and isinstance(data["params"], dict):
        job["params"].update(data["params"])
    save_jobs(jobs_data)
    return jsonify(job)


@app.route("/api/jobs/<job_id>/run", methods=["POST"])
@require_auth
def run_job(job_id):
    jobs_data = load_jobs()
    job = next((j for j in jobs_data["jobs"] if j["id"] == job_id), None)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    script = Path(job["script"])
    if not script.is_absolute():
        script = BASE_DIR / script
    if not script.exists():
        return jsonify({"error": "Script not found"}), 500
    venv_python = BASE_DIR / "venv" / "bin" / "python"
    try:
        result = subprocess.run(
            [str(venv_python), str(script)],
            capture_output=True, text=True, timeout=60
        )
        status = "success" if result.returncode == 0 else "error"
        message = result.stdout.strip() or result.stderr.strip()
    except subprocess.TimeoutExpired:
        status = "error"
        message = "Script timed out"
    job["last_run"] = datetime.now(timezone.utc).isoformat()
    job["last_status"] = status
    job["last_message"] = message
    save_jobs(jobs_data)
    return jsonify({"status": status, "message": message})


# --- Settings API ---

@app.route("/api/settings", methods=["GET"])
@require_auth
def get_settings():
    config = load_config()
    # Don't expose secrets
    safe = {
        "email": {k: v for k, v in config["email"].items() if k != "app_password"},
    }
    safe["email"]["app_password_set"] = bool(config["email"].get("app_password", "").strip("x "))
    return jsonify(safe)


@app.route("/api/settings", methods=["PATCH"])
@require_auth
def update_settings():
    return jsonify({"error": "Settings are managed in config/config.py — edit the file directly."}), 501


# --- Frontend ---

@app.route("/")
def index():
    return render_template("index.html")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
