#!/usr/bin/env python3
"""
Daily news digest

Fetches top headlines from RSS feeds, summarises with Gemini,
emails the digest, and pushes _data/news.json to the portfolio repo.

Cron (add with: crontab -e):
  0 20 * * * ~/venv/bin/python ~/homeserver/scripts/news.py --real >> ~/homeserver/logs/news.log 2>&1

Manual:
  python scripts/news.py            # dry run: fetches + prints, no email/push
  python scripts/news.py --email-only  # sends email, no GitHub push
  python scripts/news.py --real     # sends email + pushes to GitHub
"""

import argparse
import base64
import json
import logging
import smtplib
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import List

import requests
from google import genai
from google.genai import types
from google.genai.types import ThinkingConfig
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).parent.parent / "config"))
import config as cfg

GEMINI_MODEL = "gemini-2.5-flash"
PORTFOLIO_REPO = "s-jac/s-jac.github.io"
PORTFOLIO_DATA_PATH = "_data/news.json"

RSS_FEEDS = [
    ("World",     "https://feeds.bbci.co.uk/news/world/rss.xml"),
    ("Tech",      "https://feeds.bbci.co.uk/news/technology/rss.xml"),
    ("Australia", "https://www.abc.net.au/news/feed/51120/rss.xml"),
    ("Science",   "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml"),
]
MAX_ITEMS_PER_FEED = 8

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


# ── Response schema ───────────────────────────────────────────────────────────

class NewsSection(BaseModel):
    heading: str
    summary: str

class NewsDigest(BaseModel):
    sections: List[NewsSection]


# ── RSS ───────────────────────────────────────────────────────────────────────

def fetch_rss(url: str, max_items: int) -> list:
    resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    root = ET.fromstring(resp.content)
    items = []
    for item in root.iter("item"):
        title = item.findtext("title", "").strip()
        desc  = item.findtext("description", "").strip()
        if title:
            items.append(f"- {title}: {desc[:200]}")
        if len(items) >= max_items:
            break
    return items

def build_headlines() -> str:
    lines = ["Here are today's top headlines:\n"]
    for category, url in RSS_FEEDS:
        try:
            items = fetch_rss(url, MAX_ITEMS_PER_FEED)
            lines.append(f"## {category}")
            lines.extend(items)
            lines.append("")
            log.info(f"Fetched {len(items)} items from {category}")
        except Exception as e:
            log.warning(f"Failed to fetch {category} feed: {e}")
    return "\n".join(lines)


# ── Gemini ────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a news editor writing a concise daily digest for a general audience.
You will receive today's top headlines from several RSS feeds, grouped by category.
For each category, write a 2-3 sentence summary of the most important stories.
Be clear, factual, and neutral in tone."""

def call_gemini(headlines: str) -> NewsDigest:
    client = genai.Client(api_key=cfg.gemini_api_key)
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        temperature=0.8,
        max_output_tokens=2000,
        response_mime_type="application/json",
        response_schema=NewsDigest,
        thinking_config=ThinkingConfig(include_thoughts=False, thinking_budget=0),
    )
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=headlines,
        config=config,
    )
    return response.parsed


# ── Email ─────────────────────────────────────────────────────────────────────

def send_email(digest: NewsDigest, today: str) -> None:
    ec = cfg.email
    if not ec.get("enabled"):
        log.info("Email disabled in config, skipping")
        return

    lines = [f"Daily News — {today}", "=" * 40, ""]
    for section in digest.sections:
        lines.append(section.heading.upper())
        lines.append(section.summary)
        lines.append("")
    body = "\n".join(lines)

    msg = EmailMessage()
    msg["Subject"] = f"Daily News — {today}"
    msg["From"]    = ec["from_address"]
    msg["To"]      = ", ".join(cfg.news_recipients)
    msg.set_content(body)

    with smtplib.SMTP(ec["smtp_host"], ec["smtp_port"]) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.login(ec["username"], ec["app_password"])
        smtp.send_message(msg)
    log.info(f"Email sent to {cfg.news_recipients}")


# ── GitHub ────────────────────────────────────────────────────────────────────

def push_to_github(digest: NewsDigest, today: str) -> None:
    headers = {
        "Authorization": f"token {cfg.github_token}",
        "Accept":        "application/vnd.github.v3+json",
    }
    api_url = f"https://api.github.com/repos/{PORTFOLIO_REPO}/contents/{PORTFOLIO_DATA_PATH}"

    # Fetch current file SHA (required to update an existing file)
    get_resp = requests.get(api_url, headers=headers, timeout=15)
    sha = get_resp.json().get("sha") if get_resp.status_code == 200 else None

    data = {
        "date": today,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sections": [{"heading": s.heading, "summary": s.summary} for s in digest.sections],
    }
    payload = {
        "message": f"daily news update {today}",
        "content": base64.b64encode(
            json.dumps(data, indent=2, ensure_ascii=False).encode()
        ).decode(),
    }
    if sha:
        payload["sha"] = sha

    put_resp = requests.put(api_url, headers=headers, json=payload, timeout=15)
    put_resp.raise_for_status()
    log.info(f"Pushed news.json to {PORTFOLIO_REPO}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Daily news digest")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--real", action="store_true",
        help="Send email and push to GitHub (default: dry run, print only)"
    )
    mode.add_argument(
        "--email-only", action="store_true",
        help="Send email but do not push to GitHub"
    )
    args = parser.parse_args()

    today = datetime.now(timezone.utc).strftime("%-d %B %Y")

    log.info("Fetching RSS feeds")
    headlines = build_headlines()

    log.info("Calling Gemini")
    digest = call_gemini(headlines)

    print(f"\n{'=' * 40}")
    print(f"  {today}")
    print(f"{'=' * 40}")
    for section in digest.sections:
        print(f"\n{section.heading.upper()}")
        print(section.summary)
    print()

    if not args.real and not args.email_only:
        log.info("Dry run — skipping email and GitHub push. Pass --real to publish.")
        return

    send_email(digest, today)

    if args.real:
        push_to_github(digest, today)

    log.info("Done")


if __name__ == "__main__":
    main()
