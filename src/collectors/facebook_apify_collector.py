"""Facebook Events discovery provider using Apify."""
import json
import logging
from typing import Dict, Any, List
from datetime import datetime, timezone
import re
from zoneinfo import ZoneInfo

from dateutil import parser as dateutil_parser

from src.models import Event, Source
from src.collectors.discovery_provider import DiscoveryProvider
from src.services.apify_client import ApifyClient
from src.services.normalization import normalize_event, apply_region_tags

logger = logging.getLogger(__name__)

EVENT_LOCAL_TZ = ZoneInfo("America/New_York")


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
                    logger.info(f"Raw event: {json.dumps(item)}")
                    event = self._raw_to_event(item, source)
                    logger.info(
                        "Raw event conversion result",
                        extra={
                            "source": source.source_name,
                            "event_id": item.get("id"),
                            "converted": event is not None,
                            "event_title": event.title if event else None,
                            "start_datetime": str(event.start_datetime) if event else None,
                        },
                    )
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
        title = (raw_item.get("title") or raw_item.get("name") or "").strip()
        if not title:
            logger.info(
                "Dropping Facebook event: missing title",
                extra={"source": source.source_name, "event_id": raw_item.get("id")},
            )
            return None
        
        # Parse dates
        start_str = (
            raw_item.get("startDate")
            or raw_item.get("start_time")
            or raw_item.get("startTime")
            or raw_item.get("utcStartDate")
            or raw_item.get("dateTimeSentence")
        )
        start_datetime = self._parse_datetime(start_str)
        if not start_datetime:
            logger.info(
                "Dropping Facebook event: missing/invalid start datetime",
                extra={
                    "source": source.source_name,
                    "event_id": raw_item.get("id"),
                    "start_value": start_str,
                },
            )
            return None
        
        end_str = (
            raw_item.get("endDate")
            or raw_item.get("end_time")
            or raw_item.get("endTime")
            or raw_item.get("utcEndDate")
        )
        end_datetime = self._parse_datetime(end_str) if end_str else None
        
        # Extract location
        location = raw_item.get("location") if isinstance(raw_item.get("location"), dict) else {}
        venue_name = raw_item.get("venue_name") or location.get("name")
        address = raw_item.get("address") or location.get("streetAddress") or ""

        city = raw_item.get("city") or location.get("city") or ""
        state = raw_item.get("state") or location.get("state") or ""

        # Some payloads provide city like "Pawtucket, RI, United States".
        if city and (not state or state == "Unknown") and "," in city:
            parts = [part.strip() for part in city.split(",")]
            if parts:
                city = parts[0]
            if len(parts) > 1 and re.fullmatch(r"[A-Z]{2}", parts[1]):
                state = parts[1]

        # Best-effort state extraction from address strings (", RI,").
        if (not state or state == "Unknown") and address:
            match = re.search(r",\s*([A-Z]{2})\b", address)
            if match:
                state = match.group(1)

        if not city:
            city = "Unknown"
        if not state:
            state = "Unknown"

        event_url = raw_item.get("url") or raw_item.get("event_url")
        if not event_url:
            logger.info(
                "Dropping Facebook event: missing URL",
                extra={"source": source.source_name, "event_id": raw_item.get("id")},
            )
            return None
        
        # Create event
        event = Event(
            title=title,
            description=raw_item.get("description"),
            start_datetime=start_datetime,
            end_datetime=end_datetime,
            venue_name=venue_name,
            city=city,
            state=state,
            address=address,
            event_url=event_url,
            image_url=raw_item.get("image_url") or raw_item.get("imageUrl"),
            source_id=source.id or 0,
            region_tag="Other",
        )
        
        # Normalize and apply region tags
        event = normalize_event(event, source)
        if event:
            event = apply_region_tags(event)
        
        return event

    def _parse_datetime(self, date_str: str | None) -> datetime | None:
        """Parse datetime string into naive local datetime (America/New_York)."""
        if not date_str:
            return None
        
        try:
            parsed = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                return parsed.astimezone(EVENT_LOCAL_TZ).replace(tzinfo=None)
            return parsed
        except Exception:
            pass

        try:
            parsed = dateutil_parser.parse(date_str)
            if parsed.tzinfo is not None:
                return parsed.astimezone(EVENT_LOCAL_TZ).replace(tzinfo=None)
            return parsed
        except Exception:
            return None
