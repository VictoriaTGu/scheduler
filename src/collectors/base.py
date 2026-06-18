"""Base collector abstract class and extraction strategy interface."""

from abc import ABC, abstractmethod
from typing import List
import logging

from src.models import Event, Source


logger = logging.getLogger(__name__)


class ExtractionStrategy(ABC):
    """Base class for event extraction strategies."""

    @abstractmethod
    async def extract(self, html: str, source: Source) -> List[Event]:
        """Extract events from HTML content."""
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        """Name of the extraction strategy."""
        pass


class EventCollector(ABC):
    """Abstract base class for event collectors."""

    def __init__(self, source: Source):
        """Initialize collector with source configuration."""
        self.source = source
        self.strategies: List[ExtractionStrategy] = []

    @abstractmethod
    async def collect(self) -> List[Event]:
        """Collect events from the source."""
        pass

    async def extract_from_html(self, html: str) -> List[Event]:
        """Try extraction strategies in order."""
        events = []

        for strategy in self.strategies:
            try:
                extracted = await strategy.extract(html, self.source)
                if extracted:
                    logger.info(
                        f"Extracted {len(extracted)} events using {strategy.name}",
                        extra={"source": self.source.source_name, "strategy": strategy.name},
                    )
                    events.extend(extracted)
                    break  # Use first successful strategy
            except Exception as e:
                logger.warning(
                    f"Extraction strategy {strategy.name} failed: {str(e)}",
                    extra={"source": self.source.source_name, "strategy": strategy.name},
                )
                continue

        return events
