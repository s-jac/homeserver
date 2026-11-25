#!/usr/bin/env python3
"""Check Tu Boleto for Machupicchu tickets on 10 June 2027.

This uses the same public route/date API as the booking calendar. It never
starts a purchase or attempts the site's human verification.
"""

import argparse
import base64
import hashlib
import hmac
import json
import subprocess
import sys
import time
from datetime import date
from pathlib import Path
from urllib.request import Request, urlopen

BASE_DIR = Path(__file__).resolve().parent.parent
API_URL = "https://api-tuboleto.cultura.pe"
BOOKING_URL = "https://tuboleto.cultura.pe/llaqta_machupicchu"
TARGET_DATE = date(2027, 6, 10)
STATE_FILE = BASE_DIR / "config" / "machupicchu_alerts.json"

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


def routes():
    place = api_json("/visita/public/informacion-lugar?idLugar=llaqta_machupicchu")
    if place.get("nidlugar") != 1 or not place.get("circuitos"):
        raise ValueError("Unexpected Machupicchu route information")
    for circuit in place["circuitos"]:
        for route in circuit.get("rutas", []):
            yield circuit["nidcircuito"], route["nidruta"], route["nombre_ruta"]


def available_dates(circuit_id, route_id, target, clock_offset):
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
    dates = decrypt_data(response["data"])
    if not isinstance(dates, list) or any(not isinstance(item, dict) or "dfecha" not in item for item in dates):
        raise ValueError(f"Unexpected availability format for route {route_id}")
    return dates


def load_notified(target):
    try:
        state = json.loads(STATE_FILE.read_text())
    except FileNotFoundError:
        return set()
    if state.get("date") != target.isoformat():
        return set()
    return set(state.get("notified_route_ids", []))


def save_notified(target, notified):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp = STATE_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps({"date": target.isoformat(), "notified_route_ids": sorted(notified)}) + "\n")
    temp.replace(STATE_FILE)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", type=date.fromisoformat, default=TARGET_DATE, help="YYYY-MM-DD")
    parser.add_argument("--dry-run", action="store_true", help="Print status without email or state changes")
    args = parser.parse_args()

    server_time = int(api_json("/comunes/tiempo-servidor")["tiempoServidor"])
    clock_offset = server_time - int(time.time() * 1000)
    target = args.date
    target_label = target.strftime("%-d %B %Y")
    date_key = target.strftime("%d-%m-%Y")
    found = {}
    checked = 0
    for circuit_id, route_id, name in routes():
        checked += 1
        if any(item["dfecha"] == date_key for item in available_dates(circuit_id, route_id, target, clock_offset)):
            found[route_id] = name
    if checked == 0:
        raise ValueError("No Machupicchu routes were returned")
    print(f"Machupicchu {target_label}: {len(found)} of {checked} routes have tickets")
    if not found:
        if not args.dry_run:
            save_notified(target, set())
        return

    for name in found.values():
        print(f"  {name}")
    if args.dry_run:
        return

    previous = load_notified(target)
    new_routes = set(found) - previous
    if new_routes:
        sys.path.insert(0, str(BASE_DIR / "config"))
        import config

        if not config.email.get("enabled") or not config.email.get("app_password"):
            print("Email is not configured; availability will be checked again on the next run")
            return
        from notify import send_notification

        route_list = "\n".join(f"- {found[route_id]}" for route_id in sorted(found))
        send_notification(
            f"Machupicchu tickets available for {target_label}",
            f"Tu Boleto lists tickets for {target_label}:\n\n{route_list}\n\nBook at {BOOKING_URL}",
        )
        print("Availability email sent")
    save_notified(target, set(found))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Machupicchu check failed: {exc}", file=sys.stderr)
        sys.exit(1)
