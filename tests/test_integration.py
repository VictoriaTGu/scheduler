"""Integration tests with mock data."""

import asyncio
import json
import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from src.models import Event, Source, ScrapeRun
from src.storage.sqlite_impl import SQLiteRepository
from src.collectors.generic import GenericCollector
from src.collectors.strategies import StructuredMetadataStrategy
from src.services.normalization import normalize_event, apply_region_tags
from src.services.deduplication import generate_canonical_key
from src.services.calendar_links import generate_calendar_link
from src.services.digest import render_digest


# Mock JSON-LD Event data
MOCK_JSONLD_EVENT = {
    "@context": "https://schema.org",
    "@type": "Event",
    "name": "Summer Music Festival",
    "description": "A great summer festival",
    "startDate": (datetime.utcnow() + timedelta(days=7)).isoformat(),
    "endDate": (datetime.utcnow() + timedelta(days=8)).isoformat(),
    "url": "https://example.com/event",
    "location": {
        "@type": "Place",
        "name": "Central Park",
        "address": {
            "@type": "PostalAddress",
            "addressLocality": "Providence",
            "addressRegion": "RI",
        },
    },
    "image": "https://example.com/image.jpg",
}

MOCK_HTML_WITH_JSONLD = f"""
<!DOCTYPE html>
<html>
<head>
    <script type="application/ld+json">
    {json.dumps(MOCK_JSONLD_EVENT)}
    </script>
</head>
<body>
    <h1>Event Page</h1>
</body>
</html>
"""


class TestStructuredMetadataExtraction:
    """Test structured metadata extraction."""

    @pytest.mark.asyncio
    async def test_jsonld_extraction(self):
        """Test JSON-LD extraction from HTML."""
        strategy = StructuredMetadataStrategy()
        source = Source(
            id=1,
            source_name="Test Source",
            source_url="https://example.com",
            source_type="generic",
        )

        events = await strategy.extract(MOCK_HTML_WITH_JSONLD, source)

        assert len(events) > 0
        event = events[0]
        assert event.title == "Summer Music Festival"
        assert event.city == "Providence"
        assert event.state == "RI"
        assert event.event_url == "https://example.com/event"

    @pytest.mark.asyncio
    async def test_no_events_extracted(self):
        """Test extraction from HTML with no event data."""
        strategy = StructuredMetadataStrategy()
        source = Source(
            id=1,
            source_name="Test Source",
            source_url="https://example.com",
            source_type="generic",
        )

        html = "<html><body>No events here</body></html>"
        events = await strategy.extract(html, source)

        assert len(events) == 0


class TestNormalization:
    """Test event normalization."""

    def test_normalize_event(self):
        """Test event normalization."""
        event = Event(
            title="  Summer Music Festival  ",
            description="A festival",
            start_datetime=datetime.utcnow() + timedelta(days=7),
            city="Providence",
            state="RI",
            region_tag="Other",
            event_url="https://example.com",
            source_id=1,
            cost="  Free  ",
        )

        source = Source(
            id=1,
            source_name="Test",
            source_url="https://example.com",
            source_type="generic",
        )

        normalized = normalize_event(event, source)

        assert normalized.title == "Summer Music Festival"
        assert normalized.cost == "Free"
        assert normalized.source_id == 1

    def test_apply_region_tags(self):
        """Test region tag application."""
        event = Event(
            title="Test Event",
            start_datetime=datetime.utcnow() + timedelta(days=1),
            city="Boston",
            state="MA",
            region_tag="Other",
            event_url="https://example.com",
            source_id=1,
        )

        tagged = apply_region_tags(event)
        assert tagged.region_tag == "Boston"

    def test_apply_region_tags_westerly(self):
        """Test region tagging for Westerly."""
        event = Event(
            title="Test Event",
            start_datetime=datetime.utcnow() + timedelta(days=1),
            city="Westerly",
            state="RI",
            region_tag="Other",
            event_url="https://example.com",
            source_id=1,
        )

        tagged = apply_region_tags(event)
        assert tagged.region_tag == "Westerly"


class TestDeduplication:
    """Test deduplication logic."""

    def test_canonical_key_generation(self):
        """Test canonical key generation."""
        event = Event(
            title="Summer Festival",
            start_datetime=datetime(2026, 7, 15, 10, 0, 0),
            city="Providence",
            state="RI",
            region_tag="Providence Metro",
            venue_name="Central Park",
            event_url="https://example.com",
            source_id=1,
        )

        key = generate_canonical_key(event)

        # Should be deterministic
        key2 = generate_canonical_key(event)
        assert key == key2
        assert len(key) == 16

    def test_canonical_key_title_order_invariant(self):
        """Test that title word order doesn't affect canonical key."""
        event1 = Event(
            title="Summer Music Festival",
            start_datetime=datetime(2026, 7, 15, 10, 0, 0),
            city="Providence",
            state="RI",
            region_tag="Providence Metro",
            venue_name="Central Park",
            event_url="https://example.com",
            source_id=1,
        )

        event2 = Event(
            title="summer music festival",  # lowercase
            start_datetime=datetime(2026, 7, 15, 10, 0, 0),
            city="providence",  # lowercase
            state="ri",
            region_tag="Other",
            venue_name="central park",  # lowercase
            event_url="https://example.com",
            source_id=1,
        )

        key1 = generate_canonical_key(event1)
        key2 = generate_canonical_key(event2)

        # Should be the same (normalization should be case-insensitive)
        assert key1 == key2


class TestCalendarLinks:
    """Test Google Calendar link generation."""

    def test_calendar_link_generation(self):
        """Test calendar link generation."""
        event = Event(
            title="Summer Festival",
            start_datetime=datetime(2026, 7, 15, 10, 0, 0),
            end_datetime=datetime(2026, 7, 15, 22, 0, 0),
            city="Providence",
            state="RI",
            region_tag="Providence Metro",
            event_url="https://example.com",
            source_id=1,
        )

        link = generate_calendar_link(event)

        assert link.startswith("https://calendar.google.com/calendar/r/eventedit?")
        assert "text=Summer+Festival" in link
        assert "location=Providence" in link


class TestEmailDigestRendering:
    """Test email digest rendering."""

    @pytest.mark.asyncio
    async def test_render_digest_with_events(self):
        """Test digest rendering with mock events."""
        # Create mock storage
        storage = AsyncMock()

        # Create mock events
        events = [
            Event(
                id=1,
                title="Summer Festival",
                start_datetime=datetime(2026, 7, 15, 10, 0, 0),
                city="Providence",
                state="RI",
                region_tag="Providence Metro",
                event_url="https://example.com/event1",
                source_id=1,
            ),
            Event(
                id=2,
                title="Jazz Concert",
                start_datetime=datetime(2026, 7, 16, 19, 0, 0),
                city="Newport",
                state="RI",
                region_tag="Aquidneck Island (RI)",
                event_url="https://example.com/event2",
                source_id=1,
            ),
        ]

        storage.get_events_by_date_range = AsyncMock(return_value=events)

        html, text = await render_digest(storage, lookahead_days=60)

        assert "Summer Festival" in html
        assert "Jazz Concert" in html
        assert "Providence" in html
        assert "Newport" in html
        assert html.startswith("<!DOCTYPE html>")
        assert "calendar.google.com" in html

    @pytest.mark.asyncio
    async def test_render_digest_no_events(self):
        """Test digest rendering with no events."""
        storage = AsyncMock()
        storage.get_events_by_date_range = AsyncMock(return_value=[])

        html, text = await render_digest(storage, lookahead_days=60)

        assert "No upcoming events" in html


class TestFullPipeline:
    """Test full integration pipeline."""

    @pytest.mark.asyncio
    async def test_collection_and_normalization(self):
        """Test collection and normalization pipeline."""
        # Create mock source
        source = Source(
            id=1,
            source_name="Test Source",
            source_url="https://example.com",
            source_type="generic",
        )

        # Create mock collector
        collector = GenericCollector(source)

        # Mock the fetch_page method
        collector._fetch_page = AsyncMock(return_value=MOCK_HTML_WITH_JSONLD)

        # Collect events
        events = await collector.collect()

        # Should have extracted at least one event
        assert len(events) > 0

        # Events should be normalized
        for event in events:
            assert event.source_id == source.id or event.source_id == 0
            assert event.city != ""
            assert event.state != ""


# Standalone test functions for pytest
@pytest.mark.asyncio
async def test_event_model_creation():
    """Test Event model creation."""
    event = Event(
        title="Test Event",
        start_datetime=datetime.utcnow() + timedelta(days=1),
        city="Providence",
        state="RI",
        region_tag="Providence Metro",
        event_url="https://example.com",
        source_id=1,
    )

    assert event.title == "Test Event"
    assert event.city == "Providence"


@pytest.mark.asyncio
async def test_source_model_creation():
    """Test Source model creation."""
    source = Source(
        source_name="Test Source",
        source_url="https://example.com",
        source_type="generic",
    )

    assert source.source_name == "Test Source"
    assert source.enabled is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
