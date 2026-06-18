# Implementation Plan: Weekly Event Digest

**Date:** 2026-06-18  
**Status:** Planning (no implementation started)

---

## Overview

Build a Python 3.12 batch application that scrapes event websites, normalizes and deduplicates events, and generates a weekly HTML email digest grouping events by date and city. Deployment via GitHub Actions on Sundays.

---

## 1. Project Structure

```
/src
  /collectors        # Event extraction from sources
  /models            # Pydantic/dataclass schemas
  /services          # Business logic (dedup, digest, calendar links)
  /email             # Email rendering and delivery
  /storage           # Database access layer
  /config            # Settings and environment loading
  /utils             # Logging, helpers
/tests
/migrations         # If using Alembic for schema versioning
/.github/workflows  # GitHub Actions
/requirements.txt
/.env.example
/README.md
```

---

## 2. Data Models & Schemas

### Event Model (Normalized)

```python
class Event:
    id: int | None  # DB primary key
    title: str
    description: str | None
    start_datetime: datetime
    end_datetime: datetime | None
    venue_name: str | None
    region_tag: str  # Westerly, South County (RI), Providence Metro, etc.
    city: str
    state: str
    address: str | None
    cost: str | None  # "Free", "$20", "TBD", etc.
    event_url: str
    source_id: int  # Foreign key to sources table
    image_url: str | None
    recurrence_rule: str | None  # RFC 5545 RRULE if recurring
    canonical_key: str  # Hash fingerprint for deduplication
    created_at: datetime
    updated_at: datetime
```

### Source Model

```python
class Source:
    id: int | None
    source_name: str
    source_url: str
    source_type: str  # "generic", "eventbrite", etc. (extensible)
    enabled: bool
    created_at: datetime
    updated_at: datetime
```

### ScrapeRun Model

```python
class ScrapeRun:
    id: int | None
    source_id: int
    started_at: datetime
    finished_at: datetime | None
    status: str  # "in_progress", "success", "failed"
    pages_crawled: int
    events_found: int
    events_new: int
    events_updated: int
    failures_count: int
    error_summary: str | None
```

---

## 3. Storage Layer

### Database Schema

**sources**
```sql
CREATE TABLE sources (
    id INTEGER PRIMARY KEY,
    source_name TEXT NOT NULL,
    source_url TEXT NOT NULL,
    source_type TEXT NOT NULL,
    enabled BOOLEAN DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

**events**
```sql
CREATE TABLE events (
    id INTEGER PRIMARY KEY,
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
    UNIQUE(canonical_key, source_id)  -- Prevent duplicate source records
);

CREATE INDEX idx_events_start_datetime ON events(start_datetime);
CREATE INDEX idx_events_region_city ON events(region_tag, city);
CREATE INDEX idx_events_canonical_key ON events(canonical_key);
```

**scrape_runs**
```sql
CREATE TABLE scrape_runs (
    id INTEGER PRIMARY KEY,
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
    FOREIGN KEY (source_id) REFERENCES sources(id)
);

CREATE INDEX idx_scrape_runs_source_started ON scrape_runs(source_id, started_at DESC);
```

### Storage Interface

Implement a repository/DAO pattern:

```python
class StorageBackend(ABC):
    async def get_source(self, id: int) -> Source
    async def list_sources(self, enabled_only: bool = True) -> list[Source]
    async def upsert_source(self, source: Source) -> int
    
    async def insert_event(self, event: Event) -> int
    async def upsert_event(self, event: Event) -> int
    async def get_events_by_date_range(self, start: datetime, end: datetime) -> list[Event]
    async def get_event_by_canonical_key(self, key: str) -> Event | None
    
    async def create_scrape_run(self, run: ScrapeRun) -> int
    async def update_scrape_run(self, run: ScrapeRun) -> None
    async def get_recent_scrape_runs(self, source_id: int, limit: int = 10) -> list[ScrapeRun]
```

Provide SQLite implementations behind this interface.

---

## 4. Collection & Extraction Pipeline

### Source Loader

- Read sources from CSV file or Google Sheet (configurable)
- Normalize into Source objects
- Store/update in database

### Collector Orchestration

For each enabled source:

1. Create a `ScrapeRun` record (status: in_progress)
2. Discover event pages on the source URL (look for /events, /calendar, etc.)
3. For each page, try extraction strategies in order:
   - **Strategy 1:** Structured metadata (schema.org Event, JSON-LD, microdata)
   - **Strategy 2:** Generic event listing page parsing (DOM selectors)
   - **Strategy 3:** LLM-assisted extraction (fallback only)
4. Normalize each extracted event immediately (dates, locations, costs)
5. Apply region tagging (deterministic rules first, fallback if needed)
6. Log extraction failures and successes
7. Update `ScrapeRun` with final counts and status

### Normalization

For each extracted raw event:
- Canonicalize title (trim, lowercase for comparison)
- Parse start_datetime (handle multiple formats)
- Standardize venue_name and address
- Format cost (normalize "Free" variants, extract price if present)
- Ensure city and state are not None
- Apply region_tag rules

### Region Tagging

Use deterministic rules (city/state/keywords → region):
```python
REGION_RULES = {
    "Westerly": ["westerly", "ri"],
    "South County (RI)": ["south county", "ri", "narragansett", "kingston"],
    "Providence Metro": ["providence", "cranston", "warwick", "ri"],
    "Aquidneck Island (RI)": ["newport", "middletown", "portsmouth", "ri"],
    "Boston": ["boston", "cambridge", "somerville", "ma"],
    "Connecticut": ["ct", "connecticut"],
    "Other": []
}
```

---

## 5. Deduplication Strategy

### Canonical Key Generation

Create a stable fingerprint from:
- Normalized title (lowercase, trim)
- Start date (YYYY-MM-DD)
- Normalized city
- Normalized venue_name (or use a fuzzy hash if venue is missing)

```python
def generate_canonical_key(event: Event) -> str:
    title_norm = event.title.lower().strip()
    date_norm = event.start_datetime.date().isoformat()
    city_norm = event.city.lower().strip()
    venue_norm = (event.venue_name or "").lower().strip()
    
    key_parts = [title_norm, date_norm, city_norm, venue_norm]
    return hashlib.sha256("||".join(key_parts).encode()).hexdigest()[:16]
```

### Fuzzy Matching (Optional Enhancement)

For titles that differ only in word order or minor variations:
- Use `difflib.SequenceMatcher` or `fuzzywuzzy` library
- If similarity > 0.85 and dates match and cities match, treat as duplicate

### Merge Strategy

When duplicate found:
- Keep the event record with the richest metadata (most fields populated)
- If both have the same canonical_key in the DB, update the existing record with new data
- Preserve the best event_url (prefer the source URL over aggregator URLs)
- Update `updated_at` timestamp

---

## 6. Calendar Link Generation

Generate Google Calendar "Add Event" URLs using the `https://calendar.google.com/calendar/r/eventedit` endpoint:

```python
def generate_calendar_link(event: Event) -> str:
    params = {
        "text": event.title,
        "dates": event.start_datetime.strftime("%Y%m%dT%H%M%S"),
        "location": event.city,
        "details": f"Source: {event.source_url}\n\n{event.description or event.event_url}",
    }
    if event.end_datetime:
        params["dates"] += "/" + event.end_datetime.strftime("%Y%m%dT%H%M%S")
    
    return "https://calendar.google.com/calendar/r/eventedit?" + urlencode(params)
```

Used during email digest rendering; no separate endpoint needed.

---

## 7. Email Digest Generation & Delivery

### Digest Structure

Group upcoming events (next 60 days) by:
1. Date (ascending)
2. City (alphabetical)

Within each city, list events by time.

### HTML Template

```
[Header with generation date and totals]

Sunday, July 12

  Providence
  • Housing Forum | 6:00 PM | Free | Source: URI Events
    [Event Page Link] 
    [Add to Calendar]
  
  • PorchFest | 7:00 PM | $20 | Source: Eventbrite
    [Event Page Link] 
    [Add to Calendar]

  Newport
  • Art Walk | 10:00 AM | Free | Source: South County Chamber
    [Event Page Link] 
    [Add to Calendar]

[Footer with unsubscribe link if using SendGrid]
```

### Delivery

Supported providers (configurable):
- **SendGrid** (primary)
- **SMTP** (fallback)

Credentials from environment variables only.

### Email Service Interface

```python
class EmailProvider(ABC):
    async def send(self, to: str, subject: str, html: str, text: str) -> bool
```

---

## 8. Scheduling & GitHub Actions

### Workflow File: `.github/workflows/weekly-digest.yml`

```yaml
name: Weekly Event Digest
on:
  schedule:
    - cron: '0 10 * * 0'  # Every Sunday at 10:00 AM UTC
  workflow_dispatch:  # Manual trigger

jobs:
  digest:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      
      - name: Install dependencies
        run: pip install -r requirements.txt
      
      - name: Run scraper & digest
        env:
          EMAIL_TO: ${{ secrets.EMAIL_TO }}
          EMAIL_FROM: ${{ secrets.EMAIL_FROM }}
          SENDGRID_API_KEY: ${{ secrets.SENDGRID_API_KEY }}
          GOOGLE_SHEET_URL: ${{ secrets.GOOGLE_SHEET_URL }}
        run: python -m src.main
```

### CLI Entry Point

```python
# src/main.py
async def main():
    config = Config.from_env()
    storage = StorageFactory.create(config)
    
    await run_scraper(config, storage)
    events = await storage.get_events_by_date_range(
        datetime.now(), 
        datetime.now() + timedelta(days=60)
    )
    digest_html = render_digest(events)
    await send_email(config, digest_html)
```

---

## 9. Minimal API Layer (Operational)

Simple REST API for testing and validation. **Endpoints (MVP):**

- `GET /health` – Returns {"status": "ok", "database": "connected", "timestamp": "..."}
- `GET /events` – List upcoming events (query params: start_date, end_date, city, region)
- `GET /scrape-runs` – List recent scrape runs with status and counts
- `POST /digests/generate` – Trigger digest generation (returns preview JSON, doesn't send email)

**Notes:**
- No authentication for MVP (assume private deployment)
- Skipped `/digests/preview`, `/calendar-link/{event_id}`, `/regions/summary` (not needed for MVP)
- Can be embedded in the same process or run as a simple FastAPI/Flask app
- Primarily for debugging and validation

---

## 10. Testing Strategy

- **Unit tests:** Normalization, deduplication, canonical key generation, calendar link generation, email rendering
- **Integration tests:** Full pipeline with mock sources and SQLite
- **Fixtures:** Sample HTML from real event websites, structured metadata examples
- **Logging assertions:** Verify that events are logged at each stage

---

## 11. Configuration

All settings via `.env` file:

```env
# Email
EMAIL_TO=akv02813@gmail.com
EMAIL_FROM=akv02813@gmail.com
SENDGRID_API_KEY=sg_...
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USER=akv02813@gmail.com
SMTP_PASSWORD=pass

# Event Sources
SOURCES_CSV_PATH=./sources.csv
GOOGLE_SHEET_URL=https://docs.google.com/spreadsheets/d/...

# Event Extraction
EVENT_LOOKAHEAD_DAYS=60
LLM_FALLBACK_ENABLED=true

# Storage
DATABASE_URL=sqlite:///./events.db

# Logging
LOG_LEVEL=INFO
```

---

## 12. Deliverables Sequence

1. **Phase 1: Foundation**
   - Project structure and dependencies
   - Configuration loading
   - Logging setup
   - Storage schema and repository interface

2. **Phase 2: Collection**
   - Source loader
   - Collector orchestration
   - Extraction strategies (structured metadata, generic parsing, LLM fallback)
   - Event normalization
   - Region tagging

3. **Phase 3: Normalization & Dedup**
   - Canonical key generation
   - Fuzzy matching logic
   - Merge strategy

4. **Phase 4: Digest & Delivery**
   - Calendar link generation
   - Email digest rendering (HTML)
   - Email provider implementations (SendGrid, SMTP)

5. **Phase 5: Scheduling & API**
   - GitHub Actions workflow
   - Minimal REST API for testing
   - CLI entry point

6. **Phase 6: Documentation & Sample**
   - README with setup instructions
   - Example .env file
   - Sample digest output (mock data)
   - Unit and integration tests

---

## Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| Option A: `recurrence_rule` field on events | Most sources don't expose structured recurrence; MVP handles individual occurrences. Can expand later without schema changes. |
| Skip `/digests/preview`, `/calendar-link`, `/regions/summary` endpoints | Not needed for MVP; dry-run mode in CLI and inline calendar link generation are sufficient. |
| Storage repository pattern | Allows swapping SQLite ↔ Postgres without changing business logic. |
| Canonical key fingerprinting | Deterministic and stable across runs; enables reliable deduplication. |
| Structured logging from the start | Essential for debugging scraper failures and email delivery issues in production. |

---

## Notes

- Assume Python 3.12 async/await throughout for scalability
- Use Pydantic for schema validation
- Prefer SQLite initially; Postgres is a drop-in replacement via repository interface
- No database migrations needed for MVP (simple schema); add Alembic if schema evolves
- LLM extraction is a fallback only; prioritize structured metadata and DOM parsing

