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
app.py                     Flask app — auth, jobs API, settings API
gunicorn.conf.py           Gunicorn config (workers, log paths, bind address)
homeserver.service         Systemd unit file (also installed at /etc/systemd/system/)
requirements.txt           Python dependencies
config/
  config.py                All config (auth, email, identities) — gitignored, populate from config.sample.py
  config.sample.py         Template — copy to config.py and fill in real values
  jobs.json                Job definitions and last-run state — gitignored
cron/
  pull.py                  Hourly git pull + conditional homeserver restart if app.py changed
  cron.py                  Crontab backup/restore script (backup + install commands)
  crontab.txt              Latest crontab snapshot (auto-updated daily at 4am AEST)
  README.md                Setup and usage docs
scripts/
  gym.py                   HIIT class auto-booker (Manly Aquatic Centre)
  news.py                  Daily news digest — RSS → Gemini summary → email + portfolio push
  nsw_campsite.py          NSW National Parks campsite booking
  notify.py                Gmail SMTP notification helper
templates/
  index.html               Mobile-friendly SPA frontend
logs/                      Access, error, and job logs (logrotated, gitignored)
```

## Setup on a new machine

See [homeserver-setup](https://github.com/s-jac/homeserver-setup) — `install.sh` handles everything in one command. After running it:

```bash
cp ~/homeserver/config/config.sample.py ~/homeserver/config/config.py
chmod 600 ~/homeserver/config/config.py
# Fill in config.py — auth, email, and the sam identity dict
```

## Config

All config lives in `config/config.py` (gitignored). Top-level names:

| Name | Used by |
|------|---------|
| `auth` | app.py — login password, JWT secret |
| `email` | notify.py — Gmail SMTP for failure alerts |
| `gordon` | scripts (default) — safe test identity, fake card details |
| `sam` | scripts with `--real` — real credentials, charges the card |

Edit `config.py` directly to change any settings — the web UI does not persist changes.

## Jobs

Jobs are defined in `config/jobs.json` (gitignored — it holds live state). Each entry has:

- `script` — path to the script (relative to homeserver root, or absolute)
- `cron` — schedule shown in the UI (actual cron entry is in the user crontab)
- `enabled` — toggled from the UI; scripts check this flag and exit early if false

The UI at `http://<tailscale-ip>:5000` shows last run time, status, and output per job, and lets you enable/disable or manually trigger runs.

## Scripts

### gym.py

Auto-books 7am HIIT classes at Manly Aquatic Centre (nabooki.com). Runs Saturday 00:30 to book Tuesday, and Monday 00:30 to book Thursday (booking window opens 3 days in advance). Uses the gordon identity by default; pass `--real` to use sam. Sends an email via notify.py on failure (real runs only).

```bash
# Dry run with gordon (test identity, no real booking)
~/homeserver/venv/bin/python ~/homeserver/scripts/gym.py --date 2026-04-01 --dry-run

# Dry run with real sam credentials
~/homeserver/venv/bin/python ~/homeserver/scripts/gym.py --date 2026-04-01 --dry-run --real

# Real run
~/homeserver/venv/bin/python ~/homeserver/scripts/gym.py --date 2026-04-01 --real
```

### news.py

Fetches top headlines from RSS feeds (BBC, Guardian, ABC, FT, Bloomberg, etc.), summarises each topic group with Gemini (gemini-2.5-flash), emails the digest, and pushes `_data/news.json` to the portfolio GitHub repo. Runs daily at 10pm via cron.

```bash
# Dry run — fetches RSS + calls Gemini, prints digest, no email or GitHub push
~/homeserver/venv/bin/python ~/homeserver/scripts/news.py

# Send email only (no GitHub push)
~/homeserver/venv/bin/python ~/homeserver/scripts/news.py --email-only

# Full run — email + push to portfolio repo
~/homeserver/venv/bin/python ~/homeserver/scripts/news.py --real
```

Requires `gemini_api_keys`, `github_token`, `news_recipients`, and `email` in `config/config.py`. Rotates through multiple Gemini API keys on rate limit (429).

### nsw_campsite.py

Check availability and book NSW National Parks campsites via the rezexpert API and Westpac payment gateway. Run manually, not via cron. Uses gordon identity by default; pass `--real` to use sam.

```bash
# Check availability
~/homeserver/venv/bin/python ~/homeserver/scripts/nsw_campsite.py check --campground frazer --checkin 2026-09-04 --nights 2

# Book dry run (gordon — stops before charging the card)
~/homeserver/venv/bin/python ~/homeserver/scripts/nsw_campsite.py book --campground frazer --checkin 2026-09-04 --nights 2 --sites 2,3,4 --adults 1 --dry-run

# Book for real (sam identity — charges the card)
~/homeserver/venv/bin/python ~/homeserver/scripts/nsw_campsite.py book --campground frazer --checkin 2026-09-04 --nights 2 --sites 2,3,4 --adults 1 --real
```

## Adding a new job

1. Add a script to `scripts/`
2. Add an entry to `config/jobs.json`
3. Add a cron line (`crontab -e`) pointing at `~/homeserver/venv/bin/python ~/homeserver/scripts/yourscript.py`
4. Update `~/setup/install.sh` with the cron entry
5. Add any new config keys to `config/config.sample.py`
6. Run `python ~/homeserver/cron/cron.py backup` to snapshot the updated crontab
