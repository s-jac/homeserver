# AGENTS.md — homeserver guide for AI agents

This file documents the homeserver codebase, conventions, and gotchas for AI agents working in this repo.

---

## What this repo is

A Raspberry Pi (hostname: `jim`, user: `gmac`) running a Flask web app that manages scheduled automation scripts. Everything lives here — the web app, all scripts, and config templates. Sensitive config lives outside the repo at `~/homeserver/config/config.json` (gitignored).

---

## After making changes

Run `bash ~/homeserver/update.sh` to pull, commit, and push all repos at once. Only commit individually if you need a specific commit message for a significant change.

---

## Directory layout

```
app.py                  Flask web app
gunicorn.conf.py        Gunicorn config — logs to logs/, binds 0.0.0.0:5000
homeserver.service      Systemd unit (copy to /etc/systemd/system/ after edits)
config/
  config.json           GITIGNORED — all secrets live here
  config.sample.json    Template — update this when adding new config keys
  jobs.json             GITIGNORED — live job state (last_run, enabled, etc)
scripts/
  gym.py                HIIT booking script
  nsw_campsite.py       NSW NP campsite booking script
  notify.py             Gmail SMTP helper, used by gym.py
templates/index.html    SPA frontend
logs/                   GITIGNORED — access.log, error.log, gym.log
```

---

## Config

Single file `config/config.json` with sections:
- `auth` — login password, JWT secret, token expiry
- `email` — Gmail SMTP for failure notifications
- `gym` — personal details for HIIT booking
- `campsite` — real rezexpert credentials + card details (used with `--real`)
- `campsite_fake` — safe dummy values for dry-run testing (default for nsw_campsite.py)

**Never print or log the full contents of config.json.** It contains live passwords and card details.

When adding new config keys, always update `config/config.sample.json` too.

---

## app.py

Key globals:
```python
BASE_DIR    = Path(__file__).parent               # ~/homeserver/
CONFIG_FILE = BASE_DIR / "config" / "config.json"
JOBS_FILE   = BASE_DIR / "config" / "jobs.json"
```

Script path resolution in `run_job`: relative paths are resolved from `BASE_DIR` (e.g. `"scripts/gym.py"` → `~/homeserver/scripts/gym.py`).

Venv python: `BASE_DIR.parent / "venv" / "bin" / "python"` (i.e. `~/venv/bin/python`).

After changes to app.py: `sudo systemctl restart homeserver`.

---

## jobs.json

`config/jobs.json` is **live state** — it is written by both `app.py` (on manual runs) and `gym.py` (on cron runs). Always read it fresh before writing. Script paths use relative form `"scripts/gym.py"` (relative to homeserver root).

Both gym jobs are currently **disabled** (`"enabled": false`). Enable from the UI or edit directly.

---

## gym.py

- Reads credentials from `config.json["gym"]` and job state from `config/jobs.json`
- Writes job status back to `config/jobs.json` after each run
- Saves HTML booking responses to `logs/booking_response_<date>.html` (keeps last 10)
- On failure (real run only): calls `notify.send_notification()`
- `--fake` uses hardcoded test credentials, `--dry-run` stops before final POST

---

## nsw_campsite.py

- Default (`--real` not passed): uses `config.json["campsite_fake"]` — safe for testing
- `--real`: uses `config.json["campsite"]` — charges the actual card
- `_load_campsite_cfg(real)` loads the appropriate section from `CONFIG_FILE`
- `cmd_check` also loads config (needed for rezexpert login to check availability)

---

## notify.py

Reads `config.json["email"]`. Silently returns if `email.enabled` is false or `app_password` is empty. Used only by `gym.py`.

---

## Systemd service

```bash
sudo systemctl status homeserver
sudo systemctl restart homeserver
sudo journalctl -u homeserver -f      # live logs

# After editing homeserver.service:
sudo cp ~/homeserver/homeserver.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl restart homeserver
```

---

## Cron

```
30 0 * * SAT  ~/venv/bin/python ~/homeserver/scripts/gym.py >> ~/homeserver/logs/gym.log 2>&1
30 0 * * MON  ~/venv/bin/python ~/homeserver/scripts/gym.py >> ~/homeserver/logs/gym.log 2>&1
```

Cron uses `$HOME` expansion. The schedule is also stored in `jobs.json` for display in the UI, but the actual trigger is the crontab entry.

---

## Logrotate

Config source of truth: `~/homeserver/logrotate.conf` (installed to `/etc/logrotate.d/homeserver`). Covers `~/homeserver/logs/*.log`. Weekly, 8 weeks, compressed.

---

## Networking

- Tailscale: `http://100.95.29.87:5000`
- Local: `http://192.168.0.253:5000`
- UFW: only SSH (22) and `tailscale0` allowed

---

## Gotchas

- **config.json and jobs.json are gitignored** — never try to `git add` them
- **jobs.json is live state** — both app.py and gym.py write to it; don't overwrite casually
- **Both gym jobs are disabled by default** — cron runs but the script exits early
- **nsw_campsite.py defaults to fake config** — must pass `--real` to actually book/charge
- **The homeserver must be restarted** after `app.py` changes to take effect
- **systemd doesn't expand `~`** — the service file uses `/home/gmac/` explicitly
- **config.sample.json is committed** — it has no secrets and documents all keys
