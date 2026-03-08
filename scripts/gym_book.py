#!/usr/bin/env python3
"""
Manly Aquatic Centre - HIIT Class Auto-Booker

Books the Tuesday or Thursday 7am HIIT class on nabooki.com.
Reads credentials from config/settings.json under the "gym" key.

Cron schedule (set up via crontab -e):
    30 0 * * SAT  → runs Saturday 00:30, books Tuesday  (3 days ahead)
    30 0 * * MON  → runs Monday  00:30, books Thursday (3 days ahead)

Manual test run:
    venv/bin/python scripts/gym_book.py --date 2026-03-10
"""

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytz
from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

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

# weekday int → job id
WEEKDAY_JOB = {1: "gym_tuesday_7am", 3: "gym_thursday_7am"}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


def load_gym_creds():
    with open(SETTINGS_FILE) as f:
        gym = json.load(f).get("gym", {})
    creds = {k: gym.get(k, "") for k in ("first_name", "last_name", "email", "mobile")}
    missing = [k for k, v in creds.items() if not v]
    if missing:
        raise ValueError(f"Missing gym credentials in settings.json: {missing}")
    return creds


def target_date():
    """Return (date_str, job_id) for the class 3 days from now if Tue or Thu, else (None, None)."""
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
    """Attempt to book the HIIT class on date_str. Returns True on success."""
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

        # Track POST response statuses after the confirm step
        post_responses = []

        def on_response(response):
            if response.request.method == "POST":
                post_responses.append((response.url, response.status))
                log.info(f"  POST {response.url} → {response.status}")

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
                return False

            page.wait_for_load_state("networkidle")

            # ── Step 3: Navigate to target date ─────────────────────────
            # TODO: If the widget calendar doesn't default to the right week,
            # add prev/next month navigation here. Inspect selectors on the
            # live page if bookings land on the wrong date.
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
                log.warning("Could not click specific date — continuing, widget may auto-select.")

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

            # ── Step 5: Find and click the 7:00 am slot ─────────────────
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
                return False

            page.wait_for_load_state("networkidle")

            # ── Step 6: Fill in booking form ─────────────────────────────
            # TODO: Verify field name attributes against the live page.
            log.info("Filling booking form…")
            try:
                page.fill("input[name='first_name']", creds["first_name"], timeout=5000)
                page.fill("input[name='last_name']",  creds["last_name"])
                page.fill("input[name='email']",      creds["email"])
                page.fill("input[name='mobile']",     creds["mobile"])
            except PlaywrightTimeout:
                log.error("Could not fill form fields — selectors may need updating.")
                return False

            # ── Step 7: Confirm — start tracking responses now ───────────
            log.info("Submitting booking…")
            page.on("response", on_response)
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
            # Check 1: any POST came back with a non-success status?
            failed_posts = [(url, s) for url, s in post_responses if s >= 400]
            if failed_posts:
                log.error(f"Booking POST returned error status(es): {failed_posts}")
                return False

            # Check 2: page content confirms booking
            content = page.content().lower()
            success_phrases = ["confirmed", "success", "thank you", "booking reference", "you're booked", "youre booked"]
            if any(phrase in content for phrase in success_phrases):
                log.info(f"Booking CONFIRMED for {date_str} at {TARGET_TIME}")
                return True

            # Neither a clear success nor a clear HTTP error — log what we see
            snippet = page.inner_text("body")[:400].replace("\n", " ").strip()
            log.warning(f"Unclear result. Page text: {snippet}")
            return False

        except Exception as e:
            log.error(f"Unexpected error: {e}")
            return False

        finally:
            context.close()
            browser.close()


def main():
    parser = argparse.ArgumentParser(description="Book a gym HIIT class")
    parser.add_argument(
        "--date",
        help="Override date to book (YYYY-MM-DD). Skips the enabled check.",
        default=None,
    )
    args = parser.parse_args()

    if args.date:
        # Manual run — resolve job from the date's weekday
        from datetime import date as date_type
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
            log.info("Not a booking day (no Tue/Thu class 3 days from now). Exiting.")
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
        sys.exit(1)

    success = book(date_str, creds)
    msg = (
        f"Booked {date_str} at {TARGET_TIME}"
        if success
        else f"Failed to book {date_str} at {TARGET_TIME}"
    )
    update_job_status(job_id, "success" if success else "error", msg)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
