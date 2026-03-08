#!/usr/bin/env python3
"""
Manly Aquatic Centre - HIIT Class Auto-Booker

Books the Tuesday or Thursday 7am HIIT class on nabooki.com.
Reads credentials from config/settings.json under the "gym" key.

Cron schedule (set up via crontab -e):
    30 0 * * SAT  → runs Saturday 00:30, books Tuesday  (3 days ahead)
    30 0 * * MON  → runs Monday  00:30, books Thursday (3 days ahead)
"""

import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytz
from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).parent))
from notify import send_notification

BASE_DIR = Path(__file__).parent.parent
SETTINGS_FILE = BASE_DIR / "config" / "settings.json"
JOBS_FILE = BASE_DIR / "config" / "jobs.json"
LOGS_DIR = BASE_DIR / "logs"

WIDGET_URL = (
    "https://app.nabooki.com/booking/popup/widget"
    "?token=5fade28f6f4d07.93412102"
)
TARGET_TIME = "7:00 am"
SYDNEY_TZ = pytz.timezone("Australia/Sydney")

# Maps weekday int → job id (for updating UI status)
WEEKDAY_JOB = {1: "gym_tuesday_7am", 3: "gym_thursday_7am"}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


def load_settings():
    with open(SETTINGS_FILE) as f:
        return json.load(f)


def load_gym_creds():
    gym = load_settings().get("gym", {})
    creds = {
        "first_name": gym.get("first_name", ""),
        "last_name":  gym.get("last_name", ""),
        "email":      gym.get("email", ""),
        "mobile":     gym.get("mobile", ""),
    }
    missing = [k for k, v in creds.items() if not v]
    if missing:
        raise ValueError(f"Missing gym credentials in settings.json: {missing}")
    return creds


def target_date():
    """
    Return (date_str, job_id) for the class 3 days from now if it's a Tue or Thu.
    Returns (None, None) if today is not a booking day.
    """
    today = datetime.now(SYDNEY_TZ).date()
    target = today + timedelta(days=3)
    job_id = WEEKDAY_JOB.get(target.weekday())
    if job_id:
        return target.strftime("%Y-%m-%d"), job_id
    return None, None


def is_job_enabled(job_id):
    with open(JOBS_FILE) as f:
        data = json.load(f)
    job = next((j for j in data["jobs"] if j["id"] == job_id), None)
    return job["enabled"] if job else False


def update_job_status(job_id, status, message):
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


def book(date_str, creds):
    """
    Attempt to book the HIIT class on date_str. Returns True on success.
    date_str format: YYYY-MM-DD
    """
    # Format as the page likely displays it, e.g. "15" for the 15th
    target_day_num = str(int(date_str.split("-")[2]))

    log.info(f"Starting booking for {date_str} at {TARGET_TIME}")
    LOGS_DIR.mkdir(exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/144.0.0.0 Safari/537.36"
            ),
        )
        page = context.new_page()

        def screenshot(name):
            path = str(LOGS_DIR / f"{name}_{date_str}.png")
            try:
                page.screenshot(path=path)
                log.info(f"Screenshot saved: {path}")
            except Exception:
                pass

        try:
            # ── Step 1: Load widget ──────────────────────────────────────
            log.info("Loading booking widget…")
            page.goto(WIDGET_URL, wait_until="networkidle")

            # ── Step 2: Select HIIT ──────────────────────────────────────
            log.info("Selecting HIIT class…")
            hiit_clicked = False
            for selector in [
                "text=HIIT",
                "button:has-text('HIIT')",
                "li:has-text('HIIT')",
                "[class*='service']:has-text('HIIT')",
            ]:
                try:
                    page.click(selector, timeout=3000)
                    hiit_clicked = True
                    log.info(f"  Clicked HIIT via: {selector}")
                    break
                except PlaywrightTimeout:
                    continue

            if not hiit_clicked:
                log.error("Could not find HIIT button.")
                screenshot("debug_hiit")
                return False

            page.wait_for_load_state("networkidle")

            # ── Step 3: Navigate to the target date in the calendar ──────
            # TODO: The nabooki calendar may need date navigation (prev/next
            # month arrows) to reach the right week. Inspect the live page
            # to confirm the calendar's selector structure and add clicks
            # here if the widget doesn't default to showing 3 days ahead.
            #
            # Basic attempt — look for the day number and click it:
            log.info(f"Selecting date {date_str} (day {target_day_num})…")
            date_clicked = False
            for selector in [
                f"td[data-date='{date_str}']",
                f"td:has-text('{target_day_num}')",
                f"[class*='day']:has-text('{target_day_num}')",
                f"button:has-text('{target_day_num}')",
            ]:
                try:
                    page.click(selector, timeout=3000)
                    date_clicked = True
                    log.info(f"  Clicked date via: {selector}")
                    break
                except PlaywrightTimeout:
                    continue

            if not date_clicked:
                log.warning(
                    "Could not click specific date — page may auto-show correct week. Continuing…"
                )
                screenshot("debug_date")

            page.wait_for_load_state("networkidle")

            # ── Step 4: Click 'Check Availability' ──────────────────────
            log.info("Clicking Check Availability…")
            for selector in [
                "button:has-text('Check Availability')",
                "input[value*='Check']",
                "text=Check Availability",
            ]:
                try:
                    page.click(selector, timeout=3000)
                    break
                except PlaywrightTimeout:
                    continue

            page.wait_for_load_state("networkidle")

            # ── Step 5: Find and click the 7:00 am time slot ────────────
            log.info(f"Looking for {TARGET_TIME} slot…")
            slot_found = False
            try:
                slot = page.locator(f"*:has-text('{TARGET_TIME}')").last
                book_btn = slot.locator("button:has-text('Book'), a:has-text('Book')")
                book_btn.click(timeout=5000)
                slot_found = True
                log.info("  Clicked time slot.")
            except Exception:
                try:
                    page.click("button:has-text('Book Now')", timeout=5000)
                    slot_found = True
                    log.info("  Clicked first 'Book Now' button.")
                except PlaywrightTimeout:
                    pass

            if not slot_found:
                log.error("Could not find time slot — class may be full or booking not yet open.")
                screenshot("debug_slot")
                return False

            page.wait_for_load_state("networkidle")

            # ── Step 6: Fill in booking form ─────────────────────────────
            # TODO: Verify these field name attributes against the live page.
            log.info("Filling booking form…")
            try:
                page.fill("input[name='first_name']", creds["first_name"], timeout=5000)
                page.fill("input[name='last_name']",  creds["last_name"])
                page.fill("input[name='email']",      creds["email"])
                page.fill("input[name='mobile']",     creds["mobile"])
            except PlaywrightTimeout:
                log.error("Could not fill form fields — selectors may need updating.")
                screenshot("debug_form")
                return False

            # ── Step 7: Confirm ──────────────────────────────────────────
            log.info("Submitting booking…")
            for selector in [
                "input[name='confirm']",
                "button[name='confirm']",
                "button:has-text('Confirm')",
                "input[type='submit']",
            ]:
                try:
                    page.click(selector, timeout=3000)
                    break
                except PlaywrightTimeout:
                    continue

            page.wait_for_load_state("networkidle")

            # ── Step 8: Verify success ───────────────────────────────────
            content = page.content().lower()
            if any(w in content for w in ["confirmed", "success", "thank you", "booking reference"]):
                log.info(f"Booking CONFIRMED for {date_str} at {TARGET_TIME}")
                screenshot("confirmed")
                return True
            else:
                log.warning("Could not confirm success from page content.")
                screenshot("uncertain")
                return False

        except Exception as e:
            log.error(f"Unexpected error: {e}")
            screenshot("debug_error")
            return False

        finally:
            context.close()
            browser.close()


def main():
    date_str, job_id = target_date()

    if not date_str:
        log.info("Not a booking day (no class 3 days from now). Exiting.")
        sys.exit(0)

    if not is_job_enabled(job_id):
        log.info(f"Job {job_id} is disabled. Skipping.")
        sys.exit(0)

    try:
        creds = load_gym_creds()
    except ValueError as e:
        msg = str(e)
        log.error(msg)
        update_job_status(job_id, "error", msg)
        send_notification("Gym booking ERROR", msg)
        sys.exit(1)

    success = book(date_str, creds)
    msg = (
        f"Booked {date_str} at {TARGET_TIME}"
        if success
        else f"Failed to book {date_str} at {TARGET_TIME}"
    )
    update_job_status(job_id, "success" if success else "error", msg)
    send_notification(
        f"Gym booking {'SUCCESS' if success else 'FAILED'}",
        msg
    )
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
