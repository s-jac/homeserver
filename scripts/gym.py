#!/usr/bin/env python3
"""
Manly Aquatic Centre - HIIT Class Auto-Booker

Flow:
  1. GET widget          → session cookies + CSRF token
  2. POST step1          → select HIIT service
  3. GET step2/step3     → navigate (maintains session state)
  4. POST timeslots AJAX → get available slots for target date, find 7am slot ID
  5. POST schedule AJAX  → select the 7am slot
  6. POST step4          → confirm with personal details

Cron schedule:
  30 0 * * SAT  → Saturday 00:30, books Tuesday  (3 days ahead)
  30 0 * * MON  → Monday  00:30, books Thursday (3 days ahead)

Manual run:
  ~/venv/bin/python ~/homeserver/scripts/gym.py --date 2026-03-10
"""

import argparse
import json
import logging
import re
import sys
from datetime import date as date_type
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytz
import requests

sys.path.insert(0, str(Path(__file__).parent))
from notify import send_notification

BASE_DIR      = Path(__file__).parent.parent
CONFIG_FILE   = BASE_DIR / "config" / "config.json"
JOBS_FILE     = BASE_DIR / "config" / "jobs.json"
LOGS_DIR      = BASE_DIR / "logs"

BASE_URL       = "https://app.nabooki.com"
TOKEN          = "5fade28f6f4d07.93412102"
WIDGET_TOKEN   = "aHR0cHM6Ly9hcHAubmFib29raS5jb20vYm9va2luZy9zdGVwMT90b2tlbj01ZmFkZTI4ZjZmNGQwNy45MzQxMjEwMg=="
SERVICE_ID     = "221273"
LOCATION_ID    = "42841"
BUSINESS_ID    = "42400"
RESOURCE_IDS   = ["63942", "64112", "66093", "66380", "66381", "66382"]
TARGET_TIME    = "7:00"   # matched against timeslot time strings

# All service IDs listed on the step1 form (for the number_of_people fields)
ALL_SERVICE_IDS = ["220774", "221305", "220773", "221273",
                   "220775", "221276", "258839", "220776", "221459", "221277"]

SYDNEY_TZ  = pytz.timezone("Australia/Sydney")
WEEKDAY_JOB = {1: "gym_tuesday_7am", 3: "gym_thursday_7am"}

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-GB,en-US;q=0.9,en;q=0.8",
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


# ── Helpers ──────────────────────────────────────────────────────────────────

def extract_csrf(html: str) -> str:
    for pattern in [
        r'<meta name="csrf-token" content="([^"]+)"',
        r'<input[^>]+name="_token"[^>]+value="([^"]+)"',
        r'<input[^>]+value="([^"]+)"[^>]+name="_token"',
    ]:
        m = re.search(pattern, html)
        if m:
            return m.group(1)
    raise ValueError("Could not find CSRF token in page HTML")


def find_slot_id(data, target_time: str) -> tuple[str | None, str | None]:
    """
    Parse the nabooki timeslots AJAX response.
    Response shape: {"data": [{"07:00": {"id_staff_resource": "schedule-443559--64112"}}, ...]}
    Returns (schedule_id, resource_id) for the matching time slot.
    """
    slots_list = data.get("data", []) if isinstance(data, dict) else data
    for slot_dict in slots_list:
        if not isinstance(slot_dict, dict):
            continue
        for time_key, slot_info in slot_dict.items():
            if target_time in time_key:
                id_staff = slot_info.get("id_staff_resource", "")
                # Format: "schedule-443559--64112"
                m = re.match(r"schedule-(\d+)--(\d+)", id_staff)
                if m:
                    return m.group(1), m.group(2)
    return None, None


def load_gym_creds() -> dict:
    with open(CONFIG_FILE) as f:
        gym = json.load(f).get("gym", {})
    creds = {k: gym.get(k, "") for k in ("first_name", "last_name", "email", "mobile")}
    missing = [k for k, v in creds.items() if not v]
    if missing:
        raise ValueError(f"Missing gym credentials in config.json: {missing}")
    return creds


def target_date() -> tuple[str | None, str | None]:
    today = datetime.now(SYDNEY_TZ).date()
    target = today + timedelta(days=3)
    job_id = WEEKDAY_JOB.get(target.weekday())
    if job_id:
        return target.strftime("%Y-%m-%d"), job_id
    return None, None


def is_job_enabled(job_id: str) -> bool:
    with open(JOBS_FILE) as f:
        data = json.load(f)
    job = next((j for j in data["jobs"] if j["id"] == job_id), None)
    return bool(job and job.get("enabled"))


def update_job_status(job_id: str, status: str, message: str):
    try:
        with open(JOBS_FILE) as f:
            data = json.load(f)
        job = next((j for j in data["jobs"] if j["id"] == job_id), None)
        if job:
            job["last_run"] = datetime.now(timezone.utc).isoformat()
            job["last_status"] = status
            job["last_message"] = message
        with open(JOBS_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        log.warning(f"Could not update job status: {e}")


# ── Booking flow ──────────────────────────────────────────────────────────────

def book(date_str: str, creds: dict, dry_run: bool = False) -> bool:
    mobile_masked = creds["mobile"][:4] + "****" + creds["mobile"][-2:]
    log.info(
        f"  Booking as: {creds['first_name']} {creds['last_name']} "
        f"<{creds['email']}> mob {mobile_masked}"
    )

    session = requests.Session()
    session.headers.update(BROWSER_HEADERS)
    step_params = {"token": TOKEN, "widget_token": WIDGET_TOKEN}

    # ── 1. GET widget → session cookies + CSRF ───────────────────────────────
    log.info("Loading booking widget…")
    r = session.get(f"{BASE_URL}/booking/popup/widget", params={"token": TOKEN})
    r.raise_for_status()
    csrf = extract_csrf(r.text)
    log.info(f"  Session established, CSRF: {csrf[:12]}…")

    # ── 2. POST step1 → select HIIT service ──────────────────────────────────
    log.info("Selecting HIIT service (step1)…")
    step1_data = [
        ("_token",                csrf),
        ("token",                 TOKEN),
        ("widget_token",          WIDGET_TOKEN),
        ("validated_promo_code",  ""),
        ("service_ids",           f"location_{LOCATION_ID}_category_0_service_{SERVICE_ID}"),
        ("is_schedule_service",   "yes"),
        ("no_service_error",      ""),
    ]
    for sid in ALL_SERVICE_IDS:
        prefix = f"location_{LOCATION_ID}_category_0_service_{sid}"
        step1_data += [
            (f"{prefix}-number_of_people_min",     "1"),
            (f"{prefix}-number_of_people_max",     "1"),
            (f"{prefix}-number_of_people_default", "1"),
            (f"{prefix}-number_of_people",         "1"),
        ]
    step1_data.append(("next", ""))

    r = session.post(
        f"{BASE_URL}/booking/step1",
        data=step1_data,
        headers={"Referer": f"{BASE_URL}/booking/step1?token={TOKEN}"},
    )
    r.raise_for_status()
    log.info(f"  step1 → {r.status_code}, landed on: {r.url}")

    # ── 3. Extract CSRF from step3 page (step1 already redirected us there) ──
    # Do NOT go back to step2 — that resets server session state.
    # step1 redirect already landed on step3; use its CSRF.
    try:
        csrf = extract_csrf(r.text)
        log.info(f"  step3 CSRF: {csrf[:12]}…")
    except ValueError:
        log.warning("  Could not extract CSRF from step3 page, using widget CSRF")

    ajax_headers = {
        "X-Requested-With": "XMLHttpRequest",
        "X-CSRF-TOKEN": csrf,
        "Origin": BASE_URL,
        "Referer": f"{BASE_URL}/booking/step3?token={TOKEN}&widget_token={WIDGET_TOKEN}",
        "Accept": "*/*",
    }

    # ── 4. POST timeslots AJAX → get available slots for date ────────────────
    log.info(f"Fetching timeslots for {date_str}…")
    timeslot_payload = [
        ("location_id",          LOCATION_ID),
        ("service_id",           SERVICE_ID),
        ("timezone",             "Australia/Sydney"),
        ("number_of_people",     "1"),
        ("modified_booking_id",  ""),
        ("is_scheduled_service", "1"),
        ("date",                 date_str),
    ]
    for rid in RESOURCE_IDS:
        timeslot_payload.append(("resource_ids[]", rid))

    r = session.post(
        f"{BASE_URL}/booking/scheduled_available_timeslots_ajax",
        params=step_params,
        data=timeslot_payload,
        headers=ajax_headers,
    )
    r.raise_for_status()

    # Log raw response to help debug slot ID parsing if needed
    log.info(f"  Timeslots response ({r.status_code}): {r.text[:500]}")

    try:
        timeslots = r.json()
    except ValueError:
        # Response might be HTML — check for "no availability" indicators
        if any(p in r.text.lower() for p in ["no availability", "no slots", "fully booked"]):
            log.error("No availability for this date.")
            return False
        log.error(f"Unexpected non-JSON timeslots response: {r.text[:300]}")
        return False

    slot_id, resource_id = find_slot_id(timeslots, TARGET_TIME)
    if not slot_id:
        log.error(
            f"Could not find {TARGET_TIME} slot. "
            f"Full timeslots response: {json.dumps(timeslots)}"
        )
        return False
    log.info(f"  Found slot ID: {slot_id}, resource ID: {resource_id}")

    # ── 5. POST schedule AJAX → select the timeslot ──────────────────────────
    log.info("Selecting timeslot…")
    r = session.post(
        f"{BASE_URL}/booking/availability/ajax/schedule",
        params=step_params,
        data={
            "date":                             date_str,
            "staffs_selected":                  "",
            "resources_selected":               resource_id,
            "schedule_or_finetune_selected":    "schedule",
            "schedule_or_finetune_id_selected": slot_id,
            "service_id":                       SERVICE_ID,
            "number_of_people":                 "1",
        },
        headers=ajax_headers,
    )
    r.raise_for_status()
    log.info(f"  Schedule select → {r.status_code}, body: {r.text[:300]}")

    # ── 6. POST step3 → commit booking state to session ─────────────────────
    # This is the "Next" button submit on the time selection page.
    # It stores date/slot/service in the session, enabling step4 to load.
    log.info("Submitting step3 (committing slot to session)…")
    r = session.post(
        f"{BASE_URL}/booking/step3",
        data={
            "_token":                           csrf,
            "token":                            TOKEN,
            "widget_token":                     WIDGET_TOKEN,
            "validated_promo_code":             "",
            "original_service_id":              SERVICE_ID,
            "should_be_added_to_waitlist":      "false",
            "multiple_sessions":                "no",
            "max_number_of_sessions":           "1",
            "max_number_of_sessions_orig":      "1",
            "booked_number_of_sessions":        "0",
            "count_number_of_sessions":         "0",
            "max_sessions_number_of_people":    "99999",
            "staff_name_invisible":             "true",
            "resource_invisible":               "true",
            "availability_only":                "no",
            "is_booking_request":               "no",
            "service_ids":                      f"location_{LOCATION_ID}_category_0_service_{SERVICE_ID}",
            "timeslot_selected":                TARGET_TIME,
            "schedule_or_finetune_id_selected": slot_id,
            "schedule_or_finetune_selected":    "schedule",
            "time_end_selected":                "",
            "staffs_selected":                  "",
            "resources_selected":               resource_id,
            "staffs_resources_selected":        "",
            "number_of_people_selected":        "1",
            "number_of_people_label":           "Number of people",
            "number_of_people_label_plural":    "people",
            "number_of_people_label_single":    "person",
            "service_max_number_of_people":     "1",
            "widget_datepicker_input":          date_str,
            "next":                             "",
        },
        headers={"Referer": f"{BASE_URL}/booking/step3?token={TOKEN}&widget_token={WIDGET_TOKEN}"},
    )
    r.raise_for_status()
    log.info(f"  step3 POST → {r.status_code}, landed on: {r.url}")

    # ── 7. GET step4 → load the customer details form ────────────────────────
    log.info("Loading step4 form…")
    r = session.get(
        f"{BASE_URL}/booking/step4",
        params={"token": TOKEN, "widget_token": WIDGET_TOKEN},
        headers={"Referer": f"{BASE_URL}/booking/step3?token={TOKEN}&widget_token={WIDGET_TOKEN}"},
    )
    r.raise_for_status()
    log.info(f"  step4 GET landed on: {r.url}")
    try:
        csrf = extract_csrf(r.text)
        log.info(f"  step4 CSRF: {csrf[:12]}…")
    except ValueError:
        log.warning("  Could not refresh CSRF from step4 page")

    if dry_run:
        log.info(
            f"DRY RUN — would POST step4 to confirm booking for "
            f"{creds['first_name']} {creds['last_name']} <{creds['email']}> "
            f"on {date_str} at {TARGET_TIME}. Stopping here."
        )
        return True

    # ── 7. POST step4 → confirm booking ──────────────────────────────────────
    log.info("Confirming booking (step4)…")
    r = session.post(
        f"{BASE_URL}/booking/step4",
        data={
            "_token":                    csrf,
            "token":                     TOKEN,
            "business_id":               BUSINESS_ID,
            "widget_token":              WIDGET_TOKEN,
            "validated_promo_code":      "",
            "service_ids":               f"location_{LOCATION_ID}_category_0_service_{SERVICE_ID}",
            "number_of_people_selected": "1",
            "require_customer_payment":  "",
            "first_name":                creds["first_name"],
            "last_name":                 creds["last_name"],
            "email":                     creds["email"],
            "mobile":                    creds["mobile"],
            "mobile_country":            "AU",
            "booking_comments":          "",
            "booking_confirmation_by":   "email",
            "confirm":                   "",
        },
        headers={
            "Referer": f"{BASE_URL}/booking/step4?token={TOKEN}&widget_token={WIDGET_TOKEN}",
        },
    )

    # Save the full step4 response for manual verification, keep last 10 only
    LOGS_DIR.mkdir(exist_ok=True)
    response_log = LOGS_DIR / f"booking_response_{date_str}.html"
    response_log.write_text(r.text)
    log.info(f"  Step4 response saved to {response_log}")
    old_responses = sorted(LOGS_DIR.glob("booking_response_*.html"))[:-10]
    for f in old_responses:
        f.unlink()

    if r.status_code >= 400:
        log.error(f"step4 returned HTTP {r.status_code}")
        return False

    log.info(f"  Final URL after step4: {r.url}")

    # Redirect back to step1 or step3 means the POST was rejected
    final_url = r.url.lower()
    if "step1" in final_url or "step3" in final_url:
        visible = re.sub(r"<[^>]+>", " ", re.sub(r"<script[^>]*>.*?</script>", "", r.text, flags=re.DOTALL))
        visible = re.sub(r"\s+", " ", visible).strip()
        log.error(f"Booking rejected — redirected back to {r.url}")
        log.error(f"  Page text: {visible[:400]}")
        return False

    # Strip <script> blocks before checking page content
    body_no_scripts = re.sub(r"<script[^>]*>.*?</script>", "", r.text, flags=re.DOTALL)
    content = body_no_scripts.lower()

    success_phrases = [
        "booking confirmed", "booking has been confirmed",
        "thank you for your booking", "booking reference",
        "you're booked", "youre booked",
    ]
    matched = next((p for p in success_phrases if p in content), None)
    if matched:
        idx = content.find(matched)
        snippet = body_no_scripts[max(0, idx - 80):idx + 200].replace("\n", " ").strip()
        log.info(f"Booking CONFIRMED for {date_str} at {TARGET_TIME}")
        log.info(f"  Matched {matched!r}: …{snippet}…")
        return True

    visible = re.sub(r"<[^>]+>", " ", body_no_scripts)
    visible = re.sub(r"\s+", " ", visible).strip()
    log.warning(f"Unclear result (HTTP {r.status_code}, url={r.url})")
    log.warning(f"  Page text: {visible[:600]}")
    return False


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Book a gym HIIT class")
    parser.add_argument("--date", help="Override date to book (YYYY-MM-DD). Skips the enabled check.")
    parser.add_argument("--dry-run", action="store_true", help="Go through all steps but skip the final confirmation POST.")
    parser.add_argument("--fake", action="store_true", help="Use fake test credentials (real email so you can cancel).")
    parser.add_argument("--fail", action="store_true", help="Simulate a booking failure to test email notification.")
    args = parser.parse_args()

    if args.date:
        d = date_type.fromisoformat(args.date)
        job_id = WEEKDAY_JOB.get(d.weekday())
        if not job_id:
            log.error(f"{args.date} is not a Tuesday or Thursday.")
            sys.exit(1)
        date_str = args.date
        log.info(f"Manual run: booking {date_str} (job: {job_id})")
    else:
        date_str, job_id = target_date()
        if not date_str:
            log.info("Not a booking day. Exiting.")
            sys.exit(0)
        if not is_job_enabled(job_id):
            log.info(f"Job {job_id} is disabled. Skipping.")
            sys.exit(0)

    if args.fake:
        creds = {"first_name": "Gordon", "last_name": "Macdonald", "email": "gordonmaccas@proton.me", "mobile": "0417826417"}
        log.info("  Using fake test credentials")
    else:
        try:
            creds = load_gym_creds()
        except ValueError as e:
            msg = str(e)
            log.error(msg)
            update_job_status(job_id, "error", msg)
            sys.exit(1)

    if args.fail:
        log.info("--fail flag set, simulating booking failure for notification test.")
        success = False
    else:
        success = book(date_str, creds, dry_run=args.dry_run)
    msg = (
        f"Booked {date_str} at {TARGET_TIME}"
        if success
        else f"Failed to book {date_str} at {TARGET_TIME}"
    )
    update_job_status(job_id, "success" if success else "error", msg)
    if not success and (args.fail or (not args.fake and not args.dry_run)):
        day_name = "Thurs" if job_id == "gym_thursday_7am" else "Tues"
        send_notification(f"Gym booking FAILED — {day_name} {date_str} {TARGET_TIME}", msg)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
