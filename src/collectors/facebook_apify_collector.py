"""Facebook Events discovery provider using Apify."""

import logging
from typing import Dict, Any, List
from datetime import datetime

from src.models import Event, Source
from src.collectors.discovery_provider import DiscoveryProvider
from src.services.apify_client import ApifyClient
from src.services.normalization import normalize_event, apply_region_tags

logger = logging.getLogger(__name__)


class FacebookEventsProvider(DiscoveryProvider):
    """Discover events from Facebook using Apify actor."""

    def __init__(self, apify_client: ApifyClient):
        """Initialize Facebook provider.
        
        Args:
            apify_client: Configured Apify client
        """
        self.apify_client = apify_client

    @property
    def name(self) -> str:
        return "facebook_events"

    async def discover(
        self,
        source: Source,
        search_config: Dict[str, Any],
    ) -> List[Event]:
        """Discover events from Facebook.
        
        Args:
            source: Event source configuration
            search_config: Facebook-specific search config
            
        Returns:
            List of discovered events
        """
        events = []
        
        try:
            # Build actor input
            actor_input = {
                "startUrls": [search_config.get("start_url", "")],
                "maxEventsScraped": search_config.get("max_events", 100),
                "daysAhead": search_config.get("days_ahead", 30),
            }
            
            # Run the Apify actor
            logger.info(
                f"Starting Facebook Events actor for {source.source_name}",
                extra={"source": source.source_name}
            )
            
            items = await self.apify_client.run_actor(
                "apify/facebook-events-scraper",
                actor_input,
                max_wait_seconds=300,
            )
            
            logger.info(
                f"Retrieved {len(items)} events from Facebook for {source.source_name}",
                extra={"source": source.source_name}
            )
            
            # Convert raw items to Event models
            for item in items:
                try:
                    event = self._raw_to_event(item, source)
                    if event:
                        events.append(event)
                except Exception as e:
                    logger.warning(
                        f"Failed to convert Facebook event: {str(e)}",
                        extra={"source": source.source_name}
                    )
            
            return events
            
        except Exception as e:
            logger.error(
                f"Facebook discovery failed: {str(e)}",
                extra={"source": source.source_name}
            )
            return []

    def _raw_to_event(self, raw_item: Dict[str, Any], source: Source) -> Event | None:
        """Convert raw Apify response to Event model.
        
        Args:
            raw_item: Raw item from Apify
            source: Event source
            
        Returns:
            Event model or None if invalid
        """
        title = raw_item.get("title", "").strip()
        if not title:
            return None
        
        # Parse dates
        start_str = raw_item.get("startDate") or raw_item.get("start_time")
        start_datetime = self._parse_datetime(start_str)
        if not start_datetime:
            return None
        
        end_str = raw_item.get("endDate") or raw_item.get("end_time")
        end_datetime = self._parse_datetime(end_str) if end_str else None
        
        # Extract location
        venue_name = raw_item.get("venue_name")
        address = raw_item.get("address", "")
        city = raw_item.get("city", "Unknown")
        state = raw_item.get("state", "Unknown")
        
        # Create event
        event = Event(
            title=title,
            description=raw_item.get("description"),
            start_datetime=start_datetime,
            end_datetime=end_datetime,
            venue_name=venue_name,
            city=city,
            state=state,
            event_url=raw_item.get("url") or raw_item.get("event_url"),
            image_url=raw_item.get("image_url"),
            source_id=source.id or 0,
            region_tag="Other",
        )
        
        # Normalize and apply region tags
        event = normalize_event(event, source)
        if event:
            event = apply_region_tags(event)
        
        return event

    def _parse_datetime(self, date_str: str | None) -> datetime | None:
        """Parse ISO datetime string."""
        if not date_str:
            return None
        
        try:
            return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        except Exception:
            return None
