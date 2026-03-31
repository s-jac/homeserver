# cron

Backs up the Pi's crontab to `crontab.txt` in this repo, committed and pushed to GitHub on a schedule. Useful for disaster recovery when rebuilding the Pi.

## How it works

`cron.py backup` runs periodically (via a cron job), dumps `crontab -l` to `crontab.txt`, and pushes the change to GitHub. The cron job that runs the backup is itself captured in `crontab.txt`, so the whole setup is self-documenting — albeit slightly circular.

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
