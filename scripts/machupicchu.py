#!/usr/bin/env python3
"""Send the daily South America email with Machupicchu ticket status.

This uses the same public route/date API as the booking calendar. It never
starts a purchase or attempts the site's human verification.
"""

import argparse
import base64
import hashlib
import hmac
import json
import smtplib
import subprocess
import sys
import time
from datetime import date, datetime
from email.message import EmailMessage
from pathlib import Path
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

BASE_DIR = Path(__file__).resolve().parent.parent
API_URL = "https://api-tuboleto.cultura.pe"
BOOKING_URL = "https://tuboleto.cultura.pe/llaqta_machupicchu"
TARGET_DATE = date(2027, 6, 10)
HOLIDAY_DATE = date(2027, 5, 21)
STATE_FILE = BASE_DIR / "config" / "machupicchu_alerts.json"
URGENT_SUBJECT = "TICKETS ARE AVAILABLE FOR MACCU PICCU GO BUY EM!!!!"

# These values are published in the Tu Boleto frontend bundle. If its API
# changes, the checker fails visibly instead of treating an error as no seats.
SIGNING_KEY = b"5t4jPtv4LpmGgWU7ZYk8FhZf5LNTpk"
ENCRYPTION_PASSWORD = b"Km4pDqgVZdLNXYdde5jypBysh9MzkL"
ENCRYPTION_SALT = b"In5iAIxnHwMTLg9ldHFUb3"

HEADERS = {
    "Origin": "https://tuboleto.cultura.pe",
    "Referer": BOOKING_URL,
    "User-Agent": "Mozilla/5.0 (compatible; homeserver ticket availability check)",
}


def api_json(path, payload=None):
    headers = dict(HEADERS)
    body = None
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(API_URL + path, data=body, headers=headers)
    with urlopen(request, timeout=20) as response:
        return json.load(response)


def decrypt_data(encoded):
    """Decode the frontend's PBKDF2 / AES-256-CBC response format."""
    raw = base64.b64decode(encoded, validate=True)
    if len(raw) < 32:
        raise ValueError("Availability response is too short")
    key = hashlib.pbkdf2_hmac(
        "sha256", ENCRYPTION_PASSWORD, ENCRYPTION_SALT, 65536, dklen=32
    )
    result = subprocess.run(
        ["openssl", "enc", "-d", "-aes-256-cbc", "-K", key.hex(), "-iv", raw[:16].hex()],
        input=raw[16:],
        capture_output=True,
        check=True,
    )
    return json.loads(result.stdout)


def classic_routes():
    place = api_json("/visita/public/informacion-lugar?idLugar=llaqta_machupicchu")
    if place.get("nidlugar") != 1 or not place.get("circuitos"):
        raise ValueError("Unexpected Machupicchu route information")
    for circuit in place["circuitos"]:
        if circuit["nidcircuito"] != 2:
            continue
        for route in circuit.get("rutas", []):
            name = route["nombre_ruta"]
            if name.startswith(("Ruta 2-A:", "Ruta 2-B:")):
                yield circuit["nidcircuito"], route["nidruta"], name


def availability_response(circuit_id, route_id, target, clock_offset):
    timestamp = str(int(time.time() * 1000) + clock_offset)
    code = base64.b64encode(
        hmac.new(SIGNING_KEY, SIGNING_KEY + b":" + timestamp.encode(), hashlib.sha256).digest()
    ).decode("ascii")
    month_start = target.replace(day=1)
    if target.month == 12:
        next_month = target.replace(year=target.year + 1, month=1, day=1)
    else:
        next_month = target.replace(month=target.month + 1, day=1)
    month_end = date.fromordinal(next_month.toordinal() - 1)
    response = api_json(
        "/visita/public/consulta-fechas-disponibles",
        {
            "nidUsuario": 0,
            "nidLugar": 1,
            "nidCircuito": circuit_id,
            "nidRuta": route_id,
            "dfInicio": month_start.isoformat(),
            "dfFin": month_end.isoformat(),
            "code": code,
            "timestamp": timestamp,
            "valorPunto": 0,
        },
    )
    if response.get("estado") is not True or not isinstance(response.get("data"), str):
        raise ValueError(f"Availability API rejected route {route_id}: {response.get('mensaje')}")
    return decrypt_data(response["data"])


def load_ever_available(target):
    try:
        state = json.loads(STATE_FILE.read_text())
    except FileNotFoundError:
        return False
    if state.get("date") != target.isoformat():
        return False
    return state.get("ever_available") is True


def save_ever_available(target, ever_available):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp = STATE_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps({"date": target.isoformat(), "ever_available": ever_available}) + "\n")
    temp.replace(STATE_FILE)


def email_content(today, available, ever_available, responses):
    days = (HOLIDAY_DATE - today).days
    if ever_available:
        subject = URGENT_SUBJECT
    elif days >= 0:
        subject = f"South America Daily - {days} days until the holiday!"
    else:
        subject = "South America Daily - The holiday has started!"
    status = "AVAILABLE" if available else "NOT AVAILABLE"
    body = (
        f"Tickets are {status}, but double check here just in case: {BOOKING_URL}, "
        "with options 10 June 2027, Circuit 2 – Classic, Route 2-A (2-B as backup), "
        "9 am, 4 tickets.\n\n"
        "Checker output:\n"
        + "\n".join(f"{name}: {json.dumps(value, ensure_ascii=False)}" for name, value in responses.items())
        + "\n"
    )
    return subject, body


def send_email(config, subject, body):
    if not config.email.get("enabled") or not config.email.get("app_password"):
        raise ValueError("Email is not enabled or has no app password in config/config.py")
    recipients = getattr(config, "south_america_recipients", None)
    if not isinstance(recipients, list) or not recipients or any(not isinstance(value, str) or not value for value in recipients):
        raise ValueError("Set south_america_recipients to a nonempty list in config/config.py")
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = config.email["from_address"]
    message["To"] = ", ".join(recipients)
    message.set_content(body)
    with smtplib.SMTP(config.email["smtp_host"], config.email["smtp_port"], timeout=30) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.login(config.email["username"], config.email["app_password"])
        smtp.send_message(message)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Print the email without sending or saving state")
    args = parser.parse_args()

    server_time = int(api_json("/comunes/tiempo-servidor")["tiempoServidor"])
    clock_offset = server_time - int(time.time() * 1000)
    responses = {}
    for circuit_id, route_id, name in classic_routes():
        responses[name] = availability_response(circuit_id, route_id, TARGET_DATE, clock_offset)
    if not any(name.startswith("Ruta 2-A:") for name in responses) or not any(name.startswith("Ruta 2-B:") for name in responses):
        raise ValueError("The site did not return both Classic routes")

    # Both routes return [] today. Flag any successful, decrypted change as
    # available so a new response shape cannot silently hide ticket sales.
    available = any(value != [] for value in responses.values())
    ever_available = load_ever_available(TARGET_DATE) or available
    today = datetime.now(ZoneInfo("Australia/Sydney")).date()
    subject, body = email_content(today, available, ever_available, responses)
    print(f"Subject: {subject}\n\n{body}")
    if args.dry_run:
        return
    sys.path.insert(0, str(BASE_DIR / "config"))
    import config

    send_email(config, subject, body)
    save_ever_available(TARGET_DATE, ever_available)
    print("Daily email sent")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Machupicchu check failed: {exc}", file=sys.stderr)
        sys.exit(1)
