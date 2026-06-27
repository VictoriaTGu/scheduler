"""Meetup discovery provider using Apify."""

import logging
from typing import Dict, Any, List
from datetime import datetime

from src.models import Event, Source
from src.collectors.discovery_provider import DiscoveryProvider
from src.services.apify_client import ApifyClient
from src.services.normalization import normalize_event, apply_region_tags

logger = logging.getLogger(__name__)


class MeetupProvider(DiscoveryProvider):
    """Discover events from Meetup using Apify actor."""

    def __init__(self, apify_client: ApifyClient):
        """Initialize Meetup provider.
        
        Args:
            apify_client: Configured Apify client
        """
        self.apify_client = apify_client

    @property
    def name(self) -> str:
        return "meetup"

    async def discover(
        self,
        source: Source,
        search_config: Dict[str, Any],
    ) -> List[Event]:
        """Discover events from Meetup.
        
        Args:
            source: Event source configuration
            search_config: Meetup-specific search config
            
        Returns:
            List of discovered events
        """
        events = []
        
        try:
            # Build actor input
            start_url = search_config.get("start_url")
            if not start_url:
                # Build Meetup URL from search term and location
                search_term = search_config.get("search_term", "")
                location = search_config.get("location", "")
                if search_term or location:
                    start_url = f"https://www.meetup.com/find/?keywords={search_term}&location={location}"
                else:
                    logger.warning(f"No Meetup URL or search criteria for {source.source_name}")
                    return []
            
            actor_input = {
                "startUrls": [start_url],
                "searchKeyword": search_config.get("search_term"),
                "maxResults": search_config.get("max_events", 50),
                "city": search_config.get("city", "Providence"),
                "state": search_config.get("state") or search_config.get("location", "Rhode Island"),
                "country": search_config.get("country", "US"),
            }

            actor_id = search_config.get("actor_id", "filip_cicvarek/meetup-scraper")
            actor_build = search_config.get("actor_build")
            
            logger.info(
                f"Starting Meetup actor for {source.source_name}",
                extra={"source": source.source_name}
            )
            
            items = await self.apify_client.run_actor(
                actor_id,
                actor_input,
                max_wait_seconds=300,
                build=actor_build,
            )
            
            logger.info(
                f"Retrieved {len(items)} events from Meetup for {source.source_name}",
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
                        f"Failed to convert Meetup event: {str(e)}",
                        extra={"source": source.source_name}
                    )
            
            return events
            
        except Exception as e:
            logger.error(
                f"Meetup discovery failed: {str(e)}",
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
        
        # Parse date (Meetup returns as ISO string)
        date_str = raw_item.get("date") or raw_item.get("eventDate")
        start_datetime = self._parse_datetime(date_str)
        if not start_datetime:
            return None
        
        # Extract location
        venue = raw_item.get("venue", {})
        if isinstance(venue, dict):
            venue_name = venue.get("name")
            city = venue.get("city", "Unknown")
            state = venue.get("state", "Unknown")
        else:
            venue_name = venue if isinstance(venue, str) else None
            city = raw_item.get("city", "Unknown")
            state = raw_item.get("state", "Unknown")
        
        # Cost extraction (Meetup may have price info)
        cost = raw_item.get("price")
        if cost:
            if cost == 0 or cost == "0":
                cost = "Free"
            elif isinstance(cost, (int, float)):
                cost = f"${cost}"
        
        # Create event
        event = Event(
            title=title,
            description=raw_item.get("description"),
            start_datetime=start_datetime,
            end_datetime=None,  # Meetup doesn't always provide end time
            venue_name=venue_name,
            city=city,
            state=state,
            event_url=raw_item.get("url") or raw_item.get("event_url"),
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
            return None
