"""
Gym class booking script.
This is a placeholder — fill in the booking logic once you have the gym site details.
"""

import sys
import json
from pathlib import Path

# Allow importing notify from sibling directory
sys.path.insert(0, str(Path(__file__).parent))
from notify import send_notification

JOBS_FILE = Path(__file__).parent.parent / "config" / "jobs.json"


def get_job_params():
    with open(JOBS_FILE) as f:
        data = json.load(f)
    job = next((j for j in data["jobs"] if j["id"] == "gym_thursday_7am"), None)
    return job["params"] if job else {}


def book_class(class_day: str, class_time: str):
    """
    TODO: implement actual booking logic here.

    Steps will likely be:
    1. POST to gym login endpoint with credentials
    2. Find the class for `class_day` at `class_time`
    3. POST to the booking endpoint
    4. Confirm booking in response

    Credentials should be added to settings.json under a "gym" key and read here.
    """
    raise NotImplementedError("Booking logic not yet implemented")


def main():
    params = get_job_params()
    class_day = params.get("class_day", "thursday")
    class_time = params.get("class_time", "07:00")

    try:
        book_class(class_day, class_time)
        msg = f"Successfully booked {class_day} {class_time} class."
        print(msg)
        send_notification("Gym booking SUCCESS", msg)
    except NotImplementedError as e:
        print(f"Not implemented: {e}")
        sys.exit(1)
    except Exception as e:
        msg = f"Failed to book {class_day} {class_time} class: {e}"
        print(msg, file=sys.stderr)
        send_notification("Gym booking FAILED", msg)
        sys.exit(1)


if __name__ == "__main__":
    main()
