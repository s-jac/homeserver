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

`scripts/machupicchu.py` checks the public Tu Boleto calendar for **10 June 2027**, Circuit 2, Routes 2-A and 2-B. It sends a daily South America email to `south_america_recipients` in `config/config.py`. Before the 21 May 2027, 7:55pm San Francisco takeoff, its subject counts down to departure; a ticket alert replaces that subject until takeoff. It then counts time since takeoff, time since Charli left on 12 June at 11:30am and time left until 20 June at 9am, then time since the holiday ended. All milestone times use San Francisco time. Ticket checks stop after 10 June in Peru, while the daily emails continue. The current response for both routes is an empty list; any successful response that differs from it is flagged as available. It does not buy tickets. The alert state is kept in gitignored `config/machupicchu_alerts.json`.

The checker uses `openssl` to decode the site's availability response. Verify `openssl` is installed on the Pi or your computer, then preview the email without sending it:

```bash
~/homeserver/venv/bin/python ~/homeserver/scripts/machupicchu.py --dry-run
```

To check and send an email immediately from a computer with `config/config.py` set up, run from the repo root:

```bash
python3 scripts/machupicchu.py
```

The Pi's live crontab already has the daily job. On a fresh device, install the backed-up crontab after configuring the repo:

```bash
~/homeserver/venv/bin/python ~/homeserver/cron/cron.py install
```

The Pi uses Sydney local time. The live cron entry checks at 7am and 8am, then uses the UTC guard in `crontab.txt` to run only at 21:00 UTC (8am AEDT or 7am AEST). After changing the live crontab, run `cron.py backup` immediately. Email requires `email["enabled"] = True`, a valid Gmail app password, and `south_america_recipients` in `config/config.py`.

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
