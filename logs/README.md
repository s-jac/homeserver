# logs/

Runtime logs. Not committed to git.

## Files

| File | Written by | Rotated |
|---|---|---|
| `access.log` | Gunicorn | Yes — weekly, 8 weeks |
| `error.log` | Gunicorn | Yes — weekly, 8 weeks |
| `gym_book.log` | Cron (stdout from gym_book.py) | No — small, append-only |
| `booking_response_YYYY-MM-DD.html` | gym_book.py | Auto — keeps last 10 |

Logrotate config: `/etc/logrotate.d/homeserver`
