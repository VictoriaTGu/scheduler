"""Database schema definitions."""

import sqlite3
from typing import Optional


SCHEMA_VERSION = 1


def get_schema_sql() -> str:
    """Return the complete database schema SQL."""
    return """
-- Sources table
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_name TEXT NOT NULL,
    source_url TEXT NOT NULL,
    source_type TEXT NOT NULL,
    enabled BOOLEAN DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Events table
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT,
    start_datetime TIMESTAMP NOT NULL,
    end_datetime TIMESTAMP,
    venue_name TEXT,
    region_tag TEXT NOT NULL,
    city TEXT NOT NULL,
    state TEXT NOT NULL,
    address TEXT,
    cost TEXT,
    event_url TEXT NOT NULL,
    source_id INTEGER NOT NULL,
    image_url TEXT,
    recurrence_rule TEXT,
    canonical_key TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (source_id) REFERENCES sources(id),
    UNIQUE(canonical_key, source_id)
);

CREATE INDEX IF NOT EXISTS idx_events_start_datetime ON events(start_datetime);
CREATE INDEX IF NOT EXISTS idx_events_region_city ON events(region_tag, city);
CREATE INDEX IF NOT EXISTS idx_events_canonical_key ON events(canonical_key);

-- Scrape runs table
CREATE TABLE IF NOT EXISTS scrape_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL,
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    finished_at TIMESTAMP,
    status TEXT DEFAULT 'in_progress',
    pages_crawled INTEGER DEFAULT 0,
    events_found INTEGER DEFAULT 0,
    events_new INTEGER DEFAULT 0,
    events_updated INTEGER DEFAULT 0,
    failures_count INTEGER DEFAULT 0,
    error_summary TEXT,
    external_run_id TEXT,
    external_platform TEXT,
    FOREIGN KEY (source_id) REFERENCES sources(id)
);

CREATE INDEX IF NOT EXISTS idx_scrape_runs_source_started ON scrape_runs(source_id, started_at DESC);
"""


def init_db(db_path: str) -> None:
    """Initialize the database with schema."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    try:
        # Create all tables
        cursor.executescript(get_schema_sql())
        conn.commit()
    finally:
        conn.close()


def ensure_schema_compatibility(db_path: str) -> None:
    """Apply lightweight schema upgrades for existing databases."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    try:
        # Ensure new scrape_runs columns exist for older DBs.
        cursor.execute("PRAGMA table_info(scrape_runs)")
        columns = {row[1] for row in cursor.fetchall()}

        if "external_run_id" not in columns:
            cursor.execute("ALTER TABLE scrape_runs ADD COLUMN external_run_id TEXT")

        if "external_platform" not in columns:
            cursor.execute("ALTER TABLE scrape_runs ADD COLUMN external_platform TEXT")

        conn.commit()
    finally:
        conn.close()
