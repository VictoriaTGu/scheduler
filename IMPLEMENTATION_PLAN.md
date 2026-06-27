# Implementation Plan: Weekly Event Digest

**Date:** 2026-06-18  
**Updated:** 2026-06-27 — Added Playwright rendering layer, targeted LLM time-extraction fallback (OpenAI), Meetup scraping via Playwright + JSON-LD, and expanded test strategy  
**Status:** MVP built — applying targeted fixes

> **For the coder:** Sections marked `[CHANGE NEEDED]` require edits to the existing MVP codebase. Sections marked `[NEW]` are net-new files or modules to add. Unchanged sections are included for reference only.

---

## Overview

Build a Python 3.12 batch application that scrapes event websites, normalizes and deduplicates events, and generates a weekly HTML email digest grouping events by date and city. Deployment via GitHub Actions on Sundays.

---

## 1. Project Structure

```
/src
  /collectors        # Event extraction from sources
    renderer.py      # [NEW] Playwright rendering layer
    meetup.py        # [NEW] Meetup-specific collector using Playwright + JSON-LD
  /models            # Pydantic/dataclass schemas
  /services          # Business logic (dedup, digest, calendar links)
    llm_fallback.py  # [NEW] Targeted OpenAI time/field extraction
  /email             # Email rendering and delivery
  /storage           # Database access layer
  /config            # Settings and environment loading
  /utils             # Logging, helpers
/tests
  /unit
    test_renderer.py         # [NEW]
    test_llm_fallback.py     # [NEW]
    test_meetup_collector.py # [NEW]
  /integration
    test_pipeline_playwright.py  # [NEW]
    fixtures/
      meetup_event_page.html     # [NEW] Saved HTML fixture
      mbadrivein_event.html      # [NEW] Saved HTML fixture (JS-rendered)
/migrations
/.github/workflows
/requirements.txt    # [CHANGE NEEDED] Add playwright, openai
/.env.example        # [CHANGE NEEDED] Add OPENAI_API_KEY
/README.md
```

---

## 2. Data Models & Schemas

*(Unchanged — no schema changes required for these fixes)*

```python
class Event:
    id: int | None
    title: str
    description: str | None
    start_datetime: datetime
    end_datetime: datetime | None
    venue_name: str | None
    region_tag: str
    city: str
    state: str
    address: str | None
    cost: str | None
    event_url: str
    source_id: int
    image_url: str | None
    recurrence_rule: str | None
    canonical_key: str
    created_at: datetime
    updated_at: datetime
```

```python
class Source:
    id: int | None
    source_name: str
    source_url: str
    source_type: str  # "generic", "eventbrite", "meetup" ← meetup is now a valid type
    enabled: bool
    created_at: datetime
    updated_at: datetime
```

```python
class ScrapeRun:
    id: int | None
    source_id: int
    started_at: datetime
    finished_at: datetime | None
    status: str
    pages_crawled: int
    events_found: int
    events_new: int
    events_updated: int
    failures_count: int
    error_summary: str | None
```

---

## 3. Storage Layer

*(Unchanged)*

-**sources**
-```sql
-CREATE TABLE sources (
-    id INTEGER PRIMARY KEY,
-    source_name TEXT NOT NULL,
-    source_url TEXT NOT NULL,
-    source_type TEXT NOT NULL,
-    enabled BOOLEAN DEFAULT 1,
-    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
-    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
-);
-```

-**events**
-```sql
-CREATE TABLE events (
-    id INTEGER PRIMARY KEY,
-    title TEXT NOT NULL,
-    description TEXT,
-    start_datetime TIMESTAMP NOT NULL,
-    end_datetime TIMESTAMP,
-    venue_name TEXT,
-    region_tag TEXT NOT NULL,
-    city TEXT NOT NULL,
-    state TEXT NOT NULL,
-    address TEXT,
-    cost TEXT,
-    event_url TEXT NOT NULL,
-    source_id INTEGER NOT NULL,
-    image_url TEXT,
-    recurrence_rule TEXT,
-    canonical_key TEXT NOT NULL,
-    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
-    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
-    FOREIGN KEY (source_id) REFERENCES sources(id),
-    UNIQUE(canonical_key, source_id)  -- Prevent duplicate source records
-);
-
-CREATE INDEX idx_events_start_datetime ON events(start_datetime);
-CREATE INDEX idx_events_region_city ON events(region_tag, city);
-CREATE INDEX idx_events_canonical_key ON events(canonical_key);```

-**scrape_runs**
-```sql
-CREATE TABLE scrape_runs (
-    id INTEGER PRIMARY KEY,
-    source_id INTEGER NOT NULL,
-    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
-    finished_at TIMESTAMP,
-    status TEXT DEFAULT 'in_progress',
-    pages_crawled INTEGER DEFAULT 0,
-    events_found INTEGER DEFAULT 0,
-    events_new INTEGER DEFAULT 0,
-    events_updated INTEGER DEFAULT 0,
-    failures_count INTEGER DEFAULT 0,
-    error_summary TEXT,
-    FOREIGN KEY (source_id) REFERENCES sources(id)
-);
-
-CREATE INDEX idx_scrape_runs_source_started ON scrape_runs(source_id, started_at DESC);```

---

## 4. Collection & Extraction Pipeline

### [NEW] Fix 1: Playwright Rendering Layer

**File to create:** `src/collectors/renderer.py`

The root cause of missing event times on sites like mbadrivein.com is that their content is injected by JavaScript after page load. A plain `requests` fetch only returns the app shell. Playwright renders the full page before any extraction strategy runs.

**How it fits into the existing pipeline:** The collector orchestration already tries strategies in order. Add a `get_html(url)` utility that is called before Strategy 1. It tries `requests` first (fast, free) and falls back to Playwright only if the result looks like an unrendered shell (heuristic: no `<time>` tags or structured event metadata found in the raw HTML).

```python
# src/collectors/renderer.py
# [NEW FILE]

import asyncio
import re
import httpx
from playwright.async_api import async_playwright

_SHELL_INDICATORS = [
    r'<div id="root">\s*</div>',   # React empty root
    r'<div id="app">\s*</div>',    # Vue/Angular empty root
    r'window\.__NEXT_DATA__',      # Next.js SSR marker (needs hydration)
]

def _looks_like_shell(html: str) -> bool:
    """Return True if the HTML appears to be an unrendered JS app shell."""
    # If there's meaningful text content it's probably not a shell
    if len(re.findall(r'<p[^>]*>.{40,}</p>', html)) > 2:
        return False
    return any(re.search(pat, html) for pat in _SHELL_INDICATORS)

async def get_rendered_html(url: str, force_playwright: bool = False) -> str:
    """
    Fetch a URL and return fully rendered HTML.

    Strategy:
      1. Try a plain HTTPX request (fast, no browser overhead).
      2. If the result looks like an unrendered shell OR force_playwright=True,
         fall back to Playwright headless Chromium.

    Caller should pass force_playwright=True for known JS-heavy domains
    (e.g. mbadrivein.com, meetup.com).
    """
    if not force_playwright:
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(url, follow_redirects=True)
                html = resp.text
                if not _looks_like_shell(html):
                    return html
        except Exception:
            pass  # fall through to Playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(url, wait_until="networkidle", timeout=30_000)
        html = await page.content()
        await browser.close()
        return html
```

**[CHANGE NEEDED] in `src/collectors/orchestrator.py` (or equivalent):**

Replace the current `requests.get(url)` call at the top of your per-source scrape loop with:

```python
# Before (MVP):
# html = requests.get(source.source_url).text

# After:
from src.collectors.renderer import get_rendered_html

force_pw = source.source_type in ("meetup",) or source.source_url in config.PLAYWRIGHT_FORCE_DOMAINS
html = await get_rendered_html(source.source_url, force_playwright=force_pw)
```

Add `PLAYWRIGHT_FORCE_DOMAINS` as a comma-separated env var so you can flag problem domains without code changes:

```env
# .env
PLAYWRIGHT_FORCE_DOMAINS=mbadrivein.com,anotherjssite.com
```

---

### [NEW] Fix 2: Targeted LLM Time-Extraction Fallback

**File to create:** `src/services/llm_fallback.py`

**When to call it:** Only when `start_datetime` resolves to midnight (00:00) after Strategy 1 and Strategy 2 run. Do not call it for every event — that wastes tokens and money.

**Token budget design:** The prompt sends only a stripped plain-text snippet (~300–500 chars) around the event title, not the full page HTML. This keeps each call under ~200 input tokens and the response under 20 output tokens. At GPT-4o-mini pricing (~$0.15/1M input tokens), 100 fallback calls per week costs less than $0.01.

```python
# src/services/llm_fallback.py
# [NEW FILE]

import re
from bs4 import BeautifulSoup
from openai import AsyncOpenAI

_client = AsyncOpenAI()  # reads OPENAI_API_KEY from env

def _extract_snippet(html: str, event_title: str, window_chars: int = 500) -> str:
    """
    Extract a short plain-text window around the event title from the HTML.
    Strips all tags first to minimize tokens sent to the LLM.
    """
    soup = BeautifulSoup(html, "html.parser")
    # Remove script/style noise
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    text = soup.get_text(separator=" ", strip=True)
    text = re.sub(r'\s+', ' ', text)

    idx = text.lower().find(event_title.lower()[:30])
    if idx == -1:
        # Title not found in plain text — return first window
        return text[:window_chars]
    start = max(0, idx - 100)
    return text[start : start + window_chars]


async def extract_missing_time(html: str, event_title: str) -> str | None:
    """
    Use GPT-4o-mini to extract a start time when structured parsing returned midnight.

    Returns a time string like "7:30 PM" or None if not found.
    Caller is responsible for parsing the returned string into the datetime.
    """
    snippet = _extract_snippet(html, event_title)

    response = await _client.chat.completions.create(
        model="gpt-4o-mini",
        max_tokens=20,
        temperature=0,
        messages=[
            {
                "role": "system",
                "content": (
                    "You extract event start times from text. "
                    "Reply with ONLY the time in 12-hour format like '7:30 PM'. "
                    "If no time is present, reply with the single word: null"
                ),
            },
            {
                "role": "user",
                "content": f"Event: {event_title}\n\nContext:\n{snippet}",
            },
        ],
    )

    result = response.choices[0].message.content.strip()
    return None if result.lower() == "null" else result


async def extract_missing_fields(html: str, event_title: str) -> dict:
    """
    Broader fallback: extract time, cost, and venue in one call when multiple
    fields are missing. Use this only if two or more fields are absent,
    to avoid making multiple single-field calls.

    Returns a dict with keys: time, cost, venue_name (any may be None).
    """
    snippet = _extract_snippet(html, event_title)

    response = await _client.chat.completions.create(
        model="gpt-4o-mini",
        max_tokens=60,
        temperature=0,
        messages=[
            {
                "role": "system",
                "content": (
                    "Extract event details from the text. "
                    "Reply in this exact format with no extra words:\n"
                    "time: <HH:MM AM/PM or null>\n"
                    "cost: <amount or Free or null>\n"
                    "venue: <name or null>"
                ),
            },
            {
                "role": "user",
                "content": f"Event: {event_title}\n\nContext:\n{snippet}",
            },
        ],
    )

    raw = response.choices[0].message.content.strip()
    result = {"time": None, "cost": None, "venue_name": None}
    for line in raw.splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            val = val.strip()
            if val.lower() == "null":
                val = None
            if key.strip() == "time":
                result["time"] = val
            elif key.strip() == "cost":
                result["cost"] = val
            elif key.strip() == "venue":
                result["venue_name"] = val
    return result
```

**[CHANGE NEEDED] in your normalization / post-extraction step:**

After Strategy 1 and Strategy 2 run and you've assembled the raw event, add this guard:

```python
# In src/services/normalization.py (or wherever you finalize start_datetime)

from src.services.llm_fallback import extract_missing_time, extract_missing_fields

# Detect "midnight default" — times that defaulted to 00:00 are suspect
time_is_missing = event.start_datetime.hour == 0 and event.start_datetime.minute == 0
cost_is_missing = event.cost is None
venue_is_missing = event.venue_name is None

if config.LLM_FALLBACK_ENABLED:
    if sum([time_is_missing, cost_is_missing, venue_is_missing]) >= 2:
        # Two or more fields missing — one combined call
        fields = await extract_missing_fields(page_html, event.title)
        if fields["time"]:
            event.start_datetime = _parse_time_into_date(event.start_datetime, fields["time"])
        if fields["cost"] and cost_is_missing:
            event.cost = fields["cost"]
        if fields["venue_name"] and venue_is_missing:
            event.venue_name = fields["venue_name"]
    elif time_is_missing:
        # Only time is missing — single targeted call
        time_str = await extract_missing_time(page_html, event.title)
        if time_str:
            event.start_datetime = _parse_time_into_date(event.start_datetime, time_str)
```

You will need a small helper to merge a time string back onto an existing date:

```python
def _parse_time_into_date(existing_dt: datetime, time_str: str) -> datetime:
    """Merge a '7:30 PM' string into an existing date, preserving the date."""
    from datetime import datetime as dt
    parsed = dt.strptime(time_str.strip().upper(), "%I:%M %p")
    return existing_dt.replace(hour=parsed.hour, minute=parsed.minute, second=0)
```

---

### [NEW] Fix 3: Meetup Collector via Playwright + JSON-LD (Option B)

**File to create:** `src/collectors/meetup.py`

Meetup embeds full `schema.org/Event` JSON-LD in its rendered HTML. No API key, no Apify actor, no cost — just Playwright to render the page and your existing Strategy 1 extractor to parse the JSON-LD.

**How to configure which Meetup searches to run:** Add to `config/discovery_sources.yaml`. The collector reads these rows and routes them here instead of the generic collector.

```python
# src/collectors/meetup.py
# [NEW FILE]

import json
import logging
from bs4 import BeautifulSoup
from src.collectors.renderer import get_rendered_html
from src.models.event import Event
from src.services.normalization import normalize_event

logger = logging.getLogger(__name__)


async def collect_meetup_events(source_url: str, source_id: int) -> list[Event]:
    """
    Fetch a Meetup search or group page, extract all JSON-LD Event objects,
    and return normalized Event instances.

    Playwright is always used (force_playwright=True) because Meetup's
    event listings are entirely JavaScript-rendered.
    """
    logger.info("Fetching Meetup page: %s", source_url)
    html = await get_rendered_html(source_url, force_playwright=True)

    soup = BeautifulSoup(html, "html.parser")
    events: list[Event] = []

    for script_tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script_tag.string or "")
        except (json.JSONDecodeError, TypeError):
            continue

        # JSON-LD may be a single object or a list
        items = data if isinstance(data, list) else [data]

        for item in items:
            if item.get("@type") != "Event":
                continue
            try:
                raw = _jsonld_to_raw(item)
                event = normalize_event(raw, source_id=source_id)
                if event:
                    events.append(event)
            except Exception as exc:
                logger.warning("Failed to parse Meetup event: %s", exc)

    logger.info("Collected %d events from %s", len(events), source_url)
    return events


def _jsonld_to_raw(item: dict) -> dict:
    """Map a schema.org Event JSON-LD dict to the raw event dict your
    normalization layer already expects."""
    location = item.get("location", {})
    address = location.get("address", {})

    return {
        "title": item.get("name", ""),
        "description": item.get("description"),
        "start_datetime": item.get("startDate"),
        "end_datetime": item.get("endDate"),
        "event_url": item.get("url", ""),
        "venue_name": location.get("name"),
        "address": address.get("streetAddress") if isinstance(address, dict) else str(address),
        "city": address.get("addressLocality") if isinstance(address, dict) else None,
        "state": address.get("addressRegion") if isinstance(address, dict) else None,
        "image_url": item.get("image"),
        "cost": _parse_offers(item.get("offers")),
    }


def _parse_offers(offers) -> str | None:
    if not offers:
        return None
    if isinstance(offers, list):
        offers = offers[0]
    price = offers.get("price", "")
    currency = offers.get("priceCurrency", "")
    if str(price) == "0":
        return "Free"
    if price:
        return f"{currency}{price}".strip()
    return None
```

**[CHANGE NEEDED] in `src/collectors/orchestrator.py`:**

Add a routing check so that `source_type == "meetup"` sources go to the new collector instead of the generic one:

```python
from src.collectors.meetup import collect_meetup_events

# Inside your per-source loop:
if source.source_type == "meetup":
    events = await collect_meetup_events(source.source_url, source.id)
else:
    # existing generic collection path
    html = await get_rendered_html(source.source_url, force_playwright=force_pw)
    events = await extract_events_from_html(html, source)
```

---

### Existing Extraction Pipeline (unchanged strategies, updated orchestration)

For each enabled non-Meetup source:

1. Create a `ScrapeRun` record (status: in_progress)
2. Fetch HTML via `get_rendered_html()` ← **[CHANGE NEEDED]** replace raw `requests.get()`
3. For each page, try extraction strategies in order:
   - **Strategy 1:** Structured metadata (schema.org Event, JSON-LD, microdata)
   - **Strategy 2:** Generic event listing page parsing (DOM selectors)
   - **Strategy 3:** LLM fallback — `extract_missing_time` / `extract_missing_fields` ← **[CHANGE NEEDED]** replace or supplement existing LLM fallback with targeted calls
4. Normalize each extracted event
5. Apply region tagging
6. Log extraction failures and successes
7. Update `ScrapeRun` with final counts and status

---

## 5. Deduplication Strategy

*(Unchanged)*

---

## 6. Calendar Link Generation

*(Unchanged)*

---

## 7. Email Digest Generation & Delivery

*(Unchanged)*

---

## 8. Scheduling & GitHub Actions

### [CHANGE NEEDED] Workflow File: `.github/workflows/weekly-digest.yml`

Add Playwright browser installation and the OpenAI secret:

```yaml
name: Weekly Event Digest
on:
  schedule:
    - cron: '0 10 * * 0'  # Every Sunday at 10:00 AM UTC
  workflow_dispatch:

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

      # [NEW] Install Playwright browsers — required for JS rendering and Meetup
      - name: Install Playwright browsers
        run: playwright install chromium --with-deps

      - name: Run scraper & digest
        env:
          EMAIL_TO: ${{ secrets.EMAIL_TO }}
          EMAIL_FROM: ${{ secrets.EMAIL_FROM }}
          SENDGRID_API_KEY: ${{ secrets.SENDGRID_API_KEY }}
          GOOGLE_SHEET_URL: ${{ secrets.GOOGLE_SHEET_URL }}
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}    # [NEW]
          PLAYWRIGHT_FORCE_DOMAINS: ${{ vars.PLAYWRIGHT_FORCE_DOMAINS }}  # [NEW]
        run: python -m src.main
```

**Note on Actions runtime:** Playwright Chromium adds ~2–3 minutes to cold start. For a weekly job this is acceptable. If it becomes a problem, cache the browser binaries with `actions/cache` keyed on the Playwright version.

---

## 9. Minimal API Layer (Operational)

*(Unchanged)*

---

## 10. Testing Strategy

### [CHANGE NEEDED / EXPAND] — Replace the one-liner test plan with this

The existing test plan listed categories without specifying what to actually test. This section replaces it with concrete test cases for the new modules and the failure modes they address.

---

### Unit Tests

**`tests/unit/test_renderer.py`** [NEW]

```python
# What to test:
# 1. _looks_like_shell() returns True for a bare React div, False for content-rich HTML
# 2. get_rendered_html() returns plain-request result when HTML is not a shell
# 3. get_rendered_html() with force_playwright=True invokes Playwright (mock Playwright)
# 4. Network timeout on plain request falls through to Playwright without raising

import pytest
from unittest.mock import AsyncMock, patch
from src.collectors.renderer import _looks_like_shell, get_rendered_html

def test_shell_detection_bare_react():
    html = '<html><body><div id="root"></div></body></html>'
    assert _looks_like_shell(html) is True

def test_shell_detection_rich_content():
    html = '<html><body>' + '<p>' + 'x' * 50 + '</p>' * 5 + '</body></html>'
    assert _looks_like_shell(html) is False

@pytest.mark.asyncio
async def test_plain_request_used_when_html_is_rich(respx_mock):
    rich_html = '<html><body>' + '<p>' + 'x' * 50 + '</p>' * 5 + '</body></html>'
    respx_mock.get("https://example.com").mock(return_value=httpx.Response(200, text=rich_html))
    result = await get_rendered_html("https://example.com")
    assert result == rich_html  # Playwright never called

@pytest.mark.asyncio
async def test_playwright_used_for_shell(respx_mock):
    shell_html = '<html><body><div id="root"></div></body></html>'
    playwright_html = '<html><body><p>Event at 7:30 PM</p></body></html>'
    respx_mock.get("https://example.com").mock(return_value=httpx.Response(200, text=shell_html))
    with patch("src.collectors.renderer.async_playwright") as mock_pw:
        # Set up mock chain: async_playwright().__aenter__().chromium.launch()...
        mock_page = AsyncMock()
        mock_page.content.return_value = playwright_html
        # ... (wire up the full mock chain)
        result = await get_rendered_html("https://example.com")
    assert result == playwright_html
```

---

**`tests/unit/test_llm_fallback.py`** [NEW]

```python
# What to test:
# 1. _extract_snippet() returns a window centered near the event title
# 2. _extract_snippet() strips script/style/nav tags before extracting text
# 3. extract_missing_time() parses valid response ("7:30 PM") → returns string
# 4. extract_missing_time() returns None when LLM replies "null"
# 5. extract_missing_fields() correctly parses the structured response format
# 6. _parse_time_into_date() merges time string onto existing date without changing date

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime
from src.services.llm_fallback import (
    _extract_snippet,
    extract_missing_time,
    extract_missing_fields,
    _parse_time_into_date,
)

def test_extract_snippet_strips_noise():
    html = """
    <html><head><script>alert(1)</script></head>
    <body><nav>Menu</nav>
    <p>Come join us for Summer Fest at 7:30 PM on the waterfront.</p>
    <footer>Footer</footer></body></html>
    """
    snippet = _extract_snippet(html, "Summer Fest")
    assert "alert" not in snippet
    assert "Menu" not in snippet
    assert "7:30 PM" in snippet

def test_extract_snippet_centers_on_title():
    long_text = "A" * 200 + " My Event " + "B" * 200
    # Wrap in minimal HTML
    html = f"<html><body><p>{long_text}</p></body></html>"
    snippet = _extract_snippet(html, "My Event", window_chars=100)
    assert "My Event" in snippet

@pytest.mark.asyncio
async def test_extract_missing_time_valid_response():
    with patch("src.services.llm_fallback._client") as mock_client:
        mock_client.chat.completions.create = AsyncMock(
            return_value=MagicMock(choices=[MagicMock(message=MagicMock(content="7:30 PM"))])
        )
        result = await extract_missing_time("<html></html>", "Summer Fest")
    assert result == "7:30 PM"

@pytest.mark.asyncio
async def test_extract_missing_time_null_response():
    with patch("src.services.llm_fallback._client") as mock_client:
        mock_client.chat.completions.create = AsyncMock(
            return_value=MagicMock(choices=[MagicMock(message=MagicMock(content="null"))])
        )
        result = await extract_missing_time("<html></html>", "Summer Fest")
    assert result is None

@pytest.mark.asyncio
async def test_extract_missing_fields_parses_format():
    raw = "time: 6:00 PM\ncost: Free\nvenue: Riverside Park"
    with patch("src.services.llm_fallback._client") as mock_client:
        mock_client.chat.completions.create = AsyncMock(
            return_value=MagicMock(choices=[MagicMock(message=MagicMock(content=raw))])
        )
        result = await extract_missing_fields("<html></html>", "Art Walk")
    assert result["time"] == "6:00 PM"
    assert result["cost"] == "Free"
    assert result["venue_name"] == "Riverside Park"

def test_parse_time_into_date_preserves_date():
    existing = datetime(2026, 7, 12, 0, 0, 0)
    result = _parse_time_into_date(existing, "7:30 PM")
    assert result.date() == existing.date()
    assert result.hour == 19
    assert result.minute == 30
```

---

**`tests/unit/test_meetup_collector.py`** [NEW]

```python
# What to test:
# 1. _jsonld_to_raw() maps all JSON-LD fields to expected raw dict keys
# 2. _parse_offers() returns "Free" for price=0, "$15" for price=15
# 3. _parse_offers() returns None for missing offers
# 4. collect_meetup_events() calls get_rendered_html with force_playwright=True
# 5. collect_meetup_events() skips non-Event @type items in JSON-LD
# 6. collect_meetup_events() handles malformed JSON-LD without crashing

import pytest
from unittest.mock import AsyncMock, patch
from src.collectors.meetup import _jsonld_to_raw, _parse_offers, collect_meetup_events

SAMPLE_JSONLD_EVENT = {
    "@type": "Event",
    "name": "RI Tech Meetup",
    "startDate": "2026-07-15T18:30:00",
    "endDate": "2026-07-15T20:00:00",
    "url": "https://www.meetup.com/ri-tech/events/12345/",
    "location": {
        "name": "AS220",
        "address": {
            "streetAddress": "115 Empire St",
            "addressLocality": "Providence",
            "addressRegion": "RI",
        }
    },
    "offers": {"price": "0", "priceCurrency": "USD"},
}

def test_jsonld_to_raw_maps_fields():
    raw = _jsonld_to_raw(SAMPLE_JSONLD_EVENT)
    assert raw["title"] == "RI Tech Meetup"
    assert raw["city"] == "Providence"
    assert raw["state"] == "RI"
    assert raw["venue_name"] == "AS220"
    assert raw["cost"] == "Free"

def test_parse_offers_free():
    assert _parse_offers({"price": "0", "priceCurrency": "USD"}) == "Free"

def test_parse_offers_paid():
    assert _parse_offers({"price": "15", "priceCurrency": "$"}) == "$15"

def test_parse_offers_none():
    assert _parse_offers(None) is None

@pytest.mark.asyncio
async def test_collect_uses_playwright():
    fixture_html = open("tests/integration/fixtures/meetup_event_page.html").read()
    with patch("src.collectors.meetup.get_rendered_html", new_callable=AsyncMock) as mock_render:
        mock_render.return_value = fixture_html
        with patch("src.collectors.meetup.normalize_event", return_value=None):
            await collect_meetup_events("https://www.meetup.com/find/?location=providence--ri", 1)
        mock_render.assert_called_once_with(
            "https://www.meetup.com/find/?location=providence--ri",
            force_playwright=True
        )

@pytest.mark.asyncio
async def test_collect_skips_non_event_jsonld():
    html = """
    <html><body>
    <script type="application/ld+json">{"@type": "Organization", "name": "Meetup"}</script>
    </body></html>
    """
    with patch("src.collectors.meetup.get_rendered_html", new_callable=AsyncMock) as mock_render:
        mock_render.return_value = html
        events = await collect_meetup_events("https://www.meetup.com/find/", 1)
    assert events == []
```

---

### Integration Tests

**`tests/integration/test_pipeline_playwright.py`** [NEW]

These tests use saved HTML fixtures instead of live network calls. They verify the full path from raw HTML → normalized Event with correct times.

```python
# What to test:
# 1. A saved mbadrivein.com event page (JS-rendered fixture) produces an event
#    with a non-midnight start_datetime after the full pipeline runs.
# 2. A saved Meetup search page fixture produces at least one Event with
#    city, state, and start_datetime populated.
# 3. When start_datetime is midnight AND LLM_FALLBACK_ENABLED=True, the
#    LLM fallback is called exactly once per affected event.
# 4. When LLM_FALLBACK_ENABLED=False, the fallback is never called regardless
#    of midnight start times.

# HOW TO CREATE FIXTURES:
# Run this once manually and commit the output to tests/integration/fixtures/:
#
#   from src.collectors.renderer import get_rendered_html
#   import asyncio, pathlib
#   html = asyncio.run(get_rendered_html("https://mbadrivein.com/events", force_playwright=True))
#   pathlib.Path("tests/integration/fixtures/mbadrivein_event.html").write_text(html)
#
#   html = asyncio.run(get_rendered_html(
#       "https://www.meetup.com/find/?location=providence--ri&source=EVENTS",
#       force_playwright=True))
#   pathlib.Path("tests/integration/fixtures/meetup_event_page.html").write_text(html)

import pytest
from unittest.mock import AsyncMock, patch
from src.collectors.meetup import collect_meetup_events

@pytest.mark.asyncio
async def test_meetup_fixture_produces_events():
    fixture_html = open("tests/integration/fixtures/meetup_event_page.html").read()
    with patch("src.collectors.meetup.get_rendered_html", return_value=fixture_html):
        events = await collect_meetup_events(
            "https://www.meetup.com/find/?location=providence--ri", source_id=99
        )
    assert len(events) > 0
    for e in events:
        assert e.city is not None
        assert e.start_datetime.year >= 2026

@pytest.mark.asyncio
async def test_llm_fallback_called_for_midnight_events(mock_config):
    # mock_config has LLM_FALLBACK_ENABLED=True
    # Load a fixture page known to produce midnight times without Playwright
    fixture_html = open("tests/integration/fixtures/mbadrivein_event.html").read()
    with patch("src.services.llm_fallback.extract_missing_time", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = "7:30 PM"
        # ... run the full normalization pipeline with the fixture HTML
        # assert that mock_llm was called and the resulting event has hour=19
        pass  # fill in with your pipeline's actual call signature

@pytest.mark.asyncio
async def test_llm_fallback_skipped_when_disabled(mock_config):
    # mock_config has LLM_FALLBACK_ENABLED=False
    with patch("src.services.llm_fallback.extract_missing_time", new_callable=AsyncMock) as mock_llm:
        # ... run pipeline
        mock_llm.assert_not_called()
```

---

### Existing Unit Tests to Update [CHANGE NEEDED]

The MVP tests for normalization need one new case: verify that a `start_datetime` at 00:00 with `LLM_FALLBACK_ENABLED=True` triggers the fallback. If your existing normalization tests mock at the `requests.get` level, update them to mock `get_rendered_html` instead.

---

## 11. Configuration

### [CHANGE NEEDED] `.env` / `.env.example`

Add these variables:

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
GOOGLE_SHEET_URL=https://docs.google.com/spreadsheets/d/...

# Event Extraction
EVENT_LOOKAHEAD_DAYS=60
LLM_FALLBACK_ENABLED=true

# [NEW] OpenAI — used only for targeted time/field extraction fallback
OPENAI_API_KEY=sk-...

# [NEW] Comma-separated domains that always use Playwright (no shell detection)
# Add any site where JS rendering is required and shell detection is unreliable.
PLAYWRIGHT_FORCE_DOMAINS=mbadrivein.com

# [NEW] Meetup search locations — added to config/discovery_sources.yaml
# See Section 4 (Meetup Collector) for the CSV format.

# Storage
DATABASE_URL=sqlite:///./events.db

# Logging
LOG_LEVEL=INFO
```

### [CHANGE NEEDED] `requirements.txt`

Add:

```
playwright>=1.44.0
openai>=1.30.0
httpx>=0.27.0      # replaces requests in renderer.py for async compatibility
```

Run after adding:
```bash
playwright install chromium
```

---

## 12. Deliverables Sequence

### Updated Phase Order

1. **Phase 1: Foundation** *(unchanged)*

2. **Phase 2: Collection** *(updated)*
   - Source loader
   - Collector orchestration
   - **[NEW]** `renderer.py` — Playwright rendering layer
   - **[NEW]** `meetup.py` — Meetup-specific collector
   - Extraction strategies (structured metadata, generic parsing)
   - Event normalization
   - Region tagging

3. **Phase 3: Normalization & Dedup** *(updated)*
   - Canonical key generation
   - Fuzzy matching logic
   - Merge strategy
   - **[NEW]** `llm_fallback.py` — targeted OpenAI time/field extraction

4. **Phase 4: Digest & Delivery** *(unchanged)*

5. **Phase 5: Scheduling & API** *(updated)*
   - **[CHANGE NEEDED]** GitHub Actions workflow — add Playwright install step + OPENAI_API_KEY secret
   - Minimal REST API for testing
   - CLI entry point

6. **Phase 6: Tests & Documentation** *(updated)*
   - **[NEW]** Unit tests: renderer, llm_fallback, meetup_collector
   - **[NEW]** Integration tests with saved HTML fixtures
   - **[CHANGE NEEDED]** Update existing normalization tests to mock `get_rendered_html`
   - README with setup instructions
   - Example .env file
   - Sample digest output (mock data)

---

## Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| Option A: `recurrence_rule` field on events | Most sources don't expose structured recurrence; MVP handles individual occurrences. Can expand later without schema changes. |
| Skip `/digests/preview`, `/calendar-link`, `/regions/summary` endpoints | Not needed for MVP; dry-run mode in CLI and inline calendar link generation are sufficient. |
| Storage repository pattern | Allows swapping SQLite ↔ Postgres without changing business logic. |
| Canonical key fingerprinting | Deterministic and stable across runs; enables reliable deduplication. |
| Structured logging from the start | Essential for debugging scraper failures and email delivery issues in production. |
| **[NEW]** Playwright falls back from `requests`, not replaces it | `requests` is fast and free; Playwright only runs when shell detection fires or `force_playwright=True`. Keeps GitHub Actions runtime short. |
| **[NEW]** LLM fallback fires only on midnight default times | Calling the LLM for every event wastes tokens. Triggering only when `hour == 0 and minute == 0` after parsing limits calls to events that genuinely failed time extraction. |
| **[NEW]** Single combined LLM call when 2+ fields missing | One call for time + cost + venue (max 60 output tokens) is cheaper than three separate calls. Single-field miss uses the even cheaper single-field prompt (max 20 tokens). |
| **[NEW]** Meetup via Playwright + JSON-LD, not Apify | Meetup embeds schema.org JSON-LD on rendered pages. No actor fees, no API key, no third-party dependency. Same extraction path as other structured sources. |
| **[NEW]** Meetup locations configured in config/discovery_sources.yaml | Keeps all source management in one place.  |
| **[NEW]** Integration tests use saved HTML fixtures | Prevents flaky tests caused by live site changes. Fixtures should be refreshed manually when sites redesign. |

---

## Notes

- Assume Python 3.12 async/await throughout for scalability
- Use Pydantic for schema validation
- Prefer SQLite initially; Postgres is a drop-in replacement via repository interface
- No database migrations needed for MVP (simple schema); add Alembic if schema evolves
- LLM extraction is a fallback only; prioritize structured metadata and DOM parsing
- **[NEW]** Playwright Chromium binary is ~130 MB. It is installed fresh on each GitHub Actions run via `playwright install chromium --with-deps`. This adds ~90 seconds to the workflow but keeps the repo clean.
- **[NEW]** OpenAI costs for the LLM fallback are negligible at this scale: ~$0.01/week assuming 50–100 fallback calls at gpt-4o-mini rates. Monitor with `LOG_LEVEL=DEBUG` to see how often the fallback fires; if it's triggering on events that already have correct times, tighten the midnight-detection heuristic.
