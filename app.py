import importlib.util
import json
import subprocess
from datetime import datetime, timezone, timedelta
from functools import wraps
from pathlib import Path

import jwt
from flask import Flask, request, jsonify, render_template, abort

BASE_DIR   = Path(__file__).parent
JOBS_FILE  = BASE_DIR / "config" / "jobs.json"
JOBS_SAMPLE_FILE = BASE_DIR / "config" / "jobs.sample.json"

app = Flask(__name__)


def load_config():
    # Reload config.py fresh each call so changes take effect without restart
    spec = importlib.util.spec_from_file_location("config", BASE_DIR / "config" / "config.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return {"auth": mod.auth, "email": mod.email}


def load_jobs():
    with open(JOBS_FILE) as f:
        data = json.load(f)
    return ensure_job_defaults(data)


def save_jobs(data):
    with open(JOBS_FILE, "w") as f:
        json.dump(data, f, indent=2)


def ensure_job_defaults(data):
    """Merge newly committed job templates into live jobs.json without clobbering state."""
    if not JOBS_SAMPLE_FILE.exists():
        return data
    with open(JOBS_SAMPLE_FILE) as f:
        sample = json.load(f)

    changed = False
    jobs = data.setdefault("jobs", [])
    jobs_by_id = {job.get("id"): job for job in jobs}
    for sample_job in sample.get("jobs", []):
        job_id = sample_job.get("id")
        if not job_id:
            continue
        live_job = jobs_by_id.get(job_id)
        if live_job is None:
            jobs.append(sample_job)
            changed = True
            continue
        for key in ("name", "description", "script", "cron"):
            if not live_job.get(key) and sample_job.get(key):
                live_job[key] = sample_job[key]
                changed = True
        live_params = live_job.setdefault("params", {})
        for key, value in (sample_job.get("params") or {}).items():
            if key not in live_params:
                live_params[key] = value
                changed = True

    if changed:
        save_jobs(data)
    return data


def job_command(script, job):
    venv_python = BASE_DIR / "venv" / "bin" / "python"
    cmd = [str(venv_python), str(script)]
    if str(job.get("script", "")).endswith("scripts/gym.py"):
        if job.get("id"):
            cmd.extend(["--job-id", str(job["id"])])
        params = job.get("params") or {}
        if params.get("class"):
            cmd.extend(["--class", str(params["class"])])
        identities = params.get("identities")
        if isinstance(identities, str):
            identities = [i.strip() for i in identities.split(",") if i.strip()]
        for identity in identities or []:
            cmd.extend(["--identity", str(identity)])
    return cmd


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
        job.setdefault("params", {}).update(data["params"])
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
    try:
        result = subprocess.run(job_command(script, job), capture_output=True, text=True, timeout=180)
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
