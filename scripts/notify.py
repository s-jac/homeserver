"""Email notification helper. Import and call send_notification() from job scripts."""

import smtplib
import sys
from email.message import EmailMessage
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "config"))
import config as _cfg


def send_notification(subject: str, body: str, to_address: str | None = None):
    cfg = _cfg.email
    if not cfg.get("enabled"):
        return

    msg = EmailMessage()
    msg["Subject"] = f"[HomeServer] {subject}"
    msg["From"] = cfg["from_address"]
    msg["To"] = to_address or cfg["to_address"]
    msg.set_content(body)

    with smtplib.SMTP(cfg["smtp_host"], cfg["smtp_port"]) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.login(cfg["username"], cfg["app_password"])
        smtp.send_message(msg)
