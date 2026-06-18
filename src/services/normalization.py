"""Event normalization service."""

import logging
from datetime import datetime
from typing import Optional

from src.models import Event, Source

logger = logging.getLogger(__name__)


def normalize_event(event: Event, source: Source) -> Event:
    """Normalize an event immediately after extraction."""
    # Set source_id if not already set
    if event.source_id == 0:
        event.source_id = source.id or 0

    # Normalize title
    event.title = event.title.strip() if event.title else ""

    # Normalize cost
    if event.cost:
        event.cost = normalize_cost(event.cost)

    # Ensure required fields
    if not event.city:
        event.city = "Unknown"
    if not event.state:
        event.state = "Unknown"

    # Remove future dates beyond 60 days (will be filtered at query time)
    # Just validate that start_datetime is in the future
    if event.start_datetime < datetime.utcnow():
        logger.debug(f"Ignoring past event: {event.title} ({event.start_datetime})")
        return None  # type: ignore

    return event


def normalize_cost(cost_str: str) -> str:
    """Normalize cost strings."""
    cost = cost_str.strip().lower()

    # Normalize free variants
    if cost in ("free", "no cost", "complimentary"):
        return "Free"

    # Keep original if it looks like a price
    if "$" in cost or cost.startswith("price"):
        return cost_str.strip()

    # Default to TBD
    return "TBD"


REGION_RULES = {
    "Westerly": ["westerly"],
    "South County (RI)": ["south county", "narragansett", "kingston", "ri"],
    "Providence Metro": ["providence", "cranston", "warwick", "ri"],
    "Aquidneck Island (RI)": ["newport", "middletown", "portsmouth", "ri"],
    "Boston": ["boston", "cambridge", "somerville", "ma"],
    "Connecticut": ["connecticut", "ct"],
}


def apply_region_tags(event: Event) -> Event:
    """Apply region tag based on city and state."""
    if event.region_tag and event.region_tag != "Other":
        return event  # Already tagged

    city_lower = event.city.lower()
    state_lower = event.state.lower()
    search_text = f"{city_lower} {state_lower}"

    # Check each region's rules
    for region, keywords in REGION_RULES.items():
        if any(keyword in search_text for keyword in keywords):
            event.region_tag = region
            return event

    # Default to Other
    event.region_tag = "Other"
    return event
