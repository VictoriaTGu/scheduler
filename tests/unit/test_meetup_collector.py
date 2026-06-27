"""Unit tests for src/collectors/meetup.py"""

import json
import pytest
from unittest.mock import AsyncMock, patch

from src.collectors.meetup import _jsonld_to_event, _parse_offers, collect_meetup_events

SAMPLE_JSONLD_EVENT = {
    "@type": "Event",
    "name": "RI Tech Meetup",
    "startDate": "2027-07-15T18:30:00",
    "endDate": "2027-07-15T20:00:00",
    "url": "https://www.meetup.com/ri-tech/events/12345/",
    "description": "Monthly tech networking event.",
    "location": {
        "name": "AS220",
        "address": {
            "streetAddress": "115 Empire St",
            "addressLocality": "Providence",
            "addressRegion": "RI",
        },
    },
    "offers": {"price": "0", "priceCurrency": "USD"},
    "image": "https://example.com/photo.jpg",
}


def test_jsonld_to_event_maps_fields():
    event = _jsonld_to_event(SAMPLE_JSONLD_EVENT, source_id=1)
    assert event is not None
    assert event.title == "RI Tech Meetup"
    assert event.city == "Providence"
    assert event.state == "RI"
    assert event.venue_name == "AS220"
    assert event.cost == "Free"
    assert event.source_id == 1
    assert event.image_url == "https://example.com/photo.jpg"
    assert event.address == "115 Empire St"


def test_jsonld_to_event_returns_none_when_no_title():
    item = dict(SAMPLE_JSONLD_EVENT)
    item["name"] = ""
    assert _jsonld_to_event(item, source_id=1) is None


def test_jsonld_to_event_returns_none_when_no_start_date():
    item = {k: v for k, v in SAMPLE_JSONLD_EVENT.items() if k != "startDate"}
    assert _jsonld_to_event(item, source_id=1) is None


def test_jsonld_to_event_converts_utc_to_local_time():
    item = dict(SAMPLE_JSONLD_EVENT)
    item["startDate"] = "2099-06-27T22:00:00.000Z"
    item["endDate"] = "2099-06-27T23:30:00.000Z"

    event = _jsonld_to_event(item, source_id=1)
    assert event is not None
    assert event.start_datetime.hour == 18
    assert event.start_datetime.minute == 0
    assert event.end_datetime is not None
    assert event.end_datetime.hour == 19
    assert event.end_datetime.minute == 30


def test_parse_offers_free():
    assert _parse_offers({"price": "0", "priceCurrency": "USD"}) == "Free"


def test_parse_offers_paid():
    assert _parse_offers({"price": "15", "priceCurrency": "$"}) == "$15"


def test_parse_offers_paid_list():
    assert _parse_offers([{"price": "10", "priceCurrency": "USD"}]) == "USD10"


def test_parse_offers_none():
    assert _parse_offers(None) is None


def test_parse_offers_empty_price():
    assert _parse_offers({"priceCurrency": "USD"}) is None


@pytest.mark.asyncio
async def test_collect_uses_playwright():
    """collect_meetup_events must always call get_rendered_html with force_playwright=True."""
    fixture_html = """
    <html><body>
    <script type="application/ld+json">
    {"@type": "Event", "name": "Test", "startDate": "2027-08-01T18:00:00",
     "url": "https://meetup.com/test",
     "location": {"name": "Venue", "address": {"addressLocality": "Providence", "addressRegion": "RI"}}}
    </script>
    </body></html>
    """
    with patch("src.collectors.meetup.get_rendered_html", new_callable=AsyncMock) as mock_render:
        mock_render.return_value = fixture_html
        await collect_meetup_events("https://www.meetup.com/find/?location=providence--ri", 1)

    mock_render.assert_called_once_with(
        "https://www.meetup.com/find/?location=providence--ri",
        force_playwright=True,
    )


@pytest.mark.asyncio
async def test_collect_skips_non_event_jsonld():
    html = """
    <html><body>
    <script type="application/ld+json">{"@type": "Organization", "name": "Meetup"}</script>
    </body></html>
    """
    with patch("src.collectors.meetup.get_rendered_html", new_callable=AsyncMock) as mock_render:
        mock_render.return_value = html
        events = await collect_meetup_events("https://www.meetup.com/find/", 1)
    assert events == []


@pytest.mark.asyncio
async def test_collect_handles_malformed_json_ld():
    html = """
    <html><body>
    <script type="application/ld+json">NOT VALID JSON {{{</script>
    </body></html>
    """
    with patch("src.collectors.meetup.get_rendered_html", new_callable=AsyncMock) as mock_render:
        mock_render.return_value = html
        events = await collect_meetup_events("https://www.meetup.com/find/", 1)
    assert events == []


@pytest.mark.asyncio
async def test_collect_processes_jsonld_list():
    """JSON-LD can be a list of objects; all Event items should be collected."""
    html = """
    <html><body>
    <script type="application/ld+json">
    [
      {"@type": "Event", "name": "Event A", "startDate": "2027-09-01T18:00:00",
       "url": "https://meetup.com/a",
       "location": {"address": {"addressLocality": "Newport", "addressRegion": "RI"}}},
      {"@type": "Organization", "name": "Organizer"},
      {"@type": "Event", "name": "Event B", "startDate": "2027-09-02T19:00:00",
       "url": "https://meetup.com/b",
       "location": {"address": {"addressLocality": "Westerly", "addressRegion": "RI"}}}
    ]
    </script>
    </body></html>
    """
    with patch("src.collectors.meetup.get_rendered_html", new_callable=AsyncMock) as mock_render:
        mock_render.return_value = html
        events = await collect_meetup_events("https://www.meetup.com/find/", 1)
    assert len(events) == 2
    titles = {e.title for e in events}
    assert titles == {"Event A", "Event B"}
