"""Official flood-data adapters and provenance-aware route comparison."""
from .adapter import FloodPolicy, FloodSnapshot, prepare_flood_hazards, flood_hazard_batch, registered_flood_adapters
from .freshness import Freshness, FreshnessPolicy, freshness
from .models import FloodAlert, SourceResult, WaterObservation, use_local_fallback
from .nws import NWSClient, parse_nws_alerts
from .routing import compare_flood_routes
from .usgs import USGSClient, parse_usgs_observations

__all__ = ['registered_flood_adapters', 'flood_hazard_batch', 'FloodPolicy', 'FloodSnapshot', 'prepare_flood_hazards', 'Freshness',
           'FreshnessPolicy', 'freshness', 'FloodAlert', 'WaterObservation',
           'SourceResult', 'use_local_fallback', 'NWSClient', 'USGSClient',
           'parse_nws_alerts', 'parse_usgs_observations', 'compare_flood_routes']
