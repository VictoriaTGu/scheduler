"""Google Calendar link generation."""

from datetime import datetime
from urllib.parse import urlencode
import logging

from src.models import Event

logger = logging.getLogger(__name__)


def generate_calendar_link(event: Event) -> str:
    """Generate a Google Calendar add-event URL."""
    # Format dates for Google Calendar
    start_date = event.start_datetime.strftime("%Y%m%dT%H%M%S")
    dates = start_date

    if event.end_datetime:
        end_date = event.end_datetime.strftime("%Y%m%dT%H%M%S")
        dates = f"{start_date}/{end_date}"

    # Build description with source info
    description_parts = []
    if event.description:
        description_parts.append(event.description)
    description_parts.append(f"\nSource: {event.event_url}")

    # Build parameters
    params = {
        "text": event.title,
        "dates": dates,
        "ctz": "America/New_York",
        "location": f"{event.city}, {event.state}",
        "details": "\n".join(description_parts),
    }

    # Build URL
    base_url = "https://calendar.google.com/calendar/r/eventedit"
    return f"{base_url}?{urlencode(params)}"
