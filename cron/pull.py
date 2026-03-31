#!/usr/bin/env python3
"""pull.py — git pull the homeserver repo and restart the service if app.py changed."""

import subprocess
import sys
from pathlib import Path

HOMESERVER_DIR = Path("~/homeserver").expanduser()


def run(cmd, check=True):
    result = subprocess.run(cmd, capture_output=True, text=True)
    if check and result.returncode != 0:
        print(f"Error: {' '.join(cmd)}")
        if result.stderr:
            print(result.stderr.strip())
        sys.exit(1)
    return result


def main():
    # Record app.py hash before pull
    before = run(["git", "-C", str(HOMESERVER_DIR), "rev-parse", "HEAD:app.py"], check=False)
    before_hash = before.stdout.strip() if before.returncode == 0 else None

    # Pull
    result = run(["git", "-C", str(HOMESERVER_DIR), "pull"])
    print(result.stdout.strip() or result.stderr.strip())

    # Record app.py hash after pull
    after = run(["git", "-C", str(HOMESERVER_DIR), "rev-parse", "HEAD:app.py"])
    after_hash = after.stdout.strip()

    if before_hash != after_hash:
        print("app.py changed — restarting homeserver service.")
        run(["sudo", "systemctl", "restart", "homeserver"])
        print("Restarted.")
    else:
        print("app.py unchanged — no restart needed.")


if __name__ == "__main__":
    main()
