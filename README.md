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
homeserver.service   Systemd unit file (also installed at /etc/systemd/system/)
requirements.txt     Python dependencies
config/
  settings.example.json   Template — copy to ~/config/homeserver/settings.json
templates/           Frontend HTML
logs/                Access and error logs (logrotated)
```

Config and job state live outside this repo at `~/config/homeserver/` — see below.

## Runtime config (outside repo)

| File | Contents |
|------|----------|
| `~/config/homeserver/settings.json` | Auth password, JWT secret, gym credentials, email config |
| `~/config/homeserver/jobs.json` | Job definitions and last-run state |

Both files are `chmod 600`. See `config/settings.example.json` for the expected shape.

The shared Python environment is at `~/venv/`.

## Fresh install

See the [homeserver-setup](https://github.com/s-jac/homeserver-setup) repo — `install.sh` handles cloning, venv creation, systemd, logrotate, and crontab in one command.

## Jobs

Jobs are defined in `~/config/homeserver/jobs.json`. Each job has:

- `script` — absolute path to the Python script to run
- `cron` — schedule (display only; actual cron entry lives in the user crontab)
- `enabled` — toggled from the UI; scripts check this flag and exit early if false
- `params` — arbitrary key/value data (informational, passed as context)

The UI at `http://<tailscale-ip>:5000` shows each job's last run time, status, and output, and lets you enable/disable or manually trigger a run.

## Adding a new job

1. Create the script in its own repo (e.g. `~/my-script/`)
2. Add an entry to `~/config/homeserver/jobs.json`
3. Add a cron line (`crontab -e`) pointing at `~/venv/bin/python ~/my-script/script.py`
4. Update `~/setup/install.sh` to clone the new repo and register the cron entry
