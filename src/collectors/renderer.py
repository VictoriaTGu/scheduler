"""Playwright rendering layer for JavaScript-heavy pages."""

import re
import logging

import httpx
from playwright.async_api import async_playwright

logger = logging.getLogger(__name__)

_SHELL_INDICATORS = [
    r'<div id="root">\s*</div>',   # React empty root
    r'<div id="app">\s*</div>',    # Vue/Angular empty root
    r'window\.__NEXT_DATA__',      # Next.js SSR marker (needs hydration)
]


def _looks_like_shell(html: str) -> bool:
    """Return True if the HTML appears to be an unrendered JS app shell."""
    # If there's meaningful text content it's probably not a shell
    if len(re.findall(r'<p[^>]*>.{40,}</p>', html)) > 2:
        return False
    return any(re.search(pat, html) for pat in _SHELL_INDICATORS)


async def get_rendered_html(url: str, force_playwright: bool = False) -> str:
    """
    Fetch a URL and return fully rendered HTML.

    Strategy:
      1. Try a plain HTTPX request (fast, no browser overhead).
      2. If the result looks like an unrendered shell OR force_playwright=True,
         fall back to Playwright headless Chromium.

    Pass force_playwright=True for known JS-heavy domains
    (e.g. mbadrivein.com, meetup.com).
    """
    if not force_playwright:
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(url, follow_redirects=True)
                html = resp.text
                if not _looks_like_shell(html):
                    return html
                logger.debug("Shell detected for %s, falling back to Playwright", url)
        except Exception as exc:
            logger.debug("Plain request failed for %s (%s), trying Playwright", url, exc)

    logger.info("Using Playwright to render %s", url)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.goto(url, wait_until="networkidle", timeout=30_000)
            html = await page.content()
        finally:
            await browser.close()
    return html
