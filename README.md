# Weekly Event Digest

A Python 3.12 application that scrapes event websites, deduplicates events, and generates a weekly HTML email digest.

## Features

- **Automated Scraping**: Collects events from configured sources using multiple extraction strategies
- **Structured Metadata Priority**: Leverages schema.org JSON-LD and microdata before falling back to DOM parsing
- **Smart Deduplication**: Identifies the same event across multiple sources using fuzzy matching
- **Email Digest**: Generates a mobile-friendly HTML email grouped by date and city
- **Calendar Integration**: Includes one-click "Add to Google Calendar" links
- **GitHub Actions Ready**: Deploy with a single workflow file
- **Configurable**: All settings via `.env` file

## Requirements

- Python 3.12+
- SQLite or Postgres
- SendGrid API key OR SMTP credentials
- GitHub account (for scheduling)

## Installation

### 1. Clone or download this repository

```bash
cd scheduling
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure environment

Copy `.env.example` to `.env` and configure:

```bash
cp .env.example .env
```

Edit `.env` with your settings:

```env
EMAIL_TO=your-email@example.com
EMAIL_FROM=digest@yourdomain.com
EMAIL_PROVIDER=sendgrid
SENDGRID_API_KEY=sg_your_key_here
EVENT_LOOKAHEAD_DAYS=60
LOG_LEVEL=INFO
```

### 5. Run locally (optional)

```bash
python -m src.main --dry-run
```
usage: main.py [-h] [--mode {full,collect}] [--dry-run] [--mock-data]

## GitHub Actions Setup

### 1. Create repository secrets

In your GitHub repository settings, add these secrets:

- `EMAIL_TO` - recipient email
- `EMAIL_FROM` - sender email
- `EMAIL_PROVIDER` - "sendgrid" or "smtp"
- `SENDGRID_API_KEY` - SendGrid API key (if using SendGrid)
- `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD` - (if using SMTP)

### 2. Commit workflow file

The workflow file is already in `.github/workflows/weekly-digest.yml`. It runs every Sunday at 10:00 AM UTC.

To change the schedule, edit the cron expression:

```yaml
schedule:
  - cron: '0 10 * * 0'  # Change the time here
```

### 3. Enable GitHub Actions

Push your repository to GitHub and enable Actions in the repository settings.

## Architecture

```
src/
  config/       # Configuration loading
  collectors/   # Event extraction strategies
  models/       # Pydantic data models
  services/     # Business logic (dedup, digest, calendar links)
  email/        # Email delivery providers
  storage/      # Database layer
  utils/        # Logging and helpers
  main.py       # Entry point
```

## How It Works

### 1. Collection Phase

- Fetches sources from `sources.csv`
- For each enabled source:
  - Crawls the source website
  - Tries extraction strategies in order:
    1. Structured metadata (JSON-LD, schema.org)
    2. Generic event listing page parsing
    3. LLM-assisted extraction (fallback)
  - Normalizes dates, locations, and costs
  - Applies region tagging (Westerly, South County, etc.)

### 2. Deduplication Phase

- Generates canonical fingerprints from: title + date + city + venue
- Identifies duplicate events across sources
- Keeps the richest metadata from duplicates

### 3. Digest Generation

- Filters for events in the next 60 days (configurable)
- Groups by date, then by city
- Renders mobile-friendly HTML with:
  - Event title, time, location, cost
  - Source website
  - Event page link
  - "Add to Google Calendar" link

### 4. Email Delivery

- Sends via SendGrid (primary) or SMTP
- Includes HTML and plain text versions
- Includes event count and generation timestamp

## Database

Events are stored in SQLite by default. The schema includes:

- **sources** - Event source configurations
- **events** - Normalized events (deduplicated)
- **scrape_runs** - Scraping history and statistics

For production, Postgres is a drop-in replacement: set `DATABASE_URL=postgresql://...`

## Example Output

```
Sunday, July 12

Providence

• Housing Forum
6:00 PM
Free
Source: URI
[Event Page] [Add to Calendar]

• PorchFest
7:00 PM
$20
Source: Eventbrite
[Event Page] [Add to Calendar]

Newport

• Art Walk
10:00 AM
Free
Source: South County Chamber
[Event Page] [Add to Calendar]
```

## Logging

All operations are logged in JSON format to stdout:

```json
{"timestamp": "2026-06-18T10:00:00", "level": "INFO", "logger": "src.collectors.orchestrator", "message": "Starting collection", "extra_fields": {"source": "URI Events"}}
```

## Configuration Reference

| Variable | Default | Description |
|----------|---------|-------------|
| EMAIL_TO | - | Recipient email address |
| EMAIL_FROM | - | Sender email address |
| EMAIL_PROVIDER | sendgrid | Email provider: `sendgrid` or `smtp` |
| SENDGRID_API_KEY | - | SendGrid API key (if using SendGrid) |
| SMTP_HOST | - | SMTP hostname (if using SMTP) |
| SMTP_PORT | 587 | SMTP port (if using SMTP) |
| SMTP_USER | - | SMTP username (if using SMTP) |
| SMTP_PASSWORD | - | SMTP password (if using SMTP) |
| EVENT_LOOKAHEAD_DAYS | 60 | Days ahead to include in digest |
| DATABASE_URL | sqlite:///./events.db | Database connection URL |
| LOG_LEVEL | INFO | Logging level: DEBUG, INFO, WARNING, ERROR |

## Extending with New Sources

To add a new event source:

1. Add a row to `sources.csv`
2. The generic collector will attempt to extract events
3. If you need a specialized collector:
   - Create a new class in `src/collectors/`
   - Inherit from `EventCollector`
   - Implement extraction strategies specific to that source

## MVP Limitations

This MVP focuses on simplicity and reliability:

- ❌ No calendar conflict detection
- ❌ No AI event ranking
- ❌ No travel time calculations
- ✅ Simple, maintainable codebase
- ✅ Structured metadata extraction prioritized
- ✅ Async/await throughout for performance

## Troubleshooting

### No events extracted

Check logs for extraction failures:

```bash
LOG_LEVEL=DEBUG python -m src.main
```

Verify that event sources have structured metadata (JSON-LD) or ensure generic DOM selectors apply.

### Email not sending

- Verify credentials in `.env`
- Check SendGrid API key or SMTP settings
- Review logs for error messages

### Database errors

Delete `events.db` to reset the database:

```bash
rm events.db
python -m src.main
```

## Future Enhancements

- Recurring event expansion from RRULE
- Web dashboard for viewing events
- Calendar conflict detection
- Travel time optimization
- LLM-powered event ranking
- User preferences and filtering

## License

MIT

## Support

For issues or questions, check the logs and verify your `.env` configuration.
