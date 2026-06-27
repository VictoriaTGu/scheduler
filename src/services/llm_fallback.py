"""Targeted LLM fallback for extracting missing event fields via OpenAI."""

import re
import logging
from datetime import datetime
from typing import Optional

from bs4 import BeautifulSoup
from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

_client = AsyncOpenAI()  # reads OPENAI_API_KEY from env


def _extract_snippet(html: str, event_title: str, window_chars: int = 500) -> str:
    """
    Extract a short plain-text window around the event title from the HTML.
    Strips all tags first to minimize tokens sent to the LLM.
    """
    soup = BeautifulSoup(html, "html.parser")
    # Remove script/style noise
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    text = soup.get_text(separator=" ", strip=True)
    text = re.sub(r'\s+', ' ', text)

    idx = text.lower().find(event_title.lower()[:30])
    if idx == -1:
        # Title not found in plain text — return first window
        return text[:window_chars]
    start = max(0, idx - 100)
    return text[start: start + window_chars]


async def extract_missing_time(html: str, event_title: str) -> Optional[str]:
    """
    Use GPT-4o-mini to extract a start time when structured parsing returned midnight.

    Returns a time string like "7:30 PM" or None if not found.
    Caller is responsible for parsing the returned string into the datetime.
    """
    snippet = _extract_snippet(html, event_title)

    response = await _client.chat.completions.create(
        model="gpt-4o-mini",
        max_tokens=20,
        temperature=0,
        messages=[
            {
                "role": "system",
                "content": (
                    "You extract event start times from text. "
                    "Reply with ONLY the time in 12-hour format like '7:30 PM'. "
                    "If no time is present, reply with the single word: null"
                ),
            },
            {
                "role": "user",
                "content": f"Event: {event_title}\n\nContext:\n{snippet}",
            },
        ],
    )

    result = response.choices[0].message.content.strip()
    return None if result.lower() == "null" else result


async def extract_missing_fields(html: str, event_title: str) -> dict:
    """
    Broader fallback: extract time, cost, and venue in one call when multiple
    fields are missing. Use this only if two or more fields are absent,
    to avoid making multiple single-field calls.

    Returns a dict with keys: time, cost, venue_name (any may be None).
    """
    snippet = _extract_snippet(html, event_title)

    response = await _client.chat.completions.create(
        model="gpt-4o-mini",
        max_tokens=60,
        temperature=0,
        messages=[
            {
                "role": "system",
                "content": (
                    "Extract event details from the text. "
                    "Reply in this exact format with no extra words:\n"
                    "time: <HH:MM AM/PM or null>\n"
                    "cost: <amount or Free or null>\n"
                    "venue: <name or null>"
                ),
            },
            {
                "role": "user",
                "content": f"Event: {event_title}\n\nContext:\n{snippet}",
            },
        ],
    )

    raw = response.choices[0].message.content.strip()
    result: dict = {"time": None, "cost": None, "venue_name": None}
    for line in raw.splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            val = val.strip()
            if val.lower() == "null":
                val = None
            if key.strip() == "time":
                result["time"] = val
            elif key.strip() == "cost":
                result["cost"] = val
            elif key.strip() == "venue":
                result["venue_name"] = val
    return result


def _parse_time_into_date(existing_dt: datetime, time_str: str) -> datetime:
    """Merge a '7:30 PM' string into an existing date, preserving the date."""
    parsed = datetime.strptime(time_str.strip().upper(), "%I:%M %p")
    return existing_dt.replace(hour=parsed.hour, minute=parsed.minute, second=0)


async def apply_llm_fallback_to_event(event, html: str, llm_fallback_enabled: bool):
    """
    Check event for missing fields and call LLM fallback if needed.

    Mutates the event in-place. Only fires when llm_fallback_enabled=True.
    Returns the (possibly updated) event.
    """
    if not llm_fallback_enabled:
        return event

    time_is_missing = event.start_datetime.hour == 0 and event.start_datetime.minute == 0
    cost_is_missing = event.cost is None
    venue_is_missing = event.venue_name is None

    missing_count = sum([time_is_missing, cost_is_missing, venue_is_missing])
    if missing_count == 0:
        return event

    try:
        if missing_count >= 2:
            logger.debug("LLM fallback (multi-field) for event: %s", event.title)
            fields = await extract_missing_fields(html, event.title)
            if fields["time"] and time_is_missing:
                event.start_datetime = _parse_time_into_date(event.start_datetime, fields["time"])
            if fields["cost"] and cost_is_missing:
                event.cost = fields["cost"]
            if fields["venue_name"] and venue_is_missing:
                event.venue_name = fields["venue_name"]
        elif time_is_missing:
            logger.debug("LLM fallback (time only) for event: %s", event.title)
            time_str = await extract_missing_time(html, event.title)
            if time_str:
                event.start_datetime = _parse_time_into_date(event.start_datetime, time_str)
    except Exception as exc:
        logger.warning("LLM fallback failed for '%s': %s", event.title, exc)

    return event
