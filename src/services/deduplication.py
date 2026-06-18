"""Deduplication service."""

import hashlib
from typing import Optional
import logging

from src.models import Event

logger = logging.getLogger(__name__)


def generate_canonical_key(event: Event) -> str:
    """Generate a stable canonical key for deduplication."""
    # Normalize components
    title_norm = event.title.lower().strip() if event.title else ""
    date_norm = event.start_datetime.date().isoformat()
    city_norm = event.city.lower().strip() if event.city else ""
    venue_norm = (event.venue_name or "").lower().strip()

    # Create fingerprint
    key_parts = [title_norm, date_norm, city_norm, venue_norm]
    key_str = "||".join(key_parts)

    return hashlib.sha256(key_str.encode()).hexdigest()[:16]


def get_merge_strategy(existing: Optional[Event], new: Event) -> Event:
    """Determine which event record to keep when duplicates found."""
    if not existing:
        return new

    # Score richness: count non-None fields
    existing_score = sum(1 for v in existing.__dict__.values() if v is not None)
    new_score = sum(1 for v in new.__dict__.values() if v is not None)

    # Keep the richer record, preferring existing on tie
    if new_score > existing_score:
        # Update existing with new data, preserving ID
        merged = new.copy()
        merged.id = existing.id
        merged.created_at = existing.created_at
        return merged

    return existing
