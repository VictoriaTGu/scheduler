"""Unit tests for src/collectors/renderer.py"""

import pytest
import httpx
from unittest.mock import AsyncMock, patch, MagicMock

from src.collectors.renderer import _looks_like_shell, get_rendered_html


def test_shell_detection_bare_react():
    html = '<html><body><div id="root"></div></body></html>'
    assert _looks_like_shell(html) is True


def test_shell_detection_bare_app_div():
    html = '<html><body><div id="app"></div></body></html>'
    assert _looks_like_shell(html) is True


def test_shell_detection_rich_content():
    html = '<html><body>' + ''.join(f'<p>{"x" * 50}</p>' for _ in range(5)) + '</body></html>'
    assert _looks_like_shell(html) is False


@pytest.mark.asyncio
async def test_plain_request_used_when_html_is_rich():
    rich_html = '<html><body>' + ''.join(f'<p>{"x" * 50}</p>' for _ in range(5)) + '</body></html>'
    with patch("src.collectors.renderer.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=MagicMock(text=rich_html))
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)
        result = await get_rendered_html("https://example.com")

    assert result == rich_html


@pytest.mark.asyncio
async def test_playwright_used_when_force_playwright():
    playwright_html = '<html><body><p>Event at 7:30 PM rendered by Playwright</p></body></html>'

    mock_page = AsyncMock()
    mock_page.content = AsyncMock(return_value=playwright_html)

    mock_browser = AsyncMock()
    mock_browser.new_page = AsyncMock(return_value=mock_page)
    mock_browser.close = AsyncMock()

    mock_chromium = MagicMock()
    mock_chromium.launch = AsyncMock(return_value=mock_browser)

    mock_pw = MagicMock()
    mock_pw.chromium = mock_chromium

    mock_pw_ctx = AsyncMock()
    mock_pw_ctx.__aenter__ = AsyncMock(return_value=mock_pw)
    mock_pw_ctx.__aexit__ = AsyncMock(return_value=None)

    with patch("src.collectors.renderer.async_playwright", return_value=mock_pw_ctx):
        result = await get_rendered_html("https://example.com", force_playwright=True)

    assert result == playwright_html


@pytest.mark.asyncio
async def test_network_failure_falls_through_to_playwright():
    """A failed plain request should fall through to Playwright without raising."""
    playwright_html = '<html><body><p>Rendered content</p></body></html>'

    mock_page = AsyncMock()
    mock_page.content = AsyncMock(return_value=playwright_html)

    mock_browser = AsyncMock()
    mock_browser.new_page = AsyncMock(return_value=mock_page)
    mock_browser.close = AsyncMock()

    mock_chromium = MagicMock()
    mock_chromium.launch = AsyncMock(return_value=mock_browser)

    mock_pw = MagicMock()
    mock_pw.chromium = mock_chromium

    mock_pw_ctx = AsyncMock()
    mock_pw_ctx.__aenter__ = AsyncMock(return_value=mock_pw)
    mock_pw_ctx.__aexit__ = AsyncMock(return_value=None)

    with patch("src.collectors.renderer.httpx.AsyncClient") as mock_client_cls:
        mock_client_cls.return_value.__aenter__ = AsyncMock(side_effect=httpx.ConnectError("timeout"))
        with patch("src.collectors.renderer.async_playwright", return_value=mock_pw_ctx):
            result = await get_rendered_html("https://example.com")

    assert result == playwright_html
