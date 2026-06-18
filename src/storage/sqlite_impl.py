"""SQLite implementation of storage repository."""

import sqlite3
from datetime import datetime
from typing import Optional, List
from pathlib import Path

from src.models import Event, Source, ScrapeRun
from src.storage.repository import StorageRepository
from src.storage.schema import init_db


class SQLiteRepository(StorageRepository):
    """SQLite-based storage repository."""

    def __init__(self, db_path: str):
        """Initialize SQLite repository."""
        self.db_path = db_path
        self._ensure_db_exists()

    def _ensure_db_exists(self) -> None:
        """Ensure database and schema are initialized."""
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        if not Path(self.db_path).exists():
            init_db(self.db_path)

    def _get_connection(self) -> sqlite3.Connection:
        """Get a database connection."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    async def get_source(self, source_id: int) -> Optional[Source]:
        """Get a source by ID."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM sources WHERE id = ?", (source_id,))
            row = cursor.fetchone()
            if row:
                return Source(**dict(row))
            return None
        finally:
            conn.close()

    async def list_sources(self, enabled_only: bool = True) -> List[Source]:
        """List all sources, optionally filtered to enabled only."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            if enabled_only:
                cursor.execute("SELECT * FROM sources WHERE enabled = 1")
            else:
                cursor.execute("SELECT * FROM sources")
            rows = cursor.fetchall()
            return [Source(**dict(row)) for row in rows]
        finally:
            conn.close()

    async def upsert_source(self, source: Source) -> int:
        """Insert or update a source. Returns source ID."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            if source.id:
                cursor.execute(
                    """UPDATE sources SET source_name = ?, source_url = ?, source_type = ?, 
                       enabled = ?, updated_at = ? WHERE id = ?""",
                    (
                        source.source_name,
                        source.source_url,
                        source.source_type,
                        source.enabled,
                        datetime.utcnow(),
                        source.id,
                    ),
                )
            else:
                cursor.execute(
                    """INSERT INTO sources (source_name, source_url, source_type, enabled) 
                       VALUES (?, ?, ?, ?)""",
                    (source.source_name, source.source_url, source.source_type, source.enabled),
                )
                source.id = cursor.lastrowid
            conn.commit()
            return source.id
        finally:
            conn.close()

    async def insert_event(self, event: Event) -> int:
        """Insert a new event. Returns event ID."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """INSERT INTO events 
                   (title, description, start_datetime, end_datetime, venue_name, region_tag,
                    city, state, address, cost, event_url, source_id, image_url, recurrence_rule,
                    canonical_key)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event.title,
                    event.description,
                    event.start_datetime,
                    event.end_datetime,
                    event.venue_name,
                    event.region_tag,
                    event.city,
                    event.state,
                    event.address,
                    event.cost,
                    event.event_url,
                    event.source_id,
                    event.image_url,
                    event.recurrence_rule,
                    event.canonical_key,
                ),
            )
            event.id = cursor.lastrowid
            conn.commit()
            return event.id
        finally:
            conn.close()

    async def upsert_event(self, event: Event) -> int:
        """Insert or update an event. Returns event ID."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()

            # Check if event exists by canonical_key and source_id
            cursor.execute(
                "SELECT id FROM events WHERE canonical_key = ? AND source_id = ?",
                (event.canonical_key, event.source_id),
            )
            existing = cursor.fetchone()

            if existing:
                # Update existing event
                cursor.execute(
                    """UPDATE events SET title = ?, description = ?, start_datetime = ?,
                       end_datetime = ?, venue_name = ?, region_tag = ?, city = ?, state = ?,
                       address = ?, cost = ?, event_url = ?, image_url = ?, recurrence_rule = ?,
                       updated_at = ? WHERE id = ?""",
                    (
                        event.title,
                        event.description,
                        event.start_datetime,
                        event.end_datetime,
                        event.venue_name,
                        event.region_tag,
                        event.city,
                        event.state,
                        event.address,
                        event.cost,
                        event.event_url,
                        event.image_url,
                        event.recurrence_rule,
                        datetime.utcnow(),
                        existing["id"],
                    ),
                )
                event.id = existing["id"]
            else:
                # Insert new event
                event_id = await self.insert_event(event)
                conn.commit()
                return event_id

            conn.commit()
            return event.id
        finally:
            conn.close()

    async def get_event_by_canonical_key(self, canonical_key: str) -> Optional[Event]:
        """Get an event by its canonical key."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM events WHERE canonical_key = ?", (canonical_key,))
            row = cursor.fetchone()
            if row:
                return Event(**dict(row))
            return None
        finally:
            conn.close()

    async def get_events_by_date_range(
        self, start: datetime, end: datetime
    ) -> List[Event]:
        """Get events within a date range, ordered by date then city."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """SELECT * FROM events 
                   WHERE start_datetime >= ? AND start_datetime <= ?
                   ORDER BY start_datetime, city""",
                (start, end),
            )
            rows = cursor.fetchall()
            return [Event(**dict(row)) for row in rows]
        finally:
            conn.close()

    async def create_scrape_run(self, run: ScrapeRun) -> int:
        """Create a new scrape run. Returns run ID."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """INSERT INTO scrape_runs 
                   (source_id, status, pages_crawled, events_found, events_new, 
                    events_updated, failures_count, error_summary)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run.source_id,
                    run.status,
                    run.pages_crawled,
                    run.events_found,
                    run.events_new,
                    run.events_updated,
                    run.failures_count,
                    run.error_summary,
                ),
            )
            run.id = cursor.lastrowid
            conn.commit()
            return run.id
        finally:
            conn.close()

    async def update_scrape_run(self, run: ScrapeRun) -> None:
        """Update an existing scrape run."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """UPDATE scrape_runs SET status = ?, pages_crawled = ?, events_found = ?,
                   events_new = ?, events_updated = ?, failures_count = ?, error_summary = ?,
                   finished_at = ? WHERE id = ?""",
                (
                    run.status,
                    run.pages_crawled,
                    run.events_found,
                    run.events_new,
                    run.events_updated,
                    run.failures_count,
                    run.error_summary,
                    run.finished_at,
                    run.id,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    async def get_recent_scrape_runs(
        self, source_id: int, limit: int = 10
    ) -> List[ScrapeRun]:
        """Get recent scrape runs for a source."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """SELECT * FROM scrape_runs WHERE source_id = ? 
                   ORDER BY started_at DESC LIMIT ?""",
                (source_id, limit),
            )
            rows = cursor.fetchall()
            return [ScrapeRun(**dict(row)) for row in rows]
        finally:
            conn.close()
