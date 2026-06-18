"""Main entry point for the event digest application."""

import asyncio
import argparse
import logging
import json
import csv
from datetime import datetime, timedelta, timezone
from typing import Optional
from pathlib import Path

from src.config.settings import get_settings
from src.utils.logging import setup_logging, get_logger
from src.storage.sqlite_impl import SQLiteRepository
from src.collectors.orchestrator import CollectorOrchestrator
from src.services.digest import render_digest
from src.email.sendgrid_provider import SendGridProvider
from src.email.smtp_provider import SMTPProvider
from src.models import Source

logger = get_logger(__name__)


async def load_sources_from_csv(storage: SQLiteRepository, csv_path: str) -> None:
    """Load event sources from CSV file into the database."""
    csv_file = Path(csv_path)
    if not csv_file.exists():
        logger.warning(f"Sources CSV file not found: {csv_path}")
        return

    logger.info(f"Loading sources from {csv_path}")
    
    # Get existing sources to avoid duplicates
    existing_sources = await storage.list_sources(enabled_only=False)
    existing_urls = {s.source_url for s in existing_sources}
    
    loaded_count = 0
    try:
        with open(csv_file, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                if not row.get('source_url'):
                    continue
                    
                # Skip if already exists
                if row['source_url'] in existing_urls:
                    logger.debug(f"Source already exists: {row['source_name']}")
                    continue
                
                source = Source(
                    source_name=row.get('source_name', 'Unknown'),
                    source_url=row['source_url'],
                    source_type=row.get('source_type', 'generic'),
                    enabled=True
                )
                await storage.upsert_source(source)
                loaded_count += 1
                logger.info(f"Loaded source: {source.source_name}")
        
        logger.info(f"Loaded {loaded_count} new sources from CSV")
    except Exception as e:
        logger.error(f"Error loading sources from CSV: {str(e)}")
        raise

    



async def main(mode: str = "full", dry_run: bool = False):
    """Main entry point for the application.
    
    Args:
        mode: "full" (collect + digest + email), "collect" (collect only, print events)
        dry_run: If True, don't send email (for testing)
    """
    settings = get_settings()

    # Setup logging
    setup_logging(settings.log_level)

    logger.info("Starting event digest application", extra={"version": "0.1.0", "mode": mode})

    try:
        # Initialize storage
        storage = SQLiteRepository(settings.database_url.replace("sqlite:///", ""))

        # Load sources from CSV
        await load_sources_from_csv(storage, settings.sources_csv_path or "./sources.csv")

        # Collect events from all sources
        logger.info("Starting collection phase")
        orchestrator = CollectorOrchestrator(storage)
        await orchestrator.collect_all()

        if mode == "collect":
            # Print collected events and exit
            await collect_and_print(storage, settings)
            return

        # Generate digest
        logger.info("Generating digest")
        html, text = await render_digest(storage, settings.event_lookahead_days)

        if dry_run:
            logger.info("DRY RUN: Would send email (not actually sending)")
            print("\n" + "=" * 60)
            print("DIGEST PREVIEW (DRY RUN)")
            print("=" * 60)
            print(text)
            return

        # Send email
        logger.info("Sending digest email")
        await send_digest_email(settings, html, text)

        logger.info("Event digest completed successfully")

    except Exception as e:
        logger.error(f"Application error: {str(e)}")
        raise


async def collect_and_print(storage: SQLiteRepository, settings) -> None:
    """Collect and print events without sending email."""
    now = datetime.now(timezone.utc)
    end = now + timedelta(days=settings.event_lookahead_days)

    events = await storage.get_events_by_date_range(now, end)

    print("\n" + "=" * 80)
    print(f"COLLECTED EVENTS ({len(events)} total)")
    print("=" * 80)

    if not events:
        print(f"No events found in the next {settings.event_lookahead_days} days.")
        return

    # Group by date and city for readability
    from collections import defaultdict
    grouped = defaultdict(lambda: defaultdict(list))

    for event in events:
        date_key = event.start_datetime.date().isoformat()
        city_key = event.city
        grouped[date_key][city_key].append(event)

    # Print grouped events
    for date_str in sorted(grouped.keys()):
        date_obj = datetime.fromisoformat(date_str)
        date_formatted = date_obj.strftime("%A, %B %d, %Y")
        print(f"\n{date_formatted}")
        print("-" * 80)

        for city_name in sorted(grouped[date_str].keys()):
            city_events = grouped[date_str][city_name]
            print(f"\n  {city_name}")

            for event in city_events:
                time_str = event.start_datetime.strftime("%I:%M %p")
                print(f"    • {event.title}")
                print(f"      Time: {time_str}")
                print(f"      Location: {event.venue_name or event.city}, {event.state}")
                print(f"      Cost: {event.cost or 'TBD'}")
                print(f"      URL: {event.event_url}")
                if event.description:
                    print(f"      Description: {event.description[:100]}...")

    print("\n" + "=" * 80)


async def send_digest_email(settings, html: str, text: str) -> None:
    """Send the digest email using configured provider."""
    subject = f"Weekly Event Digest - {datetime.now(timezone.utc).strftime('%B %d, %Y')}"

    # Select provider
    if settings.email_provider == "sendgrid":
        if not settings.sendgrid_api_key:
            raise ValueError("SENDGRID_API_KEY not configured")

        provider = SendGridProvider(settings.sendgrid_api_key, settings.email_from)

    elif settings.email_provider == "smtp":
        if not (settings.smtp_host and settings.smtp_user and settings.smtp_password):
            raise ValueError("SMTP configuration not complete")

        provider = SMTPProvider(
            settings.smtp_host,
            settings.smtp_port,
            settings.smtp_user,
            settings.smtp_password,
            settings.email_from,
        )
    else:
        raise ValueError(f"Unknown email provider: {settings.email_provider}")

    # Send email
    success = await provider.send(settings.email_to, subject, html, text)

    if not success:
        raise RuntimeError("Failed to send email")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Weekly event digest generator")
    parser.add_argument(
        "--mode",
        choices=["full", "collect"],
        default="full",
        help="Mode: 'full' (collect + digest + email), 'collect' (collect only, print events)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Don't send email, just preview the digest",
    )
    parser.add_argument(
        "--mock-data",
        action="store_true",
        help="Load mock events for testing (useful for demo without real data)",
    )

    args = parser.parse_args()
    asyncio.run(main(mode=args.mode, dry_run=args.dry_run))
