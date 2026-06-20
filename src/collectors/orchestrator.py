"""Collector orchestration."""

import logging
from datetime import datetime
from typing import Optional, Dict, Any, List

from src.models import Source, ScrapeRun
from src.collectors.generic import GenericCollector
from src.storage.repository import StorageRepository
from src.services.normalization import apply_region_tags
from src.services.deduplication import generate_canonical_key
from src.config.discovery_settings import DiscoveryConfig

logger = logging.getLogger(__name__)


class CollectorOrchestrator:
    """Orchestrates collection from multiple sources."""

    def __init__(self, storage: StorageRepository, apify_client: Optional[Any] = None, discovery_config: Optional[Any] = None):
        """Initialize orchestrator."""
        self.storage = storage
        self.apify_client = apify_client
        self.discovery_config = discovery_config

    async def collect_all(self) -> None:
        """Collect events from discovery config sources.

        Discovery configuration is the source of truth. Sources are upserted
        into storage so scrape runs and events still retain source references.
        """
        sources = await self._build_sources_from_discovery_config()
        for source in sources:
            await self._collect_from_source(source)

    async def _build_sources_from_discovery_config(self) -> List[Source]:
        """Build enabled sources from discovery config and upsert into storage."""
        if not self.discovery_config:
            raise ValueError("Discovery config is required; source list no longer comes from sources.csv or sources table")

        discovered_sources: List[Source] = []

        # Dataclass config path (preferred)
        if isinstance(self.discovery_config, DiscoveryConfig):
            for configured in self.discovery_config.sources:
                if not configured.enabled:
                    continue
                discovered_sources.append(
                    Source(
                        source_name=configured.source_name,
                        source_url=configured.source_url,
                        source_type=configured.source_type,
                        enabled=configured.enabled,
                    )
                )
        elif isinstance(self.discovery_config, dict):
            # Backward-compatible dict path
            for source_data in self.discovery_config.get("sources", []):
                if not source_data.get("enabled", True):
                    continue
                discovered_sources.append(
                    Source(
                        source_name=source_data["source_name"],
                        source_url=source_data["source_url"],
                        source_type=source_data["source_type"],
                        enabled=source_data.get("enabled", True),
                    )
                )

        if not discovered_sources:
            raise ValueError("No enabled sources found in discovery config")

        # De-duplicate by (type, url)
        unique_sources: Dict[tuple[str, str], Source] = {}
        for source in discovered_sources:
            unique_sources[(source.source_type, source.source_url)] = source

        sources = list(unique_sources.values())

        # Upsert to storage and attach ids.
        existing = await self.storage.list_sources(enabled_only=False)
        existing_by_url: Dict[str, Source] = {s.source_url: s for s in existing}

        for source in sources:
            existing_source = existing_by_url.get(source.source_url)
            if existing_source:
                source.id = existing_source.id
            source_id = await self.storage.upsert_source(source)
            source.id = source_id

        logger.info("Prepared sources from discovery config", extra={"sources_count": len(sources)})
        return sources

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
            # Route based on source type
            events = []
            external_run_id = None
            external_platform = None

            if source.source_type == "facebook_events" and self.apify_client and self.discovery_config:
                from src.collectors.facebook_apify_collector import FacebookEventsProvider
                provider = FacebookEventsProvider(self.apify_client)
                search_config = self._get_search_config_for_source(source)
                if search_config:
                    events = await provider.discover(source, search_config)
                    external_platform = "facebook_events"

            elif source.source_type == "meetup" and self.apify_client and self.discovery_config:
                from src.collectors.meetup_apify_collector import MeetupProvider
                provider = MeetupProvider(self.apify_client)
                search_config = self._get_search_config_for_source(source)
                if search_config:
                    events = await provider.discover(source, search_config)
                    external_platform = "meetup"

            elif source.source_type == "eventbrite" and self.apify_client and self.discovery_config:
                from src.collectors.eventbrite_apify_collector import EventbriteProvider
                provider = EventbriteProvider(self.apify_client)
                search_config = self._get_search_config_for_source(source)
                if search_config:
                    events = await provider.discover(source, search_config)
                    external_platform = "eventbrite"

            else:
                # Use generic collector for "generic" type or fallback
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
            run.external_platform = external_platform
            run.external_run_id = external_run_id

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

    def _get_search_config_for_source(self, source: Source) -> Optional[Dict[str, Any]]:
        """Get search configuration for a source from discovery config."""
        if not self.discovery_config:
            return None

        source_type_map = {
            "facebook_events": "facebook_events",
            "meetup": "meetup",
            "eventbrite": "eventbrite",
        }

        provider_key = source_type_map.get(source.source_type)
        if not provider_key:
            return None

        # discovery_config may be a dict (old format) or a dataclass (DiscoveryConfig).
        searches: list = []

        # Case 1: dict-style config with 'providers' key
        if isinstance(self.discovery_config, dict):
            provider_config = self.discovery_config.get("providers", {}).get(provider_key) or self.discovery_config.get(provider_key)
            if provider_config:
                # provider_config may already be a dict
                searches = provider_config.get("search_configs", []) if isinstance(provider_config, dict) else getattr(provider_config, "search_configs", [])

        else:
            # dataclass-style DiscoveryConfig: attributes like facebook_events, meetup, eventbrite
            provider_obj = getattr(self.discovery_config, provider_key, None)
            if provider_obj:
                raw_searches = getattr(provider_obj, "search_configs", [])
                # Convert dataclass SearchConfig objects to plain dicts for uniform handling
                for s in raw_searches:
                    if hasattr(s, "to_dict"):
                        searches.append(s.to_dict())
                    elif hasattr(s, "__dict__"):
                        searches.append({k: v for k, v in s.__dict__.items() if v is not None})
                    else:
                        searches.append(s)

        # Try to match by source name or return first config
        for search in searches:
            name = search.get("name") if isinstance(search, dict) else getattr(search, "name", None)
            if name == source.source_name:
                if isinstance(search, dict):
                    # Include provider-level actor settings for downstream provider calls.
                    provider_cfg = None
                    if isinstance(self.discovery_config, dict):
                        provider_cfg = self.discovery_config.get(provider_key, {})
                    else:
                        provider_cfg = getattr(self.discovery_config, provider_key, None)

                    if provider_cfg:
                        actor_id = provider_cfg.get("actor_id") if isinstance(provider_cfg, dict) else getattr(provider_cfg, "actor_id", None)
                        actor_build = provider_cfg.get("actor_build") if isinstance(provider_cfg, dict) else getattr(provider_cfg, "actor_build", None)
                        if actor_id and "actor_id" not in search:
                            search["actor_id"] = actor_id
                        if actor_build and "actor_build" not in search:
                            search["actor_build"] = actor_build
                return search

        # Return first search config if available
        return searches[0] if searches else None
