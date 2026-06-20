"""Tests for Apify integration components."""

import asyncio
import pytest
from datetime import datetime
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from pathlib import Path
import yaml

from src.models import Event, Source, ScrapeRun
from src.services.apify_client import ApifyClient, ApifyClientError
from src.collectors.discovery_provider import DiscoveryProvider
from src.collectors.facebook_apify_collector import FacebookEventsProvider
from src.collectors.meetup_apify_collector import MeetupProvider
from src.collectors.eventbrite_apify_collector import EventbriteProvider
from src.config.discovery_settings import load_discovery_config, SearchConfig, ProviderConfig, DiscoveryConfig, CostControls


class TestApifyClient:
    """Test Apify API client."""

    @pytest.mark.asyncio
    async def test_client_initialization(self):
        """Test client initialization."""
        client = ApifyClient(
            api_token="test_token",
            timeout_seconds=300,
            max_retries=3
        )
        assert client.api_token == "test_token"
        assert client.timeout_seconds == 300
        assert client.max_retries == 3
        assert client.base_url == "https://api.apify.com/v2"

    @pytest.mark.asyncio
    async def test_start_actor_run(self):
        """Test starting an actor run via official async client."""
        client = ApifyClient(api_token="test_token")

        actor_client = Mock()
        actor_client.call = AsyncMock(return_value={"id": "run_123"})

        official_client = Mock()
        official_client.actor = Mock(return_value=actor_client)
        client._official_client = official_client

        run_id = await client.start_actor_run("actor_123", {"key": "value"})

        assert run_id == "run_123"
        official_client.actor.assert_called_once_with("actor_123")
        actor_client.call.assert_awaited_once_with(run_input={"key": "value"})

    @pytest.mark.asyncio
    async def test_get_run_status(self):
        """Test getting run status via official async client."""
        client = ApifyClient(api_token="test_token")

        run_client = Mock()
        run_client.get = AsyncMock(return_value={
            "id": "run_123",
            "status": "SUCCEEDED",
            "defaultDatasetId": "dataset_123",
        })

        official_client = Mock()
        official_client.run = Mock(return_value=run_client)
        client._official_client = official_client

        status = await client.get_run_status("run_123")

        assert status["status"] == "SUCCEEDED"
        assert status["defaultDatasetId"] == "dataset_123"

    @pytest.mark.asyncio
    async def test_get_dataset(self):
        """Test getting dataset items via official async client."""
        client = ApifyClient(api_token="test_token")

        dataset_result = Mock()
        dataset_result.items = [
            {"title": "Event 1", "startDate": "2024-06-01"},
            {"title": "Event 2", "startDate": "2024-06-02"},
        ]

        dataset_client = Mock()
        dataset_client.list_items = AsyncMock(return_value=dataset_result)

        official_client = Mock()
        official_client.dataset = Mock(return_value=dataset_client)
        client._official_client = official_client

        items = await client.get_dataset("dataset_123")

        assert len(items) == 2
        assert items[0]["title"] == "Event 1"


class TestFacebookEventsProvider:
    """Test Facebook Events provider."""

    def test_provider_initialization(self):
        """Test provider initialization."""
        mock_client = Mock()
        provider = FacebookEventsProvider(mock_client)
        
        assert provider.name == "facebook_events"
        assert provider.apify_client == mock_client


class TestMeetupProvider:
    """Test Meetup provider."""

    def test_provider_initialization(self):
        """Test provider initialization."""
        mock_client = Mock()
        provider = MeetupProvider(mock_client)
        
        assert provider.name == "meetup"
        assert provider.apify_client == mock_client


class TestEventbriteProvider:
    """Test Eventbrite provider."""

    def test_provider_initialization(self):
        """Test provider initialization."""
        mock_client = Mock()
        provider = EventbriteProvider(mock_client)
        
        assert provider.name == "eventbrite"
        assert provider.apify_client == mock_client

    @pytest.mark.asyncio
    async def test_raw_to_event_handles_dict_venue(self):
        """Test Eventbrite venue payloads where venue is returned as a dict."""
        provider = EventbriteProvider(Mock())
        source = Source(id=1, source_name="Providence Events", source_url="https://example.com", source_type="eventbrite")

        raw_item = {
            "title": "Test Event",
            "startDate": "2026-07-26",
            "startDateTime": "2026-07-26T12:00",
            "endDate": "2026-07-26",
            "endDateTime": "2026-07-26T18:00",
            "venue": {
                "name": "Test Venue",
                "address": "123 Main St, Providence, RI",
                "city": "Providence",
                "state": "RI",
            },
            "url": "https://example.com/event",
        }

        event = provider._raw_to_event(raw_item, source)

        assert event is not None
        assert event.start_datetime.hour == 12
        assert event.start_datetime.minute == 0
        assert event.end_datetime is not None
        assert event.end_datetime.hour == 18
        assert event.venue_name == "Test Venue"
        assert event.city == "Providence"
        assert event.state == "RI"
        assert event.address == "123 Main St, Providence, RI"


class TestDiscoveryConfigLoading:
    """Test discovery configuration loading."""

    def test_load_discovery_config_from_yaml(self, tmp_path):
        """Test loading discovery configuration from YAML."""
        config_data = {
            "cost_controls": {
                "max_runs_per_week": 100,
                "max_events_per_source": 100,
                "max_total_events_per_run": 500
            },
            "facebook_events": {
                "enabled": True,
                "actor_id": "apify/facebook-events-scraper",
                "search_configs": [
                    {
                        "name": "Save The Bay",
                        "start_url": "https://facebook.com/SaveTheBayRI",
                        "max_events": 50
                    }
                ]
            }
        }
        
        config_file = tmp_path / "discovery_sources.yaml"
        with open(config_file, 'w') as f:
            yaml.dump(config_data, f)
        
        config = load_discovery_config(str(config_file))
        
        assert config.cost_controls.max_runs_per_week == 100
        assert config.facebook_events.enabled is True
        assert len(config.facebook_events.search_configs) == 1

    def test_load_discovery_config_missing_file(self, tmp_path):
        """Test loading discovery configuration from missing file returns defaults."""
        config_file = tmp_path / "nonexistent.yaml"
        
        config = load_discovery_config(str(config_file))
        
        # Should return default config, not raise
        assert isinstance(config, DiscoveryConfig)
        assert config.cost_controls.max_runs_per_week == 50

    def test_search_config_dataclass(self):
        """Test SearchConfig dataclass."""
        search = SearchConfig(
            name="Test Search",
            start_url="https://example.com",
            search_term="test",
            location="Providence, RI",
            max_events=50,
            days_ahead=30
        )
        
        assert search.name == "Test Search"
        assert search.max_events == 50
        
        # Test to_dict method
        d = search.to_dict()
        assert d["name"] == "Test Search"
        assert "start_url" in d


class TestScrapeRunModel:
    """Test ScrapeRun model with external fields."""

    def test_scrape_run_external_fields(self):
        """Test ScrapeRun has external fields."""
        run = ScrapeRun(
            source_id=1,
            status="success",
            events_found=10,
            events_new=8,
            events_updated=2,
            external_run_id="apify_run_123",
            external_platform="facebook_events"
        )
        
        assert run.external_run_id == "apify_run_123"
        assert run.external_platform == "facebook_events"

    def test_scrape_run_external_fields_optional(self):
        """Test ScrapeRun external fields are optional."""
        run = ScrapeRun(
            source_id=1,
            status="success",
            events_found=0
        )
        
        assert run.external_run_id is None
        assert run.external_platform is None


class TestOrchestratorIntegration:
    """Test CollectorOrchestrator with Apify integration."""

    @pytest.mark.asyncio
    async def test_orchestrator_initialization_with_apify(self):
        """Test orchestrator initialization with Apify client."""
        from src.collectors.orchestrator import CollectorOrchestrator
        
        mock_storage = Mock()
        mock_client = Mock()
        discovery_config = {
            "facebook_events": {
                "search_configs": []
            }
        }
        
        orchestrator = CollectorOrchestrator(mock_storage, mock_client, discovery_config)
        
        assert orchestrator.apify_client == mock_client
        assert orchestrator.discovery_config == discovery_config

    @pytest.mark.asyncio
    async def test_get_search_config_for_source(self):
        """Test retrieving search config for source."""
        from src.collectors.orchestrator import CollectorOrchestrator
        
        mock_storage = Mock()
        discovery_config = {
            "providers": {
                "facebook_events": {
                    "search_configs": [
                        {"name": "Save The Bay", "start_url": "https://facebook.com/SaveTheBayRI"}
                    ]
                }
            }
        }
        
        orchestrator = CollectorOrchestrator(mock_storage, Mock(), discovery_config)
        
        source = Source(
            id=1,
            source_name="Save The Bay",
            source_url="https://facebook.com/SaveTheBayRI",
            source_type="facebook_events"
        )
        
        config = orchestrator._get_search_config_for_source(source)
        
        assert config is not None
        assert config["name"] == "Save The Bay"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
