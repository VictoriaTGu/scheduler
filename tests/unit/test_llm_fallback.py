"""Unit tests for src/services/llm_fallback.py"""

import pytest
from datetime import datetime
from unittest.mock import AsyncMock, patch, MagicMock

from src.services.llm_fallback import (
    _extract_snippet,
    extract_missing_time,
    extract_missing_fields,
    _parse_time_into_date,
)


def test_extract_snippet_strips_noise():
    html = """
    <html><head><script>alert(1)</script></head>
    <body><nav>Menu</nav>
    <p>Come join us for Summer Fest at 7:30 PM on the waterfront.</p>
    <footer>Footer</footer></body></html>
    """
    snippet = _extract_snippet(html, "Summer Fest")
    assert "alert" not in snippet
    assert "Menu" not in snippet
    assert "7:30 PM" in snippet


def test_extract_snippet_centers_on_title():
    # Prefix shorter than the look-back (100), so the window includes the title
    html = f"<html><body><p>{'A' * 50} My Event {'B' * 200}</p></body></html>"
    snippet = _extract_snippet(html, "My Event", window_chars=150)
    assert "My Event" in snippet


def test_extract_snippet_fallback_when_title_not_found():
    html = "<html><body><p>Some unrelated content here on this page.</p></body></html>"
    snippet = _extract_snippet(html, "Totally Missing Event", window_chars=50)
    assert isinstance(snippet, str)
    assert len(snippet) <= 50


@pytest.mark.asyncio
async def test_extract_missing_time_valid_response():
    with patch("src.services.llm_fallback._client") as mock_client:
        mock_client.chat.completions.create = AsyncMock(
            return_value=MagicMock(
                choices=[MagicMock(message=MagicMock(content="7:30 PM"))]
            )
        )
        result = await extract_missing_time("<html></html>", "Summer Fest")
    assert result == "7:30 PM"


@pytest.mark.asyncio
async def test_extract_missing_time_null_response():
    with patch("src.services.llm_fallback._client") as mock_client:
        mock_client.chat.completions.create = AsyncMock(
            return_value=MagicMock(
                choices=[MagicMock(message=MagicMock(content="null"))]
            )
        )
        result = await extract_missing_time("<html></html>", "Summer Fest")
    assert result is None


@pytest.mark.asyncio
async def test_extract_missing_time_null_case_insensitive():
    with patch("src.services.llm_fallback._client") as mock_client:
        mock_client.chat.completions.create = AsyncMock(
            return_value=MagicMock(
                choices=[MagicMock(message=MagicMock(content="NULL"))]
            )
        )
        result = await extract_missing_time("<html></html>", "Summer Fest")
    assert result is None


@pytest.mark.asyncio
async def test_extract_missing_fields_parses_format():
    raw_response = "time: 6:00 PM\ncost: Free\nvenue: Riverside Park"
    with patch("src.services.llm_fallback._client") as mock_client:
        mock_client.chat.completions.create = AsyncMock(
            return_value=MagicMock(
                choices=[MagicMock(message=MagicMock(content=raw_response))]
            )
        )
        result = await extract_missing_fields("<html></html>", "Art Walk")
    assert result["time"] == "6:00 PM"
    assert result["cost"] == "Free"
    assert result["venue_name"] == "Riverside Park"


@pytest.mark.asyncio
async def test_extract_missing_fields_handles_null_values():
    raw_response = "time: null\ncost: null\nvenue: null"
    with patch("src.services.llm_fallback._client") as mock_client:
        mock_client.chat.completions.create = AsyncMock(
            return_value=MagicMock(
                choices=[MagicMock(message=MagicMock(content=raw_response))]
            )
        )
        result = await extract_missing_fields("<html></html>", "Art Walk")
    assert result["time"] is None
    assert result["cost"] is None
    assert result["venue_name"] is None


def test_parse_time_into_date_preserves_date():
    existing = datetime(2026, 7, 12, 0, 0, 0)
    result = _parse_time_into_date(existing, "7:30 PM")
    assert result.date() == existing.date()
    assert result.hour == 19
    assert result.minute == 30
    assert result.second == 0


def test_parse_time_into_date_am_time():
    existing = datetime(2026, 7, 12, 0, 0, 0)
    result = _parse_time_into_date(existing, "10:00 AM")
    assert result.hour == 10
    assert result.minute == 0


def test_parse_time_into_date_noon():
    existing = datetime(2026, 7, 12, 0, 0, 0)
    result = _parse_time_into_date(existing, "12:00 PM")
    assert result.hour == 12
