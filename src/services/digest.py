"""Email digest rendering service."""

from datetime import datetime, timedelta
from typing import List
from collections import defaultdict
import logging

from src.models import Event
from src.services.calendar_links import generate_calendar_link
from src.storage.repository import StorageRepository

logger = logging.getLogger(__name__)


async def render_digest(
    storage: StorageRepository, lookahead_days: int = 60
) -> tuple[str, str]:
    """
    Render HTML and plain text email digest.
    
    Returns: (html_content, text_content)
    """
    now = datetime.utcnow()
    end = now + timedelta(days=lookahead_days)

    # Fetch events
    events = await storage.get_events_by_date_range(now, end)

    if not events:
        html = "<p>No upcoming events in the next {lookahead_days} days.</p>"
        text = f"No upcoming events in the next {lookahead_days} days."
        return html, text

    # Group by date, then by city
    grouped = _group_events(events)

    # Render HTML
    html = _render_html_digest(grouped, len(events), lookahead_days)
    text = _render_text_digest(grouped, len(events), lookahead_days)

    return html, text


def _group_events(events: List[Event]) -> dict:
    """Group events by date, then by city."""
    grouped = defaultdict(lambda: defaultdict(list))

    for event in events:
        date_key = event.start_datetime.date().isoformat()
        city_key = event.city

        grouped[date_key][city_key].append(event)

    # Sort by date
    return dict(sorted(grouped.items()))


def _render_html_digest(
    grouped: dict, total_events: int, lookahead_days: int
) -> str:
    """Render HTML email digest."""
    today = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

    html_parts = [
        """<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body { font-family: Arial, sans-serif; color: #333; }
        .container { max-width: 600px; margin: 0 auto; }
        .header { background: #2c3e50; color: white; padding: 20px; text-align: center; }
        .header h1 { margin: 0; }
        .header p { margin: 5px 0 0 0; font-size: 12px; }
        .summary { background: #ecf0f1; padding: 10px 20px; margin: 10px 0; font-size: 12px; }
        .date-group { margin: 20px 0; }
        .date-header { font-weight: bold; font-size: 16px; color: #2c3e50; margin: 10px 0 5px 0; border-bottom: 2px solid #2c3e50; padding-bottom: 5px; }
        .city-header { font-weight: bold; font-size: 14px; color: #34495e; margin: 10px 0 5px 20px; }
        .event-item { margin: 10px 40px; padding: 10px; border-left: 3px solid #3498db; }
        .event-title { font-weight: bold; }
        .event-time { color: #666; font-size: 12px; }
        .event-details { font-size: 12px; margin-top: 5px; }
        .event-links { margin-top: 5px; }
        .event-links a { color: #3498db; text-decoration: none; margin-right: 15px; font-size: 12px; }
        .footer { text-align: center; font-size: 11px; color: #999; margin-top: 20px; padding-top: 10px; border-top: 1px solid #ecf0f1; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Weekly Event Digest</h1>
            <p>Generated on """ + today + """</p>
        </div>
        
        <div class="summary">
            <p><strong>Total Events:</strong> """ + str(total_events) + """</p>
            <p><strong>Time Range:</strong> Next """ + str(lookahead_days) + """ days</p>
        </div>
""",
    ]

    # Render date groups
    for date_str, cities in grouped.items():
        date_obj = datetime.fromisoformat(date_str)
        date_formatted = date_obj.strftime("%A, %B %d, %Y")

        html_parts.append(f'        <div class="date-group">\n')
        html_parts.append(f'            <div class="date-header">{date_formatted}</div>\n')

        # Render cities
        for city_name in sorted(cities.keys()):
            events = cities[city_name]

            html_parts.append(f'            <div class="city-header">{city_name}</div>\n')

            for event in events:
                time_str = event.start_datetime.strftime("%I:%M %p")
                cost_str = event.cost or "TBD"
                source_name = _get_source_name(event)

                html_parts.append(
                    f"""            <div class="event-item">
                <div class="event-title">{event.title}</div>
                <div class="event-time">{time_str} • {cost_str}</div>
                <div class="event-details">
                    <strong>Location:</strong> {event.venue_name or event.city}<br>
                    <strong>Source:</strong> {source_name}
                </div>
                <div class="event-links">
                    <a href="{event.event_url}" target="_blank">[Event Page]</a>
                    <a href="{generate_calendar_link(event)}" target="_blank">[Add to Calendar]</a>
                </div>
            </div>
"""
                )

        html_parts.append("        </div>\n")

    html_parts.append(
        """    </div>
    
    <div class="footer">
        <p>This is an automated digest. Do not reply to this email.</p>
    </div>
</body>
</html>
"""
    )

    return "".join(html_parts)


def _render_text_digest(grouped: dict, total_events: int, lookahead_days: int) -> str:
    """Render plain text email digest."""
    text_parts = [
        "WEEKLY EVENT DIGEST\n",
        f"Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}\n",
        f"Total Events: {total_events}\n",
        f"Time Range: Next {lookahead_days} days\n",
        "\n" + "=" * 60 + "\n\n",
    ]

    # Render date groups
    for date_str, cities in grouped.items():
        date_obj = datetime.fromisoformat(date_str)
        date_formatted = date_obj.strftime("%A, %B %d, %Y")

        text_parts.append(f"{date_formatted}\n")
        text_parts.append("-" * len(date_formatted) + "\n")

        # Render cities
        for city_name in sorted(cities.keys()):
            events = cities[city_name]

            text_parts.append(f"\n  {city_name}\n")

            for event in events:
                time_str = event.start_datetime.strftime("%I:%M %p")
                cost_str = event.cost or "TBD"
                source_name = _get_source_name(event)

                text_parts.append(
                    f"""  • {event.title}
    Time: {time_str}
    Cost: {cost_str}
    Location: {event.venue_name or event.city}
    Source: {source_name}
    Event: {event.event_url}
    Calendar: {generate_calendar_link(event)}

"""
                )

        text_parts.append("\n")

    text_parts.append("=" * 60 + "\nThis is an automated digest.\n")

    return "".join(text_parts)


def _get_source_name(event: Event) -> str:
    """Extract clean source name from event."""
    # In production, this would look up the source by ID
    # For now, derive from URL
    from urllib.parse import urlparse

    parsed = urlparse(event.event_url)
    return parsed.netloc.replace("www.", "") if parsed.netloc else "Unknown"
