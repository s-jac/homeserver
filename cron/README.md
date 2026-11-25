# cron

Backs up the Pi's crontab to `crontab.txt` in this repo, committed and pushed to GitHub on a schedule. Useful for disaster recovery when rebuilding the Pi.

## Scripts

### pull.py

Runs `git pull` on the homeserver repo and restarts the `homeserver` systemd service if `app.py` changed. Run hourly via cron. No restart if `app.py` is unchanged.

```bash
python ~/homeserver/cron/pull.py
```

### cron.py

`cron.py backup` runs periodically (via a cron job), dumps `crontab -l` to `crontab.txt`, and pushes the change to GitHub. The cron job that runs the backup is itself captured in `crontab.txt`, so the whole setup is self-documenting — albeit slightly circular.

### Machupicchu tickets

`scripts/machupicchu.py` checks the public Tu Boleto calendar for **10 June 2027**, Circuit 2, Routes 2-A and 2-B. It sends a daily South America email to `south_america_recipients` in `config/config.py`, counting down to the 21 May 2027, 7:55pm San Francisco takeoff. The current response for both routes is an empty list; any successful response that differs from it is flagged as available. Once flagged, the urgent subject stays in future daily emails. It does not buy tickets. The alert state is kept in gitignored `config/machupicchu_alerts.json`.

The checker uses `openssl` to decode the site's availability response. Verify `openssl` is installed on the Pi or your computer, then preview the email without sending it:

```bash
~/homeserver/venv/bin/python ~/homeserver/scripts/machupicchu.py --dry-run
```

To check and send an email immediately from a computer with `config/config.py` set up, run from the repo root:

```bash
python3 scripts/machupicchu.py
```

After the new code is on the Pi, add the daily job to its **live** crontab and run the backup immediately so `crontab.txt` matches. If you previously installed the every-two-hours job, remove that line first:

```bash
(crontab -l; echo '0 21 * * * $HOME/homeserver/venv/bin/python $HOME/homeserver/scripts/machupicchu.py >> $HOME/homeserver/logs/machupicchu.log 2>&1') | crontab -
~/homeserver/venv/bin/python ~/homeserver/cron/cron.py backup
```

The Pi's crontab uses UTC, so `0 21` is 8am AEDT or 7am AEST. Email requires `email["enabled"] = True`, a valid Gmail app password, and `south_america_recipients` in `config/config.py`.

## Usage

```
python cron/cron.py -h
```

### Backup (run manually or via cron)

```bash
python ~/homeserver/cron/cron.py backup
```

Saves the current crontab to `crontab.txt`, commits it, and pushes to GitHub via HTTPS using `github_token` from `config/config.py`.

### Restore on a new device

After cloning the repo and filling in `config/config.py`:

```bash
python ~/homeserver/cron/cron.py install
```

This installs the crontab from `crontab.txt`. The self-backup job will then maintain it going forward.

## Self-backup cron job

Add this once (it captures itself in future backups):

```
0 18 * * * $HOME/homeserver/venv/bin/python $HOME/homeserver/cron/cron.py backup >> $HOME/homeserver/logs/cron.log 2>&1
```

Add it by running:

```bash
(crontab -l; echo "0 18 * * * $HOME/homeserver/venv/bin/python $HOME/homeserver/cron/cron.py backup >> $HOME/homeserver/logs/cron.log 2>&1") | crontab -
```

## Notes

- The cron job runs at `0 18 * * *` UTC = **4am AEST (UTC+10)**. During AEDT (daylight saving, UTC+11) this shifts to 3am — cron doesn't follow DST.
- The push uses HTTPS with a GitHub PAT (`github_token` in `config/config.py`) rather than SSH, so it works independently of SSH key setup.
- Commits are no-op if the crontab hasn't changed, so the daily run is cheap.
- `crontab.txt` is committed to the repo — don't put secrets directly in cron job commands.
