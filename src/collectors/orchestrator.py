"""Collector orchestration."""

import logging
from datetime import datetime

from src.models import Source, ScrapeRun
from src.collectors.generic import GenericCollector
from src.storage.repository import StorageRepository
from src.services.normalization import apply_region_tags
from src.services.deduplication import generate_canonical_key

logger = logging.getLogger(__name__)


class CollectorOrchestrator:
    """Orchestrates collection from multiple sources."""

    def __init__(self, storage: StorageRepository):
        """Initialize orchestrator."""
        self.storage = storage

    async def collect_all(self) -> None:
        """Collect events from all enabled sources."""
        sources = await self.storage.list_sources(enabled_only=True)

        for source in sources:
            await self._collect_from_source(source)

    async def _collect_from_source(self, source: Source) -> None:
        """Collect events from a single source."""
        # Create scrape run record
        run = ScrapeRun(source_id=source.id or 0, status="in_progress")
        run_id = await self.storage.create_scrape_run(run)
        run.id = run_id

        logger.info(
            "Starting collection",
            extra={"source": source.source_name, "run_id": run_id},
        )

        try:
            # Collect events
            collector = GenericCollector(source)
            events = await collector.collect()

            # Apply region tagging and generate canonical keys
            for event in events:
                event = apply_region_tags(event)
                # Generate canonical key for deduplication
                event.canonical_key = generate_canonical_key(event)

            # Upsert events to storage
            new_count = 0
            updated_count = 0

            for event in events:
                existing = await self.storage.get_event_by_canonical_key(
                    event.canonical_key or ""
                )
                event_id = await self.storage.upsert_event(event)

                if existing:
                    updated_count += 1
                else:
                    new_count += 1

            # Update scrape run
            run.status = "success"
            run.events_found = len(events)
            run.events_new = new_count
            run.events_updated = updated_count
            run.finished_at = datetime.utcnow()
            run.pages_crawled = 1  # TODO: Track actual page count

            logger.info(
                f"Completed collection: {len(events)} events ({new_count} new, {updated_count} updated)",
                extra={
                    "source": source.source_name,
                    "run_id": run_id,
                    "events_found": len(events),
                    "events_new": new_count,
                    "events_updated": updated_count,
                },
            )

        except Exception as e:
            logger.error(
                f"Collection failed: {str(e)}",
                extra={"source": source.source_name, "run_id": run_id},
            )
            run.status = "failed"
            run.error_summary = str(e)
            run.finished_at = datetime.utcnow()

        await self.storage.update_scrape_run(run)
