"""Generic event collector for sources."""

import logging
from typing import List
from datetime import datetime, timedelta

import httpx

from src.models import Event, Source
from src.collectors.base import EventCollector
from src.collectors.renderer import get_rendered_html
from src.collectors.strategies import (
    StructuredMetadataStrategy,
    GenericListingPageStrategy,
    LLMAssistedStrategy,
)

logger = logging.getLogger(__name__)


class GenericCollector(EventCollector):
    """Generic collector that tries multiple extraction strategies."""

    def __init__(self, source: Source, force_playwright: bool = False):
        """Initialize generic collector."""
        super().__init__(source)
        self.force_playwright = force_playwright
        # Setup strategies in priority order
        self.strategies = [
            StructuredMetadataStrategy(),
            GenericListingPageStrategy(),
            LLMAssistedStrategy(),
        ]

    async def collect(self) -> List[Event]:
        """Collect events from the source."""
        events = []

        try:
            # Discover event pages
            event_pages = await self._discover_event_pages()

            # Extract from each page
            for page_url in event_pages:
                try:
                    logger.info(
                        f"Crawling page: {page_url}",
                        extra={"source": self.source.source_name},
                    )
                    html = await self._fetch_page(page_url, force_playwright=self.force_playwright)
                    page_events = await self.extract_from_html(html)
                    events.extend(page_events)
                except Exception as e:
                    logger.warning(
                        f"Failed to extract from {page_url}: {str(e)}",
                        extra={"source": self.source.source_name, "url": page_url},
                    )

        except Exception as e:
            logger.error(
                f"Collector failed for source: {str(e)}",
                extra={"source": self.source.source_name},
            )

        return events

    async def _discover_event_pages(self) -> List[str]:
        """Discover event pages on the source website."""
        # Start with the main URL, try /events path
        base_url = self.source.source_url.rstrip('/')
        return [
            base_url,  # Homepage first
            f"{base_url}/events",  # Most common pattern
            f"{base_url}/calendar",  # Most common pattern
        ]

    async def _fetch_page(self, url: str, force_playwright: bool = False) -> str:
        """Fetch a page and return HTML, using Playwright for JS-heavy pages."""
        return await get_rendered_html(url, force_playwright=force_playwright)
