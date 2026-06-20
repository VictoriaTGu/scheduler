"""Abstract base class for discovery providers."""

from abc import ABC, abstractmethod
from typing import Dict, Any, List
from src.models import Event, Source


class DiscoveryProvider(ABC):
    """Abstract base class for event discovery providers."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider name (e.g., 'facebook_events', 'meetup', 'eventbrite')."""
        pass

    @abstractmethod
    async def discover(
        self,
        source: Source,
        search_config: Dict[str, Any],
    ) -> List[Event]:
        """Discover events from a source using the given configuration.
        
        Args:
            source: The event source configuration
            search_config: Search-specific configuration
            
        Returns:
            List of discovered events
        """
        pass
