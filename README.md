# homeserver

A personal automation server running on a Raspberry Pi. Accessible privately via Tailscale. Provides a mobile-friendly web UI to manage and trigger scheduled jobs.

## Stack

- **Flask + Gunicorn** — web app and API, port 5000
- **Systemd** — keeps the app running across reboots (`homeserver.service`)
- **Cron** — triggers job scripts on schedule
- **JWT auth** — single-password login, token stored in browser localStorage
- **Tailscale** — all access is through the Tailscale VPN; UFW blocks everything else

## Directory structure

```
app.py               Flask app — auth, jobs API, settings API
gunicorn.conf.py     Gunicorn config (workers, log paths, bind address)
homeserver.service   Systemd unit file (copy to /etc/systemd/system/ on fresh install)
requirements.txt     Python dependencies for the venv
setup.sh             Recreates the environment from scratch on a new machine
config/              Runtime config (secrets, job state) — not committed
scripts/             Automation scripts, one per job
templates/           Frontend HTML
logs/                Access, error, and job logs
venv/                Python virtualenv — not committed
```

## Setup on a new machine

```bash
bash setup.sh
# Then fill in config/settings.json with real credentials
# Then add cron jobs — see scripts/README.md
```

## Adding a new job

1. Add a script in `scripts/`
2. Add an entry in `config/jobs.json`
3. Add a cron line (`crontab -e`)
4. Update `scripts/README.md`
