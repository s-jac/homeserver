# scripts/

Automation scripts. Each script corresponds to a job entry in `config/jobs.json` and is run by cron.

## Conventions

- Scripts read credentials from `config/settings.json` — never hardcode secrets
- Scripts call `update_job_status(job_id, status, message)` on completion to update the UI
- Exit code `0` = success, `1` = failure
- Logging via the standard `logging` module to stdout (captured by cron into `logs/`)
- `notify.send_notification(subject, body)` is available for failure alerts

## Scripts

### gym_book.py

Books a HIIT class at Manly Aquatic Centre via the nabooki.com booking widget.

Cron schedule:
```
30 0 * * SAT   # books Tuesday 7am (3 days ahead)
30 0 * * MON   # books Thursday 7am (3 days ahead)
```

Useful flags:
- `--date YYYY-MM-DD` — override target date (bypasses enabled check)
- `--dry-run` — full flow but skips the final confirmation POST
- `--fake` — uses throwaway credentials (for testing without a real booking)
- `--fail` — simulates failure to test email notification

### notify.py

Helper module, not a standalone script. Import and call `send_notification(subject, body)`. Reads SMTP config from `settings.json`. Silently no-ops if email is disabled.

## Adding a new script

1. Create `scripts/your_script.py` following the conventions above
2. Add a job entry to `config/jobs.json`
3. Add a cron line (`crontab -e`)
4. Add a one-paragraph entry to this file
