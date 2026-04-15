# AGENTS.md — homeserver guide for AI agents

This file documents the homeserver codebase, conventions, and gotchas for AI agents working in this repo.

---

## What this repo is

A Raspberry Pi (hostname: `jim`, user: `gmac`) running a Flask web app that manages scheduled automation scripts. Everything lives here — the web app, all scripts, and config templates. Sensitive config lives outside the repo at `~/homeserver/config/config.py` (gitignored).

---

## After making changes

Commit and push manually with `git add`, `git commit`, `git push`. Only commit what's relevant — don't bundle unrelated changes.

---

## Directory layout

```
app.py                  Flask web app
gunicorn.conf.py        Gunicorn config — logs to logs/, binds 0.0.0.0:5000
homeserver.service      Systemd unit (copy to /etc/systemd/system/ after edits)
config/
  config.py             GITIGNORED — all secrets live here (auth, email, gordon, sam)
  config.sample.py      Template — update this when adding new config keys
  jobs.json             GITIGNORED — live job state (last_run, enabled, etc)
cron/
  pull.py               Hourly git pull + restarts homeserver service if app.py changed
  cron.py               Crontab backup/restore — `backup` and `install` subcommands
  crontab.txt           Latest crontab snapshot (committed, auto-updated daily)
  README.md             Setup docs
scripts/
  gym.py                HIIT booking script
  news.py               Daily news digest — RSS → Gemini → email + portfolio push
  nsw_campsite.py       NSW NP campsite booking script
  notify.py             Gmail SMTP helper, used by gym.py
templates/index.html    SPA frontend
logs/                   GITIGNORED — access.log, error.log, gym.log, news.log, cron.log
```

---

## Config

Single file `config/config.py` with top-level names:
- `auth` — login password, JWT secret, token expiry (used by app.py)
- `email` — Gmail SMTP config (used by notify.py and news.py)
- `gordon` — fake/test identity dict (default for all scripts)
- `sam` — real identity dict (used when `--real` is passed)
- `gemini_api_keys` — list of Gemini API keys (used by news.py; rotates on 429)
- `github_token` — GitHub PAT for pushing to portfolio repo and committing crontab backups
- `news_recipients` — list of email addresses to send the daily digest to

Both `gordon` and `sam` have the same fields: `first_name`, `last_name`, `email`, `mobile`, `password`, `phone`, `address`, `city`, `state`, `postcode`, `vehicle_rego`, `vehicle_state`, `card_number`, `card_expiry_month`, `card_expiry_year`, `card_cvv`, `card_name`.

**Never print or log the full contents of config.py.** It contains live passwords and card details.

When adding new config keys, always update `config/config.sample.py` too.

---

## app.py

Key globals:
```python
BASE_DIR  = Path(__file__).parent               # ~/homeserver/
JOBS_FILE = BASE_DIR / "config" / "jobs.json"
```

Loads `config.py` via `importlib` on each request (so changes take effect without restart). The settings PATCH endpoint returns 501 — edit `config/config.py` directly to change auth/email settings.

Script path resolution in `run_job`: relative paths are resolved from `BASE_DIR` (e.g. `"scripts/gym.py"` → `~/homeserver/scripts/gym.py`).

Venv python: `BASE_DIR / "venv" / "bin" / "python"` (i.e. `~/homeserver/venv/bin/python`).

After changes to app.py: `sudo systemctl restart homeserver`.

---

## jobs.json

`config/jobs.json` is **live state** — it is written by both `app.py` (on manual runs) and `gym.py` (on cron runs). Always read it fresh before writing. Script paths use relative form `"scripts/gym.py"` (relative to homeserver root).

Both gym jobs are currently **disabled** (`"enabled": false`). Enable from the UI or edit directly.

---

## gym.py

- Imports identity from `config.py` (`gordon` by default, `sam` with `--real`)
- Reads job state from `config/jobs.json`, writes status back after each run
- Saves HTML booking responses to `logs/booking_response_<date>.html` (keeps last 10)
- On failure: calls `notify.send_notification()` only on `--real` runs

---

## nsw_campsite.py

- Default: uses `cfg.gordon` — safe for testing (fake card, no real charge)
- `--real`: uses `cfg.sam` — charges the actual card
- `_load_campsite_cfg(real)` returns the appropriate identity dict from `config.py`
- `cmd_check` also loads config (needed for rezexpert login to check availability)

---

## news.py

- Fetches RSS feeds grouped by topic (World, Australia, Economics)
- Calls Gemini (`gemini-2.5-flash`) once per topic group; rotates through `cfg.gemini_api_keys` on rate limit
- Emails a formatted digest via `cfg.email` to `cfg.news_recipients`
- Pushes `_data/news.json` to the portfolio repo (`s-jac/s-jac.github.io`) via GitHub API using `cfg.github_token`
- Dry run (no args): fetches + summarises + prints, no email or push
- `--email-only`: sends email, no GitHub push
- `--real`: email + GitHub push
- Runs daily at 22:00 via cron

---

## notify.py

Imports `config.email`. Silently returns if `email["enabled"]` is false or `app_password` is empty. Used only by `gym.py` (news.py has its own `send_email` function).

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
30 0 * * SAT  $HOME/homeserver/venv/bin/python $HOME/homeserver/scripts/gym.py >> $HOME/homeserver/logs/gym.log 2>&1
30 0 * * MON  $HOME/homeserver/venv/bin/python $HOME/homeserver/scripts/gym.py >> $HOME/homeserver/logs/gym.log 2>&1
0 22 * * *    $HOME/homeserver/venv/bin/python $HOME/homeserver/scripts/news.py --real >> $HOME/homeserver/logs/news.log 2>&1
0  * * * *    $HOME/homeserver/venv/bin/python $HOME/homeserver/cron/pull.py >> $HOME/homeserver/logs/cron.log 2>&1
0 18 * * *    $HOME/homeserver/venv/bin/python $HOME/homeserver/cron/cron.py backup >> $HOME/homeserver/logs/cron.log 2>&1
```

Cron uses `$HOME` expansion. The schedule is also stored in `jobs.json` for display in the UI, but the actual trigger is the crontab entry.

`pull.py` runs hourly — pulls latest from GitHub and restarts `homeserver.service` only if `app.py` changed. This means pushing from a dev machine propagates to the Pi within the hour automatically.

The crontab itself is backed up daily at 4am AEST (`0 18 * * *` UTC) — `cron/crontab.txt` is the source of truth. To restore on a new device: `python ~/homeserver/cron/cron.py install`.

After adding or changing any cron jobs, run `python ~/homeserver/cron/cron.py backup` to snapshot immediately rather than waiting for the daily run.

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

- **config.py and jobs.json are gitignored** — never try to `git add` them
- **jobs.json is live state** — both app.py and gym.py write to it; don't overwrite casually
- **Both gym jobs are disabled by default** — cron runs but the script exits early
- **Scripts default to gordon (fake identity)** — must pass `--real` to actually book/charge
- **The homeserver must be restarted** after `app.py` changes to take effect
- **systemd doesn't expand `~`** — the service file uses `/home/gmac/` explicitly
- **config.sample.py is committed** — it has no secrets and documents all keys
- **Web UI cannot persist settings changes** — edit `config/config.py` directly
