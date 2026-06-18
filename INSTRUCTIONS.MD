Build a Python application that generates a weekly email digest of upcoming events from a curated list of event websites.

## Goal

Every Sunday morning, I want to receive an email containing upcoming events from my list of websites.

The digest should:

* Group events by date.
* Within each date, group events by city/town.
* Show event title, time, location, cost (if available), source website, and event URL.
* Include a one-click "Add to Google Calendar" link for each event.
* Deduplicate events that appear on multiple websites.
* Focus on events occurring within the next 60 days.

This is the MVP. Do not implement calendar conflict detection, AI ranking, or travel calculations yet.

## Technical Requirements

### Language

Python 3.12

### Architecture

Use a modular architecture:

/src
/collectors
/models
/services
/email
/storage
/config

Design the code so additional event sources can easily be added later.

### Event Model

Create a normalized event model:

Event:

* title
* description
* start_datetime
* end_datetime (optional)
* venue_name
* region_tag
* city
* state
* address
* cost
* event_url
* source_id (foreign key to sources table)
* image_url (optional)

Store events internally in a consistent format regardless of source.

## Event Sources

Input should be a CSV or Google Sheet containing:

* source_name
* source_url
* source_type

Example:

URI Events, https://events.uri.edu, generic
South County Chamber, https://www.southcountychamber.com/events, generic

Build the framework so source-specific collectors can be added later.

## Event Extraction

Implement extraction strategies in this order:

1. Structured event metadata

   * schema.org Event
   * JSON-LD
   * microdata

2. Event listing pages

3. LLM-assisted extraction only as a fallback

Avoid relying on an LLM for every page.

For each source:

* Automatically classify events with a region_tag like: Westerly, South County (RI), Providence Metro, Aquidneck Island (RI), Boston, Connecticut, or Other
* Crawl a reasonable number of pages (look for 'events' page on the website).
* Extract future events only.
* Ignore past events.
* Log extraction failures.


## Deduplication

Deduplicate events using:

* title similarity
* date similarity
* venue or city/town/region similarity

Examples:

"Climate Summit 2026"
and
"2026 Climate Summit"

should likely be treated as one event.

Keep the best URL and richest metadata.

## Calendar Link Generation

For every event generate a Google Calendar "Add Event" URL.

The link should prepopulate:

* title
* date/time
* location
* description
* source URL

Do NOT require Google API access.

Use Google's URL-based event creation format.

## Storage

Use Postgres or SQLite- whatever is available with Github Actions.

Tables:

* sources
* events
* scrape_runs

Persist event history so the same event is not repeatedly treated as new.

## Email Digest

Generate an HTML email.

Format:

Sunday, July 12

Providence

• Housing Forum
6:00 PM
Free
Source: URI
[Event Page]
[Add to Calendar]

• PorchFest
7:00 PM
$20
Source: Eventbrite
[Event Page]
[Add to Calendar]

Newport

...

Include:

* total event count
* number of new events discovered this week
* generation timestamp

The email should be readable on mobile devices.

## Scheduling

Create a weekly scheduled job.

Assume deployment via GitHub Actions.

Provide:

* GitHub Actions workflow file
* cron schedule
* setup instructions

The workflow should:

1. Run scraper.
2. Update SQLite database.
3. Generate digest.
4. Send email.

## Email Delivery

Use a configurable provider.

Prefer:

1. SendGrid
2. SMTP

Store secrets in environment variables.

Do not hardcode credentials.

## Logging

Implement structured logging.

Log:

* source start/end
* events discovered
* extraction failures
* email delivery status

## Configuration

All configuration should be in a .env file.

Examples:

EMAIL_TO=
EMAIL_FROM=
SENDGRID_API_KEY=
GOOGLE_SHEET_URL=
EVENT_LOOKAHEAD_DAYS=60

## Deliverables

1. Full project structure.
2. Python source code.
3. requirements.txt.
4. Example .env.
5. GitHub Actions workflow.
6. README with deployment instructions.
7. Sample digest output using mock data.

Focus on simplicity, maintainability, and reliability rather than advanced AI features.
