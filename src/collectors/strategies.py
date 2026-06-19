"""Extraction strategies for event data."""

import json
import re
from typing import List, Optional
from datetime import datetime, timedelta
import logging

from bs4 import BeautifulSoup
import httpx
from dateutil import parser as dateutil_parser

from src.models import Event, Source
from src.collectors.base import ExtractionStrategy
from src.services.normalization import normalize_event

logger = logging.getLogger(__name__)


class StructuredMetadataStrategy(ExtractionStrategy):
    """Extract events from structured metadata (schema.org, JSON-LD, microdata)."""

    @property
    def name(self) -> str:
        return "StructuredMetadata"

    async def extract(self, html: str, source: Source) -> List[Event]:
        """Extract events from JSON-LD and schema.org markup."""
        events = []
        soup = BeautifulSoup(html, "html.parser")

        # Try JSON-LD first
        json_ld_scripts = soup.find_all("script", type="application/ld+json")
        for script in json_ld_scripts:
            try:
                data = json.loads(script.string)
                extracted = self._extract_from_json_ld(data, source)
                events.extend(extracted)
            except Exception as e:
                logger.debug(f"Failed to parse JSON-LD: {str(e)}")

        return events

    def _extract_from_json_ld(self, data: dict, source: Source) -> List[Event]:
        """Extract events from JSON-LD data."""
        events = []

        # Handle both single object and array
        items = data if isinstance(data, list) else [data]

        for item in items:
            if item.get("@type") == "Event" or "Event" in item.get("@type", ""):
                event = self._json_ld_to_event(item, source)
                if event:
                    events.append(event)

        return events

    def _json_ld_to_event(self, json_ld: dict, source: Source) -> Optional[Event]:
        """Convert JSON-LD Event to Event model."""
        try:
            title = json_ld.get("name", "")
            if not title:
                return None

            # Parse dates
            start_datetime = self._parse_datetime(json_ld.get("startDate"))
            if not start_datetime:
                return None

            end_datetime = self._parse_datetime(json_ld.get("endDate"))

            # Extract location info
            location = json_ld.get("location", {})
            if isinstance(location, dict):
                venue_name = location.get("name")
                address = location.get("address", {})
                if isinstance(address, dict):
                    city = address.get("addressLocality", "")
                    state = address.get("addressRegion", "")
                else:
                    city = ""
                    state = ""
            else:
                venue_name = None
                city = ""
                state = ""

            event = Event(
                title=title,
                description=json_ld.get("description"),
                start_datetime=start_datetime,
                end_datetime=end_datetime,
                venue_name=venue_name,
                city=city or "Unknown",
                state=state or "Unknown",
                event_url=json_ld.get("url", ""),
                source_id=source.id or 0,
                image_url=json_ld.get("image"),
                region_tag="Other",  # Will be set by region tagging service
            )

            return normalize_event(event, source)
        except Exception as e:
            logger.debug(f"Failed to convert JSON-LD to event: {str(e)}")
            return None

    def _parse_datetime(self, date_str: Optional[str]) -> Optional[datetime]:
        """Parse ISO datetime string."""
        if not date_str:
            return None

        try:
            return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        except Exception:
            return None


class GenericListingPageStrategy(ExtractionStrategy):
    """Extract events from generic event listing pages using DOM selectors."""

    @property
    def name(self) -> str:
        return "GenericListingPage"

    async def extract(self, html: str, source: Source) -> List[Event]:
        """Extract events from generic listing pages."""
        events = []
        soup = BeautifulSoup(html, "html.parser")
        
        # Look for common event container patterns
        event_selectors = [
            'div.event', 'div[class*="event-item"]', 'div[class*="event-card"]',
            'article.event', 'article[class*="event"]',
            'li.event', 'li[class*="event-item"]',
            'div[class*="listing-item"]', 'div[class*="post-item"]',
        ]
        
        for selector in event_selectors:
            containers = soup.select(selector)
            if containers:
                logger.debug(
                    f"Found {len(containers)} event containers with selector: {selector}",
                    extra={"source": source.source_name}
                )
                for container in containers:
                    try:
                        event = self._extract_event_from_container(container, source)
                        if event:
                            events.append(event)
                    except Exception as e:
                        logger.debug(
                            f"Failed to extract event from container: {str(e)}",
                            extra={"source": source.source_name}
                        )
                
                if events:
                    logger.info(
                        f"Successfully extracted {len(events)} events using selector strategy",
                        extra={"source": source.source_name}
                    )
                    return events
        
        # Fallback: look for any links with event-like text
        if not events:
            logger.debug(
                "No event containers found, trying fallback link extraction",
                extra={"source": source.source_name}
            )
            events = self._extract_from_links(soup, source)
            if events:
                logger.info(
                    f"Successfully extracted {len(events)} events using fallback link strategy",
                    extra={"source": source.source_name}
                )
        
        return events

    def _extract_event_from_container(self, container, source: Source) -> Optional[Event]:
        """Extract a single event from a container element."""
        # Try to find title
        title = None
        for tag in ['h2', 'h3', 'h4', 'a', 'span']:
            title_elem = container.find(tag, class_=lambda x: x and 'title' in x.lower())
            if title_elem:
                title = title_elem.get_text(strip=True)
                break
        
        if not title:
            title = container.find(['h2', 'h3', 'h4'])
            if title:
                title = title.get_text(strip=True)
        
        if not title or len(title) < 3:
            return None
        
        # Try to find date and time
        start_datetime = self._extract_date_from_container(container)
        if not start_datetime:
            return None
        
        # Try to extract end time if available
        end_datetime = self._extract_end_time_from_container(container, start_datetime)
        
        # Try to find URL
        event_url = ""
        link = container.find('a', href=True)
        if link:
            event_url = link['href']
            if not event_url.startswith('http'):
                # Make relative URLs absolute
                source_domain = self._get_domain(source.source_url)
                event_url = f"{source_domain}{event_url}"
        
        # Try to find description
        description = ""
        desc_elem = container.find(['p', 'div'], class_=lambda x: x and 'desc' in x.lower())
        if desc_elem:
            description = desc_elem.get_text(strip=True)[:200]
        
        # Create event with minimal info
        try:
            event = Event(
                title=title,
                description=description or None,
                start_datetime=start_datetime,
                end_datetime=end_datetime,
                venue_name=None,
                city="Unknown",
                state="Unknown",
                event_url=event_url,
                source_id=source.id or 0,
                region_tag="Other",
            )
            return normalize_event(event, source)
        except Exception as e:
            logger.debug(f"Failed to create event: {str(e)}")
            return None

    def _extract_date_from_container(self, container) -> Optional[datetime]:
        """Extract date from container using various patterns."""
        # Try date attributes
        date_attrs = ['data-date', 'data-start', 'datetime', 'data-datetime']
        for attr in date_attrs:
            elem = container.find(attrs={attr: True})
            if elem:
                date_str = elem.get(attr)
                if date_str:
                    dt = self._parse_datetime(date_str)
                    if dt:
                        return dt
        
        # Try to find date in text
        text = container.get_text()
        
        # Enhanced patterns that capture date AND time information
        # These patterns are prioritized from most specific to least specific
        datetime_patterns = [
            # Full date+time with explicit separators: "June 19, 2026 @ 9:00 pm"
            r'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]* \d{1,2},? \d{4}\s+[at@]\s+\d{1,2}:\d{2}\s*(?:am|pm|AM|PM)',
            # Date+time with @ separator: "June 19 @ 9:00 pm"
            r'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]* \d{1,2}\s+[at@]\s+\d{1,2}:\d{2}\s*(?:am|pm|AM|PM)',
            # Numeric date+time: "6/19/2026 7:30 pm" or "2026-06-19 7:30 pm"
            r'\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\s+\d{1,2}:\d{2}\s*(?:am|pm|AM|PM)?',
            r'\d{4}-\d{2}-\d{2}\s+\d{1,2}:\d{2}\s*(?:am|pm|AM|PM)?',
            # Month name with day and optional year (less specific - may match wrong times)
            r'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]* \d{1,2}(?:,? \d{4})?',
            # Numeric dates without time
            r'\d{1,2}[/-]\d{1,2}[/-]\d{2,4}',
            r'\d{4}-\d{2}-\d{2}',
        ]
        
        # For the last few patterns (month name without time), be more careful
        # Only use them if we can find them clearly separated from other content
        for pattern_idx, pattern in enumerate(datetime_patterns):
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                date_str = match.group(0)
                
                # For patterns without explicit time (indices >= 4), 
                # check if there's obvious junk before/after that suggests a false positive
                if pattern_idx >= 4:
                    # Check context around the match
                    start = max(0, match.start() - 20)
                    end = min(len(text), match.end() + 20)
                    context = text[start:end]
                    
                    # Avoid matches where the date is mixed with random numbers/symbols
                    # that might indicate it's not actually an event date
                    if re.search(r'\d{1,2}\s*:\s*\d{2}(?!\s*(?:am|pm|AM|PM))', context[max(0, 20-start):]):
                        # There's a time-like pattern that's not am/pm, this might be a false positive
                        continue
                
                dt = self._parse_datetime(date_str)
                if dt:
                    # If no year specified, assume current or next year
                    if dt.year == 1900:
                        now = datetime.utcnow()
                        dt = dt.replace(year=now.year if dt.month >= now.month else now.year + 1)
                    return dt
        
        return None

    def _extract_end_time_from_container(self, container, start_datetime: datetime) -> Optional[datetime]:
        """Extract end time from container if available (for time ranges)."""
        if not start_datetime:
            return None
        
        text = container.get_text()
        
        # Look for time range patterns: "9:00 pm - 11:00 pm" or "7pm - 9pm"
        time_range_pattern = r'-\s*(\d{1,2}):?(\d{2})?\s*(am|pm|AM|PM)'
        match = re.search(time_range_pattern, text)
        
        if match:
            hour_str = match.group(1)
            minute_str = match.group(2) or "00"
            am_pm = match.group(3).lower()
            
            try:
                hour = int(hour_str)
                minute = int(minute_str)
                
                # Convert to 24-hour format
                if am_pm == 'pm' and hour != 12:
                    hour += 12
                elif am_pm == 'am' and hour == 12:
                    hour = 0
                
                # Create end datetime with the same date as start, but different time
                end_datetime = start_datetime.replace(hour=hour, minute=minute, second=0)
                
                # If end time is earlier than start time, assume it's the next day
                if end_datetime <= start_datetime:
                    end_datetime = end_datetime.replace(day=end_datetime.day + 1)
                
                return end_datetime
            except Exception as e:
                logger.debug(f"Failed to parse end time: {str(e)}")
                return None
        
        return None

    def _extract_from_links(self, soup: BeautifulSoup, source: Source) -> List[Event]:
        """Fallback: extract events from links with event-like text."""
        events = []
        keywords = ['event', 'festival', 'concert', 'show', 'performance', 'program']
        
        for link in soup.find_all('a', href=True):
            text = link.get_text(strip=True).lower()
            if any(kw in text for kw in keywords) and len(text) > 5:
                try:
                    # Only extract if we can find some date information in the page
                    # Don't use hardcoded default dates
                    parent = link.find_parent(['div', 'li', 'article', 'section'])
                    if parent:
                        date = self._extract_date_from_container(parent)
                        if date:  # Only extract if we found a date
                            event = Event(
                                title=link.get_text(strip=True)[:100],
                                start_datetime=date,
                                venue_name=None,
                                city="Unknown",
                                state="Unknown",
                                event_url=link['href'],
                                source_id=source.id or 0,
                                region_tag="Other",
                            )
                            events.append(normalize_event(event, source))
                            if len(events) >= 5:  # Limit to avoid noise
                                break
                except Exception as e:
                    logger.debug(f"Failed to extract from link: {str(e)}")
        
        return events

    def _parse_datetime(self, date_str: str) -> Optional[datetime]:
        """Try to parse various date formats."""
        if not date_str:
            return None
        
        # Clean up the string - remove common separators that might interfere
        date_str = date_str.strip()
        
        try:
            dt = dateutil_parser.parse(date_str, fuzzy=False)
            return dt
        except Exception:
            try:
                # Try with fuzzy parsing as fallback (allows extra text)
                dt = dateutil_parser.parse(date_str, fuzzy=True)
                # Only accept if the parsed datetime seems reasonable
                # (not in year 1900 or 2000 unless explicitly specified)
                if dt.year >= 2020:
                    return dt
            except Exception:
                try:
                    return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                except Exception:
                    return None

    def _get_domain(self, url: str) -> str:
        """Extract domain from URL."""
        match = re.match(r'https?://[^/]+', url)
        return match.group(0) if match else url



class LLMAssistedStrategy(ExtractionStrategy):
    """Extract events using LLM assistance (fallback only)."""

    @property
    def name(self) -> str:
        return "LLMAssisted"

    async def extract(self, html: str, source: Source) -> List[Event]:
        """Extract events using LLM (placeholder)."""
        # This would require LLM integration (OpenAI, Claude, etc.)
        # For MVP, this is deferred
        logger.debug(f"LLMAssisted strategy: Not implemented yet for {source.source_name}")
        return []
