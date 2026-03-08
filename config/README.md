# config/

Runtime configuration. These files are **not committed to git** (see `.gitignore`). Both are `chmod 600`.

## Files

### settings.json

App secrets and integration config. Structure mirrors `settings.example.json`, which IS committed and shows the expected shape with placeholder values.

Top-level keys:
- `auth` — login password and JWT secret
- `gym` — personal details used when booking gym classes
- `email` — SMTP config for failure notifications

To add config for a new integration, add a new top-level key here and a matching placeholder in `settings.example.json`.

### jobs.json

State for all scheduled jobs. Each entry has:
- `id`, `name`, `description` — identity
- `enabled` — toggled via the UI; scripts check this before running
- `cron` — the schedule (for reference; actual cron is in crontab)
- `script` — path relative to project root
- `last_run`, `last_status`, `last_message` — written by the script after each run

To add a new job, append an entry here following the existing pattern.
