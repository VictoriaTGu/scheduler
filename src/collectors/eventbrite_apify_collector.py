"""Eventbrite discovery provider using Apify."""

import logging
from typing import Dict, Any, List
from datetime import datetime

from dateutil import parser as date_parser

from src.models import Event, Source
from src.collectors.discovery_provider import DiscoveryProvider
from src.services.apify_client import ApifyClient
from src.services.normalization import normalize_event, apply_region_tags

logger = logging.getLogger(__name__)


class EventbriteProvider(DiscoveryProvider):
    """Discover events from Eventbrite using Apify actor."""

    def __init__(self, apify_client: ApifyClient):
        """Initialize Eventbrite provider.
        
        Args:
            apify_client: Configured Apify client
        """
        self.apify_client = apify_client

    @property
    def name(self) -> str:
        return "eventbrite"

    async def discover(
        self,
        source: Source,
        search_config: Dict[str, Any],
    ) -> List[Event]:
        """Discover events from Eventbrite.
        
        Args:
            source: Event source configuration
            search_config: Eventbrite-specific search config
            
        Returns:
            List of discovered events
        """
        events = []
        
        try:
            start_url = search_config.get("start_url")
            if not start_url:
                logger.warning(f"No Eventbrite URL for {source.source_name}")
                return []
            
            # Build actor input
            category = search_config.get("category")
            if not category:
                logger.warning(f"No Eventbrite category for {source.source_name}")
                return []

            actor_input = {
                "startUrls": [start_url],
                "category": category,
                "maxEventsPerPage": search_config.get("max_events", 100),
                "city": search_config.get("city", "providence")
            }
            
            logger.info(
                f"Starting Eventbrite actor for {source.source_name}",
                extra={"source": source.source_name}
            )
            
            items = await self.apify_client.run_actor(
                "parseforge/eventbrite-scraper",
                actor_input,
                max_wait_seconds=300,
            )
            
            logger.info(
                f"Retrieved {len(items)} events from Eventbrite for {source.source_name}",
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
                        f"Failed to convert Eventbrite event: {str(e)}",
                        extra={"source": source.source_name}
                    )
            
            return events
            
        except Exception as e:
            logger.error(
                f"Eventbrite discovery failed: {str(e)}",
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
        start_str = (
            raw_item.get("startDateTime")
            or raw_item.get("start_datetime")
            or raw_item.get("startDate")
        )
        start_datetime = self._parse_datetime(start_str)
        if not start_datetime:
            return None
        
        end_str = (
            raw_item.get("endDateTime")
            or raw_item.get("end_datetime")
            or raw_item.get("endDate")
        )
        end_datetime = self._parse_datetime(end_str) if end_str else None
        
        # Extract location
        venue = raw_item.get("venue_name") or raw_item.get("venue")
        venue_name = None
        address = raw_item.get("address")
        city = raw_item.get("city", "Unknown")
        state = raw_item.get("state", "Unknown")

        if isinstance(venue, dict):
            venue_name = venue.get("name") or venue.get("title")
            venue_address = venue.get("address")
            if not address and isinstance(venue_address, str):
                address = venue_address

            venue_city = venue.get("city")
            if isinstance(venue_city, str) and venue_city:
                city = venue_city

            venue_state = venue.get("state") or venue.get("region")
            if isinstance(venue_state, str) and venue_state:
                state = venue_state
        elif isinstance(venue, str):
            venue_name = venue
        else:
            venue_name = raw_item.get("venue_name") if isinstance(raw_item.get("venue_name"), str) else None
        
        # Cost extraction
        price = raw_item.get("price")
        cost = None
        if price:
            if price == 0 or price == "0" or "free" in str(price).lower():
                cost = "Free"
            elif isinstance(price, (int, float)):
                cost = f"${price}"
            else:
                cost = str(price)
        
        # Create event
        event = Event(
            title=title,
            description=raw_item.get("description"),
            start_datetime=start_datetime,
            end_datetime=end_datetime,
            venue_name=venue_name,
            address=address,
            city=city,
            state=state,
            event_url=raw_item.get("url") or raw_item.get("event_url"),
            image_url=raw_item.get("image_url"),
            source_id=source.id or 0,
            cost=cost,
            region_tag="Other",
        )
        
        # Normalize and apply region tags
        event = normalize_event(event, source)
        if event:
            event = apply_region_tags(event)
        
        return event

    def _parse_datetime(self, date_str: str | None) -> datetime | None:
        """Parse datetime string."""
        if not date_str:
            return None
        
        try:
            return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        except Exception:
            try:
                parsed = date_parser.parse(date_str)
                if parsed.tzinfo is not None:
                    return parsed.astimezone().replace(tzinfo=None)
                return parsed
            except Exception:
                return None
