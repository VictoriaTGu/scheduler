"""Meetup-specific collector using Playwright + JSON-LD."""

import json
import logging
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
from dateutil import parser as dateutil_parser

from src.collectors.renderer import get_rendered_html
from src.models.event import Event
from src.services.normalization import apply_region_tags

logger = logging.getLogger(__name__)

MEETUP_LOCAL_TZ = ZoneInfo("America/New_York")


async def collect_meetup_events(source_url: str, source_id: int) -> list[Event]:
    """
    Fetch a Meetup search or group page, extract all JSON-LD Event objects,
    and return normalized Event instances.

    Playwright is always used (force_playwright=True) because Meetup's
    event listings are entirely JavaScript-rendered.
    """
    logger.info("Fetching Meetup page: %s", source_url)
    html = await get_rendered_html(source_url, force_playwright=True)

    soup = BeautifulSoup(html, "html.parser")
    events: list[Event] = []

    for script_tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script_tag.string or "")
        except (json.JSONDecodeError, TypeError):
            continue

        # JSON-LD may be a single object or a list
        items = data if isinstance(data, list) else [data]

        for item in items:
            if item.get("@type") != "Event":
                continue
            try:
                event = _jsonld_to_event(item, source_id)
                if event is not None:
                    event = apply_region_tags(event)
                    events.append(event)
            except Exception as exc:
                logger.warning("Failed to parse Meetup event: %s", exc)

    logger.info("Collected %d events from %s", len(events), source_url)
    return events


def _jsonld_to_event(item: dict, source_id: int) -> Optional[Event]:
    """Convert a schema.org Event JSON-LD dict to an Event model instance."""
    title = item.get("name", "").strip()
    if not title:
        return None

    start_str = item.get("startDate")
    if not start_str:
        return None
    try:
        start_datetime = dateutil_parser.parse(start_str)
    except (ValueError, TypeError):
        logger.debug("Could not parse startDate: %s", start_str)
        return None

    start_datetime_utc = _to_utc_aware_datetime(start_datetime)

    end_datetime: Optional[datetime] = None
    end_str = item.get("endDate")
    if end_str:
        try:
            end_datetime = dateutil_parser.parse(end_str)
            end_datetime = _to_local_naive_datetime(end_datetime)
        except (ValueError, TypeError):
            pass

    # Filter past events
    now_utc = datetime.now(timezone.utc)
    if start_datetime_utc < now_utc:
        logger.debug("Skipping past Meetup event: %s (%s)", title, start_datetime_utc)
        return None

    start_datetime = _to_local_naive_datetime(start_datetime_utc)

    location = item.get("location", {})
    address = location.get("address", {}) if isinstance(location, dict) else {}
    venue_name: Optional[str] = location.get("name") if isinstance(location, dict) else None

    if isinstance(address, dict):
        street = address.get("streetAddress")
        city = address.get("addressLocality") or "Unknown"
        state = address.get("addressRegion") or "Unknown"
    else:
        street = str(address) if address else None
        city = "Unknown"
        state = "Unknown"

    cost = _parse_offers(item.get("offers"))

    return Event(
        title=title,
        description=item.get("description"),
        start_datetime=start_datetime,
        end_datetime=end_datetime,
        venue_name=venue_name,
        region_tag="Other",
        city=city,
        state=state,
        address=street,
        cost=cost,
        event_url=item.get("url", source_id and "" or ""),
        source_id=source_id,
        image_url=item.get("image"),
    )


def _to_local_naive_datetime(value: datetime) -> datetime:
    """Convert Meetup timestamps to naive America/New_York local time."""
    if value.tzinfo is None:
        return value
    return value.astimezone(MEETUP_LOCAL_TZ).replace(tzinfo=None)


def _to_utc_aware_datetime(value: datetime) -> datetime:
    """Normalize Meetup timestamps to UTC-aware datetimes for filtering."""
    if value.tzinfo is None:
        return value.replace(tzinfo=MEETUP_LOCAL_TZ).astimezone(timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_offers(offers) -> Optional[str]:
    if not offers:
        return None
    if isinstance(offers, list):
        offers = offers[0]
    price = offers.get("price", "")
    currency = offers.get("priceCurrency", "")
    if str(price) == "0":
        return "Free"
    if price:
        return f"{currency}{price}".strip()
    return None
