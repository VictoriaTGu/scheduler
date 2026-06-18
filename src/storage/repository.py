"""Storage repository interface for database operations."""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional, List

from src.models import Event, Source, ScrapeRun


class StorageRepository(ABC):
    """Abstract base class for database storage operations."""

    @abstractmethod
    async def get_source(self, source_id: int) -> Optional[Source]:
        """Get a source by ID."""
        pass

    @abstractmethod
    async def list_sources(self, enabled_only: bool = True) -> List[Source]:
        """List all sources, optionally filtered to enabled only."""
        pass

    @abstractmethod
    async def upsert_source(self, source: Source) -> int:
        """Insert or update a source. Returns source ID."""
        pass

    @abstractmethod
    async def insert_event(self, event: Event) -> int:
        """Insert a new event. Returns event ID."""
        pass

    @abstractmethod
    async def upsert_event(self, event: Event) -> int:
        """Insert or update an event. Returns event ID."""
        pass

    @abstractmethod
    async def get_event_by_canonical_key(self, canonical_key: str) -> Optional[Event]:
        """Get an event by its canonical key."""
        pass

    @abstractmethod
    async def get_events_by_date_range(
        self, start: datetime, end: datetime
    ) -> List[Event]:
        """Get events within a date range, ordered by date then city."""
        pass

    @abstractmethod
    async def create_scrape_run(self, run: ScrapeRun) -> int:
        """Create a new scrape run. Returns run ID."""
        pass

    @abstractmethod
    async def update_scrape_run(self, run: ScrapeRun) -> None:
        """Update an existing scrape run."""
        pass

    @abstractmethod
    async def get_recent_scrape_runs(
        self, source_id: int, limit: int = 10
    ) -> List[ScrapeRun]:
        """Get recent scrape runs for a source."""
        pass
