"""Discovery source configuration loading."""

import logging
from pathlib import Path
from typing import Optional, Dict, Any, List
from urllib.parse import quote_plus
import yaml
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class SearchConfig:
    """Configuration for a discovery search."""
    name: str
    max_events: int = 50
    days_ahead: int = 30
    start_url: Optional[str] = None
    search_term: Optional[str] = None
    location: Optional[str] = None
    category: Optional[str] = None
    categories: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary, omitting None values."""
        return {k: v for k, v in self.__dict__.items() if v is not None}


@dataclass
class ProviderConfig:
    """Configuration for a discovery provider."""
    enabled: bool = False
    actor_id: Optional[str] = None
    actor_build: Optional[str] = None
    timeout_seconds: int = 300
    max_retries: int = 3
    search_configs: List[SearchConfig] = field(default_factory=list)


@dataclass
class DiscoverySource:
    """A source entry used by orchestrator collection."""

    source_name: str
    source_url: str
    source_type: str
    enabled: bool = True


@dataclass
class CostControls:
    """Global cost control settings."""
    max_runs_per_week: int = 50
    max_events_per_source: int = 50
    max_total_events_per_run: int = 200


@dataclass
class DiscoveryConfig:
    """Complete discovery configuration."""
    cost_controls: CostControls = field(default_factory=CostControls)
    facebook_events: ProviderConfig = field(default_factory=ProviderConfig)
    meetup: ProviderConfig = field(default_factory=ProviderConfig)
    eventbrite: ProviderConfig = field(default_factory=ProviderConfig)
    sources: List[DiscoverySource] = field(default_factory=list)
    source_priority: Dict[str, int] = field(default_factory=dict)


def _validate_search_config(provider_name: str, search: SearchConfig) -> None:
    if not search.name or not search.name.strip():
        raise ValueError(f"{provider_name}.search_configs contains an entry with empty 'name'")

    if search.max_events <= 0:
        raise ValueError(f"{provider_name}.search_configs[{search.name}] has invalid 'max_events': {search.max_events}")

    if search.days_ahead <= 0:
        raise ValueError(f"{provider_name}.search_configs[{search.name}] has invalid 'days_ahead': {search.days_ahead}")

    if provider_name in {"facebook_events", "eventbrite"} and not search.start_url:
        raise ValueError(f"{provider_name}.search_configs[{search.name}] requires 'start_url'")

    if provider_name == "eventbrite":
        if search.categories:
            raise ValueError(
                f"eventbrite.search_configs[{search.name}] must use single 'category' instead of 'categories'"
            )
        if not search.category:
            raise ValueError(
                f"eventbrite.search_configs[{search.name}] requires single 'category'"
            )

    if provider_name == "meetup" and not (search.start_url or (search.search_term and search.location)):
        raise ValueError(
            f"meetup.search_configs[{search.name}] requires either 'start_url' or both 'search_term' and 'location'"
        )


def _validate_provider_config(provider_name: str, provider: ProviderConfig) -> None:
    if provider.enabled and not provider.actor_id:
        raise ValueError(f"{provider_name}.enabled is true but actor_id is missing")


def _validate_source(source: DiscoverySource) -> None:
    if not source.source_name or not source.source_name.strip():
        raise ValueError("sources contains an entry with empty 'source_name'")
    if not source.source_url or not source.source_url.strip():
        raise ValueError(f"sources[{source.source_name}] has empty 'source_url'")
    if source.source_type not in {"generic", "facebook_events", "meetup", "eventbrite"}:
        raise ValueError(
            f"sources[{source.source_name}] has unsupported 'source_type': {source.source_type}"
        )


def load_discovery_config(config_path: str) -> DiscoveryConfig:
    """Load discovery configuration from YAML file.
    
    Args:
        config_path: Path to discovery_sources.yaml
        
    Returns:
        Parsed configuration
    """
    path = Path(config_path)
    
    if not path.exists():
        logger.warning(f"Discovery config not found at {config_path}, using defaults")
        return DiscoveryConfig()
    
    try:
        with open(path, "r") as f:
            data = yaml.safe_load(f) or {}
    except Exception as e:
        logger.error(f"Failed to load discovery config: {str(e)}")
        raise
    
    # Parse cost controls
    cost_data = data.get("cost_controls", {})
    cost_controls = CostControls(
        max_runs_per_week=cost_data.get("max_runs_per_week", 100),
        max_events_per_source=cost_data.get("max_events_per_source", 100),
        max_total_events_per_run=cost_data.get("max_total_events_per_run", 500),
    )
    
    # Parse provider configs
    def parse_provider(provider_name: str, provider_data: Dict[str, Any]) -> ProviderConfig:
        if not provider_data:
            return ProviderConfig()
        
        search_configs = []
        for search_data in provider_data.get("search_configs", []):
            search = SearchConfig(
                name=search_data.get("name", ""),
                max_events=search_data.get("max_events", 50),
                days_ahead=search_data.get("days_ahead", 30),
                start_url=search_data.get("start_url"),
                search_term=search_data.get("search_term"),
                location=search_data.get("location"),
                category=search_data.get("category"),
                categories=search_data.get("categories", []),
            )
            _validate_search_config(provider_name, search)
            search_configs.append(search)
        
        return ProviderConfig(
            enabled=provider_data.get("enabled", False),
            actor_id=provider_data.get("actor_id"),
            actor_build=provider_data.get("actor_build"),
            timeout_seconds=provider_data.get("timeout_seconds", 300),
            max_retries=provider_data.get("max_retries", 3),
            search_configs=search_configs,
        )
    
    facebook = parse_provider("facebook_events", data.get("facebook_events", {}))
    meetup = parse_provider("meetup", data.get("meetup", {}))
    eventbrite = parse_provider("eventbrite", data.get("eventbrite", {}))
    _validate_provider_config("facebook_events", facebook)
    _validate_provider_config("meetup", meetup)
    _validate_provider_config("eventbrite", eventbrite)

    # Parse explicit sources list (csv-equivalent format)
    sources: List[DiscoverySource] = []
    for source_data in data.get("sources", []):
        source = DiscoverySource(
            source_name=source_data.get("source_name", "").strip(),
            source_url=source_data.get("source_url", "").strip(),
            source_type=source_data.get("source_type", "").strip(),
            enabled=bool(source_data.get("enabled", True)),
        )
        _validate_source(source)
        sources.append(source)

    # Auto-add provider search configs as sources when they are not explicitly listed.
    explicit_by_name = {s.source_name for s in sources}

    def auto_source_url(provider_name: str, search: SearchConfig) -> str:
        if search.start_url:
            return search.start_url
        if provider_name == "meetup":
            query = quote_plus(search.search_term or search.name)
            location = quote_plus(search.location or "")
            return f"https://www.meetup.com/find/?keywords={query}&location={location}"
        return f"https://{provider_name}/{quote_plus(search.name)}"

    provider_map = {
        "facebook_events": facebook,
        "meetup": meetup,
        "eventbrite": eventbrite,
    }
    for provider_name, provider_cfg in provider_map.items():
        if not provider_cfg.enabled:
            continue
        for search in provider_cfg.search_configs:
            if search.name in explicit_by_name:
                continue
            sources.append(
                DiscoverySource(
                    source_name=search.name,
                    source_url=auto_source_url(provider_name, search),
                    source_type=provider_name,
                    enabled=True,
                )
            )
    
    source_priority = data.get("source_priority", {
        "generic": 100,
        "facebook_events": 80,
        "meetup": 70,
        "eventbrite": 60,
    })
    
    return DiscoveryConfig(
        cost_controls=cost_controls,
        facebook_events=facebook,
        meetup=meetup,
        eventbrite=eventbrite,
        sources=sources,
        source_priority=source_priority,
    )
