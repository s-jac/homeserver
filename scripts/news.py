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
from google.api_core.exceptions import ResourceExhausted
from google.genai import errors as genai_errors, types
from google.genai.types import ThinkingConfig
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).parent.parent / "config"))
import config as cfg

GEMINI_MODEL = "gemini-2.5-flash"
PORTFOLIO_REPO = "s-jac/s-jac.github.io"
PORTFOLIO_DATA_PATH = "_data/news.json"

RSS_FEED_GROUPS = [
    ("World", [
        ("BBC World",     "https://feeds.bbci.co.uk/news/world/rss.xml"),
        ("AP News",       "https://rsshub.app/apnews/topics/apf-topnews"),
        ("The Guardian",  "https://www.theguardian.com/world/rss"),
        ("MarketWatch",   "https://feeds.content.dowjones.io/public/rss/mw_realtimeheadlines"),
    ]),
    ("Australia", [
        ("ABC News",       "https://www.abc.net.au/news/feed/51120/rss.xml"),
        ("The Guardian AU","https://www.theguardian.com/au/rss"),
        ("SMH",            "https://www.smh.com.au/rss/feed.xml"),
    ]),
    ("Economics", [
        ("FT",             "https://www.ft.com/rss/home"),
        ("Bloomberg",      "https://feeds.bloomberg.com/markets/news.rss"),
        ("The Economist",  "https://www.economist.com/finance-and-economics/rss.xml"),
        ("Marginal Rev",   "https://feeds.feedburner.com/marginalrevolution"),
    ]),
]

MAX_ITEMS_PER_FEED = 8

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


# ── Response schema ───────────────────────────────────────────────────────────

class Bullet(BaseModel):
    text: str
    source: str  # e.g. "BBC World", "The Guardian"

class NewsSection(BaseModel):
    heading: str
    bullets: List[Bullet]

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

def build_headlines(feeds: list) -> str:
    lines = ["Here are today's top headlines:\n"]
    for category, url in feeds:
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
You will receive today's top headlines from several RSS feeds for a single topic, each labelled with its source name.
Write exactly 3 bullet points summarising the 3 most important stories.
Each bullet should be 1-2 sentences. Be clear, factual, and neutral in tone.
For each bullet, set the source field to the name of the feed it came from (e.g. "BBC World", "The Guardian").
Ignore any sports stories entirely — do not include them in your bullets.
The heading field must be set to exactly the topic name provided, nothing else."""

GEMINI_CONFIG = types.GenerateContentConfig(
    system_instruction=SYSTEM_PROMPT,
    temperature=0.8,
    max_output_tokens=8000,
    response_mime_type="application/json",
    response_schema=NewsDigest,
    thinking_config=ThinkingConfig(include_thoughts=False, thinking_budget=0),
)

def call_gemini(topic: str, headlines: str, key_index: int = 0) -> tuple[NewsDigest, int]:
    api_keys = cfg.gemini_api_keys
    for i in range(key_index, len(api_keys)):
        client = genai.Client(api_key=api_keys[i])
        try:
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=headlines,
                config=GEMINI_CONFIG,
            )
            if response.parsed is None:
                log.error(f"Gemini returned unparseable response for {topic}: {response.text[:500]}")
                raise ValueError(f"Gemini response could not be parsed into NewsDigest for topic: {topic}")
            return response.parsed, i
        except (genai_errors.ClientError, ResourceExhausted) as e:
            if '429' in str(e) or 'RESOURCE_EXHAUSTED' in str(e):
                if i + 1 < len(api_keys):
                    log.warning(f"Rate limited on {topic} (key {i}), switching to next key")
                else:
                    raise
            else:
                raise


# ── Email ─────────────────────────────────────────────────────────────────────

def send_email(digest: NewsDigest, today: str) -> None:
    ec = cfg.email
    if not ec.get("enabled"):
        log.info("Email disabled in config, skipping")
        return

    lines = [f"Daily News — {today}", "=" * 40, ""]
    for section in digest.sections:
        lines.append(section.heading.upper())
        for bullet in section.bullets:
            lines.append(f"  • {bullet.text} [{bullet.source}]")
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

    get_resp = requests.get(api_url, headers=headers, timeout=15)
    sha = get_resp.json().get("sha") if get_resp.status_code == 200 else None

    data = {
        "date": today,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sections": [
            {"heading": s.heading, "bullets": [{"text": b.text, "source": b.source} for b in s.bullets]}
            for s in digest.sections
        ],
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

    all_sections = []
    key_index = 0
    for topic, feeds in RSS_FEED_GROUPS:
        log.info(f"Fetching RSS feeds for {topic}")
        headlines = build_headlines(feeds)
        log.info(f"Calling Gemini for {topic}")
        topic_digest, key_index = call_gemini(topic, headlines, key_index)
        for section in topic_digest.sections:
            section.heading = topic
        all_sections.extend(topic_digest.sections)

    digest = NewsDigest(sections=all_sections)

    print(f"\n{'=' * 40}")
    print(f"  {today}")
    print(f"{'=' * 40}")
    for section in digest.sections:
        print(f"\n{section.heading.upper()}")
        for bullet in section.bullets:
            print(f"  • {bullet.text} [{bullet.source}]")
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