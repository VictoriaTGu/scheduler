"""Integration tests for the Playwright-backed collection pipeline.

These tests use saved HTML fixtures instead of live network calls.

HOW TO CREATE FIXTURES (run once, commit results):

    from src.collectors.renderer import get_rendered_html
    import asyncio, pathlib

    html = asyncio.run(get_rendered_html(
        "https://mbadrivein.com/events", force_playwright=True))
    pathlib.Path("tests/integration/fixtures/mbadrivein_event.html").write_text(html)

    html = asyncio.run(get_rendered_html(
        "https://www.meetup.com/find/?location=providence--ri&source=EVENTS",
        force_playwright=True))
    pathlib.Path("tests/integration/fixtures/meetup_event_page.html").write_text(html)
"""

import pathlib
import pytest
from unittest.mock import AsyncMock, patch

from src.collectors.meetup import collect_meetup_events

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


@pytest.mark.asyncio
@pytest.mark.skipif(
    not (FIXTURES / "meetup_event_page.html").exists(),
    reason="Meetup fixture not yet generated — run fixture capture script first",
)
async def test_meetup_fixture_produces_events():
    """A saved Meetup page should yield at least one correctly structured event."""
    fixture_html = (FIXTURES / "meetup_event_page.html").read_text()

    with patch("src.collectors.meetup.get_rendered_html", return_value=fixture_html):
        events = await collect_meetup_events(
            "https://www.meetup.com/find/?location=providence--ri", source_id=99
        )

    assert len(events) > 0
    for e in events:
        assert e.city is not None
        assert e.start_datetime.year >= 2026


@pytest.mark.asyncio
async def test_llm_fallback_called_for_midnight_events():
    """LLM fallback fires for events whose start_datetime resolved to midnight."""
    from src.services.llm_fallback import apply_llm_fallback_to_event
    from src.models.event import Event
    from datetime import datetime

    event = Event(
        title="Summer Fest",
        start_datetime=datetime(2027, 8, 1, 0, 0, 0),  # midnight default
        cost="Free",       # set so only time is missing
        venue_name="Park",  # set so only time is missing
        region_tag="Other",
        city="Providence",
        state="RI",
        event_url="https://example.com/event",
        source_id=1,
    )
    html = "<html><body><p>Summer Fest starts at 7:30 PM at the park.</p></body></html>"

    with patch("src.services.llm_fallback.extract_missing_time", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = "7:30 PM"
        updated = await apply_llm_fallback_to_event(event, html, llm_fallback_enabled=True)

    mock_llm.assert_called_once()
    assert updated.start_datetime.hour == 19
    assert updated.start_datetime.minute == 30


@pytest.mark.asyncio
async def test_llm_fallback_skipped_when_disabled():
    """LLM fallback is never called when llm_fallback_enabled=False."""
    from src.services.llm_fallback import apply_llm_fallback_to_event
    from src.models.event import Event
    from datetime import datetime

    event = Event(
        title="Summer Fest",
        start_datetime=datetime(2027, 8, 1, 0, 0, 0),
        region_tag="Other",
        city="Providence",
        state="RI",
        event_url="https://example.com/event",
        source_id=1,
    )
    html = "<html><body><p>Summer Fest starts at 7:30 PM</p></body></html>"

    with patch("src.services.llm_fallback.extract_missing_time", new_callable=AsyncMock) as mock_llm:
        await apply_llm_fallback_to_event(event, html, llm_fallback_enabled=False)

    mock_llm.assert_not_called()


@pytest.mark.asyncio
async def test_llm_multi_field_fallback_for_two_missing_fields():
    """When two+ fields are missing, the multi-field LLM call is used instead of single."""
    from src.services.llm_fallback import apply_llm_fallback_to_event
    from src.models.event import Event
    from datetime import datetime

    event = Event(
        title="Art Walk",
        start_datetime=datetime(2027, 9, 5, 0, 0, 0),  # midnight
        cost=None,      # missing
        venue_name=None,  # missing
        region_tag="Other",
        city="Providence",
        state="RI",
        event_url="https://example.com/art-walk",
        source_id=1,
    )
    html = "<html><body><p>Art Walk 6:00 PM, Free, at Riverside Park</p></body></html>"

    with patch("src.services.llm_fallback.extract_missing_fields", new_callable=AsyncMock) as mock_multi:
        mock_multi.return_value = {"time": "6:00 PM", "cost": "Free", "venue_name": "Riverside Park"}
        with patch("src.services.llm_fallback.extract_missing_time", new_callable=AsyncMock) as mock_single:
            updated = await apply_llm_fallback_to_event(event, html, llm_fallback_enabled=True)

    mock_multi.assert_called_once()
    mock_single.assert_not_called()
    assert updated.start_datetime.hour == 18
    assert updated.cost == "Free"
    assert updated.venue_name == "Riverside Park"
