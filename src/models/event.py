"""Event domain models."""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class Event(BaseModel):
    """Normalized event model."""

    id: Optional[int] = None
    title: str
    description: Optional[str] = None
    start_datetime: datetime
    end_datetime: Optional[datetime] = None
    venue_name: Optional[str] = None
    region_tag: str
    city: str
    state: str
    address: Optional[str] = None
    cost: Optional[str] = None
    event_url: str
    source_id: int
    image_url: Optional[str] = None
    recurrence_rule: Optional[str] = None  # RFC 5545 RRULE
    canonical_key: Optional[str] = None  # Hash fingerprint for deduplication
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class Source(BaseModel):
    """Event source configuration."""

    id: Optional[int] = None
    source_name: str
    source_url: str
    source_type: str  # "generic", "eventbrite", etc.
    enabled: bool = True
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class ScrapeRun(BaseModel):
    """Record of a scraping run."""

    id: Optional[int] = None
    source_id: int
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    status: str = "in_progress"  # in_progress, success, failed
    pages_crawled: int = 0
    events_found: int = 0
    events_new: int = 0
    events_updated: int = 0
    failures_count: int = 0
    error_summary: Optional[str] = None

    class Config:
        from_attributes = True
