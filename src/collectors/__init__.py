"""Event collectors and extraction framework."""

from .base import EventCollector
from .orchestrator import CollectorOrchestrator

__all__ = ["EventCollector", "CollectorOrchestrator"]
