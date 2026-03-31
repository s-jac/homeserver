#!/usr/bin/env python3
"""cron.py — backup and restore crontab for the homeserver.

Commands:
  backup   Dump crontab to crontab.txt, commit, and push to GitHub.
  install  Install crontab from the backed-up crontab.txt (use on a new device).
"""

import argparse
import subprocess
import sys
from pathlib import Path

HOMESERVER_DIR = Path("~/homeserver").expanduser()
CRONTAB_FILE = HOMESERVER_DIR / "crontab.txt"
REPO = "s-jac/homeserver"


def load_github_token():
    sys.path.insert(0, str(HOMESERVER_DIR / "config"))
    try:
        import config
    except ImportError:
        print("Error: could not import config/config.py — copy config.sample.py and fill it in.")
        sys.exit(1)
    token = getattr(config, "github_token", "")
    if not token:
        print("Error: github_token is empty in config/config.py.")
        sys.exit(1)
    return token


def run(cmd, check=True):
    result = subprocess.run(cmd, capture_output=True, text=True)
    if check and result.returncode != 0:
        print(f"Error: {' '.join(cmd)}")
        if result.stderr:
            print(result.stderr.strip())
        sys.exit(1)
    return result


def cmd_backup(args):
    """Dump crontab → crontab.txt, commit, and push."""
    # Read current crontab
    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    if result.returncode != 0:
        if "no crontab" in result.stderr.lower():
            crontab_content = ""
        else:
            print(f"Error reading crontab: {result.stderr.strip()}")
            sys.exit(1)
    else:
        crontab_content = result.stdout

    CRONTAB_FILE.write_text(crontab_content)
    print(f"Saved crontab to {CRONTAB_FILE}")

    # Stage
    run(["git", "-C", str(HOMESERVER_DIR), "add", "crontab.txt"])

    # Check if anything changed
    diff = run(["git", "-C", str(HOMESERVER_DIR), "diff", "--cached", "--quiet"], check=False)
    if diff.returncode == 0:
        print("No changes to crontab — nothing to commit.")
        return

    # Commit
    run(["git", "-C", str(HOMESERVER_DIR), "commit", "-m", "update crontab"])
    print("Committed.")

    # Push via HTTPS with token (avoids touching SSH config)
    token = load_github_token()
    push_url = f"https://{token}@github.com/{REPO}.git"
    run(["git", "-C", str(HOMESERVER_DIR), "push", push_url, "HEAD"])
    print("Pushed to GitHub.")


def cmd_install(args):
    """Install crontab from the backed-up crontab.txt."""
    if not CRONTAB_FILE.exists():
        print(f"Error: {CRONTAB_FILE} not found.")
        print("Pull the repo first, or run 'backup' on the source device.")
        sys.exit(1)

    content = CRONTAB_FILE.read_text().strip()
    if not content:
        print("crontab.txt is empty — no jobs to install.")
        # Clear any existing crontab silently
        subprocess.run(["crontab", "-r"], capture_output=True)
        return

    result = subprocess.run(["crontab", str(CRONTAB_FILE)], capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Error installing crontab: {result.stderr.strip()}")
        sys.exit(1)

    print(f"Installed crontab from {CRONTAB_FILE}")
    print()
    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    print(result.stdout.strip())

    # Remind user to add the self-backup cron job if it's not present
    if "cron.py" not in result.stdout:
        print()
        print("Note: the self-backup cron job is not present.")
        print("Add it with:")
        print(
            "  (crontab -l; echo '0 18 * * * "
            "$HOME/homeserver/venv/bin/python $HOME/homeserver/cron/cron.py backup"
            " >> $HOME/homeserver/logs/cron.log 2>&1') | crontab -"
        )


def main():
    parser = argparse.ArgumentParser(
        prog="cron.py",
        description="Backup and restore crontab for the homeserver.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
commands:
  backup   Dump crontab to crontab.txt and push to GitHub.
  install  Install crontab from crontab.txt (use on a new/rebuilt device).

self-backup cron job (add once, then it maintains itself):
  0 18 * * * $HOME/homeserver/venv/bin/python $HOME/homeserver/cron/cron.py backup >> $HOME/homeserver/logs/cron.log 2>&1

examples:
  python cron.py backup          # run a backup now
  python cron.py install         # restore crontab on a fresh device
        """,
    )
    subparsers = parser.add_subparsers(dest="command", metavar="command")
    subparsers.required = True

    subparsers.add_parser("backup", help="Dump crontab to crontab.txt and push to GitHub.")
    subparsers.add_parser(
        "install", help="Install crontab from crontab.txt (use on a new/rebuilt device)."
    )

    args = parser.parse_args()
    {"backup": cmd_backup, "install": cmd_install}[args.command](args)


if __name__ == "__main__":
    main()
