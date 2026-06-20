"""Apify client service for actor management."""

import logging
import asyncio
from typing import Optional, Any, Dict
from datetime import datetime

logger = logging.getLogger(__name__)


class ApifyClientError(Exception):
    """Apify client error."""
    pass


# Adapter: Prefer the official Apify Python client when available, otherwise
# fall back to lightweight httpx-based requests (previous implementation).
try:
    from apify_client import ApifyClientAsync as OfficialApifyClientAsync  # type: ignore
    HAS_OFFICIAL = True
except Exception:
    OfficialApifyClientAsync = None  # type: ignore
    HAS_OFFICIAL = False


class ApifyClient:
    """Unified Apify client used by the application.

    If the official `apify_client` package is available it will be used (async
    client); otherwise the module falls back to the prior httpx-based
    implementation.
    """

    def __init__(
        self,
        api_token: str,
        timeout_seconds: int = 300,
        max_retries: int = 3,
    ):
        self.api_token = api_token
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.base_url = "https://api.apify.com/v2"

        if HAS_OFFICIAL and OfficialApifyClientAsync is not None:
            # Official async client manages its own transport
            self._official_client = OfficialApifyClientAsync(self.api_token)
        else:
            self._official_client = None

    async def start_actor_run(self, actor_id: str, input_data: Dict[str, Any]) -> str:
        """Start an actor run and return the run ID.

        Uses the official async client (`ApifyClientAsync`) only.
        """
        if self._official_client is None:
            raise ApifyClientError("Official Apify client is not available. Install `apify-client`.")

        actor_client = self._official_client.actor(actor_id)
        call_kwargs: Dict[str, Any] = {"run_input": input_data}
        if "__apify_build" in input_data and input_data["__apify_build"]:
            call_kwargs["build"] = input_data.pop("__apify_build")

        result = await actor_client.call(**call_kwargs)
        run_id = None
        if isinstance(result, dict):
            run_id = result.get("id") or result.get("data", {}).get("id")
        else:
            run_id = getattr(result, "id", None)
        if not run_id:
            raise ApifyClientError(f"No run id returned from official client: {result}")
        logger.info(f"Started actor run: {run_id}")
        return run_id

    async def get_run_status(self, run_id: str) -> Dict[str, Any]:
        """Get the status of a run.

        Uses the Apify run metadata endpoint `/actor-runs/{id}` which is stable
        across both SDK and REST API usage.
        """
        if self._official_client is None:
            raise ApifyClientError("Official Apify client is not available. Install `apify-client`.")

        try:
            run = await self._official_client.run(run_id).get()
            if isinstance(run, dict):
                return run
            # SDK may return typed model objects instead of dicts.
            return {
                "id": getattr(run, "id", None),
                "status": getattr(run, "status", None),
                "statusMessage": getattr(run, "status_message", None),
                "defaultDatasetId": getattr(run, "default_dataset_id", None),
            }
        except Exception as e:
            # Keep transient behavior for not-yet-propagated runs.
            if "404" in str(e):
                return {"status": "UNKNOWN", "_404": True}
            raise ApifyClientError(f"Failed to get run status via official client: {str(e)}")



    async def wait_for_completion(self, run_id: str, check_interval_seconds: int = 5, max_wait_seconds: int = 3600) -> Dict[str, Any]:
        """Poll run status until completion, tolerating transient 404s.
        """
        start_time = datetime.utcnow()

        # Small initial delay to allow the platform to register the run
        await asyncio.sleep(1)

        while True:
            status = await self.get_run_status(run_id)
            # If get_run_status returned a transient 404 marker, wait and retry
            if status.get("_404"):
                await asyncio.sleep(1)
                elapsed = (datetime.utcnow() - start_time).total_seconds()
                if elapsed > max_wait_seconds:
                    raise ApifyClientError(f"Actor run {run_id} timed out after {max_wait_seconds}s (404s)")
                continue

            state = status.get("status")

            if state == "SUCCEEDED":
                logger.info(f"Actor run {run_id} completed successfully")
                return status

            if state in ("FAILED", "ABORTED", "TIMED_OUT"):
                error_msg = status.get("statusMessage", "Unknown error")
                raise ApifyClientError(f"Actor run {run_id} {state}: {error_msg}")

            elapsed = (datetime.utcnow() - start_time).total_seconds()
            if elapsed > max_wait_seconds:
                raise ApifyClientError(f"Actor run {run_id} timed out after {max_wait_seconds}s")

            logger.debug(f"Run {run_id} status: {state}, elapsed: {elapsed}s")
            await asyncio.sleep(check_interval_seconds)

    async def get_dataset(self, dataset_id: str) -> list[Dict[str, Any]]:
        """Download dataset items for a dataset ID.

        Uses the REST endpoint for simplicity and consistency.
        """
        if self._official_client is None:
            raise ApifyClientError("Official Apify client is not available. Install `apify-client`.")

        try:
            dataset_items = await self._official_client.dataset(dataset_id).list_items()
            items = getattr(dataset_items, "items", [])
            return items if isinstance(items, list) else []
        except Exception as e:
            raise ApifyClientError(f"Failed to get dataset via official client: {str(e)}")




    async def run_actor(self, actor_id: str, input_data: Dict[str, Any], check_interval_seconds: int = 5, max_wait_seconds: int = 3600, build: Optional[str] = None) -> list[Dict[str, Any]]:
        """Run an actor and return items from its default dataset.

        This method delegates to `start_actor_run`, `wait_for_completion`, and
        `get_dataset` to retrieve results. When the official client is present,
        `start_actor_run` uses it to create the run.
        """
        run_input = dict(input_data)
        if build:
            run_input["__apify_build"] = build
        run_id = await self.start_actor_run(actor_id, run_input)

        status = await self.wait_for_completion(run_id, check_interval_seconds=check_interval_seconds, max_wait_seconds=max_wait_seconds)

        dataset_id = status.get("defaultDatasetId")
        if not dataset_id:
            logger.warning(f"No dataset returned from run {run_id}")
            return []

        items = await self.get_dataset(dataset_id)
        logger.info(f"Retrieved {len(items)} items from dataset {dataset_id}")
        return items
